#!/usr/bin/env python
"""Add a rectangle to a cell in KLayout via MCP.

Usage:
    python add_rect.py <cell> <layer> <datatype> <x1> <y1> <x2> <y2>

Coordinates in microns.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
from mcp_client import init_session, execute_script


def main():
    if len(sys.argv) != 8:
        print(f"Usage: python {sys.argv[0]} <cell> <layer> <datatype> <x1> <y1> <x2> <y2>")
        sys.exit(1)

    cell, layer, dt = sys.argv[1], sys.argv[2], sys.argv[3]
    x1, y1, x2, y2 = sys.argv[4], sys.argv[5], sys.argv[6], sys.argv[7]

    init_session()
    result = execute_script(f"""
dbu = _layout.dbu
c = _layout.cell("{cell}")
if c is None:
    raise ValueError("Cell '{cell}' not found")
li = _layout.layer({layer}, {dt})
c.shapes(li).insert(pya.Box(int({x1}/dbu), int({y1}/dbu), int({x2}/dbu), int({y2}/dbu)))
result = {{"status": "ok", "cell": "{cell}", "layer": {layer}, "datatype": {dt}, "bbox": [{x1}, {y1}, {x2}, {y2}]}}
""")
    print(f"OK: rectangle on {cell} L{layer}/D{dt} [{x1},{y1}]-[{x2},{y2}]")


if __name__ == "__main__":
    main()
