#!/usr/bin/env python
"""Add a polygon to a cell in KLayout via MCP.

Usage:
    python add_polygon.py <cell> <layer> <datatype> <x1,y1> <x2,y2> ...

Coordinates in microns. Points as comma-separated x,y pairs.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
from mcp_client import init_session, execute_script


def main():
    if len(sys.argv) < 5:
        print(f"Usage: python {sys.argv[0]} <cell> <layer> <datatype> <x1,y1> <x2,y2> ...")
        sys.exit(1)

    cell, layer, dt = sys.argv[1], sys.argv[2], sys.argv[3]
    points = []
    for p in sys.argv[4:]:
        x, y = p.split(",")
        points.append(f"[{x}, {y}]")
    pts_str = "[" + ", ".join(points) + "]"

    init_session()
    execute_script(f"""
dbu = _layout.dbu
c = _layout.cell("{cell}")
if c is None:
    raise ValueError("Cell '{cell}' not found")
li = _layout.layer({layer}, {dt})
pts = [pya.Point(int(p[0]/dbu), int(p[1]/dbu)) for p in {pts_str}]
c.shapes(li).insert(pya.Polygon(pts))
result = {{"status": "ok", "cell": "{cell}", "num_points": len(pts)}}
""")
    print(f"OK: polygon on {cell} L{layer}/D{dt} ({len(sys.argv) - 4} points)")


if __name__ == "__main__":
    main()
