#!/usr/bin/env python
"""Evaluate a routed GDS file for structural correctness.

Checks that routed paths exist on the output layer, route count matches
expected pin pairs, path endpoints are near pin markers, and bounding
box dimensions are reasonable.

Usage:
    python tests/evaluate_routing.py routed.gds [output.png] \
        --pin-a-layer 102/0 --pin-b-layer 111/0 \
        --output-layer 10/0 --obstacle-layers 1/0,3/0
"""

import sys
import json
import argparse
import gdstk
import numpy as np


def parse_layer(layer_str: str) -> tuple:
    """Parse a layer string like '102/0' into (layer, datatype) tuple."""
    parts = layer_str.strip().split("/")
    return (int(parts[0]), int(parts[1]))


def get_shapes_on_layer(cell, layer: int, datatype: int):
    """Return (polygons, paths) on a given layer/datatype from a cell."""
    polys = [p for p in cell.get_polygons() if p.layer == layer and p.datatype == datatype]
    paths = [p for p in cell.get_paths() if p.layers[0] == layer and p.datatypes[0] == datatype]
    return polys, paths


def get_pin_centers(cell, layer: int, datatype: int) -> np.ndarray:
    """Extract center coordinates of pin shapes on a given layer.

    Returns an Nx2 numpy array of (x, y) centers.
    """
    polys, paths = get_shapes_on_layer(cell, layer, datatype)
    centers = []
    for poly in polys:
        pts = poly.points
        cx = (pts[:, 0].min() + pts[:, 0].max()) / 2.0
        cy = (pts[:, 1].min() + pts[:, 1].max()) / 2.0
        centers.append([cx, cy])
    for path in paths:
        path_polys = path.to_polygons()
        for pp in path_polys:
            pts = pp.points if hasattr(pp, "points") else np.array(pp)
            cx = (pts[:, 0].min() + pts[:, 0].max()) / 2.0
            cy = (pts[:, 1].min() + pts[:, 1].max()) / 2.0
            centers.append([cx, cy])
    if not centers:
        return np.empty((0, 2))
    return np.array(centers)


def evaluate_routing(
    gds_path: str,
    pin_a_layer: tuple = (102, 0),
    pin_b_layer: tuple = (111, 0),
    output_layer: tuple = (10, 0),
    obstacle_layers: list = None,
    tolerance_um: float = 20.0,
) -> dict:
    """Evaluate whether a GDS file contains valid routing.

    Returns a dict with check results and an overall pass/fail.
    """
    lib = gdstk.read_gds(gds_path)
    results = {
        "file": gds_path,
        "checks": {},
        "pass": True,
    }

    # Check 1: File has cells
    cells = lib.cells
    results["checks"]["has_cells"] = len(cells) > 0
    if not cells:
        results["pass"] = False
        return results

    # Use the top cell (first cell, or the one with most geometry)
    top_cell = cells[0]

    # Check 2: Routed paths exist on output layer
    out_layer, out_dt = output_layer
    route_polys, route_paths = get_shapes_on_layer(top_cell, out_layer, out_dt)
    route_count = len(route_polys) + len(route_paths)
    results["checks"]["output_layer"] = f"{out_layer}/{out_dt}"
    results["checks"]["route_polygon_count"] = len(route_polys)
    results["checks"]["route_path_count"] = len(route_paths)
    results["checks"]["route_total_shapes"] = route_count
    results["checks"]["has_routes"] = route_count > 0
    if route_count == 0:
        results["pass"] = False

    # Check 3: Pin counts on pin_a and pin_b layers
    pa_layer, pa_dt = pin_a_layer
    pb_layer, pb_dt = pin_b_layer
    pin_a_centers = get_pin_centers(top_cell, pa_layer, pa_dt)
    pin_b_centers = get_pin_centers(top_cell, pb_layer, pb_dt)
    results["checks"]["pin_a_layer"] = f"{pa_layer}/{pa_dt}"
    results["checks"]["pin_b_layer"] = f"{pb_layer}/{pb_dt}"
    results["checks"]["pin_a_count"] = len(pin_a_centers)
    results["checks"]["pin_b_count"] = len(pin_b_centers)

    # Check 4: Route count matches expected pin pairs
    expected_pairs = min(len(pin_a_centers), len(pin_b_centers))
    results["checks"]["expected_pairs"] = expected_pairs
    results["checks"]["route_count_matches"] = route_count >= expected_pairs
    if expected_pairs > 0 and route_count < expected_pairs:
        results["pass"] = False

    # Check 5: Path endpoints are near pin markers (within tolerance)
    if route_count > 0 and (len(pin_a_centers) > 0 or len(pin_b_centers) > 0):
        all_pin_centers = np.vstack(
            [c for c in [pin_a_centers, pin_b_centers] if len(c) > 0]
        )

        # Collect all route shape endpoints
        route_endpoints = []
        for poly in route_polys:
            pts = poly.points
            # Use bounding box center as representative point
            cx = (pts[:, 0].min() + pts[:, 0].max()) / 2.0
            cy = (pts[:, 1].min() + pts[:, 1].max()) / 2.0
            route_endpoints.append([cx, cy])
        for path in route_paths:
            spine = path.spine()
            if len(spine) >= 2:
                route_endpoints.append(spine[0].tolist() if hasattr(spine[0], 'tolist') else list(spine[0]))
                route_endpoints.append(spine[-1].tolist() if hasattr(spine[-1], 'tolist') else list(spine[-1]))

        if route_endpoints:
            route_endpoints = np.array(route_endpoints)
            # For each endpoint, find distance to nearest pin
            near_pin_count = 0
            for ep in route_endpoints:
                dists = np.linalg.norm(all_pin_centers - ep, axis=1)
                if dists.min() <= tolerance_um:
                    near_pin_count += 1
            fraction_near = near_pin_count / len(route_endpoints)
            results["checks"]["endpoints_near_pins"] = near_pin_count
            results["checks"]["total_endpoints"] = len(route_endpoints)
            results["checks"]["endpoint_near_fraction"] = float(round(fraction_near, 3))
            # At least 50% of endpoints should be near pins
            results["checks"]["endpoints_ok"] = fraction_near >= 0.5
            if fraction_near < 0.5:
                results["pass"] = False
        else:
            results["checks"]["endpoints_ok"] = False
            results["pass"] = False
    else:
        results["checks"]["endpoints_ok"] = route_count == 0 and expected_pairs == 0

    # Check 6: Bounding box is reasonable (500-5000um in each dimension)
    all_points = []
    for cell in cells:
        for poly in cell.get_polygons():
            all_points.extend(poly.points.tolist())
        for path in cell.get_paths():
            path_polys = path.to_polygons()
            for pp in path_polys:
                pts = pp.points if hasattr(pp, "points") else pp
                all_points.extend(
                    pts.tolist() if hasattr(pts, "tolist") else list(pts)
                )
    if all_points:
        pts = np.array(all_points)
        width = float(pts[:, 0].max() - pts[:, 0].min())
        height = float(pts[:, 1].max() - pts[:, 1].min())
        results["checks"]["bbox_width_um"] = round(width, 2)
        results["checks"]["bbox_height_um"] = round(height, 2)
        results["checks"]["reasonable_dimensions"] = bool(
            500 <= width <= 5000 and 500 <= height <= 5000
        )
    else:
        results["checks"]["reasonable_dimensions"] = False
        results["pass"] = False

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate a routed GDS file for structural correctness."
    )
    parser.add_argument("gds_path", help="Path to routed GDS file")
    parser.add_argument("png_path", nargs="?", default=None, help="Optional output PNG path")
    parser.add_argument(
        "--pin-a-layer", default="102/0", help="Pin A layer (default: 102/0)"
    )
    parser.add_argument(
        "--pin-b-layer", default="111/0", help="Pin B layer (default: 111/0)"
    )
    parser.add_argument(
        "--output-layer", default="10/0", help="Output route layer (default: 10/0)"
    )
    parser.add_argument(
        "--obstacle-layers",
        default="1/0,3/0",
        help="Comma-separated obstacle layers (default: 1/0,3/0)",
    )
    parser.add_argument(
        "--tolerance", type=float, default=20.0, help="Pin proximity tolerance in um (default: 20)"
    )
    args = parser.parse_args()

    pin_a = parse_layer(args.pin_a_layer)
    pin_b = parse_layer(args.pin_b_layer)
    output = parse_layer(args.output_layer)
    obstacles = [parse_layer(s) for s in args.obstacle_layers.split(",")]

    results = evaluate_routing(
        gds_path=args.gds_path,
        pin_a_layer=pin_a,
        pin_b_layer=pin_b,
        output_layer=output,
        obstacle_layers=obstacles,
        tolerance_um=args.tolerance,
    )
    print(json.dumps(results, indent=2, default=str))

    # Generate PNG if requested
    if args.png_path:
        sys.path.insert(
            0, str(__import__("pathlib").Path(__file__).parent.parent / "tools")
        )
        from gds_to_image import gds_to_image

        gds_to_image(args.gds_path, args.png_path)
        print(f"\nPNG saved: {args.png_path}")

    # Exit code
    if results["pass"]:
        print("\nEVALUATION: PASS")
        sys.exit(0)
    else:
        print("\nEVALUATION: FAIL")
        sys.exit(1)


if __name__ == "__main__":
    main()
