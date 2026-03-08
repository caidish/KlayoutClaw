#!/usr/bin/env python
"""Create a new cell in KLayout via MCP.

Usage:
    python create_cell.py <cell_name>
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
from mcp_client import init_session, execute_script


def main():
    if len(sys.argv) != 2:
        print(f"Usage: python {sys.argv[0]} <cell_name>")
        sys.exit(1)

    name = sys.argv[1]

    init_session()
    result = execute_script(f"""
cell = _layout.create_cell("{name}")
result = {{"status": "ok", "name": cell.name, "cell_index": cell.cell_index()}}
""")
    print(f"OK: created cell '{name}'")


if __name__ == "__main__":
    main()
