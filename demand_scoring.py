"""
demand_scoring.py
-----------------
Joins ACS 2022 census data to gap candidates and computes a
demand-weighted priority score for each gap:

    demand_score = (pop_density_norm    * 0.50)
                 + (bike_commuter_norm  * 0.30)
                 + (gap_length_inv_norm * 0.20)

Shorter gaps in denser areas with more bike commuters = highest priority.

ACS tables used:
    B01003 - Total population (for density calculation)
    B08301 - Commute mode (bike commuters: field B08301_018E)

GEOID note:
    NYC Open Data census tract GEOIDs omit the leading state/county
    FIPS digits. ACS GEOIDs are full 11-digit codes (state + county + tract).
    Field Calculator expression used to reconstruct:
        lpad("BOROCODE" || "CT2020", 11, '0')
    This is handled in code below via the geoid_format_fix() function.

Usage:
    python demand_scoring.py

Dependencies:
    - geopandas, pandas, numpy
"""

import os
import numpy as np
import pandas as pd
import geopandas as gpd

# ── CONFIG ────────────────────────────────────────────────────────────────────

GAP_CANDIDATES  = "data/processed/gap_candidates.gpkg"
TRACTS_SHP      = "data/raw/census/tl_2022_36_tract.shp"
ACS_POP_CSV     = "data/raw/census/ACSDT5Y2022.B01003-Data.csv"
ACS_COMMUTE_CSV = "data/raw/census/ACSDT5Y2022.B08301-Data.csv"
OUTPUT_SCORED   = "data/processed/gap_priority_scored.gpkg"
OUTPUT_CSV      = "outputs/data/gap_priority_table.csv"

TARGET_CRS     = "EPSG:2263"
BUFFER_FT      = 1640.0  # 500m in feet — tracts within this buffer of a gap are included

WEIGHTS = {"pop_density": 0.50, "bike_commuter": 0.30, "gap_length_inv": 0.20}

# ── HELPERS ───────────────────────────────────────────────────────────────────

def geoid_format_fix(raw_geoid: str) -> str:
    """
    Ensure GEOID is full 11-digit format: SSCCCTTTTTT
    Some NYC datasets use a shorter format.
    """
    raw = str(raw_geoid).strip().replace(".", "")
    if len(raw) == 11:
        return raw
    elif len(raw) < 11:
        return raw.zfill(11)
    return raw[:11]


def load_acs_population(csv_path: str) -> pd.DataFrame:
    """Load ACS B01003 (total population) and return GEOID + pop."""
    df = pd.read_csv(csv_path, skiprows=1)
    df = df.rename(columns={
        "Geography":             "geoid_raw",
        "Estimate!!Total:":      "total_pop"
    })
    df["geoid"] = df["geoid_raw"].str.replace("1400000US", "")
    df["total_pop"] = pd.to_numeric(df["total_pop"], errors="coerce")
    return df[["geoid", "total_pop"]].dropna()


def load_acs_commute(csv_path: str) -> pd.DataFrame:
    """Load ACS B08301 (commute mode) and return GEOID + bike commuters."""
    df = pd.read_csv(csv_path, skiprows=1)
    # B08301_018E = Bicycle commuters
    bike_col = [c for c in df.columns if "018E" in c and "Estimate" in c]
    if not bike_col:
        print("  WARNING: Could not find bike commute column in ACS B08301")
        return pd.DataFrame(columns=["geoid", "bike_commuters"])
    bike_col = bike_col[0]
    df = df.rename(columns={"Geography": "geoid_raw", bike_col: "bike_commuters"})
    df["geoid"] = df["geoid_raw"].str.replace("1400000US", "")
    df["bike_commuters"] = pd.to_numeric(df["bike_commuters"], errors="coerce")
    return df[["geoid", "bike_commuters"]].dropna()


def minmax_norm(series: pd.Series) -> pd.Series:
    vmin, vmax = series.min(), series.max()
    if vmax == vmin:
        return pd.Series(np.zeros(len(series)), index=series.index)
    return (series - vmin) / (vmax - vmin)


def score_gaps(gaps: gpd.GeoDataFrame,
               tracts: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    For each gap, spatial-join census tracts within BUFFER_FT,
    aggregate demand variables, then compute demand score.
    """
    # Buffer gaps to find nearby tracts
    gaps_buffered = gaps.copy()
    gaps_buffered["geometry"] = gaps.geometry.buffer(BUFFER_FT)

    # Spatial join: find tracts overlapping each gap buffer
    joined = gpd.sjoin(
        tracts[["geoid", "pop_density", "bike_commuters", "geometry"]],
        gaps_buffered[["gap_id", "gap_length_ft", "geometry"]],
        how="inner", predicate="intersects"
    )

    # Aggregate: take mean of nearby tracts for each gap
    agg = joined.groupby("gap_id").agg(
        pop_density_mean   = ("pop_density",    "mean"),
        bike_commuter_mean = ("bike_commuters",  "mean"),
    ).reset_index()

    gaps_scored = gaps.merge(agg, on="gap_id", how="left")

    # Inverse gap length: shorter gaps score higher
    max_len = gaps_scored["gap_length_ft"].max()
    gaps_scored["gap_length_inv"] = max_len - gaps_scored["gap_length_ft"]

    # Normalize
    gaps_scored["pop_density_norm"]    = minmax_norm(gaps_scored["pop_density_mean"].fillna(0))
    gaps_scored["bike_commuter_norm"]  = minmax_norm(gaps_scored["bike_commuter_mean"].fillna(0))
    gaps_scored["gap_length_inv_norm"] = minmax_norm(gaps_scored["gap_length_inv"])

    # Weighted score 0-100
    gaps_scored["demand_score"] = (
        gaps_scored["pop_density_norm"]    * WEIGHTS["pop_density"]    +
        gaps_scored["bike_commuter_norm"]  * WEIGHTS["bike_commuter"]  +
        gaps_scored["gap_length_inv_norm"] * WEIGHTS["gap_length_inv"]
    ) * 100

    gaps_scored["demand_score"] = gaps_scored["demand_score"].round(2)
    gaps_scored["priority_rank"] = gaps_scored["demand_score"].rank(
        ascending=False, method="min"
    ).astype(int)

    return gaps_scored.sort_values("priority_rank")


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print("=== Demand Scoring: Gap Priority Analysis ===\n")

    os.makedirs("data/processed", exist_ok=True)
    os.makedirs("outputs/data",   exist_ok=True)

    # Load gaps
    print("Step 1: Loading gap candidates...")
    gaps = gpd.read_file(GAP_CANDIDATES, layer="gap_candidates")
    gaps["gap_id"] = range(len(gaps))
    print(f"  {len(gaps)} gaps loaded")

    # Load and prepare census tracts
    print("\nStep 2: Loading census tracts and ACS data...")
    tracts = gpd.read_file(TRACTS_SHP)
    tracts = tracts.to_crs(TARGET_CRS)

    # NYC tracts only (state FIPS 36)
    tracts = tracts[tracts["STATEFP"] == "36"].copy()
    tracts["geoid"] = tracts["GEOID"].apply(geoid_format_fix)
    tracts["area_km2"] = tracts.geometry.area / (1000 / 0.3048) ** 2  # ft² → km²

    # Load ACS tables
    pop_df     = load_acs_population(ACS_POP_CSV)
    commute_df = load_acs_commute(ACS_COMMUTE_CSV)

    tracts = tracts.merge(pop_df,     on="geoid", how="left")
    tracts = tracts.merge(commute_df, on="geoid", how="left")

    tracts["pop_density"] = (tracts["total_pop"] / tracts["area_km2"]).round(2)

    null_pop = tracts["total_pop"].isna().sum()
    if null_pop > 0:
        print(f"  WARNING: {null_pop} tracts with null population (GEOID join issue?)")

    print(f"  {len(tracts)} NYC census tracts loaded, "
          f"population range: {tracts['total_pop'].min():.0f}–"
          f"{tracts['total_pop'].max():.0f}")

    # Score gaps
    print("\nStep 3: Computing demand scores...")
    gaps_scored = score_gaps(gaps, tracts)

    # Save
    gaps_scored.to_file(OUTPUT_SCORED, layer="gap_priority_scored", driver="GPKG")
    print(f"\n  Scored gaps → {OUTPUT_SCORED}")

    export_cols = ["gap_id", "priority_rank", "demand_score",
                   "gap_length_ft", "gap_length_m",
                   "pop_density_mean", "bike_commuter_mean"]
    export_cols = [c for c in export_cols if c in gaps_scored.columns]
    gaps_scored[export_cols].to_csv(OUTPUT_CSV, index=False)
    print(f"  Priority table → {OUTPUT_CSV}")

    print(f"\n  Top 10 priority gaps:")
    print(gaps_scored[export_cols].head(10).to_string(index=False))
    print("\nNext step: run coverage_analysis.py")


if __name__ == "__main__":
    main()
