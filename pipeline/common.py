"""Shared helpers: config, paths, logging."""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
RAW = ROOT / "data" / "raw"
INTERIM = ROOT / "data" / "interim"
DOCS_DATA = ROOT / "docs" / "data"
REPORTS = ROOT / "reports"

# Massachusetts State Plane (meters). All area math happens here.
CRS_MA = "EPSG:26986"
CRS_WGS = "EPSG:4326"
SQM_TO_SQFT = 10.7639104


def load_config() -> dict:
    with open(CONFIG_DIR / "towns.yaml") as f:
        cfg = yaml.safe_load(f)
    with open(CONFIG_DIR / "scoring.yaml") as f:
        cfg["scoring"] = yaml.safe_load(f)
    for t in cfg["towns"]:
        t["slug"] = t["name"].lower().replace(" ", "_")
        t["TOWN"] = t["name"].upper()
        t["zips"] = {str(k).zfill(5): v for k, v in (t.get("zips") or {}).items()}
        t["village_aliases"] = {str(k).upper(): v for k, v in (t.get("village_aliases") or {}).items()}
        t["street_village_suffixes"] = {
            str(k).upper(): v for k, v in (t.get("street_village_suffixes") or {}).items()
        }
    return cfg


def log(msg: str = "") -> None:
    print(msg, flush=True)


def section(title: str) -> None:
    log()
    log("=" * 72)
    log(title)
    log("=" * 72)


def ensure_dirs() -> None:
    for p in (RAW / "parcels", RAW / "structures", RAW / "towns", RAW / "masscec", INTERIM, DOCS_DATA, REPORTS):
        p.mkdir(parents=True, exist_ok=True)


def die(msg: str) -> None:
    log(f"FATAL: {msg}")
    sys.exit(1)
