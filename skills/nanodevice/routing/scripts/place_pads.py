#!/usr/bin/env python
"""Place bonding pads around an EBL field perimeter.

Usage:
    python place_pads.py --field 2000 --pad-size 80 --pads-per-edge 12

Also places pin markers at pad centers on layer 101/0 for routing.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "scripts"))
from mcp_client import init_session, execute_script


def parse_layer(s):
    parts = s.split("/")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0


def main():
    parser = argparse.ArgumentParser(description="Place bonding pads around EBL field perimeter")
    parser.add_argument("--field", type=float, default=2000.0,
                        help="EBL write field size in um (default: 2000)")
    parser.add_argument("--pad-size", type=float, default=80.0,
                        help="Bonding pad side length in um (default: 80)")
    parser.add_argument("--pads-per-edge", type=int, default=12,
                        help="Number of pads per edge (default: 12)")
    parser.add_argument("--layer", type=str, default="2/0",
                        help="Output layer as layer/datatype (default: 2/0)")
    parser.add_argument("--margin", type=float, default=60.0,
                        help="Pad center inset from field edge in um (default: 60)")
    parser.add_argument("--pin-layer", type=str, default="101/0",
                        help="Layer for pad pin markers (default: 101/0)")
    parser.add_argument("--pin-size", type=float, default=3.0,
                        help="Pin marker size in um (default: 3.0)")
    args = parser.parse_args()

    layer, dt = parse_layer(args.layer)
    pin_layer, pin_dt = parse_layer(args.pin_layer)
    half = args.field / 2.0
    n = args.pads_per_edge
    pad = args.pad_size
    margin = args.margin
    pitch = args.field / (n + 1)
    pin_s = args.pin_size

    init_session()
    result = execute_script(f"""
import json

dbu = _layout.dbu
cell = _top_cell
l_pad = _layout.layer({layer}, {dt})
l_pin = _layout.layer({pin_layer}, {pin_dt})

def um(v):
    return int(round(v / dbu))

def add_box(layer, x1, y1, x2, y2):
    cell.shapes(layer).insert(pya.Box(um(x1), um(y1), um(x2), um(y2)))

half = {half}
n = {n}
pitch = {pitch}
pad = {pad}
margin = {margin}
pin_s = {pin_s}

pad_positions = []

# Top edge
for i in range(n):
    x = -half + pitch * (i + 1)
    y = half - margin
    pad_positions.append((x, y))
# Right edge
for i in range(n):
    x = half - margin
    y = half - pitch * (i + 1)
    pad_positions.append((x, y))
# Bottom edge
for i in range(n):
    x = half - pitch * (i + 1)
    y = -half + margin
    pad_positions.append((x, y))
# Left edge
for i in range(n):
    x = -half + margin
    y = -half + pitch * (i + 1)
    pad_positions.append((x, y))

for (x, y) in pad_positions:
    add_box(l_pad, x - pad/2, y - pad/2, x + pad/2, y + pad/2)
    add_box(l_pin, x - pin_s/2, y - pin_s/2, x + pin_s/2, y + pin_s/2)

_refresh_view()
result = {{"status": "ok", "pads": len(pad_positions), "pitch": round(pitch, 1), "gap": round(pitch - pad, 1)}}
""")

    total = n * 4
    gap = pitch - pad
    print(f"OK: {total} bonding pads ({pad:.0f}x{pad:.0f} um, pitch {pitch:.1f} um, gap {gap:.1f} um)")
    print(f"  Field: {args.field:.0f} x {args.field:.0f} um")
    print(f"  Pad layer: {layer}/{dt}")
    print(f"  Pin layer: {pin_layer}/{pin_dt}")


if __name__ == "__main__":
    main()
