# KlayoutClaw MCP Tool Reference (v0.5)

All tools are called via MCP `tools/call` method over HTTP POST to `http://127.0.0.1:8765/mcp`.

All coordinates are in **microns**. The database unit (dbu) defaults to 0.001.

**5 tools:** create_layout, execute_script, save_layout, get_layout_info, auto_route

---

## create_layout

Create a new layout with a top cell.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `name` | string | no | "TOP" | Name of the top cell |
| `dbu` | number | no | 0.001 | Database unit in microns |

**Returns:** `{"status": "ok", "top_cell": "TOP", "dbu": 0.001}`

---

## execute_script

Execute arbitrary Python/pya code in KLayout. The view is refreshed automatically after execution.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `code` | string | yes | Python code to execute |

**Namespace:** The code runs with these pre-injected names:
- `pya` — KLayout Python API
- `json`, `os` — standard library modules
- `_layout` — current `pya.Layout` (may be `None`)
- `_layout_view` — current `pya.LayoutView` (may be `None`)
- `_top_cell` — current top `pya.Cell` (may be `None`)
- `_get_or_create_view()` — ensures a view/layout/top_cell exist, returns `(_layout_view, _layout, _top_cell)`
- `_refresh_view()` — updates GUI layer panel + zoom

**Returning data:** Set the `result` variable to return data to the caller. It will be JSON-serialized. If `result` is not set, `{"status": "ok"}` is returned.

### Examples

**Add a rectangle:**
```python
dbu = _layout.dbu
li = _layout.layer(1, 0)
box = pya.Box(int(-50/dbu), int(-12.5/dbu), int(50/dbu), int(12.5/dbu))
_top_cell.shapes(li).insert(box)
```

**Create a cell and add instances:**
```python
sub = _layout.create_cell("SUB")
dbu = _layout.dbu
li = _layout.layer(1, 0)
sub.shapes(li).insert(pya.Box(0, 0, int(10/dbu), int(10/dbu)))
trans = pya.Trans(pya.Point(int(20/dbu), int(30/dbu)))
_top_cell.insert(pya.CellInstArray(sub.cell_index(), trans))
```

**Add a polygon:**
```python
dbu = _layout.dbu
li = _layout.layer(1, 0)
pts = [pya.Point(int(x/dbu), int(y/dbu)) for x, y in [(0,0), (10,0), (10,10), (0,10)]]
_top_cell.shapes(li).insert(pya.Polygon(pts))
```

**Add a path:**
```python
dbu = _layout.dbu
li = _layout.layer(1, 0)
pts = [pya.Point(int(x/dbu), int(y/dbu)) for x, y in [(0,0), (50,0), (50,50)]]
_top_cell.shapes(li).insert(pya.Path(pts, int(5/dbu)))
```

**Query cells and layers:**
```python
cells = []
for ci in range(_layout.cells()):
    c = _layout.cell(ci)
    if c is not None:
        cells.append(c.name)
result = {"cells": cells, "num_layers": len(list(_layout.layer_indices()))}
```

---

## save_layout

Save the current layout to a file.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `filepath` | string | yes | | Output file path |
| `format` | string | no | "GDS2" | File format: "GDS2" or "OASIS" |

**Returns:** `{"status": "ok", "filepath": "/path/to/output.gds", "format": "GDS2"}`

---

## get_layout_info

Get summary information about the current layout. No parameters.

**Returns:**
```json
{
  "status": "ok",
  "dbu": 0.001,
  "num_cells": 1,
  "cells": ["HALLBAR"],
  "num_layers": 3
}
```

---

## auto_route

Automatically route connections between pin pairs using cost-based pathfinding. Extracts pins from two layers, uses Hungarian matching for optimal pairing, then minimum-cost pathfinding (Dijkstra on a raster grid) to create routes avoiding obstacles.

Runs routing computation in a subprocess (`tools/route_worker.py`) using numpy, scipy, and scikit-image. Requires these packages in a conda environment (default: `instrMCPdev`).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `pin_layer_a` | string | yes | | Layer with start pins (e.g. "102/0") |
| `pin_layer_b` | string | yes | | Layer with end pins (e.g. "111/0") |
| `obstacle_layers` | string[] | no | [] | Layers to avoid (e.g. ["1/0", "3/0"]) |
| `output_layer` | string | no | "10/0" | Layer for routed paths |
| `path_width` | number | no | 10.0 | Path width in microns |
| `obs_safe_distance` | number | no | 5.0 | Min distance from obstacles (um) |
| `path_safe_distance` | number | no | 5.0 | Min distance between paths (um) |
| `map_resolution` | number | no | 2.0 | Grid resolution in microns |
| `conda_env` | string | no | "instrMCPdev" | Conda env with routing deps |
| `python_path` | string | no | | Path to python binary (overrides conda_env) |

**Returns:**
```json
{
  "status": "success",
  "routed_pairs": 8,
  "total_pins_a": 8,
  "total_pins_b": 8,
  "output_layer": "10/0",
  "path_width_um": 10.0,
  "errors": []
}
```

**Algorithm:**
1. Save current layout to temp GDS
2. Extract pin centers from shapes on pin_layer_a and pin_layer_b
3. Build obstacle region from obstacle_layers, expanded by obs_safe_distance
4. Rasterize obstacles into a 2D cost grid (resolution = map_resolution)
5. Hungarian matching (scipy) finds optimal pin pairings
6. MCP_Geometric (scikit-image) finds minimum-cost paths on the grid
7. Paths compressed (collinear points removed) and inserted as pya.Path objects

**Dependencies (subprocess):** numpy, scipy, scikit-image, klayout (standalone package)
