#!/usr/bin/env python
"""Clear all shapes from specified layers.

Usage:
    python clear_routes.py 3/0 4/0 5/0
    python clear_routes.py all    # clears routing layers 3/0 4/0 5/0 100-103/0
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "scripts"))
from mcp_client import init_session, execute_script


def main():
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <layer/dt> [layer/dt ...] | all")
        sys.exit(1)

    if sys.argv[1] == "all":
        layers = [(3, 0), (4, 0), (5, 0), (100, 0), (101, 0), (102, 0), (103, 0)]
    else:
        layers = []
        for arg in sys.argv[1:]:
            parts = arg.split("/")
            l = int(parts[0])
            d = int(parts[1]) if len(parts) > 1 else 0
            layers.append((l, d))

    layer_list = str(layers)

    init_session()
    result = execute_script(f"""
layers = {layer_list}
cell = _top_cell
cleared = 0
for (l, d) in layers:
    li = _layout.layer(l, d)
    n = cell.shapes(li).size()
    if n > 0:
        cell.shapes(li).clear()
        cleared += n
_refresh_view()
result = {{"status": "ok", "layers_cleared": len(layers), "shapes_removed": cleared}}
""")

    n_shapes = result.get("shapes_removed", 0)
    layer_strs = [f"{l}/{d}" for l, d in layers]
    print(f"OK: cleared {n_shapes} shapes from layers {', '.join(layer_strs)}")


if __name__ == "__main__":
    main()
