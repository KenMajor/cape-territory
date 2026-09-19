"""Classify assessor rows: target set, owner-occupancy, home size, recent sale, village.

Works on the per-town GeoDataFrame from parcels.py and returns the same frame with
new lower-case columns. All rows are kept (interim); the SFH filter is a flag.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from common import log

PRIMARY_USE = {"101"}
SECONDARY_USE = {"104", "105", "109"}

SUFFIX_MAP = {
    "STREET": "ST", "STR": "ST",
    "AVENUE": "AVE", "AV": "AVE",
    "ROAD": "RD",
    "DRIVE": "DR", "DRV": "DR",
    "LANE": "LN", "LA": "LN",
    "CIRCLE": "CIR", "CIRC": "CIR", "CR": "CIR",
    "COURT": "CT", "CRT": "CT",
    "TERRACE": "TER", "TERR": "TER",
    "PLACE": "PL",
    "BOULEVARD": "BLVD", "BLV": "BLVD",
    "HIGHWAY": "HWY",
    "WAY": "WAY", "WY": "WAY",
    "PATH": "PATH", "PTH": "PATH",
    "TRAIL": "TRL", "TR": "TRL",
    "EXTENSION": "EXT", "EXTN": "EXT",
    "PARKWAY": "PKWY",
    "TURNPIKE": "TPKE",
    "POINT": "PT",
    "SQUARE": "SQ",
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
    "SAINT": "ST",
}
SUFFIX_TOKENS = set(SUFFIX_MAP.values()) | {"ST", "AVE", "RD", "DR", "LN", "CIR", "CT", "TER", "PL", "BLVD", "HWY", "WAY", "PATH", "TRL", "EXT", "PKWY", "TPKE", "PT", "SQ", "RTE", "ROUTE"}
UNIT_TOKENS = {"UNIT", "APT", "APARTMENT", "STE", "SUITE", "FL", "FLOOR", "BLDG", "RM", "SLIP", "LOT", "#", "PMB", "BOX"}
PO_BOX_RX = re.compile(r"\b(P\.?\s*O\.?\s*BOX|POB|POST OFFICE BOX|BOX)\s*#?\s*\d+", re.I)


def norm_tokens(s: str | None) -> list[str]:
    if s is None or (isinstance(s, float) and np.isnan(s)) or s is pd.NA:
        return []
    s = str(s).upper()
    s = re.sub(r"'S\b", "S", s)               # GOODSPEED'S -> GOODSPEEDS
    s = re.sub(r"[^\w\s#]", " ", s)          # strip punctuation
    s = re.sub(r"\s+", " ", s).strip()
    toks = s.split(" ") if s else []
    return [SUFFIX_MAP.get(t, t) for t in toks]


def split_house_and_street(tokens: list[str]) -> tuple[str | None, list[str]]:
    """'127 SEAGATE LN' -> ('127', ['SEAGATE','LN']). '12-14 MAIN ST' -> ('12', [...])."""
    if not tokens:
        return None, []
    m = re.match(r"^(\d+)", tokens[0])
    if not m:
        return None, tokens
    num = m.group(1).lstrip("0") or "0"
    street = tokens[1:]
    # cut at unit designators
    for i, t in enumerate(street):
        if t in UNIT_TOKENS or t.startswith("#"):
            street = street[:i]
            break
    return num, street


ROUTE_TOKENS = {"RTE", "ROUTE", "RT"}


def _stem(toks: list[str]) -> list[str]:
    """Cut at route designators; crude plural/possessive stemming (PITCHERS -> PITCHER)."""
    out = []
    for t in toks:
        if t in ROUTE_TOKENS:
            break
        if len(t) > 3 and t.endswith("S") and t not in SUFFIX_TOKENS:
            t = t[:-1]
        out.append(t)
    return out


def streets_match(a: list[str], b: list[str]) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    a, b = _stem(a), _stem(b)
    if a == b or "".join(a) == "".join(b):       # FLINT ROCK RD == FLINTROCK RD
        return True
    # tolerate a missing suffix on either side ('SEAGATE' vs 'SEAGATE LN')
    a2 = a[:-1] if len(a) > 1 and a[-1] in SUFFIX_TOKENS else a
    b2 = b[:-1] if len(b) > 1 and b[-1] in SUFFIX_TOKENS else b
    if a2 == b2:
        return True
    # tolerate a leading directional on one side
    if len(a2) > 1 and a2[0] in {"N", "S", "E", "W"} and a2[1:] == b2:
        return True
    if len(b2) > 1 and b2[0] in {"N", "S", "E", "W"} and b2[1:] == a2:
        return True
    return False


def derive_village(row, town: dict) -> str | None:
    zips = town["zips"]
    names_upper = {v.upper(): v for v in zips.values()}
    aliases = town["village_aliases"]
    z = row["ZIP"]
    if isinstance(z, str) and z in zips:
        return zips[z]
    # Barnstable: SITE_ADDR ends with ", CENTERVILLE" etc. Check this before CITY,
    # which is just the town name there.
    site = row["SITE_ADDR"]
    if isinstance(site, str) and "," in site:
        tail = site.rsplit(",", 1)[1].strip().upper()
        if tail in names_upper:
            return names_upper[tail]
        if tail in aliases:
            return aliases[tail]
    city = row["CITY"]
    if isinstance(city, str) and city != town["TOWN"]:
        if city in names_upper:
            return names_upper[city]
        if city in aliases:
            return aliases[city]
    sfx = town["street_village_suffixes"]
    fs = row["FULL_STR"]
    if sfx and isinstance(fs, str):
        last = fs.strip().rsplit(" ", 1)[-1].upper()
        if last in sfx:
            return sfx[last]
    return None


def site_street_tokens(full_str, town: dict) -> list[str]:
    if isinstance(full_str, str):
        full_str = re.sub(r"\([^)]*\)", " ", full_str)   # 'MAIN STREET (CENT.)' -> 'MAIN STREET'
        full_str = full_str.split("/")[0]                  # 'MAIN ST./RTE 6A' -> 'MAIN ST.'
    toks = norm_tokens(full_str)
    sfx = town["street_village_suffixes"]
    if sfx and toks and toks[-1] in sfx:
        toks = toks[:-1]
    return toks


def classify_owner_occ(row, town: dict) -> str:
    own_addr = row["OWN_ADDR"]
    if not isinstance(own_addr, str) or not own_addr.strip():
        return "UNKNOWN"
    own_state = row["OWN_STATE"] if isinstance(row["OWN_STATE"], str) else ""
    own_zip = row["OWN_ZIP"] if isinstance(row["OWN_ZIP"], str) else None
    site_zip = row["site_zip"] if isinstance(row["site_zip"], str) else None

    if PO_BOX_RX.search(own_addr):
        if own_zip and site_zip and own_zip == site_zip:
            return "LIKELY_OWNER_OCC"
        # PO box in another ZIP of the same town is still local, not owner-occ by spec
        return "ABSENTEE_LOCAL" if own_state in ("MA", "") else "ABSENTEE_OUT_OF_STATE"

    own_num, own_street = split_house_and_street(norm_tokens(own_addr))
    site_num = row["site_num"] if isinstance(row["site_num"], str) else None
    site_street = row["site_street"]
    in_town_zip = (own_zip is None) or (own_zip in town["zips"])
    if own_num and site_num and own_num == site_num and streets_match(own_street, site_street) \
            and own_state in ("MA", "") and in_town_zip:
        return "OWNER_OCC"
    if own_state == "MA" or own_state == "":
        return "ABSENTEE_LOCAL"
    return "ABSENTEE_OUT_OF_STATE"


def parse_ls_date(s) -> pd.Timestamp | None:
    """Defensive sale-date parsing: YYYYMMDD, MM/DD/YYYY, YYYY-MM-DD, M/D/YY, etc."""
    if s is None or s is pd.NA or (isinstance(s, float) and np.isnan(s)):
        return pd.NaT
    s = str(s).strip()
    if not s or s in {"0", "00000000"}:
        return pd.NaT
    for fmt in ("%Y%m%d", "%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%m-%d-%Y", "%Y/%m/%d", "%d-%b-%Y", "%m%d%Y"):
        try:
            d = pd.to_datetime(s, format=fmt)
            if 1800 < d.year <= 2100:
                return d
        except (ValueError, TypeError):
            continue
    return pd.NaT


def size_tier(sqft, tiers: dict) -> str | None:
    if sqft is None or pd.isna(sqft):
        return None
    for name, (lo, hi) in tiers.items():
        lo = lo or 0
        if sqft >= lo and (hi is None or sqft < hi):
            return name
    return None


def classify_town(gdf, town: dict, cfg: dict) -> tuple[pd.DataFrame, dict]:
    df = gdf.copy()
    df["is_sfh"] = df["use_code"].isin(PRIMARY_USE)
    df["is_secondary"] = df["use_code"].isin(SECONDARY_USE)

    # Collapse duplicate SFH rows on one LOC_ID (e.g. several cottages on a town-owned parcel):
    # one parcel = one door. Keep the largest living area.
    dup_mask = df["is_sfh"] & df.duplicated("LOC_ID", keep=False)
    n_dup_collapsed = 0
    if dup_mask.any():
        d = df[dup_mask].sort_values("RES_AREA", ascending=False)
        drop_idx = d[d.duplicated("LOC_ID", keep="first")].index
        n_dup_collapsed = len(drop_idx)
        df.loc[drop_idx, "is_sfh"] = False

    # Village + site ZIP
    df["village"] = df.apply(lambda r: derive_village(r, town), axis=1)
    zip_by_village = {v: k for k, v in town["zips"].items()}
    df["site_zip"] = df["ZIP"].where(df["ZIP"].isin(list(town["zips"])), df["village"].map(zip_by_village))

    # Site address parts
    addr_num = df["ADDR_NUM"].astype("string")
    df["site_num"] = addr_num.str.extract(r"^\s*(\d+)")[0].str.lstrip("0").replace("", "0")
    # fall back to leading number in SITE_ADDR
    fallback = df["SITE_ADDR"].astype("string").str.extract(r"^\s*(\d+)")[0].str.lstrip("0").replace("", "0")
    df["site_num"] = df["site_num"].fillna(fallback).astype(object).where(lambda x: x.notna(), None)
    df["site_street"] = df["FULL_STR"].map(lambda s: site_street_tokens(s, town))

    df["owner_occ_class"] = df.apply(lambda r: classify_owner_occ(r, town), axis=1)
    df["is_owner_occ"] = df["owner_occ_class"].isin(["OWNER_OCC", "LIKELY_OWNER_OCC"])

    # Exemption flag (Barnstable Residential Exemption) if the extract has one
    if "EXEMPT_RAW" in df.columns:
        v = df["EXEMPT_RAW"].astype("string").str.strip().str.upper()
        df["exempt_flag"] = v.isin(["Y", "YES", "1", "TRUE", "T", "X"]) | (pd.to_numeric(df["EXEMPT_RAW"], errors="coerce").fillna(0) > 0)
    else:
        df["exempt_flag"] = pd.NA

    # Home size
    res = df["RES_AREA"].where(df["RES_AREA"] > 300)
    bld = df["BLD_AREA"].where(df["BLD_AREA"] > 300)
    df["living_sqft"] = res.fillna(bld)
    tiers = cfg["scoring"]["size_tiers"]
    df["size_tier"] = df["living_sqft"].map(lambda x: size_tier(x, tiers))

    # Recent sale
    df["ls_date_parsed"] = df["LS_DATE"].map(parse_ls_date)
    # A sale date in the future is a data-entry error; treat as unparseable.
    df.loc[df["ls_date_parsed"] > pd.Timestamp.today(), "ls_date_parsed"] = pd.NaT
    has_raw = df["LS_DATE"].astype("string").str.strip().fillna("").ne("")
    unparseable = has_raw & df["ls_date_parsed"].isna()
    recent_since = pd.Timestamp(cfg["recent_sale_since"])
    new_owner_since = pd.Timestamp(cfg["new_owner_since"])
    df["recent_sale"] = df["ls_date_parsed"] >= recent_since
    df["new_owner_12mo"] = df["ls_date_parsed"] >= new_owner_since
    df["sale_ym"] = df["ls_date_parsed"].dt.strftime("%Y-%m")

    # Placeholder for Phase 2 address-level solar permits.
    df["has_solar"] = pd.NA

    sfh = df[df["is_sfh"]]
    occ_counts = sfh["owner_occ_class"].value_counts().to_dict()
    n_sfh = int(len(sfh))
    info = {
        "n_parcels": int(df["LOC_ID"].nunique()),
        "n_assessor_rows": int(len(df)),
        "n_sfh": n_sfh,
        "n_sfh_dup_collapsed": int(n_dup_collapsed),
        "n_secondary": int(df["is_secondary"].sum()),
        "owner_occ_counts": {k: int(occ_counts.get(k, 0)) for k in
                             ["OWNER_OCC", "LIKELY_OWNER_OCC", "ABSENTEE_LOCAL", "ABSENTEE_OUT_OF_STATE", "UNKNOWN"]},
        "n_owner_occ": int(sfh["is_owner_occ"].sum()),
        "pct_owner_occ_parcel_method": round(100 * sfh["is_owner_occ"].mean(), 1) if n_sfh else None,
        "pct_unknown": round(100 * (sfh["owner_occ_class"] == "UNKNOWN").mean(), 2) if n_sfh else None,
        "median_living_sqft": float(sfh["living_sqft"].median()) if n_sfh else None,
        "living_sqft_null": int(sfh["living_sqft"].isna().sum()),
        "n_recent_sale": int(sfh["recent_sale"].sum()),
        "n_new_owner_12mo": int(sfh["new_owner_12mo"].sum()),
        "ls_date_unparseable": int(unparseable[df["is_sfh"]].sum()),
        "latest_sale_date": str(sfh["ls_date_parsed"].max().date()) if sfh["ls_date_parsed"].notna().any() else None,
        "ls_date_unparseable_pct": round(100 * unparseable[df["is_sfh"]].mean(), 2) if n_sfh else None,
        "village_null_pct": round(100 * sfh["village"].isna().mean(), 1) if n_sfh else None,
        "village_counts": sfh["village"].value_counts(dropna=False).to_dict(),
    }
    if "EXEMPT_RAW" in df.columns:
        agree = (sfh["exempt_flag"].astype(bool) == sfh["is_owner_occ"]).mean()
        info["exemption_agreement_pct"] = round(100 * float(agree), 1)
        info["exemption_n_flagged"] = int(sfh["exempt_flag"].astype(bool).sum())
    else:
        info["exemption_agreement_pct"] = None
        info["exemption_note"] = "not in extract"

    log(f"  {town['name']}: SFH {n_sfh:,} (collapsed {n_dup_collapsed} dup rows) | owner-occ {info['pct_owner_occ_parcel_method']}% "
        f"| {info['owner_occ_counts']} | unparseable LS_DATE {info['ls_date_unparseable']} | village null {info['village_null_pct']}%")
    return df, info
