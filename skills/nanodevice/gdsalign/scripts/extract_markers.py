#!/usr/bin/env python
"""Extract the 4 innermost L5/0 marker pairs from a GDS template.

Parses the GDS file, finds L5/0 polygons, selects the 8 closest to the
grid center (775, 775), groups into 4 pairs by proximity, and outputs
pair centroids with bounding boxes.

Usage:
    python extract_markers.py --gds Template.gds --output-dir output/gdsalign/
"""
import argparse
import json
import os
import sys

import gdstk
import numpy as np


def main():
    parser = argparse.ArgumentParser(description="Extract GDS marker pairs")
    parser.add_argument("--gds", required=True, help="Path to Template.gds")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    args = parser.parse_args()

    if not os.path.exists(args.gds):
        print(f"ERROR: GDS file not found: {args.gds}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)

    lib = gdstk.read_gds(args.gds)
    top_cells = lib.top_level()
    if not top_cells:
        print("ERROR: No top-level cell found", file=sys.stderr)
        sys.exit(1)
    flat = top_cells[0].flatten()

    markers = []
    for p in flat.polygons:
        if p.layer == 5 and p.datatype == 0:
            bb = p.bounding_box()
            cx = (bb[0][0] + bb[1][0]) / 2
            cy = (bb[0][1] + bb[1][1]) / 2
            markers.append({
                "center": [cx, cy],
                "bbox": [[float(bb[0][0]), float(bb[0][1])],
                         [float(bb[1][0]), float(bb[1][1])]],
            })

    if len(markers) < 8:
        print(f"ERROR: Found only {len(markers)} L5/0 markers, need 8",
              file=sys.stderr)
        sys.exit(1)

    all_centers = np.array([m["center"] for m in markers])
    grid_center = all_centers.mean(axis=0)

    dists = np.sqrt(((all_centers - grid_center) ** 2).sum(axis=1))
    inner_idx = np.argsort(dists)[:8]
    inner = [markers[i] for i in inner_idx]

    centers = np.array([m["center"] for m in inner])
    used = set()
    pairs = []
    for i in range(len(inner)):
        if i in used:
            continue
        dists_i = np.sqrt(((centers - centers[i]) ** 2).sum(axis=1))
        dists_i[i] = np.inf
        for j in used:
            dists_i[j] = np.inf
        j = int(np.argmin(dists_i))
        used.add(i)
        used.add(j)
        pair_center = (centers[i] + centers[j]) / 2
        pairs.append({
            "center_um": [float(pair_center[0]), float(pair_center[1])],
            "markers": [
                {"bbox": inner[i]["bbox"]},
                {"bbox": inner[j]["bbox"]},
            ],
        })

    for pair in pairs:
        cx, cy = pair["center_um"]
        if cx > grid_center[0] and cy > grid_center[1]:
            pair["label"] = "NE"
        elif cx < grid_center[0] and cy > grid_center[1]:
            pair["label"] = "NW"
        elif cx < grid_center[0] and cy < grid_center[1]:
            pair["label"] = "SW"
        else:
            pair["label"] = "SE"

    pairs.sort(key=lambda p: p["label"])

    report = {
        "status": "complete",
        "source_gds": os.path.basename(args.gds),
        "layer": "5/0",
        "grid_center_um": [float(grid_center[0]), float(grid_center[1])],
        "pairs": pairs,
    }

    out_path = os.path.join(args.output_dir, "gds_markers.json")
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"Wrote {out_path} with {len(pairs)} pairs")


if __name__ == "__main__":
    main()
