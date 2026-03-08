#!/usr/bin/env python
"""TEST 2: Evaluate a Hall bar GDS file for correctness.

Performs structural checks on the GDS file and optionally
generates a PNG for visual AI evaluation.

Usage:
    python tests/evaluate_gds.py test_hallbar.gds [output.png]
"""

import sys
import json
import gdstk


def evaluate_hallbar(gds_path: str) -> dict:
    """Evaluate whether a GDS file contains a valid Hall bar structure.

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

    # Check 2: Has multiple layers (mesa, metal, pads)
    layers_used = set()
    for cell in cells:
        for poly in cell.get_polygons():
            layers_used.add((poly.layer, poly.datatype))
        for path in cell.get_paths():
            layers_used.add((path.layers[0], path.datatypes[0]))

    results["checks"]["has_multiple_layers"] = len(layers_used) >= 2
    results["checks"]["num_layers"] = len(layers_used)
    results["checks"]["layers"] = [list(l) for l in sorted(layers_used)]

    if len(layers_used) < 2:
        results["pass"] = False

    # Check 3: Has geometry on expected layers
    has_mesa = any(l == 1 for l, d in layers_used)
    has_metal = any(l == 2 for l, d in layers_used)
    has_pads = any(l == 3 for l, d in layers_used)
    results["checks"]["has_mesa_layer"] = has_mesa
    results["checks"]["has_metal_layer"] = has_metal
    results["checks"]["has_pads_layer"] = has_pads

    if not (has_mesa and has_metal):
        results["pass"] = False

    # Check 4: Geometry count per layer
    for cell in cells:
        for key_layer in [1, 2, 3]:
            count = 0
            for poly in cell.get_polygons():
                if poly.layer == key_layer:
                    count += 1
            for path in cell.get_paths():
                if path.layers[0] == key_layer:
                    count += 1
            results["checks"][f"layer_{key_layer}_shapes"] = count

    # Check 5: Bounding box indicates reasonable dimensions
    all_points = []
    for cell in cells:
        for poly in cell.get_polygons():
            all_points.extend(poly.points.tolist())
    if all_points:
        import numpy as np
        pts = np.array(all_points)
        width = pts[:, 0].max() - pts[:, 0].min()
        height = pts[:, 1].max() - pts[:, 1].min()
        results["checks"]["bbox_width_um"] = float(width)
        results["checks"]["bbox_height_um"] = float(height)
        # Hall bar should span ~1000-3000um in each direction
        results["checks"]["reasonable_dimensions"] = bool(
            width > 1000 and height > 1000 and width < 3000 and height < 3000
        )
    else:
        results["checks"]["reasonable_dimensions"] = False
        results["pass"] = False

    return results


def main():
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} input.gds [output.png]")
        sys.exit(1)

    gds_path = sys.argv[1]
    png_path = sys.argv[2] if len(sys.argv) > 2 else None

    # Run structural evaluation
    results = evaluate_hallbar(gds_path)
    print(json.dumps(results, indent=2, default=str))

    # Generate PNG if requested
    if png_path:
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "tools"))
        from gds_to_image import gds_to_image
        gds_to_image(gds_path, png_path)
        print(f"\nPNG saved: {png_path}")

    # Exit code
    if results["pass"]:
        print("\nEVALUATION: PASS")
        sys.exit(0)
    else:
        print("\nEVALUATION: FAIL")
        sys.exit(1)


if __name__ == "__main__":
    main()
