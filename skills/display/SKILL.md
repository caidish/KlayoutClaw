---
name: klayoutclaw:display
description: Toggle KLayout layer visibility on and off for better visualization. Use this skill when the user wants to show/hide layers, toggle layer display, change layer visibility, or focus on specific layers in KLayout. Also trigger when the user says things like "hide the metal layer", "show only mesa", "turn off layer 3", or wants to isolate layers for inspection.
---

# KLayout Display Skills

Toggle layer visibility in KLayout via the MCP server for better visualization during design review.

## Prerequisites

- KLayout running with KlayoutClaw plugin (v0.3+)
- A layout with layers must be open

## Script

### toggle_layer.py — Toggle layer visibility

```bash
python scripts/toggle_layer.py <layer> <datatype> [on|off]
```

- `on` — make layer visible
- `off` — make layer invisible
- Omit to toggle current state

Examples:
```bash
python scripts/toggle_layer.py 1 0 off    # hide mesa layer
python scripts/toggle_layer.py 2 0 on     # show metal layer
python scripts/toggle_layer.py 3 0        # toggle bonding pads
```

### show_only.py — Show only specified layers, hide all others

```bash
python scripts/show_only.py <layer1/dt1> [<layer2/dt2> ...]
```

Example:
```bash
python scripts/show_only.py 1/0 2/0      # show only mesa + metal, hide pads
```

## How It Works

The scripts use `execute_script` to manipulate `pya.LayerProperties` in the current `LayoutView`. Each layer properties entry has a `visible` attribute that controls display.

For custom display manipulation beyond these scripts, use `execute_script` directly:

```python
tool_call("execute_script", code="""
lp_iter = _layout_view.begin_layers()
while not lp_iter.at_end():
    lp = lp_iter.current()
    lp.visible = (lp.source_layer == 1)  # show only layer 1
    _layout_view.set_layer_properties(lp_iter, lp)
    lp_iter.next()
""")
```
