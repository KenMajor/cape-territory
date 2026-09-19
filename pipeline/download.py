"""Fetch raw public sources into data/raw/ (gitignored).

Every URL is verified with a HEAD request before download. Files already on disk
are skipped, so re-runs (and the Actions cache) don't re-pull hundreds of MB.

    python pipeline/download.py          # download everything
    python pipeline/download.py --list   # print the URL list (used as the cache key)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import requests

from common import RAW, ensure_dirs, load_config, log

UA = {"User-Agent": "cape-territory-pipeline (github.com/kenmajor/cape-territory)"}


def parcels_url(cfg: dict, town: dict) -> str:
    return cfg["sources"]["parcels_url"].format(town_id=town["massgis_town_id"], TOWN=town["TOWN"])


def structures_url(cfg: dict, town: dict) -> str:
    return cfg["sources"]["structures_url"].format(town_id=town["massgis_town_id"], TOWN=town["TOWN"])


def find_pts_excel_url(page_url: str) -> str:
    """The MassCEC PTS Excel filename changes every refresh; find the current one on the page."""
    r = requests.get(page_url, headers=UA, timeout=60)
    r.raise_for_status()
    links = re.findall(r'href="([^"]+\.xlsx?)"', r.text, flags=re.I)
    cands = [l for l in links if re.search(r"solar.?pv.?systems", l, re.I)]
    if not cands:
        raise RuntimeError(f"No 'Solar PV Systems' Excel link found on {page_url}")
    url = cands[0]
    if url.startswith("/"):
        url = "https://www.masscec.com" + url
    return url


def url_list(cfg: dict) -> list[tuple[str, Path]]:
    """(url, destination) for everything the pipeline needs."""
    items = []
    for t in cfg["towns"]:
        u = parcels_url(cfg, t)
        items.append((u, RAW / "parcels" / u.rsplit("/", 1)[-1]))
        u = structures_url(cfg, t)
        items.append((u, RAW / "structures" / u.rsplit("/", 1)[-1]))
    u = cfg["sources"]["towns_url"]
    items.append((u, RAW / "towns" / u.rsplit("/", 1)[-1]))
    try:
        u = find_pts_excel_url(cfg["sources"]["masscec_pts_page"])
        items.append((u, RAW / "masscec" / u.rsplit("/", 1)[-1]))
    except Exception as e:  # noqa: BLE001
        log(f"WARN: could not locate MassCEC PTS Excel: {e}")
    return items


def head_ok(url: str) -> tuple[bool, int | None]:
    r = requests.head(url, headers=UA, timeout=60, allow_redirects=True)
    size = int(r.headers.get("Content-Length", 0)) or None
    return r.status_code == 200, size


def fetch(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        log(f"  cached  {dest.name} ({dest.stat().st_size/1e6:.1f} MB)")
        return
    ok, size = head_ok(url)
    if not ok:
        raise RuntimeError(f"HEAD {url} did not return 200. The source may have moved; check the MassGIS landing page.")
    log(f"  GET     {url} ({(size or 0)/1e6:.1f} MB)")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, headers=UA, timeout=600, stream=True) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    tmp.rename(dest)


def download_all(cfg: dict) -> dict[str, Path]:
    """Download everything; return {kind:town_slug -> path}."""
    ensure_dirs()
    paths: dict[str, Path] = {}
    for t in cfg["towns"]:
        u = parcels_url(cfg, t)
        p = RAW / "parcels" / u.rsplit("/", 1)[-1]
        fetch(u, p)
        paths[f"parcels:{t['slug']}"] = p
        u = structures_url(cfg, t)
        p = RAW / "structures" / u.rsplit("/", 1)[-1]
        fetch(u, p)
        paths[f"structures:{t['slug']}"] = p
    u = cfg["sources"]["towns_url"]
    p = RAW / "towns" / u.rsplit("/", 1)[-1]
    fetch(u, p)
    paths["towns"] = p
    # MassCEC: prefer a fresh link; fall back to any xlsx already cached.
    try:
        u = find_pts_excel_url(cfg["sources"]["masscec_pts_page"])
        p = RAW / "masscec" / u.rsplit("/", 1)[-1]
        fetch(u, p)
        paths["masscec"] = p
    except Exception as e:  # noqa: BLE001
        cached = sorted((RAW / "masscec").glob("*.xls*"))
        if cached:
            log(f"WARN: MassCEC fetch failed ({e}); using cached {cached[-1].name}")
            paths["masscec"] = cached[-1]
        else:
            log(f"WARN: MassCEC fetch failed and nothing cached: {e}. Solar fields will be null.")
    return paths


if __name__ == "__main__":
    cfg = load_config()
    if "--list" in sys.argv:
        for u, _ in url_list(cfg):
            print(u)
    else:
        download_all(cfg)
