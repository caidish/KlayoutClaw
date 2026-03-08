#!/usr/bin/env python
"""Load a reference image into KLayout as a background overlay.

Usage:
    python add_image.py <filepath> [--pixel-size 0.1] [--x 0] [--y 0] [--center]
    python add_image.py <filepath> --scale-bar <um> <pixels>

Set pixel size directly or derive it from a scale bar.
Coordinates in microns.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
from mcp_client import init_session, execute_script


def main():
    parser = argparse.ArgumentParser(description="Load image into KLayout as background overlay")
    parser.add_argument("filepath", help="Path to image file (JPG, PNG, BMP)")
    parser.add_argument("--pixel-size", type=float, default=None,
                        help="Microns per pixel (default: 1.0)")
    parser.add_argument("--scale-bar", nargs=2, metavar=("UM", "PIXELS"),
                        help="Derive pixel size from scale bar: <length_um> <length_pixels>")
    parser.add_argument("--x", type=float, default=0.0,
                        help="X position offset in microns (default: 0)")
    parser.add_argument("--y", type=float, default=0.0,
                        help="Y position offset in microns (default: 0)")
    parser.add_argument("--center", action="store_true",
                        help="Center image at the given position")
    args = parser.parse_args()

    filepath = os.path.abspath(os.path.expanduser(args.filepath))
    if not os.path.exists(filepath):
        print(f"ERROR: File not found: {filepath}", file=sys.stderr)
        sys.exit(1)

    # Determine pixel size: --scale-bar takes priority, then --pixel-size, then default 1.0
    if args.scale_bar:
        bar_um = float(args.scale_bar[0])
        bar_px = float(args.scale_bar[1])
        if bar_px <= 0:
            print("ERROR: scale bar pixels must be > 0", file=sys.stderr)
            sys.exit(1)
        ps = bar_um / bar_px
        print(f"Scale bar: {bar_um} um / {bar_px} px = {ps:.4f} um/px")
    elif args.pixel_size is not None:
        ps = args.pixel_size
    else:
        ps = 1.0
    x, y = args.x, args.y
    center = args.center

    init_session()
    result = execute_script(f"""
import os

filepath = "{filepath}"
if not os.path.exists(filepath):
    raise FileNotFoundError(f"Image not found: {{filepath}}")

view, layout, cell = _get_or_create_view()

img = pya.Image(filepath)
img.visible = True

# Pixel size sets the scale: magnification = um per pixel
ps = {ps}
x_off = {x}
y_off = {y}

if {center}:
    # Shift so image center is at (x, y)
    w_um = img.width() * ps
    h_um = img.height() * ps
    x_off = {x} - w_um / 2.0
    y_off = {y} - h_um / 2.0

img.trans = pya.DCplxTrans(ps, 0, False, pya.DVector(x_off, y_off))

view.insert_image(img)
view.zoom_fit()

img_id = img.id()
w_px = img.width()
h_px = img.height()
w_um = w_px * ps
h_um = h_px * ps
result = {{
    "status": "ok",
    "id": img_id,
    "file": filepath,
    "pixels": [w_px, h_px],
    "size_um": [w_um, h_um],
    "pixel_size": ps,
    "position": [x_off, y_off],
}}
""")

    img_id = result.get("id", "?")
    pixels = result.get("pixels", [0, 0])
    size_um = result.get("size_um", [0, 0])
    pos = result.get("position", [0, 0])

    print(f"OK: image loaded (id={img_id})")
    print(f"  File: {filepath}")
    print(f"  Pixels: {pixels[0]} x {pixels[1]}")
    print(f"  Size: {size_um[0]:.1f} x {size_um[1]:.1f} um")
    print(f"  Pixel size: {ps} um/px")
    print(f"  Position: ({pos[0]:.1f}, {pos[1]:.1f}) um")


if __name__ == "__main__":
    main()
