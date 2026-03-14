#!/usr/bin/env python
"""
Subprocess routing engine for KlayoutClaw auto_route MCP tool.

Accepts a config JSON file path as CLI arg. Loads a GDS file using klayout.db,
extracts pin locations, rasterizes obstacles into a 2D numpy cost grid, uses
scipy Hungarian matching for optimal pin pairing, and scikit-image MCP_Geometric
for Dijkstra-based minimum-cost pathfinding.

Usage:
    python route_worker.py config.json
"""

import json
import sys
import math
import numpy as np
from scipy.optimize import linear_sum_assignment
from skimage.graph import MCP_Geometric
import klayout.db as kdb


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_layer(spec: str) -> tuple[int, int]:
    """Parse a layer spec like '102/0' or '5' into (layer, datatype)."""
    parts = spec.strip().split("/")
    layer = int(parts[0])
    datatype = int(parts[1]) if len(parts) > 1 else 0
    return (layer, datatype)


def compress_path(points: list[list[int]]) -> list[list[int]]:
    """Remove collinear waypoints from a path.

    Keeps the first and last point, and any point where the direction changes
    (cross product of consecutive direction vectors is non-zero).
    """
    if len(points) <= 2:
        return list(points)

    result = [points[0]]
    for i in range(1, len(points) - 1):
        # Direction vectors
        dx1 = points[i][0] - points[i - 1][0]
        dy1 = points[i][1] - points[i - 1][1]
        dx2 = points[i + 1][0] - points[i][0]
        dy2 = points[i + 1][1] - points[i][1]
        # Cross product
        cross = dx1 * dy2 - dy1 * dx2
        if cross != 0:
            result.append(points[i])
    result.append(points[-1])
    return result


# ---------------------------------------------------------------------------
# Pin extraction
# ---------------------------------------------------------------------------

def extract_pin_centers(cell: kdb.Cell, layout: kdb.Layout, layer_num: int,
                        datatype: int) -> list[tuple[int, int]]:
    """Extract center points (in dbu) of all shapes on a given layer."""
    layer_idx = layout.find_layer(layer_num, datatype)
    if layer_idx is None:
        return []

    centers = []
    for shape in cell.shapes(layer_idx).each():
        bbox = shape.bbox()
        cx = (bbox.left + bbox.right) // 2
        cy = (bbox.bottom + bbox.top) // 2
        centers.append((cx, cy))
    return centers


# ---------------------------------------------------------------------------
# Obstacle rasterization
# ---------------------------------------------------------------------------

def build_obstacle_region(cell: kdb.Cell, layout: kdb.Layout,
                          obstacle_layers: list[str],
                          safe_distance_dbu: int) -> kdb.Region:
    """Merge all obstacle layer shapes into one Region, expanded by safe distance."""
    region = kdb.Region()
    for spec in obstacle_layers:
        ln, dt = parse_layer(spec)
        li = layout.find_layer(ln, dt)
        if li is None:
            continue
        region += kdb.Region(cell.shapes(li))
    if safe_distance_dbu > 0:
        region = region.sized(safe_distance_dbu)
    region.merge()
    return region


def rasterize_region_kdb(region: kdb.Region, bbox: kdb.Box,
                         resolution_dbu: int) -> np.ndarray:
    """Rasterize a kdb.Region into a 2D boolean numpy array using KLayout's native rasterizer.

    Grid axes: row = y (bottom-to-top mapped to 0..nrows-1), col = x.
    """
    ncols = max(1, (bbox.width() + resolution_dbu - 1) // resolution_dbu)
    nrows = max(1, (bbox.height() + resolution_dbu - 1) // resolution_dbu)
    origin = kdb.Point(bbox.left, bbox.bottom)
    step = kdb.Vector(resolution_dbu, resolution_dbu)
    raster = np.array(region.rasterize(origin, step, ncols, nrows))
    return raster > 0


# ---------------------------------------------------------------------------
# Coordinate conversion helpers
# ---------------------------------------------------------------------------

def dbu_to_grid(x_dbu: int, y_dbu: int, bbox: kdb.Box,
                resolution_dbu: int) -> tuple[int, int]:
    """Convert dbu coordinates to grid (row, col)."""
    col = (x_dbu - bbox.left) // resolution_dbu
    row = (y_dbu - bbox.bottom) // resolution_dbu
    return (row, col)


def grid_to_dbu(row: int, col: int, bbox: kdb.Box,
                resolution_dbu: int) -> tuple[int, int]:
    """Convert grid (row, col) back to dbu coordinates (center of grid cell)."""
    x = bbox.left + col * resolution_dbu + resolution_dbu // 2
    y = bbox.bottom + row * resolution_dbu + resolution_dbu // 2
    return (x, y)


def conditional_overwrite(cost: np.ndarray, content: np.ndarray,
                          content_mask: np.ndarray,
                          r0: int, c0: int,
                          condition_fn=None) -> None:
    """Update cost grid subregion where both content_mask and condition_fn are true.

    Args:
        cost: Full cost grid (modified in-place).
        content: New values to write (shape must fit in cost[r0:r0+nrows, c0:c0+ncols]).
        content_mask: Boolean mask — which cells in content are candidates.
        r0, c0: Top-left offset of the subregion in the full cost grid.
        condition_fn: Function (existing_slice, content) -> bool mask.
            Receives the existing cost subregion AND the content array,
            enabling content-dependent conditions like "only increase".
            Default: always True (unconditional overwrite of masked cells).
    """
    nrows, ncols = content.shape
    # Clip to grid bounds
    grid_rows, grid_cols = cost.shape
    r1 = min(r0 + nrows, grid_rows)
    c1 = min(c0 + ncols, grid_cols)
    r0_c = max(r0, 0)
    c0_c = max(c0, 0)
    if r0_c >= r1 or c0_c >= c1:
        return
    # Compute content slice offsets
    cr0 = r0_c - r0
    cc0 = c0_c - c0
    cr1 = cr0 + (r1 - r0_c)
    cc1 = cc0 + (c1 - c0_c)
    content_slice = content[cr0:cr1, cc0:cc1]
    mask_slice = content_mask[cr0:cr1, cc0:cc1]
    region = cost[r0_c:r1, c0_c:c1]
    if condition_fn is not None:
        mask = condition_fn(region, content_slice) & mask_slice
    else:
        mask = mask_slice
    region[mask] = content_slice[mask]


def get_damping_raster(region: kdb.Region, bbox: kdb.Box,
                       resolution_dbu: int, safe_distance_dbu: int,
                       hardness: float,
                       n_steps: int) -> tuple[int, int, np.ndarray]:
    """Build a graduated cost raster around a region.

    Creates n_steps concentric expansions of the region. Each expansion
    adds hardness // n_steps to the cost. Result: cost ramps from 0 at
    safe_distance to hardness at the region boundary.

    Returns (r0, c0, damping) where (r0, c0) is the grid offset of the
    raster's top-left corner relative to the full cost grid (whose origin
    is bbox.left, bbox.bottom). The raster covers only the bounding box
    of the sized union for performance.
    """
    if n_steps <= 0 or safe_distance_dbu <= 0:
        return 0, 0, np.zeros((0, 0), dtype=np.float64)

    union = region.dup()
    for i in range(n_steps):
        sized = region.sized(int(safe_distance_dbu * (i + 1) / n_steps))
        union += sized

    # Clip to routing bbox
    union = union & bbox

    if union.is_empty():
        return 0, 0, np.zeros((0, 0), dtype=np.float64)

    union_bbox = union.bbox()
    # Snap origin to align with the full cost grid's pixel boundaries.
    # This avoids partial-coverage rounding errors at damping field edges.
    origin_x = bbox.left + ((union_bbox.left - bbox.left) // resolution_dbu) * resolution_dbu
    origin_y = bbox.bottom + ((union_bbox.bottom - bbox.bottom) // resolution_dbu) * resolution_dbu
    origin = kdb.Point(origin_x, origin_y)
    # Compute extent to cover the union bbox from the snapped origin
    extent_x = union_bbox.right - origin_x
    extent_y = union_bbox.top - origin_y
    step = kdb.Vector(resolution_dbu, resolution_dbu)
    ncols = max(1, (extent_x + resolution_dbu - 1) // resolution_dbu)
    nrows = max(1, (extent_y + resolution_dbu - 1) // resolution_dbu)

    raster_raw = np.array(union.rasterize(origin, step, ncols, nrows))
    # Normalize: each concentric layer contributes hardness // n_steps
    step_cost = hardness // n_steps if n_steps > 0 else hardness
    damping = (raster_raw // (resolution_dbu * resolution_dbu)) * step_cost
    damping = damping.astype(np.float64)

    # Grid offset: where this raster sits in the full cost grid
    r0 = (origin_y - bbox.bottom) // resolution_dbu
    c0 = (origin_x - bbox.left) // resolution_dbu
    return r0, c0, damping


# ---------------------------------------------------------------------------
# Cost grid construction
# ---------------------------------------------------------------------------

def build_cost_grid_graduated(obstacle_grid: np.ndarray,
                              obs_region: kdb.Region,
                              bbox: kdb.Box,
                              resolution_dbu: int,
                              obs_hardness: float,
                              obs_damping_step: int,
                              obs_safe_dbu: int) -> np.ndarray:
    """Build a float cost grid with graduated damping around obstacles.

    Obstacle cells get cost -1 (impassable via MCP_Geometric negative-cost
    convention). Cells near obstacles get stepped damping cost that
    increases toward the obstacle boundary.
    """
    nrows, ncols = obstacle_grid.shape
    cost = np.ones((nrows, ncols), dtype=np.float64)

    # Mark obstacles as impassable
    cost[obstacle_grid] = -1.0

    # Add graduated damping around obstacles
    if obs_damping_step > 0 and obs_safe_dbu > 0:
        r0, c0, damping = get_damping_raster(
            obs_region, bbox, resolution_dbu, obs_safe_dbu,
            obs_hardness, obs_damping_step)
        if damping.size > 0:
            conditional_overwrite(
                cost, damping, damping > 0, r0, c0,
                condition_fn=lambda existing, new: existing >= 0)

    return cost


# ---------------------------------------------------------------------------
# Pathfinding
# ---------------------------------------------------------------------------

def find_path(cost: np.ndarray, start: tuple[int, int],
              end: tuple[int, int]) -> list[tuple[int, int]] | None:
    """Find minimum-cost path using MCP_Geometric (Dijkstra on grid).

    Returns list of (row, col) or None if no path found.
    Uses negative-sentinel convention: cost < 0 means impassable.
    """
    # Clamp to grid bounds
    nrows, ncols = cost.shape
    sr = max(0, min(start[0], nrows - 1))
    sc = max(0, min(start[1], ncols - 1))
    er = max(0, min(end[0], nrows - 1))
    ec = max(0, min(end[1], ncols - 1))

    # If start or end is blocked, clear it temporarily.
    # Defensive guard: MCP_Geometric produces silently wrong results
    # when started on a negative-cost cell.
    orig_start = cost[sr, sc]
    orig_end = cost[er, ec]
    if cost[sr, sc] < 0:
        cost[sr, sc] = 1.0
    if cost[er, ec] < 0:
        cost[er, ec] = 1.0

    try:
        mcp = MCP_Geometric(cost, fully_connected=True)
        mcp.find_costs([(sr, sc)])
        path = mcp.traceback((er, ec))
        return path
    except Exception:
        return None
    finally:
        cost[sr, sc] = orig_start
        cost[er, ec] = orig_end


# ---------------------------------------------------------------------------
# Main routing logic
# ---------------------------------------------------------------------------

def route(config: dict) -> dict:
    """Execute the full routing pipeline. Returns result dict."""
    errors = []

    # Parse config
    gds_path = config["gds_path"]
    cell_name = config.get("cell_name", "TOP")
    dbu = config.get("dbu", 0.001)
    pin_layer_a = config["pin_layer_a"]
    pin_layer_b = config["pin_layer_b"]
    obstacle_layers = config.get("obstacle_layers", [])
    path_width_um = config.get("path_width_um", 1.0)
    obs_safe_um = config.get("obs_safe_distance_um", 5.0)
    path_safe_um = config.get("path_safe_distance_um", 5.0)
    map_res_um = config.get("map_resolution_um", 1.0)

    # New graduated damping parameters (backward compatible defaults)
    obs_hardness = config.get("obs_hardness", 20.0)
    obs_damping_step = config.get("obs_damping_step", 4)
    pin_safe_a_um = config.get("pin_safe_distance_a_um", 5.0)
    pin_safe_b_um = config.get("pin_safe_distance_b_um", 5.0)
    pin_hardness = config.get("pin_hardness", 20.0)
    pin_damping_step = config.get("pin_damping_step", 4)
    path_hardness = config.get("path_hardness", 10.0)
    path_damping_step = config.get("path_damping_step", 5)
    sort_pairs = config.get("sort_pairs", True)

    # Convert um to dbu
    resolution_dbu = int(round(map_res_um / dbu))
    obs_safe_dbu = int(round(obs_safe_um / dbu))
    path_width_dbu = int(round(path_width_um / dbu))

    # Convert new um params to dbu
    pin_safe_a_dbu = int(round(pin_safe_a_um / dbu))
    pin_safe_b_dbu = int(round(pin_safe_b_um / dbu))
    path_safe_dbu = int(round(path_safe_um / dbu))

    # Load GDS
    layout = kdb.Layout()
    layout.read(gds_path)
    layout.dbu = dbu

    # Find cell
    cell = None
    for ci in range(layout.cells()):
        c = layout.cell(ci)
        if c.name == cell_name:
            cell = c
            break
    if cell is None:
        return {"status": "error", "routed_pairs": 0, "total_pins_a": 0,
                "total_pins_b": 0, "paths": [],
                "errors": [f"Cell '{cell_name}' not found"]}

    # Extract pins
    la, da = parse_layer(pin_layer_a)
    lb, db = parse_layer(pin_layer_b)
    pins_a = extract_pin_centers(cell, layout, la, da)
    pins_b = extract_pin_centers(cell, layout, lb, db)

    if not pins_a or not pins_b:
        return {"status": "error", "routed_pairs": 0,
                "total_pins_a": len(pins_a), "total_pins_b": len(pins_b),
                "paths": [],
                "errors": ["No pins found on one or both layers"]}

    obs_region = build_obstacle_region(cell, layout, obstacle_layers, 0)

    # Build pin footprint regions (used for obstacle exclusion, cost marking, and damping)
    pin_radius = resolution_dbu  # half-width of pin footprint box
    pin_regions_a = kdb.Region()
    for px, py in pins_a:
        pin_regions_a.insert(kdb.Box(
            px - pin_radius, py - pin_radius,
            px + pin_radius, py + pin_radius))

    pin_regions_b = kdb.Region()
    for px, py in pins_b:
        pin_regions_b.insert(kdb.Box(
            px - pin_radius, py - pin_radius,
            px + pin_radius, py + pin_radius))

    # Subtract pin clearance from obstacles — pins often sit on device geometry
    # (e.g. contact tips on mesa layer). Clear a corridor around each pin so
    # paths can reach them through the obstacle field. The clearance must be
    # large enough to cut through the full obstacle + damping zone.
    all_pin_region = pin_regions_a + pin_regions_b
    raw_obs_region = build_obstacle_region(cell, layout, obstacle_layers, 0)
    pin_exclusion = kdb.Region()
    for pin_list in [pins_a, pins_b]:
        for px, py in pin_list:
            # Find the obstacle shape containing this pin
            pt_box = kdb.Box(px - 1, py - 1, px + 1, py + 1)
            touching = raw_obs_region.interacting(kdb.Region(pt_box))
            if touching.is_empty():
                clear_radius = obs_safe_dbu + resolution_dbu * 2
            else:
                obs_bbox = touching.bbox()
                dx = max(abs(px - obs_bbox.left), abs(px - obs_bbox.right))
                dy = max(abs(py - obs_bbox.bottom), abs(py - obs_bbox.top))
                max_dist = int(math.sqrt(dx * dx + dy * dy))
                clear_radius = max_dist + obs_safe_dbu + resolution_dbu * 2
            pin_exclusion.insert(kdb.Box(
                px - clear_radius, py - clear_radius,
                px + clear_radius, py + clear_radius))
    obs_region = obs_region - pin_exclusion

    # Compute bounding box (cell bbox with margin)
    cell_bbox = cell.bbox()
    margin = max(obs_safe_dbu, resolution_dbu * 10)
    bbox = kdb.Box(
        cell_bbox.left - margin, cell_bbox.bottom - margin,
        cell_bbox.right + margin, cell_bbox.top + margin,
    )

    # Rasterize obstacles
    obs_grid = rasterize_region_kdb(obs_region, bbox, resolution_dbu)

    # Build graduated cost grid
    cost = build_cost_grid_graduated(
        obs_grid, obs_region, bbox, resolution_dbu,
        obs_hardness, obs_damping_step, obs_safe_dbu)

    # Rasterize pin footprints as -2 (overwrite everything)
    all_pin_region = pin_regions_a + pin_regions_b
    pin_grid = rasterize_region_kdb(all_pin_region, bbox, resolution_dbu)
    cost[pin_grid] = -2.0

    # Add pin damping halos (Pin A)
    if pin_safe_a_dbu > 0 and pin_damping_step > 0:
        r0, c0, damping = get_damping_raster(
            pin_regions_a, bbox, resolution_dbu, pin_safe_a_dbu,
            pin_hardness, pin_damping_step)
        if damping.size > 0:
            conditional_overwrite(
                cost, damping, damping > 0, r0, c0,
                condition_fn=lambda existing, new: (existing > 0) & (existing < new))

    # Add pin damping halos (Pin B)
    if pin_safe_b_dbu > 0 and pin_damping_step > 0:
        r0, c0, damping = get_damping_raster(
            pin_regions_b, bbox, resolution_dbu, pin_safe_b_dbu,
            pin_hardness, pin_damping_step)
        if damping.size > 0:
            conditional_overwrite(
                cost, damping, damping > 0, r0, c0,
                condition_fn=lambda existing, new: (existing > 0) & (existing < new))

    # Hungarian matching: Euclidean distance cost matrix
    n_a, n_b = len(pins_a), len(pins_b)
    n = max(n_a, n_b)
    dist_matrix = np.full((n, n), 1e18)

    for i in range(n_a):
        for j in range(n_b):
            dx = pins_a[i][0] - pins_b[j][0]
            dy = pins_a[i][1] - pins_b[j][1]
            dist_matrix[i, j] = math.sqrt(dx * dx + dy * dy)

    row_ind, col_ind = linear_sum_assignment(dist_matrix)

    # Build matched pairs and filter dummy assignments
    pairs = []
    for idx in range(len(row_ind)):
        i, j = row_ind[idx], col_ind[idx]
        if i >= n_a or j >= n_b:
            continue
        pairs.append((i, j))

    # Sort pairs by ascending distance (short pairs first)
    if sort_pairs:
        pairs.sort(key=lambda ij: dist_matrix[ij[0], ij[1]])

    # Route each matched pair with per-pair pin recovery
    result_paths = []
    for pair_idx, (i, j) in enumerate(pairs):
        pa = pins_a[i]
        pb = pins_b[j]

        start_rc = dbu_to_grid(pa[0], pa[1], bbox, resolution_dbu)
        end_rc = dbu_to_grid(pb[0], pb[1], bbox, resolution_dbu)

        # Step 1: Recover this pair's pin footprint regions to walkable (cost=1)
        # Rasterize each pin's footprint box and save/restore all affected cells
        pin_a_box = kdb.Box(pa[0] - pin_radius, pa[1] - pin_radius,
                            pa[0] + pin_radius, pa[1] + pin_radius)
        pin_b_box = kdb.Box(pb[0] - pin_radius, pb[1] - pin_radius,
                            pb[0] + pin_radius, pb[1] + pin_radius)
        pair_pin_region = kdb.Region(pin_a_box) + kdb.Region(pin_b_box)
        pair_pin_grid = rasterize_region_kdb(pair_pin_region, bbox, resolution_dbu)
        # Save original costs and set to walkable
        saved_pin_costs = cost[pair_pin_grid].copy()
        cost[pair_pin_grid] = 1.0

        # Step 2: Find path
        path_rc = find_path(cost, start_rc, end_rc)
        if path_rc is None:
            errors.append(f"No path found for pin pair {i}->{j}")
            # Restore pin cells
            cost[pair_pin_grid] = saved_pin_costs
            continue

        # Step 3: Convert grid path to dbu coordinates
        path_dbu = []
        for r, c in path_rc:
            x, y = grid_to_dbu(r, c, bbox, resolution_dbu)
            path_dbu.append([x, y])
        path_dbu = compress_path(path_dbu)

        result_paths.append({
            "points_dbu": path_dbu,
            "pin_a": list(pa),
            "pin_b": list(pb),
        })

        # Step 4: Mark path as impassable (-3) and add graduated damping
        # Build a kdb.Region from the path for rasterization
        if len(path_rc) >= 2:
            path_points = [kdb.Point(*grid_to_dbu(r, c, bbox, resolution_dbu))
                           for r, c in path_rc]
            path_obj = kdb.Path(path_points, path_width_dbu, path_width_dbu // 2,
                                path_width_dbu // 2, True)
            path_region = kdb.Region(path_obj)
            path_grid = rasterize_region_kdb(path_region, bbox, resolution_dbu)
            cost[path_grid] = -3.0

            # Add graduated path damping
            if path_damping_step > 0 and path_safe_dbu > 0:
                r0, c0, damping = get_damping_raster(
                    path_region, bbox, resolution_dbu, path_safe_dbu,
                    path_hardness, path_damping_step)
                if damping.size > 0:
                    conditional_overwrite(
                        cost, damping, damping > 0, r0, c0,
                        condition_fn=lambda existing, new: (existing > 0) & (existing < new))

        # Step 5: Restore this pair's pin footprint to blocked (-2)
        cost[pair_pin_grid] = -2.0

    return {
        "status": "success" if not errors else "partial",
        "routed_pairs": len(result_paths),
        "total_pins_a": n_a,
        "total_pins_b": n_b,
        "paths": result_paths,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <config.json>", file=sys.stderr)
        sys.exit(1)

    config_path = sys.argv[1]
    with open(config_path) as f:
        config = json.load(f)

    result = route(config)

    output_path = config.get("output_path")
    if output_path:
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)
        print(f"Routes written to {output_path}")
    else:
        print(json.dumps(result, indent=2))

    if result["status"] == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
