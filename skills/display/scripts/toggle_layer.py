#!/usr/bin/env python
"""Toggle layer visibility in KLayout via MCP.

Usage:
    python toggle_layer.py <layer> <datatype> [on|off]

Omit on/off to toggle current state.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
from mcp_client import init_session, execute_script


def main():
    if len(sys.argv) < 3:
        print(f"Usage: python {sys.argv[0]} <layer> <datatype> [on|off]")
        sys.exit(1)

    layer = sys.argv[1]
    dt = sys.argv[2]
    mode = sys.argv[3] if len(sys.argv) > 3 else "toggle"

    if mode not in ("on", "off", "toggle"):
        print(f"ERROR: mode must be 'on', 'off', or omitted (toggle)")
        sys.exit(1)

    init_session()
    result = execute_script(f"""
target_layer = {layer}
target_dt = {dt}
mode = "{mode}"
found = False
lp_iter = _layout_view.begin_layers()
while not lp_iter.at_end():
    lp = lp_iter.current()
    if lp.source_layer == target_layer and lp.source_datatype == target_dt:
        found = True
        if mode == "on":
            lp.visible = True
        elif mode == "off":
            lp.visible = False
        else:
            lp.visible = not lp.visible
        _layout_view.set_layer_properties(lp_iter, lp)
        new_state = "visible" if lp.visible else "hidden"
        break
    lp_iter.next()
if not found:
    raise ValueError("Layer {layer}/{dt} not found in view")
result = {{"status": "ok", "layer": target_layer, "datatype": target_dt, "visible": lp.visible}}
""")
    print(f"OK: layer {layer}/{dt} toggled")


if __name__ == "__main__":
    main()
