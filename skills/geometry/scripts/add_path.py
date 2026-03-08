#!/usr/bin/env python
"""Add a path to a cell in KLayout via MCP.

Usage:
    python add_path.py <cell> <layer> <datatype> <width> <x1,y1> <x2,y2> ...

Coordinates and width in microns. Points as comma-separated x,y pairs.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
from mcp_client import init_session, execute_script


def main():
    if len(sys.argv) < 6:
        print(f"Usage: python {sys.argv[0]} <cell> <layer> <datatype> <width> <x1,y1> <x2,y2> ...")
        sys.exit(1)

    cell, layer, dt, width = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    points = []
    for p in sys.argv[5:]:
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
c.shapes(li).insert(pya.Path(pts, int({width}/dbu)))
result = {{"status": "ok", "cell": "{cell}", "width": {width}, "num_points": len(pts)}}
""")
    print(f"OK: path on {cell} L{layer}/D{dt} width={width}um ({len(sys.argv) - 5} points)")


if __name__ == "__main__":
    main()
