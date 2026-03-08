#!/usr/bin/env python
"""Show only specified layers, hide all others in KLayout via MCP.

Usage:
    python show_only.py <layer1/dt1> [<layer2/dt2> ...]

Example:
    python show_only.py 1/0 2/0
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
from mcp_client import init_session, execute_script


def main():
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <layer1/dt1> [<layer2/dt2> ...]")
        sys.exit(1)

    # Parse layer/datatype pairs
    show_layers = []
    for arg in sys.argv[1:]:
        parts = arg.split("/")
        if len(parts) != 2:
            print(f"ERROR: '{arg}' must be in layer/datatype format (e.g. 1/0)")
            sys.exit(1)
        show_layers.append((int(parts[0]), int(parts[1])))

    show_set_str = str(show_layers)

    init_session()
    execute_script(f"""
show_set = set({show_set_str})
changed = []
lp_iter = _layout_view.begin_layers()
while not lp_iter.at_end():
    lp = lp_iter.current()
    key = (lp.source_layer, lp.source_datatype)
    should_show = key in show_set
    if lp.visible != should_show:
        lp.visible = should_show
        _layout_view.set_layer_properties(lp_iter, lp)
        changed.append(key)
    lp_iter.next()
result = {{"status": "ok", "showing": list(show_set), "changed": changed}}
""")
    layer_strs = [f"{l}/{d}" for l, d in show_layers]
    print(f"OK: showing only layers {', '.join(layer_strs)}")


if __name__ == "__main__":
    main()
