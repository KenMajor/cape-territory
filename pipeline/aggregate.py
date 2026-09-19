"""H3 hex aggregation + composite Attack Score."""
from __future__ import annotations

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
from shapely.geometry import Polygon

from common import CRS_MA, CRS_WGS, log

HEX_COLS = [
    "hex_id", "town", "village", "n_sfh", "n_owner_occ", "pct_owner_occ", "n_absentee_out_of_state",
    "median_living_sqft", "median_footprint_sqft", "n_big_roof", "n_recent_sale", "pct_recent_sale",
    "n_new_owner_12mo", "score",
]


def assign_hexes(df: gpd.GeoDataFrame, res: int) -> gpd.GeoDataFrame:
    """Add lon/lat (WGS84 representative point) and hex_id to every row with geometry."""
    df = df.copy()
    pts = df.geometry.representative_point()
    pts_wgs = gpd.GeoSeries(pts, crs=CRS_MA).to_crs(CRS_WGS)
    df["lon"] = pts_wgs.x.round(5)
    df["lat"] = pts_wgs.y.round(5)
    ok = df["lon"].notna()
    df["hex_id"] = None
    df.loc[ok, "hex_id"] = [h3.latlng_to_cell(la, lo, res) for la, lo in zip(df.loc[ok, "lat"], df.loc[ok, "lon"])]
    return df


def hex_polygon(hex_id: str) -> Polygon:
    return Polygon([(lng, lat) for lat, lng in h3.cell_to_boundary(hex_id)])


def _mode(s: pd.Series):
    s = s.dropna()
    return s.mode().iloc[0] if len(s) else None


def aggregate(all_sfh: pd.DataFrame, cfg: dict, big_roof_threshold: float | None, solar_pen: dict) -> tuple[gpd.GeoDataFrame, dict]:
    """all_sfh: SFH rows from every town, with hex_id, is_owner_occ, living_sqft, footprint_sqft, recent_sale ...

    solar_pen: {town_name: penetration_pct or None}
    """
    d = all_sfh[all_sfh["hex_id"].notna()].copy()
    d["big_roof"] = (d["footprint_sqft"] >= big_roof_threshold) if big_roof_threshold else False
    d["abs_oos"] = d["owner_occ_class"] == "ABSENTEE_OUT_OF_STATE"

    g = d.groupby("hex_id")
    hx = pd.DataFrame({
        "town": g["town"].agg(_mode),
        "village": g["village"].agg(_mode),
        "n_sfh": g.size(),
        "n_owner_occ": g["is_owner_occ"].sum(),
        "n_absentee_out_of_state": g["abs_oos"].sum(),
        "median_living_sqft": g["living_sqft"].median(),
        "median_footprint_sqft": g["footprint_sqft"].median(),
        "n_big_roof": g["big_roof"].sum(),
        "n_recent_sale": g["recent_sale"].sum(),
        "n_new_owner_12mo": g["new_owner_12mo"].sum(),
    }).reset_index()
    hx["pct_owner_occ"] = (100 * hx["n_owner_occ"] / hx["n_sfh"]).round(1)
    hx["pct_recent_sale"] = (100 * hx["n_recent_sale"] / hx["n_sfh"]).round(1)

    n_all = len(hx)
    min_n = int(cfg.get("min_homes_per_hex", 5))
    hx = hx[hx["n_sfh"] >= min_n].copy()
    n_dropped = n_all - len(hx)

    # Town-level solar penetration, inverted (less solar = more headroom). Constant within a town in v1.
    hx["solar_penetration_pct"] = hx["town"].map(solar_pen)
    pen = hx["solar_penetration_pct"].astype(float)
    hx["solar_penetration_inv"] = (-pen).where(pen.notna())

    # Percentile-rank each component across ALL Phase 1 hexes, then weight.
    w = cfg["scoring"]["weights"]
    comps = {
        "pct_owner_occ": "r_owner_occ",
        "median_footprint_sqft": "r_footprint",
        "pct_recent_sale": "r_recent_sale",
        "n_sfh": "r_density",
        "solar_penetration_inv": "r_solar_inv",
    }
    score = pd.Series(0.0, index=hx.index)
    wsum = 0.0
    for col, rcol in comps.items():
        r = hx[col].rank(pct=True, method="average")
        hx[rcol] = r.round(3)
        if hx[col].notna().any():
            score = score + w[col] * r.fillna(r.mean())
            wsum += w[col]
    hx["score"] = (100 * score / wsum).round(1) if wsum else np.nan

    hx["geometry"] = hx["hex_id"].map(hex_polygon)
    hx = gpd.GeoDataFrame(hx, geometry="geometry", crs=CRS_WGS)
    info = {
        "n_hexes_total": int(n_all),
        "n_hexes_dropped_sparse": int(n_dropped),
        "n_hexes_kept": int(len(hx)),
        "hex_per_town": hx.groupby("town").size().to_dict(),
        "score_weights_used": {k: w[k] for k in comps},
    }
    log(f"  {n_all:,} hexes, dropped {n_dropped} with < {min_n} homes, kept {len(hx):,}: {info['hex_per_town']}")
    return hx, info
