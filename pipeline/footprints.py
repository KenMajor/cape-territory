"""Join MassGIS building footprints (STRUCTURES_POLY) to parcels -> roof area.

footprint_sqft = area of the LARGEST structure polygon whose centroid falls inside
the parcel (the house, not the shed). Areas computed in EPSG:26986 (meters) then
converted to sq ft.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from common import CRS_MA, SQM_TO_SQFT, log


def load_structures(zp: Path) -> gpd.GeoDataFrame:
    with zipfile.ZipFile(zp) as z:
        shp = next(m for m in z.namelist() if re.search(r"\.shp$", m, re.I))
    s = gpd.read_file(f"zip://{zp}!{shp}", columns=["STRUCT_ID"])
    if s.crs is None:
        s = s.set_crs(CRS_MA)
    elif s.crs.to_epsg() != 26986:
        s = s.to_crs(CRS_MA)
    s = s[s.geometry.notna() & ~s.geometry.is_empty].copy()
    s["fp_sqft"] = s.geometry.area * SQM_TO_SQFT
    s["geometry"] = s.geometry.centroid
    return s


def join_footprints(df: gpd.GeoDataFrame, structures: gpd.GeoDataFrame, town: dict) -> tuple[pd.Series, dict]:
    """Return footprint_sqft indexed like df (only computed for SFH rows with a polygon)."""
    target = df[df["is_sfh"] & df.geometry.notna()][["LOC_ID", "geometry"]].copy()
    target = gpd.GeoDataFrame(target, geometry="geometry", crs=CRS_MA)
    joined = gpd.sjoin(structures[["fp_sqft", "geometry"]], target, predicate="within", how="inner")
    best = joined.groupby("LOC_ID")["fp_sqft"].max()
    n_struct_in_sfh = int(len(joined))
    fp = df["LOC_ID"].map(best)
    fp = fp.where(df["is_sfh"])
    hit = float(fp[df["is_sfh"]].notna().mean()) if df["is_sfh"].any() else 0.0
    info = {
        "n_structures": int(len(structures)),
        "n_structures_in_sfh_parcels": n_struct_in_sfh,
        "footprint_hit_rate_pct": round(100 * hit, 1),
        "median_footprint_sqft": float(np.nanmedian(fp[df["is_sfh"]].astype(float))) if hit else None,
    }
    log(f"  {town['name']}: {info['n_structures']:,} structures, hit rate on SFH {info['footprint_hit_rate_pct']}%, "
        f"median footprint {info['median_footprint_sqft']:.0f} sqft")
    return fp, info
