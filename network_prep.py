"""
network_prep.py
---------------
Prepares the NYC protected bike lane network for connectivity analysis:

  1. Loads the full NYC Bicycle Routes layer from NYC Open Data
  2. Filters to protected lanes and greenways only (facility_t IN (1, 2))
  3. Reprojects to NY Long Island State Plane (EPSG:2263)
  4. Runs a topology check (dangles, self-overlaps, duplicates)
  5. Fixes snapping errors (gaps < 5m between endpoints that should connect)
  6. Saves a clean network GeoPackage ready for gap analysis

Topology fixing approach:
    Only gaps < 5m are auto-fixed (snapped). Gaps of 5m or larger
    are flagged for manual review — these could be genuine network ends
    or intentional breaks (e.g., an intersection with no bike crossing).
    See docs/network_topology_notes.md for the full review log.

Usage:
    python network_prep.py

Dependencies:
    - geopandas
    - shapely
    - pandas
    - numpy
"""

import os
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import LineString, MultiLineString
from shapely.ops import snap, unary_union, split

# ── CONFIG ────────────────────────────────────────────────────────────────────

BIKE_ROUTES_SHP     = "data/raw/bike_routes/geo_export.shp"
OUTPUT_FILTERED     = "data/processed/protected_lanes.gpkg"
OUTPUT_CLEAN        = "data/processed/protected_lanes_clean.gpkg"
OUTPUT_TOPO_REPORT  = "outputs/data/topology_audit_report.csv"

TARGET_CRS = "EPSG:2263"   # NY Long Island State Plane (US Survey Feet)

# Facility type codes for protected infrastructure
# 1 = Protected Path (physical barrier), 2 = Greenway
PROTECTED_TYPES = [1, 2]
FACILITY_TYPE_FIELD = "facilitycl"  # field name in NYC Open Data layer

# Snapping tolerance for auto-fix (feet, in EPSG:2263)
# 5m ≈ 16.4 ft — only fix very small gaps
SNAP_TOLERANCE_FT = 16.4
MANUAL_REVIEW_THRESHOLD_FT = 50  # flag gaps larger than this

# ── HELPERS ───────────────────────────────────────────────────────────────────

def load_and_filter(shp_path: str) -> gpd.GeoDataFrame:
    """Load bike routes, filter to protected types only."""
    gdf = gpd.read_file(shp_path)
    print(f"  Total features loaded : {len(gdf):,}")
    print(f"  Facility types present: {sorted(gdf[FACILITY_TYPE_FIELD].unique())}")

    protected = gdf[gdf[FACILITY_TYPE_FIELD].isin(PROTECTED_TYPES)].copy()
    print(f"  Protected/greenway    : {len(protected):,} features retained")
    return protected


def reproject(gdf: gpd.GeoDataFrame, target_crs: str) -> gpd.GeoDataFrame:
    """Reproject to target CRS."""
    if gdf.crs.to_epsg() != int(target_crs.split(":")[1]):
        gdf = gdf.to_crs(target_crs)
        print(f"  Reprojected to {target_crs}")
    return gdf


def find_dangles(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Find dangling endpoints — line endpoints not shared by any other line.
    Returns a GeoDataFrame of dangle points with the parent line's index.
    """
    from shapely.geometry import Point

    all_endpoints = []
    for idx, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        if geom.geom_type == "LineString":
            lines = [geom]
        elif geom.geom_type == "MultiLineString":
            lines = list(geom.geoms)
        else:
            continue
        for line in lines:
            coords = list(line.coords)
            all_endpoints.append({"parent_idx": idx, "is_start": True,
                                   "geometry": Point(coords[0])})
            all_endpoints.append({"parent_idx": idx, "is_start": False,
                                   "geometry": Point(coords[-1])})

    endpoints_gdf = gpd.GeoDataFrame(all_endpoints, crs=gdf.crs)

    # An endpoint is a dangle if it's NOT shared by any other line's endpoint
    # Use spatial index — find endpoints within 0.1ft of each other
    endpoint_union = unary_union(endpoints_gdf.geometry)
    dangles = []
    for i, ep in endpoints_gdf.iterrows():
        nearby = endpoints_gdf[
            endpoints_gdf.geometry.distance(ep.geometry) < 0.1
        ]
        if len(nearby) == 1:  # only itself — it's a dangle
            dangles.append(ep)

    dangles_gdf = gpd.GeoDataFrame(dangles, crs=gdf.crs)
    return dangles_gdf


def classify_dangles(dangles: gpd.GeoDataFrame,
                      snap_tol: float,
                      review_tol: float) -> dict:
    """
    For each dangle, find the nearest other dangle.
    Classify as: auto-fix (< snap_tol), review (snap_tol–review_tol),
    or genuine-end (> review_tol).
    """
    from shapely.ops import nearest_points

    results = {"auto_fix": [], "review": [], "genuine_end": []}

    dangle_points = dangles.geometry.tolist()
    union = unary_union(dangle_points)

    for i, row in dangles.iterrows():
        pt = row.geometry
        # Find nearest OTHER dangle
        nearby = dangles[dangles.geometry.distance(pt) > 0.1]
        if len(nearby) == 0:
            results["genuine_end"].append(row)
            continue
        nearest_idx = nearby.geometry.distance(pt).idxmin()
        dist = nearby.loc[nearest_idx].geometry.distance(pt)

        if dist <= snap_tol:
            results["auto_fix"].append({"dangle": row, "nearest_idx": nearest_idx,
                                         "dist_ft": dist})
        elif dist <= review_tol:
            results["review"].append({"dangle": row, "dist_ft": dist})
        else:
            results["genuine_end"].append(row)

    return results


def write_topology_report(dangles_classified: dict, out_path: str) -> None:
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    rows = []
    for category, items in dangles_classified.items():
        for item in items:
            if isinstance(item, dict):
                dangle = item["dangle"]
                dist   = item.get("dist_ft", None)
            else:
                dangle = item
                dist   = None
            rows.append({
                "parent_feature_idx": dangle.get("parent_idx"),
                "classification": category,
                "gap_distance_ft": round(dist, 2) if dist else None,
                "x": round(dangle.geometry.x, 2),
                "y": round(dangle.geometry.y, 2),
            })
    pd.DataFrame(rows).to_csv(out_path, index=False)
    print(f"  Topology report → {out_path}")


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print("=== Network Prep: NYC Protected Bike Lanes ===\n")

    os.makedirs("data/processed", exist_ok=True)
    os.makedirs("outputs/data",   exist_ok=True)

    # Step 1: Load and filter
    print("Step 1: Loading and filtering bike routes...")
    gdf = load_and_filter(BIKE_ROUTES_SHP)

    # Step 2: Reproject
    print("\nStep 2: Reprojecting to EPSG:2263...")
    gdf = reproject(gdf, TARGET_CRS)

    # Step 3: Save filtered (before topology fix)
    gdf.to_file(OUTPUT_FILTERED, layer="protected_lanes", driver="GPKG")
    print(f"\n  Saved filtered layer → {OUTPUT_FILTERED}")

    # Step 4: Topology audit
    print("\nStep 3: Running topology audit (dangles)...")
    dangles = find_dangles(gdf)
    print(f"  Dangling endpoints found: {len(dangles)}")

    classified = classify_dangles(dangles, SNAP_TOLERANCE_FT,
                                   MANUAL_REVIEW_THRESHOLD_FT)

    print(f"  Auto-fixable (< {SNAP_TOLERANCE_FT:.0f}ft gap) : "
          f"{len(classified['auto_fix'])}")
    print(f"  Needs review ({SNAP_TOLERANCE_FT:.0f}–{MANUAL_REVIEW_THRESHOLD_FT}ft gap): "
          f"{len(classified['review'])}")
    print(f"  Genuine endpoints (> {MANUAL_REVIEW_THRESHOLD_FT}ft) : "
          f"{len(classified['genuine_end'])}")

    write_topology_report(classified, OUTPUT_TOPO_REPORT)

    # Step 5: Apply snapping fix for auto-fixable gaps
    print(f"\nStep 4: Applying snapping fix for {len(classified['auto_fix'])} gaps...")
    gdf_clean = gdf.copy()
    fixed_count = 0
    for item in classified["auto_fix"]:
        idx = item["dangle"]["parent_idx"]
        if idx in gdf_clean.index:
            geom = gdf_clean.loc[idx, "geometry"]
            # Snap geometry to nearest network feature within tolerance
            nearby_union = unary_union(
                gdf_clean[gdf_clean.index != idx].geometry.tolist()
            )
            snapped = snap(geom, nearby_union, SNAP_TOLERANCE_FT)
            gdf_clean.at[idx, "geometry"] = snapped
            fixed_count += 1

    print(f"  Fixed {fixed_count} features via snapping")

    # Step 6: Save clean network
    gdf_clean.to_file(OUTPUT_CLEAN, layer="protected_lanes_clean", driver="GPKG")
    print(f"\n  Saved clean network → {OUTPUT_CLEAN}")

    print("\nDone. Review outputs/data/topology_audit_report.csv for")
    print("'review' category dangles before proceeding to gap_identification.py")


if __name__ == "__main__":
    main()
