# Town scorecard — Phase 1 (Mid-Cape)

Generated 2026-09-19 01:17 UTC. Recent sale = since 2023-09-01; new owner = since 2025-09-01.
Solar source: MassCEC PTS report as of 2025-01-14. Census: pending: CENSUS_API_KEY not set.

| Metric | Barnstable | Yarmouth | Dennis |
|---|---:|---:|---:|
| Assessor FY | 2026 | 2026 | 2025 |
| Parcels (all uses) | 26,872 | 15,701 | 14,804 |
| Single-family homes | 21,130 | 12,947 | 11,753 |
| Owner-occupied (parcel method) | 13,513 | 7,574 | 4,785 |
| Owner-occ % (parcel method) | 64.0 | 58.5 | 40.7 |
|   of which exact address match | 12,629 | 7,413 | 3,571 |
|   of which PO Box, same ZIP | 884 | 161 | 1,214 |
| Absentee, MA | 5,791 | 4,129 | 5,183 |
| Absentee, out of state | 1,809 | 1,242 | 1,785 |
| Owner address missing | 17 | 2 | 0 |
| Res. exemption agreement % | not in extract | n/a (town has no residential exemption) | n/a (town has no residential exemption) |
| Census owner-occ % (of occupied) | pending | pending | pending |
| Census seasonal-vacant % (of units) | pending | pending | pending |
| Census renter % (of occupied) | pending | pending | pending |
| Median living sqft | 1,628 | 1,344 | 1,385 |
| Median footprint sqft | 1,753 | 1,620 | 1,473 |
| Footprint join hit % | 99.8 | 99.9 | 99.8 |
| Big-roof homes (top 15%) | 4,093 | 1,282 | 1,489 |
| Sold since recent-sale date | 2,024 | 1,214 | 1,018 |
| New owner (12 mo) | 7 | 2 | 0 |
| Unparseable sale dates | 0 | 0 | 1 |
| Latest sale date in extract | 2025-12-10 | 2025-09-23 | 2024-12-12 |
| Solar installs, residential (PTS) | 1,865 | 937 | 521 |
|   in 24 mo before PTS date | 174 | 97 | 47 |
| Solar penetration % (installs / SFH) | 8.8 | 7.2 | 4.4 |
| Map hexes | 924 | 406 | 398 |
| TARGET DOORS (owner-occ SFH) | 13,513 | 7,574 | 4,785 |

## Sanity checks

| Check | Result | Detail |
|---|---|---|
| Barnstable SFH count in range | PASS | 21,130 (expected 15,000-23,000) |
| Barnstable owner-occ share 40-75% | PASS | 64.0% |
| Barnstable UNKNOWN owner-occ < 5% | PASS | 0.08% |
| Barnstable footprint hit rate >= 90% | PASS | 99.8% |
| Barnstable LS_DATE unparseable < 3% | PASS | 0.0% (0 rows) |
| Yarmouth SFH count in range | PASS | 12,947 (expected 8,000-14,000) |
| Yarmouth owner-occ share 40-75% | PASS | 58.5% |
| Yarmouth UNKNOWN owner-occ < 5% | PASS | 0.02% |
| Yarmouth footprint hit rate >= 90% | PASS | 99.9% |
| Yarmouth LS_DATE unparseable < 3% | PASS | 0.0% (0 rows) |
| Dennis SFH count in range | PASS | 11,753 (expected 7,500-12,500) |
| Dennis owner-occ share 40-75% | PASS | 40.7% |
| Dennis UNKNOWN owner-occ < 5% | PASS | 0.0% |
| Dennis footprint hit rate >= 90% | PASS | 99.8% |
| Dennis LS_DATE unparseable < 3% | PASS | 0.01% (1 rows) |
| docs/data total size < 15 MB | PASS | 2.06 MB |

## Notes

- Census fields are pending: add CENSUS_API_KEY as an Actions secret and re-run.
