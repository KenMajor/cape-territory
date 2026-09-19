"""Census ACS 5-year, county-subdivision (town) level. Needs CENSUS_API_KEY.

If no key is present, every field is null and the scorecard marks them "pending".
Nothing is ever invented.
"""
from __future__ import annotations

import os

import requests

from common import log

VARS = {
    "B25001_001E": "housing_units",
    "B25003_001E": "occupied_units",
    "B25003_002E": "owner_occupied_units",
    "B25003_003E": "renter_occupied_units",
    "B25004_006E": "vacant_seasonal_units",
    "B25024_002E": "units_1_detached",
    "B25077_001E": "median_home_value",
}
FIELDS = ["census_pct_owner_occupied", "census_pct_seasonal_vacant", "census_pct_renter",
          "census_housing_units", "census_median_home_value"]


def fetch_acs(cfg: dict) -> tuple[dict[str, dict], str]:
    """Return ({cousub_fips: {...}}, status_note)."""
    key = os.environ.get("CENSUS_API_KEY", "").strip()
    year = cfg["sources"].get("census_acs_year", 2023)
    empty = {t["cousub_fips"]: {f: None for f in FIELDS} for t in cfg["towns"]}
    if not key:
        log("  CENSUS_API_KEY not set: census fields left null (pending)")
        return empty, "pending: CENSUS_API_KEY not set"
    fips = ",".join(t["cousub_fips"] for t in cfg["towns"])
    url = f"https://api.census.gov/data/{year}/acs/acs5"
    params = {"get": "NAME," + ",".join(VARS), "for": f"county subdivision:{fips}",
              "in": "state:25 county:001", "key": key}
    try:
        r = requests.get(url, params=params, timeout=60)
        r.raise_for_status()
        rows = r.json()
    except Exception as e:  # noqa: BLE001
        log(f"  WARN census fetch failed: {e}")
        return empty, f"pending: fetch failed ({e})"
    hdr, body = rows[0], rows[1:]
    out = {}
    for row in body:
        rec = dict(zip(hdr, row))
        v = {VARS[k]: (float(rec[k]) if rec.get(k) not in (None, "", "null") else None) for k in VARS}
        hu, occ = v["housing_units"], v["occupied_units"]
        out[rec["county subdivision"]] = {
            "census_pct_owner_occupied": round(100 * v["owner_occupied_units"] / occ, 1) if occ else None,
            "census_pct_renter": round(100 * v["renter_occupied_units"] / occ, 1) if occ else None,
            "census_pct_seasonal_vacant": round(100 * v["vacant_seasonal_units"] / hu, 1) if hu else None,
            "census_housing_units": int(hu) if hu else None,
            "census_median_home_value": int(v["median_home_value"]) if v["median_home_value"] and v["median_home_value"] > 0 else None,
        }
    for f in empty:
        out.setdefault(f, empty[f])
    log(f"  census ACS {year} 5-year: {len(body)} towns fetched")
    return out, f"ACS {year} 5-year"
