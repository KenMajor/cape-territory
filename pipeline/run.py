"""Orchestrate the whole pipeline and print the sanity report.

    python pipeline/run.py               # full run
    python pipeline/run.py --no-download # assume data/raw is populated
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone

import geopandas as gpd
import numpy as np
import pandas as pd

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

from aggregate import aggregate, assign_hexes  # noqa: E402
from census import fetch_acs  # noqa: E402
from classify import classify_town  # noqa: E402
from common import CRS_MA, DOCS_DATA, INTERIM, RAW, ensure_dirs, load_config, log, section  # noqa: E402
from download import download_all  # noqa: E402
from export import export_all  # noqa: E402
from footprints import join_footprints, load_structures  # noqa: E402
from parcels import load_town_parcels  # noqa: E402
from solar import load_pts, town_solar  # noqa: E402


def load_town_boundaries(zp, cfg) -> gpd.GeoDataFrame:
    g = gpd.read_file(f"zip://{zp}!TOWNSSURVEY_POLYM.shp", columns=["TOWN", "TOWN_ID"])
    g = g[g["TOWN"].isin([t["TOWN"] for t in cfg["towns"]])].to_crs(CRS_MA)
    g = g.dissolve(by="TOWN", as_index=False, aggfunc={"TOWN_ID": "first"})
    return g


def main() -> int:
    t0 = time.time()
    cfg = load_config()
    ensure_dirs()
    meta = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "phase": cfg["phase"],
            "recent_sale_since": cfg["recent_sale_since"], "new_owner_since": cfg["new_owner_since"], "notes": []}

    section("1. Download")
    if "--no-download" in sys.argv:
        paths = {}
        for t in cfg["towns"]:
            paths[f"parcels:{t['slug']}"] = next((RAW / "parcels").glob(f"L3_SHP_M{t['massgis_town_id']:03d}_*.zip"))
            paths[f"structures:{t['slug']}"] = RAW / "structures" / f"structures_poly_{t['massgis_town_id']}.zip"
        paths["towns"] = next((RAW / "towns").glob("*.zip"))
        x = sorted((RAW / "masscec").glob("*.xls*"))
        if x:
            paths["masscec"] = x[-1]
    else:
        paths = download_all(cfg)

    section("2. Parcels + assessor")
    town_frames, town_info = {}, {}
    for t in cfg["towns"]:
        gdf, info = load_town_parcels(paths[f"parcels:{t['slug']}"], t)
        town_frames[t["name"]] = gdf
        town_info[t["name"]] = info

    section("3. Classification")
    for t in cfg["towns"]:
        df, info = classify_town(town_frames[t["name"]], t, cfg)
        town_frames[t["name"]] = df
        town_info[t["name"]].update(info)

    section("4. Building footprints")
    for t in cfg["towns"]:
        structures = load_structures(paths[f"structures:{t['slug']}"])
        fp, info = join_footprints(town_frames[t["name"]], structures, t)
        town_frames[t["name"]]["footprint_sqft"] = fp
        town_info[t["name"]].update(info)

    # Interim (gitignored) for debugging
    for t in cfg["towns"]:
        town_frames[t["name"]].drop(columns=["site_street"]).to_pickle(INTERIM / f"parcels_{t['slug']}.pkl")

    section("5. Hexes")
    frames = []
    for t in cfg["towns"]:
        d = assign_hexes(town_frames[t["name"]], cfg["h3_resolution"])
        frames.append(d)
    all_rows = pd.concat(frames, ignore_index=True)
    sfh = all_rows[all_rows["is_sfh"]].copy()
    pctl = cfg["scoring"]["big_roof_percentile"]
    big_roof_threshold = float(np.nanpercentile(sfh["footprint_sqft"].astype(float), pctl)) if sfh["footprint_sqft"].notna().any() else None
    sfh["big_roof"] = sfh["footprint_sqft"] >= big_roof_threshold if big_roof_threshold else False
    meta["big_roof_threshold_sqft"] = round(big_roof_threshold) if big_roof_threshold else None
    log(f"  big-roof threshold (p{pctl} footprint across Phase 1): {meta['big_roof_threshold_sqft']} sqft")

    section("6. Solar (MassCEC PTS, town level)")
    solar_by_town = {}
    if paths.get("masscec"):
        try:
            pts, as_of = load_pts(paths["masscec"])
            meta["pts_as_of"] = str(as_of.date())
            meta["pts_file"] = paths["masscec"].name
            for t in cfg["towns"]:
                solar_by_town[t["name"]] = town_solar(pts, as_of, t)
                log(f"  {t['name']}: {solar_by_town[t['name']]}")
        except Exception as e:  # noqa: BLE001
            log(f"  WARN PTS parse failed: {e}")
            meta["notes"].append(f"MassCEC PTS parse failed: {e}")
    else:
        meta["notes"].append("MassCEC PTS file unavailable; solar fields null.")

    section("7. Census ACS")
    census, census_status = fetch_acs(cfg)
    meta["census_status"] = census_status
    if census_status.startswith("pending"):
        meta["notes"].append("Census fields are pending: add CENSUS_API_KEY as an Actions secret and re-run.")

    section("8. Aggregate + score")
    solar_pen = {}
    for t in cfg["towns"]:
        s = solar_by_town.get(t["name"])
        n_sfh = town_info[t["name"]]["n_sfh"]
        solar_pen[t["name"]] = round(100 * s["solar_installs_res"] / n_sfh, 2) if s and n_sfh else None
    hexes, hinfo = aggregate(sfh, cfg, big_roof_threshold, solar_pen)

    section("9. Scorecard")
    scorecard = []
    for t in cfg["towns"]:
        ti = town_info[t["name"]]
        oc = ti["owner_occ_counts"]
        s = solar_by_town.get(t["name"], {})
        row = {
            "town": t["name"], "massgis_town_id": t["massgis_town_id"], "cousub_fips": t["cousub_fips"],
            "fy_vintage": ti["fy_vintage"],
            "n_parcels": ti["n_parcels"], "n_sfh": ti["n_sfh"], "n_secondary_104_105_109": ti["n_secondary"],
            "n_owner_occ": ti["n_owner_occ"], "pct_owner_occ_parcel_method": ti["pct_owner_occ_parcel_method"],
            "n_owner_occ_exact": oc["OWNER_OCC"], "n_likely_owner_occ": oc["LIKELY_OWNER_OCC"],
            "n_absentee_local": oc["ABSENTEE_LOCAL"], "n_absentee_out_of_state": oc["ABSENTEE_OUT_OF_STATE"],
            "n_unknown": oc["UNKNOWN"],
            "exemption_agreement_pct": ti.get("exemption_agreement_pct"),
            "exemption_note": ti.get("exemption_note") if t.get("residential_exemption") else "n/a (town has no residential exemption)",
            **census.get(t["cousub_fips"], {}),
            "median_living_sqft": ti["median_living_sqft"], "median_footprint_sqft": round(ti["median_footprint_sqft"]) if ti["median_footprint_sqft"] else None,
            "footprint_hit_rate_pct": ti["footprint_hit_rate_pct"],
            "n_big_roof": int(sfh[(sfh["town"] == t["name"]) & sfh["big_roof"]].shape[0]),
            "n_recent_sale": ti["n_recent_sale"], "n_new_owner_12mo": ti["n_new_owner_12mo"],
            "ls_date_unparseable": ti["ls_date_unparseable"], "latest_sale_date": ti["latest_sale_date"],
            "solar_installs_res": s.get("solar_installs_res"), "solar_installs_24mo": s.get("solar_installs_24mo"),
            "solar_kw_res": s.get("solar_kw_res"), "solar_24mo_window": s.get("solar_24mo_window"),
            "solar_penetration_pct": solar_pen[t["name"]],
            "n_hex": int(hinfo["hex_per_town"].get(t["name"], 0)),
            "target_doors": ti["n_owner_occ"],
            "villages": ti["village_counts"],
        }
        scorecard.append(row)

    section("10. Sanity checks")
    sanity = []

    def check(name, ok, detail):
        sanity.append({"check": name, "ok": bool(ok), "detail": detail})
        log(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    for t in cfg["towns"]:
        ti = town_info[t["name"]]
        lo, hi = t["sanity_sfh_range"]
        check(f"{t['name']} SFH count in range", lo <= ti["n_sfh"] <= hi, f"{ti['n_sfh']:,} (expected {lo:,}-{hi:,})")
        p = ti["pct_owner_occ_parcel_method"]
        check(f"{t['name']} owner-occ share 40-75%", p is not None and 40 <= p <= 75, f"{p}%")
        check(f"{t['name']} UNKNOWN owner-occ < 5%", ti["pct_unknown"] < 5, f"{ti['pct_unknown']}%")
        check(f"{t['name']} footprint hit rate >= 90%", ti["footprint_hit_rate_pct"] >= 90, f"{ti['footprint_hit_rate_pct']}%")
        check(f"{t['name']} LS_DATE unparseable < 3%", ti["ls_date_unparseable_pct"] < 3, f"{ti['ls_date_unparseable_pct']}% ({ti['ls_date_unparseable']} rows)")

    section("11. Export")
    towns_gdf = load_town_boundaries(paths["towns"], cfg)
    files = export_all(cfg, hexes, sfh, towns_gdf, scorecard, meta, sanity)
    total_mb = files["_total_bytes"] / 1e6
    check("docs/data total size < 15 MB", total_mb < 15, f"{total_mb:.2f} MB")
    # re-write scorecard with the final sanity list (size check included)
    sc_path = DOCS_DATA / "town_scorecard.json"
    sc = json.loads(sc_path.read_text())
    sc["sanity"] = sanity
    sc_path.write_text(json.dumps(sc, indent=1))
    from export import scorecard_markdown  # noqa: E402
    (__import__("common").REPORTS / "town_scorecard.md").write_text(scorecard_markdown(scorecard, meta, sanity))

    section("SANITY REPORT")
    fails = [s for s in sanity if not s["ok"]]
    for s in sanity:
        log(f"  [{'PASS' if s['ok'] else 'FAIL'}] {s['check']}: {s['detail']}")
    log()
    log((__import__("common").REPORTS / "town_scorecard.md").read_text())
    log(f"Done in {time.time()-t0:.0f}s. {len(fails)} sanity check(s) failed." if fails else f"Done in {time.time()-t0:.0f}s. All sanity checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
