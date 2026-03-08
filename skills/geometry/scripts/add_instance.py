#!/usr/bin/env python
"""Place a cell instance in KLayout via MCP.

Usage:
    python add_instance.py <parent> <child> [x] [y]

x, y in microns (default 0).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
from mcp_client import init_session, execute_script


def main():
    if len(sys.argv) < 3:
        print(f"Usage: python {sys.argv[0]} <parent> <child> [x] [y]")
        sys.exit(1)

    parent = sys.argv[1]
    child = sys.argv[2]
    x = sys.argv[3] if len(sys.argv) > 3 else "0"
    y = sys.argv[4] if len(sys.argv) > 4 else "0"

    init_session()
    execute_script(f"""
dbu = _layout.dbu
parent_cell = _layout.cell("{parent}")
child_cell = _layout.cell("{child}")
if parent_cell is None:
    raise ValueError("Parent cell '{parent}' not found")
if child_cell is None:
    raise ValueError("Child cell '{child}' not found")
trans = pya.Trans(pya.Point(int({x}/dbu), int({y}/dbu)))
parent_cell.insert(pya.CellInstArray(child_cell.cell_index(), trans))
result = {{"status": "ok", "parent": "{parent}", "child": "{child}", "x": {x}, "y": {y}}}
""")
    print(f"OK: placed '{child}' in '{parent}' at ({x}, {y})")


if __name__ == "__main__":
    main()
