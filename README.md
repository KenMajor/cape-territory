# Cape Territory Map

A door-to-door solar targeting map for Cape Cod, built from public records and published as a static web page. Phase 1 covers **Barnstable, Yarmouth and Dennis**.

**Live map:** `https://kenmajor.github.io/cape-territory/` (once GitHub Pages is switched on, see below).

Open it on your phone. Tap a hex to see its numbers and mark it knocked. Nothing personal is in the map: no owner names, no prices, no addresses.

## What the map shows

Every colored hexagon is roughly a few streets of a neighborhood (H3 resolution 9, about 0.1 km²). Only hexes with at least 5 single-family homes are shown. Darker = better.

| Layer | What it means |
|---|---|
| **Attack Score** (default) | 0–100 composite. Combines owner-occupied share (40%), roof size (25%), recent-sale share (15%), homes per hex (10%) and how little solar the town already has (10%). Weights live in `config/scoring.yaml`. |
| **Owner-occupied %** | Share of single-family homes where the owner's mailing address matches the house (or is a PO Box in the same ZIP). |
| **Home size** | Median building footprint (roof area) per hex. |
| **Recent sales** | Share of homes sold since Sept 2023. Zoom to street level (zoom 16+) to see individual sale pins with month/year only. |
| **Big roofs** | Zoom to street level to see pins on the top 15% of homes by roof footprint. |
| **Solar** | Town-level estimate only: MassCEC-registered residential installs divided by single-family homes. Uniform within a town in Phase 1. |

**Tap a hex** for homes, owner-occupied %, median living sqft, median roof sqft, recent sales, big roofs, score, and a **Mark knocked** button. Knocked hexes get a black-and-white hatch. Knocked state lives only in your phone's browser (localStorage). **Copy progress** puts a JSON list of knocked hexes on the clipboard so you can paste it into your tracking chat. **Town scorecard** shows the town numbers plus your knocked-hex progress per town.

**SAT / MAP** switches between satellite (Esri) and street map (OpenStreetMap). **◎** locates you.

## How the numbers are made

Everything comes from `python pipeline/run.py`, which:

1. Downloads MassGIS Level 3 parcels + assessor tables per town, MassGIS building footprints per town, MassGIS town boundaries, and the MassCEC PTS solar spreadsheet (link discovered from the PTS page each run). Every URL is HEAD-checked first. Raw files go in `data/raw/` (gitignored, cached in Actions).
2. Normalizes use codes to the 3-digit DOR code; single-family = `101`. Two/three-family and multi-house parcels (`104`, `105`, `109`) are flagged but not scored.
3. Classifies owner occupancy by comparing the owner's mailing address to the site address (normalized: suffixes, punctuation, possessives, village codes stripped). Categories: `OWNER_OCC`, `LIKELY_OWNER_OCC` (PO Box in same ZIP), `ABSENTEE_LOCAL` (MA), `ABSENTEE_OUT_OF_STATE`, `UNKNOWN`.
4. Computes living sqft (`RES_AREA`, else `BLD_AREA`, if > 300), size tier, and roof footprint (largest building polygon whose centroid falls inside the parcel).
5. Parses sale dates defensively; recent sale = since `recent_sale_since`, new owner = since `new_owner_since` (both in `config/towns.yaml`).
6. Assigns each home to an H3 hex, aggregates, percentile-ranks the score components across all Phase 1 hexes, and weights them.
7. Pulls town-level Census ACS figures if `CENSUS_API_KEY` is set; otherwise leaves them null and marks them "pending". Numbers are never invented.
8. Writes the web files, the scorecard, and prints a sanity report. Sanity ranges are in `config/towns.yaml`.

Barnstable's Residential Exemption flag is not in the MassGIS extract, so the scorecard says "not in extract" and the address method is used everywhere.

## Output files (`docs/data/`)

All committed, all small (about 2 MB total). GeoJSON property keys are short to keep downloads fast.

| File | Contents |
|---|---|
| `manifest.json` | Town list with bounds and hex counts, legend break values, score weights, big-roof threshold. Loaded first. |
| `hexes_<town>.geojson` | One polygon per hex. Properties: `h` hex id, `t` town, `v` village, `n` homes, `oo` owner-occupied count, `po` owner-occupied %, `ao` absentee out-of-state, `ml` median living sqft, `mf` median footprint sqft, `br` big-roof count, `rs` recent sales, `pr` recent-sale %, `no` new owners (12 mo), `s` score, `sp` town solar penetration %, and `r_oo r_mf r_rs r_n r_sol` the percentile ranks of each score component (so weights can be re-tuned in the browser without re-running). |
| `sales_<town>.geojson` | One point per home sold since the recent-sale date. Property `d` = sale month (`YYYY-MM`). No prices, no names. |
| `bigroof_<town>.geojson` | One point per top-15%-footprint home. Property `f` = footprint sqft. |
| `towns.geojson` | Simplified town boundaries. |
| `town_scorecard.json` | Per-town scorecard, run metadata, and sanity-check results. |

`reports/town_scorecard.md` is the same scorecard as a Markdown table, also printed at the end of every run.

**Interim data** in `data/interim/` (gitignored) keeps every assessor row, including the non-target ones, as pickles for debugging. The parcel schema includes a nullable `has_solar` field for Phase 2 permit data.

## Running it yourself

```bash
pip install -r requirements.txt
export CENSUS_API_KEY=...        # optional; get one free at https://api.census.gov/data/key_signup.html
python pipeline/run.py           # ~1 min plus downloads the first time
python pipeline/run.py --no-download   # reuse data/raw
```

Then open `docs/index.html` through any static server (e.g. `cd docs && python -m http.server`).

## Adding a town

1. Add a block under `towns:` in `config/towns.yaml`: name, MassGIS `massgis_town_id` (check the download link on the [MassGIS parcels page](https://www.mass.gov/info-details/massgis-data-property-tax-parcels)), Census `cousub_fips`, its ZIP → village map, and a `sanity_sfh_range`. If the town's assessor omits site ZIPs, add `village_aliases` (spellings found in the address) or `street_village_suffixes` (trailing codes on street names, as Dennis does).
2. Run `python pipeline/run.py`. Check the sanity report.
3. Commit `docs/data/` and `reports/`. The map picks the new town up from `manifest.json`; no code changes.

## GitHub Actions and Pages

`.github/workflows/build.yml` runs on every push to `main` and on demand (Actions tab → "Build map data and deploy to Pages" → Run workflow). It installs Python, restores the raw-download cache (keyed on the URL list), runs the pipeline, commits any changed `docs/data/` and `reports/` back to `main`, and deploys `docs/` to GitHub Pages.

**One-time setup (Ken):**

1. Repo **Settings → Pages → Build and deployment → Source: GitHub Actions**. Until this is set, the deploy step fails with a "Pages is not enabled" style error; flipping that setting and re-running fixes it.
2. Optional: **Settings → Secrets and variables → Actions → New repository secret** named `CENSUS_API_KEY`. Re-run the workflow and the Census rows fill in.
3. GitHub Pages on a free plan requires a **public** repo. Everything in `docs/` is derived from public records with no personal data.

## Data sources

- MassGIS Property Tax Parcels (Level 3): https://www.mass.gov/info-details/massgis-data-property-tax-parcels
- MassGIS Building Structures (2-D): https://www.mass.gov/info-details/massgis-data-building-structures-2-d
- MassGIS Municipalities: https://www.mass.gov/info-details/massgis-data-municipalities
- MassCEC Production Tracking System: https://www.masscec.com/production-tracking-system-pts
- Census ACS 5-year via api.census.gov
- Basemaps: OpenStreetMap tiles and Esri World Imagery (attributions shown on the map)

## Out of scope for Phase 1

The other 12 Cape towns, address-level solar from permit portals, Residential Exemption flags beyond Barnstable, Registry of Deeds, street-segment aggregation, address search, and route planning.
