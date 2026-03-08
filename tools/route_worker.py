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


def rasterize_region(region: kdb.Region, bbox: kdb.Box,
                     resolution_dbu: int) -> np.ndarray:
    """Rasterize a kdb.Region into a 2D boolean numpy array.

    Each grid cell is True if the region contains the cell center.
    Grid axes: row = y (bottom-to-top mapped to 0..nrows-1), col = x.
    """
    x_min, y_min = bbox.left, bbox.bottom
    x_max, y_max = bbox.right, bbox.top

    ncols = max(1, (x_max - x_min + resolution_dbu - 1) // resolution_dbu)
    nrows = max(1, (y_max - y_min + resolution_dbu - 1) // resolution_dbu)

    grid = np.zeros((nrows, ncols), dtype=bool)

    # Per-pixel contains check — acceptable for coarse grids
    for r in range(nrows):
        y = y_min + r * resolution_dbu + resolution_dbu // 2
        for c in range(ncols):
            x = x_min + c * resolution_dbu + resolution_dbu // 2
            if region.is_inside(kdb.Point(x, y)):
                grid[r, c] = True

    return grid


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


# ---------------------------------------------------------------------------
# Cost grid construction
# ---------------------------------------------------------------------------

def build_cost_grid(obstacle_grid: np.ndarray,
                    obs_damping_pixels: int) -> np.ndarray:
    """Build a float cost grid from boolean obstacle grid.

    Obstacle cells get infinite cost. Cells near obstacles get graduated
    damping cost that decays with distance.
    """
    nrows, ncols = obstacle_grid.shape
    cost = np.ones((nrows, ncols), dtype=np.float64)

    # Block obstacles
    cost[obstacle_grid] = np.inf

    # Add graduated damping near obstacles using distance transform
    if obs_damping_pixels > 0:
        from scipy.ndimage import distance_transform_edt
        # Distance from each free cell to nearest obstacle
        free = ~obstacle_grid
        dist = distance_transform_edt(free)
        # Damping: high cost near obstacles, decaying to 0
        damping_mask = (dist > 0) & (dist <= obs_damping_pixels)
        # Cost factor: 10 at obstacle edge, decaying linearly
        damping = np.where(damping_mask,
                           10.0 * (1.0 - dist / obs_damping_pixels),
                           0.0)
        cost += damping

    return cost


def add_path_damping(cost: np.ndarray, path_pixels: list[tuple[int, int]],
                     damping_pixels: int, path_width_pixels: int) -> None:
    """Add damping cost around a routed path to keep subsequent routes separated."""
    if damping_pixels <= 0 or not path_pixels:
        return

    nrows, ncols = cost.shape
    radius = damping_pixels + path_width_pixels

    for r, c in path_pixels:
        r_lo = max(0, r - radius)
        r_hi = min(nrows, r + radius + 1)
        c_lo = max(0, c - radius)
        c_hi = min(ncols, c + radius + 1)
        for rr in range(r_lo, r_hi):
            for cc in range(c_lo, c_hi):
                dist = math.sqrt((rr - r) ** 2 + (cc - c) ** 2)
                if dist <= radius and not np.isinf(cost[rr, cc]):
                    factor = 5.0 * (1.0 - dist / radius)
                    cost[rr, cc] += max(0.0, factor)


# ---------------------------------------------------------------------------
# Pathfinding
# ---------------------------------------------------------------------------

def find_path(cost: np.ndarray, start: tuple[int, int],
              end: tuple[int, int]) -> list[tuple[int, int]] | None:
    """Find minimum-cost path using MCP_Geometric (Dijkstra on grid).

    Returns list of (row, col) or None if no path found.
    """
    # Clamp to grid bounds
    nrows, ncols = cost.shape
    sr = max(0, min(start[0], nrows - 1))
    sc = max(0, min(start[1], ncols - 1))
    er = max(0, min(end[0], nrows - 1))
    ec = max(0, min(end[1], ncols - 1))

    # If start or end is blocked, clear it temporarily
    orig_start = cost[sr, sc]
    orig_end = cost[er, ec]
    if np.isinf(cost[sr, sc]):
        cost[sr, sc] = 1.0
    if np.isinf(cost[er, ec]):
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

    # Convert um to dbu
    resolution_dbu = int(round(map_res_um / dbu))
    obs_safe_dbu = int(round(obs_safe_um / dbu))
    path_width_dbu = int(round(path_width_um / dbu))

    # Damping distances in grid pixels
    obs_damping_px = int(round(obs_safe_um / map_res_um))
    path_damping_px = int(round(path_safe_um / map_res_um))
    path_width_px = max(1, int(round(path_width_um / map_res_um)))

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

    # Build obstacle region
    obs_region = build_obstacle_region(cell, layout, obstacle_layers, obs_safe_dbu)

    # Compute bounding box (cell bbox with margin)
    cell_bbox = cell.bbox()
    margin = max(obs_safe_dbu, resolution_dbu * 10)
    bbox = kdb.Box(
        cell_bbox.left - margin, cell_bbox.bottom - margin,
        cell_bbox.right + margin, cell_bbox.top + margin,
    )

    # Rasterize obstacles
    obs_grid = rasterize_region(obs_region, bbox, resolution_dbu)

    # Build cost grid
    cost = build_cost_grid(obs_grid, obs_damping_px)

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

    # Route each matched pair
    result_paths = []
    for idx in range(len(row_ind)):
        i, j = row_ind[idx], col_ind[idx]
        if i >= n_a or j >= n_b:
            continue  # dummy assignment from padding

        pa = pins_a[i]
        pb = pins_b[j]

        start_rc = dbu_to_grid(pa[0], pa[1], bbox, resolution_dbu)
        end_rc = dbu_to_grid(pb[0], pb[1], bbox, resolution_dbu)

        path_rc = find_path(cost, start_rc, end_rc)
        if path_rc is None:
            errors.append(f"No path found for pin pair {i}->{j}")
            continue

        # Convert grid path to dbu coordinates
        path_dbu = []
        for r, c in path_rc:
            x, y = grid_to_dbu(r, c, bbox, resolution_dbu)
            path_dbu.append([x, y])

        # Compress path
        path_dbu = compress_path(path_dbu)

        result_paths.append({
            "points_dbu": path_dbu,
            "pin_a": list(pa),
            "pin_b": list(pb),
        })

        # Add damping around this path for subsequent routes
        add_path_damping(cost, list(path_rc), path_damping_px, path_width_px)

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
