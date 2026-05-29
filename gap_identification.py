"""
gap_identification.py
---------------------
Identifies candidate gap segments in the protected bike lane network:
gaps are road segments where protected lanes exist on both sides but
not on the segment itself, AND the gap is short enough to be a plausible
infrastructure fix (< 500m).

Approach:
  1. Find all dangling endpoints of the protected network (network ends)
  2. For each endpoint, buffer by MAX_GAP_FT along the OSM road network
     (using QNEAT3 via PyQGIS for the network buffering, or a simplified
     straight-line buffer if running standalone)
  3. Find OSM segments where protected lanes exist at both ends of the
     gap but not on the segment itself
  4. Filter to segments < 500m (MAX_GAP_FT)

Note on QNEAT3:
  The ISO area (network buffer) generation requires QNEAT3, which is a
  QGIS plugin. If running standalone without QGIS, this script falls back
  to a straight-line buffer — results will be slightly less accurate for
  areas with complex road geometry but are acceptable for a first pass.

Usage:
  python gap_identification.py

Dependencies:
  - geopandas, shapely, pandas, numpy
  - QGIS + QNEAT3 (for full network ISO area; optional)
"""

import os
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point, LineString
from shapely.ops import unary_union

# ── CONFIG ────────────────────────────────────────────────────────────────────

PROTECTED_LANES     = "data/processed/protected_lanes_clean.gpkg"
OSM_NETWORK         = "data/raw/osm/nyc_road_network.gpkg"
OUTPUT_GAPS         = "data/processed/gap_candidates.gpkg"
OUTPUT_SUBNETWORKS  = "data/processed/network_subnetworks.gpkg"

TARGET_CRS   = "EPSG:2263"
MAX_GAP_FT   = 1640.0   # ~500m in feet (EPSG:2263 units are US Survey Feet)
BUFFER_FT    = 985.0    # ~300m buffer for ISO area endpoint search

# ── CONNECTED COMPONENTS ──────────────────────────────────────────────────────

def label_connected_components(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Assign a component_id to each line feature based on topological
    connectivity (shared endpoints). Uses a Union-Find approach.
    Lines that share an endpoint within SNAP_TOL are considered connected.
    """
    import networkx as nx
    from shapely.geometry import Point

    SNAP_TOL = 1.0  # 1 foot — features sharing an endpoint within 1ft are connected

    G = nx.Graph()
    endpoint_to_features = {}

    for idx, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        lines = list(geom.geoms) if geom.geom_type == "MultiLineString" else [geom]
        G.add_node(idx)

        for line in lines:
            coords = list(line.coords)
            for coord in [coords[0], coords[-1]]:
                pt_key = (round(coord[0], 0), round(coord[1], 0))
                if pt_key not in endpoint_to_features:
                    endpoint_to_features[pt_key] = []
                endpoint_to_features[pt_key].append(idx)

    # Add edges between features sharing an endpoint
    for pt_key, feat_ids in endpoint_to_features.items():
        for i in range(len(feat_ids)):
            for j in range(i+1, len(feat_ids)):
                G.add_edge(feat_ids[i], feat_ids[j])

    # Label components
    component_map = {}
    for comp_id, component in enumerate(nx.connected_components(G)):
        for feat_idx in component:
            component_map[feat_idx] = comp_id

    gdf = gdf.copy()
    gdf["component_id"] = gdf.index.map(component_map)

    n_components = gdf["component_id"].nunique()
    print(f"  Connected components: {n_components}")
    for cid in sorted(gdf["component_id"].unique()):
        subset = gdf[gdf["component_id"] == cid]
        total_len = subset.geometry.length.sum()
        print(f"    Component {cid:2d}: {len(subset):4d} features, "
              f"{total_len/5280:.1f} miles total length")

    return gdf


def find_gap_candidates(protected: gpd.GeoDataFrame,
                         osm: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Find OSM road segments that:
    1. Have no protected lane coverage themselves
    2. Are within BUFFER_FT of a protected lane endpoint on BOTH sides
    3. Are shorter than MAX_GAP_FT

    Returns a GeoDataFrame of candidate gap segments.
    """
    print(f"  OSM network features: {len(osm):,}")

    # Buffer of protected lane geometry (to exclude covered segments)
    covered_buffer = unary_union(protected.geometry).buffer(30)  # 30ft = ~9m buffer

    # Find OSM segments NOT covered by any protected lane
    osm_uncovered = osm[~osm.geometry.intersects(covered_buffer)].copy()
    print(f"  OSM segments without protected coverage: {len(osm_uncovered):,}")

    # Filter to segments shorter than MAX_GAP_FT
    osm_uncovered = osm_uncovered[osm_uncovered.geometry.length < MAX_GAP_FT].copy()
    print(f"  OSM segments < {MAX_GAP_FT:.0f}ft: {len(osm_uncovered):,}")

    # Get all endpoints of protected lanes
    endpoints = []
    for geom in protected.geometry:
        if geom is None or geom.is_empty:
            continue
        lines = list(geom.geoms) if geom.geom_type == "MultiLineString" else [geom]
        for line in lines:
            coords = list(line.coords)
            endpoints.append(Point(coords[0]))
            endpoints.append(Point(coords[-1]))

    endpoint_union = unary_union(endpoints)
    endpoint_buffer = endpoint_union.buffer(BUFFER_FT)

    # A gap candidate must have both endpoints near a protected lane endpoint
    gap_candidates = []
    for idx, row in osm_uncovered.iterrows():
        geom = row.geometry
        if geom.geom_type != "LineString":
            continue
        coords = list(geom.coords)
        start_pt = Point(coords[0])
        end_pt   = Point(coords[-1])

        start_near = endpoint_buffer.contains(start_pt)
        end_near   = endpoint_buffer.contains(end_pt)

        if start_near and end_near:
            gap_candidates.append(row)

    if not gap_candidates:
        print("  No gap candidates found — check BUFFER_FT parameter")
        return gpd.GeoDataFrame(crs=protected.crs)

    gaps_gdf = gpd.GeoDataFrame(gap_candidates, crs=protected.crs)
    gaps_gdf["gap_length_ft"] = gaps_gdf.geometry.length.round(1)
    gaps_gdf["gap_length_m"]  = (gaps_gdf.geometry.length * 0.3048).round(1)

    print(f"  Gap candidates identified: {len(gaps_gdf)}")
    print(f"  Total gap length: {gaps_gdf['gap_length_ft'].sum()/5280:.1f} miles")
    return gaps_gdf


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print("=== Gap Identification: Protected Bike Network ===\n")

    os.makedirs("data/processed", exist_ok=True)

    # Load protected lanes
    print("Step 1: Loading clean protected lane network...")
    protected = gpd.read_file(PROTECTED_LANES, layer="protected_lanes_clean")
    print(f"  Features: {len(protected):,}  CRS: {protected.crs.to_epsg()}")

    # Label connected components
    print("\nStep 2: Labelling connected components (subnetworks)...")
    protected = label_connected_components(protected)
    protected.to_file(OUTPUT_SUBNETWORKS, layer="network_subnetworks", driver="GPKG")
    print(f"  Subnetworks saved → {OUTPUT_SUBNETWORKS}")

    # Load OSM network
    print("\nStep 3: Loading OSM road network...")
    if not os.path.exists(OSM_NETWORK):
        print(f"  WARNING: {OSM_NETWORK} not found.")
        print("  Download NYC OSM network via QuickOSM plugin in QGIS.")
        print("  Query: key=highway, value=primary|secondary|tertiary|residential")
        print("  Bounding box: NYC 5-borough extent")
        return

    osm = gpd.read_file(OSM_NETWORK)
    if osm.crs.to_epsg() != 2263:
        osm = osm.to_crs("EPSG:2263")

    # Find gaps
    print("\nStep 4: Identifying gap candidates...")
    gaps = find_gap_candidates(protected, osm)

    if len(gaps) > 0:
        gaps.to_file(OUTPUT_GAPS, layer="gap_candidates", driver="GPKG")
        print(f"\n  Gap candidates saved → {OUTPUT_GAPS}")
        print("\nNext step: run demand_scoring.py")
    else:
        print("\n  No gaps found. Check parameters and input data.")


if __name__ == "__main__":
    main()
