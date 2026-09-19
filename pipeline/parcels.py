"""Load MassGIS L3 parcels (polygons) + assessor table per town; normalize fields.

Output: one GeoDataFrame per town with one row per ASSESSOR record (condos keep
their many rows), each carrying the parcel polygon joined on LOC_ID (may be null
when the assessor row has no polygon).
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd

from common import CRS_MA, log

# Assessor fields we keep. Shapefile names may be truncated to 10 chars; map explicitly.
ASSESS_FIELDS = [
    "LOC_ID", "PROP_ID", "SITE_ADDR", "ADDR_NUM", "FULL_STR", "CITY", "ZIP",
    "OWNER1", "OWN_ADDR", "OWN_CITY", "OWN_STATE", "OWN_ZIP",
    "USE_CODE", "LS_DATE", "LS_PRICE", "LOT_SIZE", "LOT_UNITS",
    "BLD_AREA", "RES_AREA", "YEAR_BUILT", "STYLE", "STORIES", "UNITS",
    "TOTAL_VAL", "BLDG_VAL", "LAND_VAL", "FY",
]
# Any known truncated / alternate spellings -> canonical.
FIELD_ALIASES = {
    "YEAR_BUIL": "YEAR_BUILT", "YR_BUILT": "YEAR_BUILT",
    "TOTAL_VALU": "TOTAL_VAL", "SITE_ADD": "SITE_ADDR",
}
# Optional owner-occupancy / exemption fields, if a town's extract exposes one.
EXEMPTION_FIELD_CANDIDATES = ["RES_EXEMPT", "RESEXEMPT", "RES_EXMPT", "OWNER_OCC", "OWN_OCC", "EXEMPTION", "EXEMPT"]


def _members(zp: Path) -> list[str]:
    with zipfile.ZipFile(zp) as z:
        return z.namelist()


def _find(members: list[str], pattern: str) -> str:
    rx = re.compile(pattern, re.I)
    hits = [m for m in members if rx.search(m)]
    if not hits:
        raise FileNotFoundError(f"No member matching {pattern!r} in zip; members: {members[:10]}")
    return hits[0]


def load_town_parcels(zp: Path, town: dict) -> tuple[gpd.GeoDataFrame, dict]:
    """Return (assessor rows with parcel geometry, info dict)."""
    members = _members(zp)
    assess_m = _find(members, r"(Assess[^/]*|L3_ASSESS[^/]*)\.dbf$")
    taxpar_m = _find(members, r"(TaxPar[^/]*|L3_TAXPAR_POLY[^/]*)\.shp$")

    assess = gpd.read_file(f"zip://{zp}!{assess_m}")
    assess = assess.rename(columns=FIELD_ALIASES)
    if "geometry" in assess.columns:
        assess = pd.DataFrame(assess.drop(columns="geometry"))
    missing = [c for c in ASSESS_FIELDS if c not in assess.columns]
    if missing:
        log(f"  WARN {town['name']}: assessor fields missing from extract: {missing}")
        for c in missing:
            assess[c] = None
    exempt_field = next((c for c in EXEMPTION_FIELD_CANDIDATES if c in assess.columns), None)
    keep = ASSESS_FIELDS + ([exempt_field] if exempt_field else [])
    assess = assess[keep].copy()
    if exempt_field:
        assess = assess.rename(columns={exempt_field: "EXEMPT_RAW"})

    taxpar = gpd.read_file(f"zip://{zp}!{taxpar_m}", columns=["LOC_ID", "POLY_TYPE"])
    if taxpar.crs is None:
        taxpar = taxpar.set_crs(CRS_MA)
    elif taxpar.crs.to_epsg() != 26986:
        taxpar = taxpar.to_crs(CRS_MA)
    # One polygon per LOC_ID: dissolve multi-part parcels (rare) so joins stay 1:1.
    taxpar = taxpar[taxpar.geometry.notna() & taxpar.LOC_ID.notna()]
    dup = taxpar.LOC_ID.duplicated().sum()
    if dup:
        taxpar = taxpar.dissolve(by="LOC_ID", as_index=False, aggfunc={"POLY_TYPE": "first"})

    # Normalize scalar fields.
    for c in ("SITE_ADDR", "ADDR_NUM", "FULL_STR", "CITY", "OWNER1", "OWN_ADDR", "OWN_CITY", "OWN_STATE", "STYLE"):
        assess[c] = assess[c].astype("string").str.strip().str.upper()
    assess["ZIP"] = assess["ZIP"].astype("string").str.extract(r"(\d{5})")[0]
    assess["OWN_ZIP"] = assess["OWN_ZIP"].astype("string").str.extract(r"(\d{5})")[0]
    assess["USE_CODE_RAW"] = assess["USE_CODE"].astype("string").str.strip()
    assess["use_code"] = assess["USE_CODE_RAW"].map(normalize_use_code)
    for c in ("RES_AREA", "BLD_AREA", "LS_PRICE", "TOTAL_VAL", "BLDG_VAL", "LAND_VAL", "YEAR_BUILT", "UNITS", "LOT_SIZE"):
        assess[c] = pd.to_numeric(assess[c], errors="coerce")
    assess["FY"] = pd.to_numeric(assess["FY"], errors="coerce")
    assess["town"] = town["name"]
    assess["town_id"] = town["massgis_town_id"]

    gdf = gpd.GeoDataFrame(
        assess.merge(taxpar[["LOC_ID", "POLY_TYPE", "geometry"]], on="LOC_ID", how="left"),
        geometry="geometry", crs=CRS_MA,
    )
    fy = int(assess["FY"].mode().iloc[0]) if assess["FY"].notna().any() else None
    info = {
        "assessor_rows": int(len(assess)),
        "parcel_polygons": int(len(taxpar)),
        "assessor_rows_with_polygon": int(gdf.geometry.notna().sum()),
        "fy_vintage": fy,
        "exemption_field": "EXEMPT_RAW" if exempt_field else None,
        "exemption_field_source": exempt_field,
        "assessor_member": assess_m,
        "taxpar_member": taxpar_m,
    }
    log(f"  {town['name']}: {info['assessor_rows']:,} assessor rows, {info['parcel_polygons']:,} parcel polygons, "
        f"{info['assessor_rows_with_polygon']:,} rows joined to a polygon, FY{fy}")
    return gdf, info


# 3-digit DOR codes for residential dwellings. A 4-char code like '0101' is a
# spurious-zero version of one of these; '0130' is mixed-use '013' + local suffix.
DWELLING_CODES = {"101", "102", "103", "104", "105", "106", "107", "108", "109", "111", "112", "113", "114"}


def normalize_use_code(raw) -> str | None:
    """Reduce a town's use code to the 3-digit DOR base code.

    Rules (config spec, processing rule 1):
      * '0101' -> '101': strip a spurious leading zero only when the remainder is a
        valid 3-digit residential code (1xx).
      * '1010', '101V', '1013' -> '101': the 4th character is a local suffix.
      * '013', '031' (3-digit mixed-use starting with 0) -> unchanged.
    """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)) or raw is pd.NA:
        return None
    s = str(raw).strip().upper()
    if not s:
        return None
    if len(s) == 4 and s[0] == "0" and s[1:4] in DWELLING_CODES:
        return s[1:4]
    if len(s) >= 3:
        return s[:3]
    return s.zfill(3)
