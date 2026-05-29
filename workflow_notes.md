# Workflow Notes — NYC Bike Lane Network Gap Analysis

Real-time notes. Written as I worked.

---

## 2023-12-04 — Data sourcing and initial filter

Downloaded the NYC Bicycle Routes layer from NYC Open Data (DOT dataset). The layer has 1,847 features but mixes very different facility types: protected paths, painted sharrows, bike route signs, and greenways are all in the same layer. The `facilitycl` field distinguishes them.

Initial confusion: the data dictionary on the NYC Open Data page listed different codes than what was actually in the data. The page said `facilitycl = 1` for "Class I — Protected Bike Path" but the actual data also used `1` for some greenways and `2` for others. Spot-checked 15–20 features against satellite imagery to confirm what was actually protected. Confident in the filter `facilitycl IN (1, 2)` after that check.

Sharrows (shared-lane markings with painted bike symbols) are coded as `3` and excluded. This was a deliberate decision — sharrows provide no physical protection and mix with motor traffic at intersections. Including them would make the network look far more connected than it actually is for a cyclist seeking safe routes.

After filtering: 623 features retained. Visualised in QGIS — looks right. The major corridors (West Side Greenway, Brooklyn protected lane network, Flushing Meadows paths) are all there.

---

## 2023-12-05 — GEOID format mismatch (the frustrating one)

Spent 90 minutes on this. After joining ACS 2022 commute data to the NYC census tracts layer, about 40% of tracts had NULL values for `bike_commuters`. That seemed wrong — those tracts exist and have commute data in the ACS.

First thought it was a spatial issue (tracts outside NYC). Checked — no, the GEOID join was the problem. The NYC Open Data tract layer uses GEOIDs like `36047000100` (11 digits, full FIPS). The ACS CSV exports use `1400000US36047000100` (the Census Bureau's "Geography" ID format). After stripping the `1400000US` prefix, the IDs should match.

But some tracts in the NYC layer had GEOIDs like `047000100` (9 digits, missing the state FIPS `36`). Those never matched. Fixed with `geoid_format_fix()` function — pads to 11 digits.

After fixing: NULL rate dropped to < 1% (only water-body tracts with no residential population, expected).

Lesson: always print a sample of join keys from both sides before running a table join. Would have caught this in 5 minutes instead of 90.

---

## 2023-12-06 — QNEAT3 memory crash

Tried to run QNEAT3 ISO area generation on the full NYC OSM road network (~280,000 segments). QGIS crashed after about 8 minutes with an out-of-memory error. My machine has 16GB RAM and it still wasn't enough.

Solution: split by borough. Generated ISO areas separately for Manhattan, Brooklyn, Queens, Bronx, Staten Island — each borough's road network is 30,000–80,000 segments, well within memory limits. Runtime per borough: 4–12 minutes. Merged results afterward using QGIS Merge Vector Layers.

Side effect: a few tracts near borough boundaries got slightly different coverage values depending on whether their service area was calculated from the Manhattan or Brooklyn network. The borough boundary is an artificial cut in the road network — a street crossing a borough line gets split. Fixed by running a small buffer (500m) around each borough boundary and including those road segments in both adjacent borough's networks.

---

## 2023-12-08 — Topology duplicates

The QGIS Topology Checker flagged three "duplicate geometry" errors. Investigated each one:
- Two features on the Hudson River Greenway shared identical geometries: one was labelled "Class I Protected" and the other "Greenway." Both correctly represent the same physical path — it's dual-classified in the source data.
- The third was a similar case on the Brooklyn waterfront.

These are not errors in the data — they're legitimate dual classifications. Added them as exceptions in the topology report rather than deleting either feature. Kept both in the network; they contribute the same physical geometry so there's no double-counting of length or connectivity.

---

## 2023-12-09 — MapInfo Pro cross-check

Re-ran the topology check in MapInfo Pro to validate. Exported the clean network as MIF/MID and ran MapInfo's topology tools.

MapInfo found 46 dangling endpoints vs QGIS's 47. The missing one: a very short segment (1.3m) that QGIS flagged as a dangle but MapInfo's minimum feature length threshold excluded. Not meaningful — the 1.3m segment is a digitizing artifact at a path terminus.

The connected component analysis matched completely: 12 subnetworks in both QGIS and MapInfo. Component sizes also matched (verified by comparing total length of the largest subnetwork: 47.3 miles in QGIS, 47.2 miles in MapInfo — 0.1-mile difference from the 1.3m excluded segment).

Good enough to trust the QGIS results.
