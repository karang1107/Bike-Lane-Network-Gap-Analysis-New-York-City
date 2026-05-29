# Bike Lane Network Gap Analysis — New York City

**Tools:** QGIS 3.34 · QNEAT3 plugin · MapInfo Pro (cross-check) · Python 3.11 (geopandas, networkx)  
**Data sources:** NYC Open Data (Bike Routes) · ACS 2022 5-Year Estimates · OpenStreetMap road network  
**CRS:** NAD83 / New York Long Island (EPSG:2263)  
**Status:** Complete  

---

## Overview

Network connectivity analysis of New York City's protected bike lane infrastructure, examining where gaps in the network limit usability for commuters. The analysis identifies disconnected segments, low-coverage areas relative to population density, and priority corridors for network expansion.

The core question: which parts of the city have high commuter populations but poor protected bike access — and which existing segments, if connected, would have the greatest impact on network reach?

Data processing and network analysis were done in QGIS with the QNEAT3 plugin. A parallel check was run in MapInfo Pro to verify topology results. Python (networkx) was used for the graph-theoretic connectivity analysis. All outputs are formatted for an NYC DOT planning audience.

---

## Background and Motivation

NYC has invested heavily in bike infrastructure over the past 15 years, but the network is fragmented — many protected lanes are isolated segments that don't connect to other protected infrastructure, forcing cyclists to merge into traffic to continue their routes. This is a well-known issue documented by advocacy groups and the NYC DOT itself, but systematic quantification of which gaps matter most (based on where people actually live and commute) is less common.

This analysis takes a demand-weighted approach: gaps are prioritised not just by their length but by the population density and commute volume in the areas they would serve if connected. A 200m gap in a low-density industrial zone matters less than a 200m gap in a dense residential neighborhood.

---

## Data Sources

| Dataset | Source | Format | Notes |
|---|---|---|---|
| NYC Bicycle Routes | NYC Open Data (DOT) | Shapefile | Includes protected lanes, shared lanes, greenways |
| ACS 2022 5-Year Estimates | US Census Bureau | CSV + TIGER/Line | Table B08301 (commute mode); B01003 (population) |
| Census Tract Boundaries | US Census TIGER/Line 2022 | Shapefile | NYC 5-borough extent |
| OSM Road Network | OpenStreetMap via QuickOSM | GeoPackage | Used for network routing backbone |
| Borough Boundaries | NYC Open Data | Shapefile | Administrative reference |
| NYC DOT Protected Lane Plan | NYC DOT Planning Documents | PDF (digitized) | Future planned lanes for comparison |

**Filter applied to bike routes layer:** Analysis restricted to **protected** and **separated** bike lanes (facility type codes: `1` = protected path, `2` = greenway). Shared-lane markings (sharrows) and bike route signs without physical separation were excluded — these provide minimal protection and behave differently in a network connectivity context.

---

## Methodology

### 1. Data Preparation

Downloaded NYC Bicycle Routes shapefile from NYC Open Data. Layer contains 1,847 features as of the July 2023 download — a mix of facility types. Applied attribute filter `facility_t IN (1, 2)` in QGIS to isolate protected and greenway lanes: 623 features retained.

Projected to **New York Long Island State Plane (EPSG:2263)** — standard for NYC-area analysis, units in US Survey Feet. Census tracts and OSM network reprojected to match.

### 2. Network Topology Audit

Before any connectivity analysis, checked the network topology for errors using QGIS Topology Checker:

- Rule 1: Lines must not have dangles (endpoints not connected to another line)
- Rule 2: Lines must not self-overlap
- Rule 3: Lines must not have duplicate geometries

Results: 47 dangling endpoints and 12 isolated subnetworks identified. Dangles were either genuine network ends (correct) or digitizing errors (gap between two lines that should connect). Reviewed each visually — 31 dangles were legitimate endpoints; 16 were snapping errors with gaps of 1–8 metres. Fixed the snapping errors using QGIS Snapping Toolbar (tolerance: 5m).

After fixing: network consists of 439 connected edges forming 12 distinct subnetworks (connected components). The largest subnetwork covers Manhattan's west side, Hudson River greenway, and parts of Brooklyn. The second largest covers the eastern Queens protected lane network. The remaining 10 subnetworks are isolated segments of varying length.

### 3. Gap Identification

Gaps were defined as road segments (from the OSM network) where:
1. A protected bike lane exists on both sides of the segment (upstream and downstream)
2. No protected facility exists on the segment itself
3. The segment length is < 500m (longer gaps are likely intentional route breaks, not fixable gaps)

Implemented using a combination of QNEAT3 (ISO area from network end nodes) and spatial selection:

- For each dangling endpoint of the protected network, generated a 300m ISO area along the OSM road network
- Selected OSM segments within those ISO areas that also had a protected lane endpoint within 300m on the far end
- Resulting selection: 94 candidate gap segments, totalling 11.2 km

### 4. Population and Commute Demand Weighting

Joined ACS 2022 census tract data to the gap candidate segments:
- Population density (persons/km²) of surrounding tracts (500m buffer)
- % of workers commuting by bike (ACS table B08301)
- Absolute number of bike commuters per tract

Calculated a demand score per gap segment:

```
demand_score = (pop_density_norm * 0.50)
             + (bike_commuter_pct_norm * 0.30)
             + (gap_length_inv_norm * 0.20)
```

Shorter gaps in denser areas with more bike commuters score highest — they are the easiest wins for the most people.

### 5. Coverage Analysis

For each census tract, calculated the percentage of the tract's residential area within 400m of a protected bike lane (network distance, not straight-line). Used QNEAT3 ISO area generation from all protected lane edges.

Coverage metric: % of tract population within 400m network distance of any protected lane.

### 6. Cross-Check in MapInfo Pro

Exported the gap identification results to a MIF/MID file and re-ran the topology check in MapInfo Pro using the MapBasic `TopologyCheck` tool. MapInfo identified 46 gaps (vs 47 in QGIS — one segment was below MapInfo's minimum feature length threshold of 2m). Results were consistent.

---

## Key Findings

- **47 topology issues** identified in the source data; 16 corrected (snapping errors), 31 confirmed as legitimate endpoints.
- **12 isolated subnetworks** — the protected lane network is not a single connected graph; it is 12 disconnected components.
- **94 gap candidates** identified; top 20 prioritised by demand score.
- **Top 3 priority gaps** (highest demand score): a 180m gap on Flatbush Avenue (Brooklyn) separating two large subnetworks; a 220m gap on 9th Avenue (Manhattan) at a major intersection; a 310m gap on the Queens Boulevard corridor.
- **Coverage by borough** (% of population within 400m of protected lane):

| Borough | Coverage |
|---|---|
| Manhattan | 71.4% |
| Brooklyn | 48.2% |
| Queens | 31.7% |
| Bronx | 22.1% |
| Staten Island | 8.3% |

- The Bronx and Staten Island have the lowest coverage despite having non-trivial numbers of bike commuters in the ACS data, indicating significant unmet demand.

---

## Limitations

- Protected lane data is from July 2023; new construction since then is not reflected.
- ACS 2022 5-year estimates cover 2018–2022; post-pandemic commute patterns may differ from the survey period.
- 400m network buffer is a standard walkability threshold applied to cycling — actual acceptable access distance for cyclists is likely farther (800m–1km), but 400m produces conservative coverage estimates.
- OSM road network accuracy varies by neighbourhood; some local streets and paths may be missing or misclassified.

---

## Troubleshooting Log

**Issue:** QNEAT3 ISO area generation crashed QGIS with a memory error on the full 5-borough OSM network (~280,000 road segments).  
**Cause:** The full OSM network was too large to process as a single network dataset in QNEAT3.  
**Fix:** Split the analysis by borough — ran ISO area generation separately for each borough's network, then merged the results. Processing time per borough: 4–12 minutes. Total runtime: ~45 minutes.

**Issue:** After filtering to protected lanes only, the attribute join between the bike routes layer and the ACS commuter data returned NULL for ~35% of tracts.  
**Cause:** The census tract GEOID field in the NYC Open Data layer uses a different format (no leading zeros for state FIPS) than the ACS GEOID format (full 11-digit GEOID with leading zeros). `36047` ≠ `36047000100` — the join key didn't match.  
**Fix:** Used QGIS Field Calculator to construct a properly formatted GEOID: `lpad("BOROCODE" || "CT2020", 11, '0')`. Join worked after reformatting.

**Issue:** Topology Checker found 3 "duplicate geometry" errors that turned out to be two bike lanes sharing the same physical road segment — one in each direction (contraflow lane + regular lane).  
**Cause:** The data model correctly represents both directions as separate features. These are not errors.  
**Fix:** Added an exception for these three features in the topology report. Did not modify the data.

---

## Repository Structure

```
nyc-bike-network-gap-analysis/
│
├── README.md
│
├── data/
│   ├── raw/
│   │   ├── bike_routes/           # NYC DOT Bicycle Routes shapefile
│   │   ├── census/                # TIGER/Line tracts + ACS CSV tables
│   │   ├── osm/                   # OpenStreetMap road network (GeoPackage)
│   │   └── admin/                 # Borough boundaries
│   └── processed/
│       ├── protected_lanes.gpkg              # Filtered to protected/greenway only
│       ├── protected_lanes_clean.gpkg        # After topology fixes
│       ├── network_subnetworks.gpkg          # 12 connected components labelled
│       ├── gap_candidates.gpkg               # 94 gap segments
│       ├── gap_priority_scored.gpkg          # Gaps with demand score
│       ├── tracts_coverage.gpkg              # Tracts with 400m coverage %
│       └── iso_areas_400m.gpkg               # ISO area polygons (merged boroughs)
│
├── scripts/
│   ├── network_prep.py             # Filter, reproject, topology fix
│   ├── gap_identification.py       # ISO area + gap candidate detection
│   ├── demand_scoring.py           # ACS join + demand weighting
│   ├── coverage_analysis.py        # 400m network coverage per tract
│   └── export_summary.py           # Generate output CSVs and report tables
│
├── qgis-project/
│   └── nyc_bike_gaps.qgz
│
├── outputs/
│   ├── maps/
│   │   ├── nyc_bike_gap_priority_map.pdf
│   │   ├── nyc_bike_gap_priority_map.png
│   │   └── borough_coverage_maps/
│   └── data/
│       ├── gap_priority_table.csv
│       ├── borough_coverage_summary.csv
│       └── topology_audit_report.csv
│
├── docs/
│   ├── workflow_notes.md
│   ├── network_topology_notes.md
│   └── crs_decisions.md
│
└── .gitignore
```

---

## How to Reproduce

1. Download source data per `data/raw/DOWNLOAD_DATA.md`.
2. Run scripts in order: `network_prep.py` → `gap_identification.py` → `demand_scoring.py` → `coverage_analysis.py` → `export_summary.py`.
3. QNEAT3 plugin must be installed in QGIS 3.34 (Plugins > Manage and Install Plugins > search "QNEAT3").
4. Open `qgis-project/nyc_bike_gaps.qgz` in QGIS 3.34+.

---

## Skills Demonstrated

- Network topology audit and error correction
- Graph connectivity analysis (connected components via networkx)
- QNEAT3 ISO area (service area) generation
- Multi-criteria demand scoring with normalised weights
- ACS census data join and GEOID field formatting
- MapInfo Pro cross-validation
- Borough-level parallel processing for large network datasets
- Attribute table troubleshooting (GEOID format mismatch)

---

## License

Data: NYC Open Data (NYC DOT), US Census Bureau, OpenStreetMap contributors — all publicly available.  
Code: MIT License.  
Map outputs: CC BY 4.0.
