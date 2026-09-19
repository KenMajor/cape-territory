"""Town-level installed solar from the MassCEC Production Tracking System (PTS) Excel."""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from common import log


def _find_header_row(path: Path) -> tuple[int, str | None]:
    raw = pd.read_excel(path, sheet_name=0, header=None, nrows=40)
    as_of = None
    for i, row in raw.iterrows():
        vals = [str(v) for v in row.tolist() if pd.notna(v)]
        joined = " ".join(vals)
        m = re.search(r"as of\s+([\d]{1,2}[-/][\d]{1,2}[-/][\d]{2,4})", joined, re.I)
        if m and as_of is None:
            as_of = m.group(1)
        if any(re.search(r"capacity", v, re.I) for v in vals) and any(re.search(r"^city$|municipal", v, re.I) for v in vals):
            return i, as_of
    raise RuntimeError("Could not find the header row (Capacity / City) in the PTS Excel")


def _col(df: pd.DataFrame, pattern: str) -> str | None:
    for c in df.columns:
        if re.search(pattern, str(c).replace("\n", " "), re.I):
            return c
    return None


def load_pts(path: Path) -> tuple[pd.DataFrame, pd.Timestamp | None]:
    hdr, as_of = _find_header_row(path)
    df = pd.read_excel(path, sheet_name=0, header=hdr)
    c_cap = _col(df, r"capacity")
    c_date = _col(df, r"date.*service|in.?service")
    c_city = _col(df, r"^city$|municipal")
    c_zip = _col(df, r"^zip")
    c_fac = _col(df, r"facility|sector|customer.?type")
    out = pd.DataFrame({
        "kw": pd.to_numeric(df[c_cap], errors="coerce"),
        "date": pd.to_datetime(df[c_date], errors="coerce"),
        "city": df[c_city].astype("string").str.strip().str.upper(),
        "zip": df[c_zip].astype("string").str.extract(r"(\d+)")[0].str.zfill(5) if c_zip else pd.NA,
        "facility": df[c_fac].astype("string").str.upper() if c_fac else pd.NA,
    })
    as_of_ts = pd.to_datetime(as_of, errors="coerce") if as_of else None
    if as_of_ts is None or pd.isna(as_of_ts):
        as_of_ts = out["date"].max()
    log(f"  PTS: {len(out):,} systems statewide, report as of {as_of_ts.date()}, columns: kW='{c_cap}', date='{c_date}', city='{c_city}', zip='{c_zip}', facility='{c_fac}'")
    return out, as_of_ts


def town_solar(pts: pd.DataFrame, as_of: pd.Timestamp, town: dict) -> dict:
    names = {town["TOWN"]} | {v.upper() for v in town["zips"].values()} | set(town["village_aliases"])
    by_city = pts["city"].isin(names)
    by_zip = pts["zip"].isin(list(town["zips"]))
    sel = pts[by_city | by_zip]
    res = sel[sel["facility"].fillna("").str.contains("RESIDENTIAL")]
    cutoff = as_of - pd.DateOffset(months=24)
    return {
        "solar_installs_all_sectors": int(len(sel)),
        "solar_installs_res": int(len(res)),
        "solar_kw_res": round(float(res["kw"].sum()), 1),
        "solar_installs_24mo": int((res["date"] >= cutoff).sum()),
        "solar_24mo_window": f"{cutoff.date()} to {as_of.date()}",
        "solar_matched_by_city": int(by_city.sum()),
        "solar_matched_by_zip_only": int((by_zip & ~by_city).sum()),
    }
