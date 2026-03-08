#!/usr/bin/env python
"""Convert a GDS file to a PNG image using gdstk and matplotlib.

Usage:
    python gds_to_image.py input.gds output.png
"""

import sys
import gdstk
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.collections import PatchCollection
import numpy as np


# Distinct colors for layers
LAYER_COLORS = [
    "#1f77b4",  # blue
    "#ff7f0e",  # orange
    "#2ca02c",  # green
    "#d62728",  # red
    "#9467bd",  # purple
    "#8c564b",  # brown
    "#e377c2",  # pink
    "#7f7f7f",  # gray
    "#bcbd22",  # olive
    "#17becf",  # cyan
]


def gds_to_image(gds_path: str, png_path: str, dpi: int = 200):
    """Read a GDS file and render all layers to a PNG image."""
    lib = gdstk.read_gds(gds_path)

    # Collect all polygons per (layer, datatype)
    layer_polys = {}
    for cell in lib.cells:
        # Get flattened polygons (resolves references)
        for poly in cell.get_polygons():
            key = (poly.layer, poly.datatype)
            if key not in layer_polys:
                layer_polys[key] = []
            layer_polys[key].append(poly.points)

        # Also get paths as polygons
        for path in cell.get_paths():
            key = (path.layers[0], path.datatypes[0])
            if key not in layer_polys:
                layer_polys[key] = []
            # Convert path to polygon
            polys = path.to_polygons()
            for p in polys:
                layer_polys[key].append(p.points if hasattr(p, 'points') else p)

    if not layer_polys:
        print("WARNING: No geometry found in the GDS file.")

    fig, ax = plt.subplots(1, 1, figsize=(10, 10))
    legend_handles = []

    sorted_keys = sorted(layer_polys.keys())
    for idx, key in enumerate(sorted_keys):
        color = LAYER_COLORS[idx % len(LAYER_COLORS)]
        alpha = 0.5
        patches = []
        for pts in layer_polys[key]:
            polygon = plt.Polygon(pts, closed=True)
            patches.append(polygon)

        pc = PatchCollection(patches, alpha=alpha, facecolor=color,
                             edgecolor=color, linewidth=0.5)
        ax.add_collection(pc)

        label = f"L{key[0]}/D{key[1]}"
        legend_handles.append(mpatches.Patch(color=color, alpha=alpha, label=label))

    ax.set_aspect("equal")
    ax.autoscale_view()
    ax.set_xlabel("X (um)")
    ax.set_ylabel("Y (um)")
    ax.set_title(f"GDS: {gds_path}")
    if legend_handles:
        ax.legend(handles=legend_handles, loc="upper right")
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(png_path, dpi=dpi, bbox_inches="tight")
    plt.close()
    print(f"Saved: {png_path}")


def main():
    if len(sys.argv) < 3:
        print(f"Usage: python {sys.argv[0]} input.gds output.png [dpi]")
        sys.exit(1)

    gds_path = sys.argv[1]
    png_path = sys.argv[2]
    dpi = int(sys.argv[3]) if len(sys.argv) > 3 else 200

    gds_to_image(gds_path, png_path, dpi)


if __name__ == "__main__":
    main()
