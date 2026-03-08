---
name: klayoutclaw:geometry
description: Create geometry in KLayout via MCP — rectangles, polygons, paths, cells, and cell instances. Use this skill whenever the user wants to draw, add, or create shapes/geometry/structures in KLayout, even if they don't say "geometry" explicitly. Trigger on phrases like "draw a rectangle", "add a polygon", "create a cell", "place an instance", "make a shape", or any layout/chip/mask design geometry task.
---

# KLayout Geometry Skills

CLI scripts for creating geometry in KLayout via the MCP server's `execute_script` tool.

Each script connects to KLayout at `127.0.0.1:8765` and runs pya code to create shapes.

## Prerequisites

- KLayout running with KlayoutClaw plugin (v0.3+)
- A layout must be open (use `create_layout` tool first)

## Scripts

All scripts live in `scripts/` relative to this skill. Coordinates are in **microns**.

### add_rect.py — Add a rectangle

```bash
python scripts/add_rect.py <cell> <layer> <datatype> <x1> <y1> <x2> <y2>
```

Example: `python scripts/add_rect.py TOP 1 0 -50 -12.5 50 12.5`

### add_polygon.py — Add a polygon

```bash
python scripts/add_polygon.py <cell> <layer> <datatype> <x1,y1> <x2,y2> ...
```

Example: `python scripts/add_polygon.py TOP 1 0 0,0 10,0 10,10 0,10`

### add_path.py — Add a path with width

```bash
python scripts/add_path.py <cell> <layer> <datatype> <width> <x1,y1> <x2,y2> ...
```

Example: `python scripts/add_path.py TOP 1 0 5 0,0 50,0 50,50`

### create_cell.py — Create a new cell

```bash
python scripts/create_cell.py <cell_name>
```

Example: `python scripts/create_cell.py CONTACT_PAD`

### add_instance.py — Place a cell instance

```bash
python scripts/add_instance.py <parent> <child> [x] [y]
```

Example: `python scripts/add_instance.py TOP CONTACT_PAD 100 200`

## Workflow

For simple shapes, call scripts directly. For complex designs with many shapes, prefer calling `execute_script` via MCP with a single Python block — this avoids per-shape HTTP round trips and is much faster.

```python
# Example: batch geometry via execute_script
tool_call("execute_script", code="""
dbu = _layout.dbu
li = _layout.layer(1, 0)
for x in range(0, 100, 10):
    _top_cell.shapes(li).insert(pya.Box(int(x/dbu), 0, int((x+5)/dbu), int(5/dbu)))
""")
```
