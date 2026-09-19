"""Write small, web-ready files to docs/data/ and the scorecard to reports/.

Property keys in the GeoJSON are deliberately short (bandwidth); README documents them.
Coordinates are rounded to 5 decimals (~1 m).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import mapping

from common import CRS_WGS, DOCS_DATA, REPORTS, log

HEX_PROPS = {  # long -> short
    "hex_id": "h", "town": "t", "village": "v", "n_sfh": "n", "n_owner_occ": "oo", "pct_owner_occ": "po",
    "n_absentee_out_of_state": "ao", "median_living_sqft": "ml", "median_footprint_sqft": "mf",
    "n_big_roof": "br", "n_recent_sale": "rs", "pct_recent_sale": "pr", "n_new_owner_12mo": "no",
    "score": "s", "solar_penetration_pct": "sp",
    "r_owner_occ": "r_oo", "r_footprint": "r_mf", "r_recent_sale": "r_rs", "r_density": "r_n", "r_solar_inv": "r_sol",
}


def _round_coords(obj, nd=5):
    if isinstance(obj, (list, tuple)):
        if obj and isinstance(obj[0], (int, float)):
            return [round(float(x), nd) for x in obj]
        return [_round_coords(o, nd) for o in obj]
    return obj


def _clean(v):
    if v is None or v is pd.NA:
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        return None if np.isnan(v) else (int(v) if float(v).is_integer() else round(float(v), 3))
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    return v


def write_geojson(gdf: gpd.GeoDataFrame, props: dict, path: Path, nd=5) -> int:
    if gdf.crs is not None and gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(CRS_WGS)
    feats = []
    for _, row in gdf.iterrows():
        geom = mapping(row.geometry)
        geom["coordinates"] = _round_coords(geom["coordinates"], nd)
        p = {short: _clean(row[long]) for long, short in props.items() if long in gdf.columns}
        feats.append({"type": "Feature", "geometry": geom, "properties": p})
    fc = {"type": "FeatureCollection", "features": feats}
    path.write_text(json.dumps(fc, separators=(",", ":")))
    return len(feats)


def export_all(cfg: dict, hexes: gpd.GeoDataFrame, sfh: pd.DataFrame, towns_gdf: gpd.GeoDataFrame,
               scorecard: list[dict], meta: dict, sanity: list[dict]) -> dict:
    DOCS_DATA.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    files = {}
    manifest_towns = []
    for t in cfg["towns"]:
        slug = t["slug"]
        hx = hexes[hexes["town"] == t["name"]]
        n = write_geojson(hx, HEX_PROPS, DOCS_DATA / f"hexes_{slug}.geojson")
        files[f"hexes_{slug}.geojson"] = n

        s = sfh[(sfh["town"] == t["name"]) & sfh["recent_sale"] & sfh["lon"].notna()]
        s = gpd.GeoDataFrame({"d": s["sale_ym"].values}, geometry=gpd.points_from_xy(s["lon"], s["lat"]), crs=CRS_WGS)
        files[f"sales_{slug}.geojson"] = write_geojson(s, {"d": "d"}, DOCS_DATA / f"sales_{slug}.geojson")

        b = sfh[(sfh["town"] == t["name"]) & sfh["big_roof"] & sfh["lon"].notna()]
        b = gpd.GeoDataFrame({"f": b["footprint_sqft"].round(0).values}, geometry=gpd.points_from_xy(b["lon"], b["lat"]), crs=CRS_WGS)
        files[f"bigroof_{slug}.geojson"] = write_geojson(b, {"f": "f"}, DOCS_DATA / f"bigroof_{slug}.geojson")

        tb = towns_gdf[towns_gdf["TOWN"] == t["TOWN"]].to_crs(CRS_WGS)
        bounds = tb.total_bounds.round(5).tolist() if len(tb) else hx.total_bounds.round(5).tolist()
        manifest_towns.append({
            "name": t["name"], "slug": slug, "bounds": bounds, "n_hex": int(len(hx)),
            "villages": sorted(set(v for v in t["zips"].values())),
        })

    tg = towns_gdf.copy()
    tg["geometry"] = tg.geometry.simplify(15)  # meters in EPSG:26986
    tg["name"] = tg["TOWN"].str.title()
    files["towns.geojson"] = write_geojson(tg, {"name": "name"}, DOCS_DATA / "towns.geojson")

    # Legend breaks: quantiles across all Phase 1 hexes so towns colour consistently.
    def q(col):
        v = hexes[col].dropna()
        return [round(float(x), 1) for x in np.quantile(v, [0.2, 0.4, 0.6, 0.8])] if len(v) else None

    manifest = {
        "generated_at": meta["generated_at"],
        "phase": cfg["phase"],
        "h3_resolution": cfg["h3_resolution"],
        "recent_sale_since": cfg["recent_sale_since"],
        "new_owner_since": cfg["new_owner_since"],
        "towns": manifest_towns,
        "weights": cfg["scoring"]["weights"],
        "breaks": {
            "score": q("score"),
            "pct_owner_occ": q("pct_owner_occ"),
            "median_footprint_sqft": q("median_footprint_sqft"),
            "pct_recent_sale": q("pct_recent_sale"),
            "solar_penetration_pct": sorted({round(float(x), 2) for x in hexes["solar_penetration_pct"].dropna().unique()}),
        },
        "big_roof_threshold_sqft": meta.get("big_roof_threshold_sqft"),
        "files": files,
    }
    (DOCS_DATA / "manifest.json").write_text(json.dumps(manifest, indent=1))

    sc = {"generated_at": meta["generated_at"], "meta": meta, "towns": scorecard, "sanity": sanity}
    (DOCS_DATA / "town_scorecard.json").write_text(json.dumps(sc, indent=1, default=_clean))

    md = scorecard_markdown(scorecard, meta, sanity)
    (REPORTS / "town_scorecard.md").write_text(md)

    total = sum(p.stat().st_size for p in DOCS_DATA.glob("*"))
    files["_total_bytes"] = total
    log(f"  wrote {len(files)-1} files to docs/data/, {total/1e6:.2f} MB total")
    for k, v in files.items():
        if not k.startswith("_"):
            log(f"    {k}: {v:,} features, {(DOCS_DATA / k).stat().st_size/1e3:.0f} KB")
    return files


SCORECARD_ROWS = [
    ("fy_vintage", "Assessor FY"),
    ("n_parcels", "Parcels (all uses)"),
    ("n_sfh", "Single-family homes"),
    ("n_owner_occ", "Owner-occupied (parcel method)"),
    ("pct_owner_occ_parcel_method", "Owner-occ % (parcel method)"),
    ("n_owner_occ_exact", "  of which exact address match"),
    ("n_likely_owner_occ", "  of which PO Box, same ZIP"),
    ("n_absentee_local", "Absentee, MA"),
    ("n_absentee_out_of_state", "Absentee, out of state"),
    ("n_unknown", "Owner address missing"),
    ("exemption_agreement_pct", "Res. exemption agreement %"),
    ("census_pct_owner_occupied", "Census owner-occ % (of occupied)"),
    ("census_pct_seasonal_vacant", "Census seasonal-vacant % (of units)"),
    ("census_pct_renter", "Census renter % (of occupied)"),
    ("median_living_sqft", "Median living sqft"),
    ("median_footprint_sqft", "Median footprint sqft"),
    ("footprint_hit_rate_pct", "Footprint join hit %"),
    ("n_big_roof", "Big-roof homes (top 15%)"),
    ("n_recent_sale", "Sold since recent-sale date"),
    ("n_new_owner_12mo", "New owner (12 mo)"),
    ("ls_date_unparseable", "Unparseable sale dates"),
    ("latest_sale_date", "Latest sale date in extract"),
    ("solar_installs_res", "Solar installs, residential (PTS)"),
    ("solar_installs_24mo", "  in 24 mo before PTS date"),
    ("solar_penetration_pct", "Solar penetration % (installs / SFH)"),
    ("n_hex", "Map hexes"),
    ("target_doors", "TARGET DOORS (owner-occ SFH)"),
]


def _fmt(v, key=None):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "pending"
    if key in ("fy_vintage",):
        return str(int(v))
    if isinstance(v, float):
        return f"{v:,.0f}" if key and key.startswith("median") else f"{v:,.1f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def scorecard_markdown(scorecard: list[dict], meta: dict, sanity: list[dict]) -> str:
    names = [t["town"] for t in scorecard]
    lines = [f"# Town scorecard — Phase {meta['phase']} (Mid-Cape)", "",
             f"Generated {meta['generated_at']}. Recent sale = since {meta['recent_sale_since']}; new owner = since {meta['new_owner_since']}.",
             f"Solar source: MassCEC PTS report as of {meta.get('pts_as_of') or 'n/a'}. Census: {meta.get('census_status')}.", "",
             "| Metric | " + " | ".join(names) + " |", "|---|" + "---:|" * len(names)]
    for key, label in SCORECARD_ROWS:
        cells = []
        for t in scorecard:
            if key == "exemption_agreement_pct" and t.get(key) is None:
                cells.append(t.get("exemption_note") or "pending")
            else:
                cells.append(_fmt(t.get(key), key))
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines += ["", "## Sanity checks", "", "| Check | Result | Detail |", "|---|---|---|"]
    for s in sanity:
        lines.append(f"| {s['check']} | {'PASS' if s['ok'] else 'FAIL'} | {s['detail']} |")
    notes = list(meta.get("notes", []))
    latest = [t.get("latest_sale_date") for t in scorecard if t.get("latest_sale_date")]
    if latest and max(latest) < meta["new_owner_since"]:
        notes.append(f"Assessor sale records end {max(latest)} (before the new-owner cutoff {meta['new_owner_since']}), "
                     "so 'New owner (12 mo)' is near zero until MassGIS publishes newer extracts. 'Sold since recent-sale date' is complete.")
    if notes:
        lines += ["", "## Notes", ""] + [f"- {n}" for n in notes]
    return "\n".join(lines) + "\n"
