# KlayoutClaw Skills Reference

Skills are Claude Code plugins that wrap KlayoutClaw MCP tools into task-oriented CLIs. They are distributed as a plugin in the [cAI-tools](https://github.com/caidish/my-agent-prompt) marketplace at `plugins/klayoutclaw/`.

All scripts share a common MCP client (`scripts/mcp_client.py`) that connects to KLayout at `127.0.0.1:8765`.

---

## Geometry

Create layout geometry via the `execute_script` MCP tool. Each script is a standalone CLI.

### add_rect.py

Add a rectangle to a cell.

```bash
python skills/geometry/scripts/add_rect.py <cell> <layer> <datatype> <x1> <y1> <x2> <y2>
```

| Arg | Description |
|-----|-------------|
| `cell` | Target cell name |
| `layer` | Layer number |
| `datatype` | Datatype number |
| `x1, y1` | Bottom-left corner (microns) |
| `x2, y2` | Top-right corner (microns) |

```bash
# 100x25um channel centered at origin on layer 1/0
python add_rect.py HALLBAR 1 0 -50 -12.5 50 12.5
```

### add_polygon.py

Add a polygon to a cell. Points are comma-separated `x,y` pairs.

```bash
python skills/geometry/scripts/add_polygon.py <cell> <layer> <datatype> <x1,y1> <x2,y2> ...
```

```bash
# Triangle on layer 1/0
python add_polygon.py TOP 1 0 0,0 10,0 5,10
```

### add_path.py

Add a path (wire) with a specified width.

```bash
python skills/geometry/scripts/add_path.py <cell> <layer> <datatype> <width> <x1,y1> <x2,y2> ...
```

```bash
# L-shaped path, 5um wide
python add_path.py TOP 1 0 5 0,0 50,0 50,50
```

### create_cell.py

Create a new empty cell in the current layout.

```bash
python skills/geometry/scripts/create_cell.py <cell_name>
```

```bash
python create_cell.py CONTACT_PAD
```

### add_instance.py

Place an instance of a child cell into a parent cell at position (x, y).

```bash
python skills/geometry/scripts/add_instance.py <parent> <child> [x] [y]
```

| Arg | Description |
|-----|-------------|
| `parent` | Parent cell name |
| `child` | Child cell name |
| `x` | X offset in microns (default: 0) |
| `y` | Y offset in microns (default: 0) |

```bash
python add_instance.py TOP CONTACT_PAD 100 200
```

### When to use scripts vs. execute_script

Use individual scripts for quick one-off edits. For complex designs with many shapes, call `execute_script` directly with a single Python block — this avoids per-shape HTTP round trips:

```python
tool_call("execute_script", code="""
dbu = _layout.dbu
li = _layout.layer(1, 0)
for x in range(0, 100, 10):
    _top_cell.shapes(li).insert(pya.Box(int(x/dbu), 0, int((x+5)/dbu), int(5/dbu)))
""")
```

---

## Display

Toggle layer visibility in KLayout for better visualization during design review.

### toggle_layer.py

Toggle a single layer's visibility.

```bash
python skills/display/scripts/toggle_layer.py <layer> <datatype> [on|off]
```

| Arg | Description |
|-----|-------------|
| `layer` | Layer number |
| `datatype` | Datatype number |
| `on/off` | Set visibility explicitly. Omit to toggle. |

```bash
python toggle_layer.py 1 0 off   # hide mesa layer
python toggle_layer.py 2 0 on    # show metal layer
python toggle_layer.py 3 0       # toggle bonding pads
```

### show_only.py

Show only the specified layers, hide everything else.

```bash
python skills/display/scripts/show_only.py <layer1/dt1> [<layer2/dt2> ...]
```

```bash
# Show mesa + metal, hide everything else
python show_only.py 1/0 2/0
```

### Advanced: custom display via execute_script

For fine-grained control (transparency, color, fill), use `execute_script`:

```python
tool_call("execute_script", code="""
lp_iter = _layout_view.begin_layers()
while not lp_iter.at_end():
    lp = lp_iter.current()
    if lp.source_layer == 1:
        lp.visible = True
        lp.transparent = True  # make layer semi-transparent
    else:
        lp.visible = False
    _layout_view.set_layer_properties(lp_iter, lp)
    lp_iter.next()
""")
```

---

## Visual

Capture the current layout as a PNG image for visual inspection.

### capture.py

Save the layout to a temp GDS file, convert to PNG, and print the paths.

```bash
python skills/visual/scripts/capture.py [--output path.png] [--gds path.gds] [--dpi 200]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--output` | `/tmp/klayoutclaw_capture.png` | PNG output path |
| `--gds` | `/tmp/klayoutclaw_capture.gds` | Temp GDS output path |
| `--dpi` | `200` | Image resolution |

```bash
# Default paths
python capture.py
# GDS saved: /tmp/klayoutclaw_capture.gds
# PNG saved: /tmp/klayoutclaw_capture.png

# Custom output
python capture.py --output ~/Desktop/my_layout.png --dpi 300
```

### How it works

1. Calls `save_layout` via MCP to write the current layout to a GDS file
2. Runs `tools/gds_to_image.py` to convert GDS to PNG using `gdstk` + `matplotlib`
3. Prints both file paths to stdout

### Dependencies

- `gdstk` — GDS file parsing
- `matplotlib` — rendering layers with colors and legend

Install: `conda install gdstk matplotlib` (or `pip install gdstk matplotlib`)

---

## Tests

### test_visual.py

End-to-end test for the visual capture workflow.

```bash
python plugins/klayoutclaw/tests/test_visual.py
```

Tests:
1. `capture.py` with default paths — verifies GDS and PNG files are created with valid sizes
2. `capture.py` with custom paths — verifies `--output`, `--gds`, `--dpi` flags work
3. PNG validity — verifies the output has correct PNG magic bytes

Requires KLayout running with KlayoutClaw plugin.
