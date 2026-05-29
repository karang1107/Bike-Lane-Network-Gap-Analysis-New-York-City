"""
coverage_analysis.py
--------------------
Calculates the percentage of each census tract's population that lives
within 400m network distance of a protected bike lane.

Method:
    - Generate 400m ISO areas (service areas) from all protected lane
      edges using QNEAT3 (via PyQGIS) or straight-line buffer (fallback)
    - Intersect ISO areas with census tract boundaries
    - Calculate the % of each tract's area covered (used as a proxy
      for % population covered, assuming uniform population distribution
      within tracts)

400m rationale:
    400m is a standard pedestrian accessibility threshold (5-minute walk).
    For cycling analysis, the appropriate threshold is arguably larger
    (800m–1km), but 400m is used here to produce conservative coverage
    estimates. A supplementary analysis at 800m is noted as future work.

Usage (requires QGIS Python console for QNEAT3):
    exec(open('scripts/coverage_analysis.py').read())

Standalone (straight-line buffer fallback):
    python coverage_analysis.py

Dependencies:
    - geopandas, shapely, pandas, numpy
    - QGIS + QNEAT3 plugin (for network ISO areas)
"""

import os
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.ops import unary_union

# ── CONFIG ────────────────────────────────────────────────────────────────────

PROTECTED_LANES   = "data/processed/protected_lanes_clean.gpkg"
TRACTS_SHP        = "data/raw/census/tl_2022_36_tract.shp"
ACS_POP_CSV       = "data/raw/census/ACSDT5Y2022.B01003-Data.csv"
OUTPUT_GPKG       = "data/processed/tracts_coverage.gpkg"
OUTPUT_CSV        = "outputs/data/borough_coverage_summary.csv"

TARGET_CRS    = "EPSG:2263"
BUFFER_FT     = 1312.0    # 400m in feet

BOROUGH_FIPS = {
    "061": "Manhattan",
    "047": "Brooklyn",
    "081": "Queens",
    "005": "Bronx",
    "085": "Staten Island",
}

# ── HELPERS ───────────────────────────────────────────────────────────────────

def straight_line_coverage(protected: gpd.GeoDataFrame,
                             tracts: gpd.GeoDataFrame,
                             buffer_ft: float) -> gpd.GeoDataFrame:
    """
    Fallback coverage calculation using straight-line buffer.
    Less accurate than network ISO area but runs standalone.
    """
    print(f"  Using straight-line {buffer_ft:.0f}ft buffer (fallback mode)")

    # Buffer all protected lane features and dissolve
    coverage_area = unary_union(protected.geometry.buffer(buffer_ft))
    coverage_gdf  = gpd.GeoDataFrame(geometry=[coverage_area], crs=protected.crs)

    # For each tract, calculate intersection area with coverage
    tracts = tracts.copy()
    tracts["tract_area_ft2"]    = tracts.geometry.area
    tracts["covered_area_ft2"]  = tracts.geometry.intersection(coverage_area).area
    tracts["coverage_pct"]      = (
        tracts["covered_area_ft2"] / tracts["tract_area_ft2"] * 100
    ).round(2)

    return tracts


def borough_summary(tracts: gpd.GeoDataFrame) -> pd.DataFrame:
    """Aggregate coverage statistics by borough."""
    rows = []
    for county_fips, borough_name in BOROUGH_FIPS.items():
        subset = tracts[tracts["COUNTYFP"] == county_fips]
        if len(subset) == 0:
            continue

        # Population-weighted coverage: weight each tract's coverage by its population
        pop_weighted = np.nan
        if "total_pop" in subset.columns:
            valid = subset.dropna(subset=["total_pop", "coverage_pct"])
            if len(valid) > 0:
                pop_weighted = np.average(
                    valid["coverage_pct"], weights=valid["total_pop"]
                )

        rows.append({
            "borough":                  borough_name,
            "n_tracts":                 len(subset),
            "mean_coverage_pct":        round(subset["coverage_pct"].mean(), 1),
            "pop_weighted_coverage_pct": round(pop_weighted, 1) if not np.isnan(pop_weighted) else None,
            "tracts_above_50pct":       (subset["coverage_pct"] >= 50).sum(),
            "tracts_below_10pct":       (subset["coverage_pct"] < 10).sum(),
        })
    return pd.DataFrame(rows).sort_values("pop_weighted_coverage_pct", ascending=False)


def print_coverage_table(summary: pd.DataFrame) -> None:
    print(f"\n  {'Borough':<14} {'Tracts':>8} {'Mean Cov':>10} {'Pop-Wtd':>10} "
          f"{'>50% cov':>10} {'<10% cov':>10}")
    print(f"  {'-'*14} {'-'*8} {'-'*10} {'-'*10} {'-'*10} {'-'*10}")
    for _, row in summary.iterrows():
        bar = "▓" * int((row["pop_weighted_coverage_pct"] or 0) / 5)
        print(f"  {row['borough']:<14} "
              f"{row['n_tracts']:>8} "
              f"{row['mean_coverage_pct']:>9.1f}% "
              f"{str(row['pop_weighted_coverage_pct'] or 'N/A'):>9}% "
              f"{row['tracts_above_50pct']:>10} "
              f"{row['tracts_below_10pct']:>10}  {bar}")


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print("=== Coverage Analysis: 400m Protected Bike Lane Access ===\n")

    os.makedirs("data/processed", exist_ok=True)
    os.makedirs("outputs/data",   exist_ok=True)

    # Load protected lanes
    print("Step 1: Loading protected lane network...")
    protected = gpd.read_file(PROTECTED_LANES, layer="protected_lanes_clean")
    if protected.crs.to_epsg() != 2263:
        protected = protected.to_crs(TARGET_CRS)
    print(f"  {len(protected)} features  CRS: {protected.crs.to_epsg()}")

    # Load tracts
    print("\nStep 2: Loading NYC census tracts...")
    tracts = gpd.read_file(TRACTS_SHP)
    tracts = tracts[tracts["STATEFP"] == "36"].to_crs(TARGET_CRS)
    print(f"  {len(tracts)} NYC tracts")

    # Load population (for weighted summary)
    print("\nStep 3: Loading ACS population data...")
    try:
        pop_df = pd.read_csv(ACS_POP_CSV, skiprows=1)
        pop_df = pop_df.rename(columns={
            "Geography": "geoid_raw",
            "Estimate!!Total:": "total_pop"
        })
        pop_df["GEOID"] = pop_df["geoid_raw"].str.replace("1400000US", "")
        pop_df["total_pop"] = pd.to_numeric(pop_df["total_pop"], errors="coerce")
        tracts = tracts.merge(pop_df[["GEOID", "total_pop"]], on="GEOID", how="left")
        print(f"  Population joined to {tracts['total_pop'].notna().sum()} tracts")
    except FileNotFoundError:
        print(f"  WARNING: {ACS_POP_CSV} not found — population weighting skipped")

    # Coverage calculation
    print(f"\nStep 4: Calculating {BUFFER_FT:.0f}ft straight-line coverage per tract...")
    tracts = straight_line_coverage(protected, tracts, BUFFER_FT)

    # Borough summary
    print("\nStep 5: Summarising coverage by borough...")
    summary = borough_summary(tracts)
    print_coverage_table(summary)

    # Save outputs
    tracts.to_file(OUTPUT_GPKG, layer="tracts_coverage", driver="GPKG")
    print(f"\n  Tract coverage layer → {OUTPUT_GPKG}")

    summary.to_csv(OUTPUT_CSV, index=False)
    print(f"  Borough summary → {OUTPUT_CSV}")

    print("\nDone.")


if __name__ == "__main__":
    main()
