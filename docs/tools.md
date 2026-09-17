# KlayoutClaw MCP Tool Reference (v0.6)

All tools are called via MCP `tools/call` over HTTP POST. The default endpoint
is `http://127.0.0.1:8765/mcp`. Configure the server's bind address, port,
endpoint path, and optional TLS certificate in
`~/.klayout/klayoutclaw.json`; restart KLayout after changing the file.
Environment variables such as `KLAYOUT_MCP_PORT` override file values.
External clients use the matching complete `KLAYOUT_MCP_URL`.

All coordinates are in **microns**. The database unit (dbu) defaults to 0.001.

**10 tools:** create_layout, execute_script, save_layout, get_layout_info, screenshot, auto_route, route_inspect, evaluate_design, validate_pixel_size, close_layout_view

---

## create_layout

Create a new empty layout in a **new KLayout tab** and switch focus to it.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `name` | string | no | "TOP" | Name of the top cell |
| `dbu` | number | no | 0.001 | Database unit in microns |

**Returns:** `{"status": "ok", "top_cell": "TOP", "dbu": 0.001}`

**Behavior:**
- Each call adds a new tab (via `pya.MainWindow.create_layout(1)`). Existing tabs, their cells, polygons, and image annotations are left untouched.
- `current_view()` automatically switches to the new tab, so subsequent MCP calls (`execute_script`, `get_layout_info`, etc.) operate on the fresh layout.
- If you want to *replace* the current tab's contents instead of adding a new one, do that via `execute_script` (`layout.clear()` + `layout.read(...)` or similar) — this tool deliberately doesn't clobber existing work.

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

### Import availability

`execute_script` runs inside KLayout's bundled `pya` Python interpreter, **not** the `instrMCPdev` conda env. The interpreter is intentionally minimal.

- **Available:** `pya`, `numpy`, the Python standard library.
- **Not available:** `cv2`, `sklearn`, `scipy`, `gdstk`, `shapely`, and other CV / ML / heavy-geometry packages. `import` of these will raise `ModuleNotFoundError`.

For work that needs the unavailable packages, do not try to install them into KLayout's interpreter. Instead, run the heavy code out-of-process and let the result come back as data:

- **Skill scripts** under `skills/.../scripts/` — invoked from bash, run in `instrMCPdev`, talk to KLayout over MCP. This is the canonical pattern for CV / segmentation / alignment workflows.
- **Dedicated MCP tools** that already wrap a subprocess: `auto_route` (numpy/scikit-image/klayout), `evaluate_design` (gdstk/shapely/numpy).
- **One-off scripts:** `conda run -n instrMCPdev python -m your_script`. If your subprocess needs the current edited geometry, call `save_layout` first so it can read the GDS from disk; then a follow-up `execute_script` (or `add_*` skill) can inject any returned shapes back into the layout.

See also `docs/skills.md` for the full skill-script catalogue.

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

**Returns:** a dict with explicit unit labels so agents never confuse dbu vs µm.

```json
{
  "status": "ok",
  "dbu": 0.001,
  "dbu_um": 0.001,
  "units": "All *_um fields are micrometers; *_dbu fields are database units. dbu_um converts dbu->um.",
  "num_cells": 1,
  "cells": ["HALLBAR"],
  "cell_bboxes": {
    "HALLBAR": {
      "bbox_dbu": [0, 0, 50000, 50000],
      "bbox_um":  [0.0, 0.0, 50.0, 50.0],
      "bbox_units": "dbu and um (see bbox_dbu and bbox_um)"
    }
  },
  "num_layers": 3,
  "layers": {
    "1/0": {
      "shapes": 2,
      "area_um2": 1200.0,
      "bbox_um":  [0.0, 0.0, 50.0, 50.0],
      "bbox_dbu": [0, 0, 50000, 50000],
      "bbox_units": "bbox_um is micrometers, bbox_dbu is database units"
    }
  }
}
```

**Notes:**
- `cells` is enumerated via `layout.each_cell()` (not index-based iteration) so it is stable across `layout.read()` merges.
- `cell_bboxes` is emitted best-effort: cells with empty bboxes are omitted from the dict.
- Use `dbu_um` to convert between dbu and micrometers (they happen to share the same numeric value — the alias makes the unit explicit).

---

## screenshot

Capture the current KLayout viewport as a PNG image. Returns exactly what the user sees, including layer colors, zoom level, and visibility settings.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `filepath` | string | no | `/tmp/klayoutclaw_screenshot.png` | Output PNG file path |
| `width` | integer | no | 1024 | Image width in pixels |
| `height` | integer | no | 768 | Image height in pixels |
| `zoom_box` | number[4] | no | (none) | Optional viewport box `[x1, y1, x2, y2]`. **UNIT: um** (micrometers, layout coordinates — NOT dbu). If provided, calls `layout_view.zoom_box(pya.DBox(x1,y1,x2,y2))` before saving the screenshot. |

**Returns:** `{"status": "ok", "filepath": "/tmp/klayoutclaw_screenshot.png", "width": 1024, "height": 768}`

**Notes:**
- Uses `pya.LayoutView.save_image()` — captures the actual viewport, not a re-render
- Preserves current zoom, pan, layer visibility, and color settings
- If `zoom_box` is omitted, captures the current viewport as-is
- No external dependencies required

---

## auto_route

Automatically route connections between pin pairs using an ordered assignment pass followed by cost-based pathfinding. Extracts pins from two layers, computes an ordered-loop pairing by default so cyclic pin order is preserved before routing, then runs a sequential minimum-cost path search on a raster grid while treating caller-declared geometry and completed routes as obstacles.

Runs routing computation in a subprocess (`tools/route_worker.py`) using numpy, scikit-image, and the standalone KLayout Python package. Requires these packages in a conda environment (default: `instrMCPdev`).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `pin_layer_a` | string | yes | | Layer with start pins (e.g. "102/0") |
| `pin_layer_b` | string | yes | | Layer with end pins (e.g. "111/0") |
| `obstacle_layers` | string[] | no | [] | Global obstacle layers (applies to every pair). Example: ["1/0", "3/0"]. |
| `output_layer` | string | no | "10/0" | Layer for routed paths |
| `path_width` | number | no | 10.0 | Path width in microns |
| `obs_safe_distance` | number | no | 5.0 | Min distance from obstacles (um) |
| `path_safe_distance` | number | no | 5.0 | Min distance between paths (um) |
| `map_resolution` | number | no | 2.0 | Grid resolution in microns. Ignored if `auto_map_resolution=true`. |
| `auto_map_resolution` | bool | no | false | Override `map_resolution` by deriving from smallest pin bbox edge (target: edge_um / 3, clamped [0.2, 5.0]). Use on layouts with small contacts (~3×2 um). |
| `dry_run` | bool | no | false | Preview ordered-loop assignment without committing routes. Returns `status="dry_run"` and a `pairs[]` array of assignments + straight-line distances. |
| `per_pair_obstacle_layers` | string[][] | no | | Per-pair extra obstacle layers, unioned with `obstacle_layers` for that pair only. Length MUST equal `len(pairs)` after the assignment pre-pass — call with `dry_run=true` first to see pair order. |
| `pin_pairs_override` | int[][] | no | | Explicit `[a_idx, b_idx]` pairs overriding the default ordered-loop assignment. Length MUST equal `min(n_pin_a, n_pin_b)`. Run with `dry_run=true` first to inspect the shape-iteration order. |
| `conda_env` | string | no | "instrMCPdev" | Conda env with routing deps |
| `python_path` | string | no | | Path to python binary (overrides conda_env) |
| `timeout` | number | no | 120 | Subprocess timeout in seconds. Clamped to `[10, 600]` by the handler. |
| `obs_damping_step` | number | no | 4 | Obstacle cost damping step for graduated damping pathfinder. Lower = stronger avoidance. |

**Advanced tuning parameters** (passed through to `route_worker.py`; some are exposed by the MCP schema, while worker-only entries are available when invoking the worker/config directly):

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `obs_hardness` | number | 20.0 | Max cost at obstacle boundary (higher = paths stay further) |
| `pin_safe_distance_a_um` | number | 5.0 | Avoidance halo radius around Pin A shapes (um) |
| `pin_safe_distance_b_um` | number | 5.0 | Avoidance halo radius around Pin B shapes (um) |
| `pin_hardness` | number | 20.0 | Max cost at pin halo boundary |
| `pin_damping_step` | int | 4 | Gradient steps for pin avoidance fields |
| `path_hardness` | number | 10.0 | Max cost near previously routed paths |
| `path_damping_step` | int | 5 | Gradient steps for path avoidance fields |
| `sort_pairs` | bool | true | Route pairs/nets by distance after assignment; default ordering is shortest first unless `sort_pairs_reverse` is set in the worker config. |
| `freeze_completed_routes_as_obstacles_with_margin` | number | 2.5 | Worker-level hard margin around completed routes before subsequent pairs are routed. |
| `bus_pairs` | array | | Worker-level explicit multi-sink nets of the form `[[a_idx, [b_idx, ...]], ...]`; multi-sink nets use the configured `routing_strategy`. |
| `routing_strategy` | string | "hybrid" | Worker-level strategy for multi-sink nets: `hybrid`, `per_pair`, or `steiner`. |

**Returns (normal run):**
```json
{
  "status": "success",
  "routed_pairs": 8,
  "total_pins_a": 8,
  "total_pins_b": 8,
  "output_layer": "10/0",
  "path_width_um": 10.0,
  "map_resolution_um_used": 2.0,
  "assignment_engine": "ordered_loop",
  "n_nets": 8,
  "errors": [],
  "next_step_suggestion": "Routes committed. Call screenshot ... then route_inspect to map each route_id back to its contact/pad, and evaluate_design with contact_isolation to detect crossings."
}
```

**Returns (dry_run=true):**
```json
{
  "status": "dry_run",
  "routed_pairs": 0,
  "total_pins_a": 8,
  "total_pins_b": 8,
  "pairs": [
    {"pin_a_idx": 0, "pin_b_idx": 5, "pin_a_um": [100.0, 200.0], "pin_b_um": [300.0, 400.0], "distance_um": 282.8}
  ],
  "assignment_engine": "ordered_loop",
  "map_resolution_um_used": 2.0,
  "errors": ["dry_run: matching only (ordered_loop), no routes inserted."],
  "next_step_suggestion": "dry_run preview: 8 assignments computed by ordered-loop ..."
}
```

The `next_step_suggestion` field adapts to the outcome (success / partial / dry_run / no routes) and names the specific follow-up tools to run. It is advisory — not a programmatic schema validator.

The `pairs[]` order from a dry_run matches the order `per_pair_obstacle_layers[pair_idx]` is interpreted against on the subsequent real run.

**Manual pairing workflow:** When the default ordered-loop assignment is not the desired device-level topology:

1. Call `auto_route(dry_run=true)` to see the ordered assignment as a `pairs[]` array.
2. Read `pin_a_idx` / `pin_b_idx` per entry to identify the mismatch.
3. Re-call `auto_route(pin_pairs_override=[[a_idx, b_idx], ...], dry_run=false)` with the corrected pairing. The override list must have length equal to the assigned pair count.

**Algorithm:**
1. Save current layout to temp GDS
2. Extract pin centers from shapes on pin_layer_a and pin_layer_b
3. Build obstacle region from obstacle_layers (raw shapes, no pre-expansion)
4. Rasterize obstacles into a 2D cost grid using `kdb.Region.rasterize()` (resolution = map_resolution)
5. Apply graduated damping fields around obstacles (stepped cost gradient within obs_safe_distance)
6. Mark all pins as impassable with their own damping halos (asymmetric per pin set)
7. Ordered-loop matching sorts both pin sets cyclically and selects a cyclic-monotonic low-cost pairing; explicit overrides bypass this assignment engine.
8. For each pair/net: temporarily recover only the active pins, apply any per-pair obstacles, find a minimum-cost path via MCP_Geometric (scikit-image), freeze the accepted route and margin as an obstacle, and re-block the pins.
9. Multi-sink bus nets can be routed as sequential pairs or as hybrid Steiner-style branches.
10. Paths are compressed (collinear points removed) and inserted as pya.Path objects.

**Dependencies (subprocess):** numpy, scikit-image, klayout (standalone package)

---

## route_inspect

Report per-route metadata (contact, pad, length, crossings) for every route on a given layer. Read-only — never modifies the layout. `route_id` in the output matches the indexing used by `evaluate_design`'s `contact_isolation.crossing_pairs`, so agents can cross-reference the two tools without re-deriving shape order.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `route_layer` | string | yes | | Layer spec of the routes to inspect (e.g. "3/0"). |
| `contact_layers` | string[] | **yes** | — | Layer specs for contact shapes route endpoints may land on. Must be a non-empty list; no default is assumed. |
| `pad_layer` | string | **yes** | — | Layer spec for bonding pads. No default is assumed. |
| `tolerance_um` | number | no | 5.0 | Endpoint-to-shape matching tolerance in microns. |

**Returns:**
```json
{
  "status": "ok",
  "route_layer": "3/0",
  "num_routes": 11,
  "routes": [
    {
      "route_id": 0,
      "kind": "path",
      "layer": "3/0",
      "length_um": 142.3,
      "endpoints_um": [[767.1, 804.0], [487.5, 1048.2]],
      "from_contact": {"layer": "21/0", "shape_idx": 0, "centroid_um": [767.17, 804.0]},
      "to_pad":       {"layer": "2/0",  "shape_idx": 12, "centroid_um": [487.5,  1048.2]}
    }
  ],
  "crossings": [[3, 4, 5.7]],
  "crossing_pairs_format": "[route_idx_A, route_idx_B, overlap_um2]",
  "next_step_suggestion": "1 crossing(s) detected. Inspect crossing_pairs (route_idx_A, route_idx_B, overlap_um2) and call screenshot(zoom_box=...) over each pair. ..."
}
```

`from_contact` and `to_pad` are `null` when the route's endpoints fall outside `tolerance_um` of any shape on the respective layers. Raise `tolerance_um` or verify the contact/pad layers if routes are unmapped.

**Crossings:** The `crossings` array uses the same tuple shape as `evaluate_design`'s `contact_isolation.crossing_pairs`. Unlike `evaluate_design`, this tool does NOT apply junction filtering — endpoint-adjacent overlaps will appear here. Cross-reference the two outputs for the junction-filtered view.

---

## evaluate_design

Evaluate a nanodevice layout using a configurable DRC-like checklist plus continuous geometric metrics. Runs `tools/evaluate_worker.py` as a subprocess. Accepts a layer map and a list of check primitives with per-check weights, so the caller can encode device-specific validity criteria rather than relying on a fixed Hall-bar score. Returns violation-oriented diagnostics, per-check scores, a weighted overall score, and a `next_step_suggestion` string that tells the agent which inspection tool to run next when a check underperforms.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `checks` | array | yes | | List of check objects: `[{name, args, weight}]`. See primitives below. |
| `layer_map` | object | yes | | Map of component names to layer specs. Accepted shapes: `[layer, dt]`, `{"layer": L, "datatype": D}`, or a list of either for multi-layer components. Keys are the ONLY names check args can reference. |
| `reference_gds` | string | no | | Path to reference GDS (only when a region references a layer that lives in the reference file rather than the current layout). |
| `timeout` | number | no | 300 | Subprocess timeout in seconds (min 60, max 900). Raise for layouts with hundreds of shapes. |
| `python_path` | string | no | | Path to python binary with gdstk/shapely/numpy (overrides `conda_env`). |
| `conda_env` | string | no | `"instrMCPdev"` | Conda environment with gdstk/shapely/numpy. |

**Returns:**
```json
{
  "status": "ok",
  "overall": 0.8234,
  "checks": [
    {"name": "bulk_containment", "score": 0.95, "weight": 0.2, "detail": "bulk_containment: 0.9500"},
    {
      "name": "contact_isolation",
      "score": 0.8,
      "weight": 0.15,
      "detail": "contact_isolation: 0.8000",
      "crossing_pairs": [[0, 1, 100.0], [0, 2, 100.0]],
      "crossing_pairs_format": "[route_idx_A, route_idx_B, overlap_um2] — 0-based indices, A<B, pad junctions excluded"
    }
  ],
  "next_step_suggestion": "Re-read the task instruction and checklist.md. … For contact_isolation < 0.8: call route_inspect …"
}
```

**Per-check extras:** `contact_isolation` adds a `crossing_pairs` list with every detected mid-body short and a `crossing_pairs_format` legend line explaining the tuple layout. Other primitives return a plain score; `contact_isolation` uses a dict-return pattern that `main()` promotes to top-level fields.

**Available check primitives (11):**
- `component_overlap` — fraction of component area overlapping with region
- `component_containment` — fraction of component area contained within region
- `bulk_containment` — fraction of component area inside a caller-declared bulk region. Use when the component has a core body plus peripherals that intentionally extend beyond the target region; `component_containment` would penalise that, `bulk_containment` does not. Args: `{component, bulk_region?, materials?, region_op?, core_bbox?}`. Pass **either** `bulk_region` (single layer_map key or list, combined via `region_op`) **or** `materials` (list of layer_map keys whose intersection defines the bulk). No default material names are assumed. Optional `core_bbox=[x1,y1,x2,y2]` in um clips the component to a rectangular core first.
- `arm_material_class` — fraction of component shapes that fall entirely inside EXACTLY ONE class. Args: `{component, classes=[{name, region, region_op?}, ...], containment_threshold?}`. A shape belongs to a class if ≥ `containment_threshold` (default 0.9) of its area is inside that class's region. Shapes that straddle multiple classes or land in zero classes score 0.
- `material_overlap_report` — compute pairwise and multi-way intersections of a set of material layers. Args: `{materials}`. `materials` is a list of ≥2 layer_map keys. Always scores 1.0; the report lives in the check's `report` side-data field: `{"<A>_only": {area_um2, bbox_um, centroid_um, num_polygons}, "<A>_and_<B>": {...}, ...}`. Use to replace the standard hand-rolled Region intersection code that every session ends up writing.
- `contact_isolation` — route crossing check with junction-aware detection and steep penalty curve
- `connectivity` — fraction of contacts that reach a bonding pad
- `route_endpoints` — fraction of route endpoints on valid targets
- `adjacency` — fraction of A shapes within tolerance of B
- `solidity` — shape solidity above/below threshold
- `spacing` — fraction of component pairs meeting minimum distance

The `region` arg can be a single `layer_map` key or a list of keys combined via `region_op` (union, intersection, difference).

**next_step_suggestion:** a short string describing what to do next. If any check scored < 0.8, it names the specific follow-up tool (`route_inspect`, `screenshot`) relevant to that check. The intent is to re-orient the agent toward the task instruction + checklist — it is NOT a programmatic schema validator. Always re-read the benchmark's task instruction after a failing evaluation.

**Dependencies (subprocess):** gdstk, shapely, numpy

---

## validate_pixel_size

Validate a microscope pixel size value and optionally compute marker spacing from a GDS template. Returns validity, likely objective mapping, warnings, and marker spacing.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `pixel_size` | number | yes | Pixel size in microns per pixel |
| `template_gds` | string | no | Path to a GDS file with L5/0 alignment markers |
| `image_path` | string | no | Path to a microscope image for reference |

**Returns:**
```json
{
  "status": "ok",
  "valid": true,
  "pixel_size": 0.087,
  "likely_objective": "100x",
  "warnings": [],
  "marker_spacing_um": 200.0
}
```

- `valid` — `true` if pixel_size is within range [0.01, 2.0] um/px
- `likely_objective` — nearest matching objective magnification
- `warnings` — list of warning strings (empty if no issues)
- `marker_spacing_um` — spacing between L5/0 markers in microns (null if no `template_gds` provided)

**Known pixel_size values:**

| Objective | pixel_size (um/px) |
|-----------|--------------------|
| 100x | 0.05 |
| 100x (alt) | 0.087 |
| 50x | 0.1 |
| 20x | 0.25 |
| 10x | 0.5 |

---

## close_layout_view

Close one or more KLayout layout tabs to keep the MCP server healthy. Layout tabs accumulate memory over a long session and can eventually stall the server — call this tool to organize tabs after a task is finished (e.g. clean up intermediate template/import tabs, or drop everything except the current working layout).

Dirty layouts are saved to a throwaway temp file before closing, so KLayout does not pop up its "Save changes?" dialog.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `index` | integer | no | | Optional 0-based view index to close. If omitted, falls back to `mode`. |
| `mode` | string | no | `"others"` | Close strategy when `index` is omitted. One of: `"current"` (close only the active tab), `"others"` (close every tab except the active one — typical end-of-task cleanup), `"all"` (close every open tab). |

**Returns:** `{"status": "ok", "closed": <N>, "remaining": <M>, "mode": "others"}` — with `"index": <i>` if called by index.

**Behavior:**
- Writes each dirty cellview to a throwaway temp file (via `LayoutView.save_as(ci, tmp_path, SaveLayoutOptions())`) before close, which clears KLayout's dirty flag and suppresses the save dialog. Temp files are left in `$TMPDIR`.
- Re-acquires `_layout`, `_layout_view`, `_top_cell` handles after closing so subsequent tool calls don't hit dangling references.
- If `mode="all"` leaves zero views, the next `create_layout` (or `execute_script` via `_get_or_create_view()`) rebuilds a fresh view automatically.
- Never touches tabs the user opened manually vs. tabs the MCP server created — it closes whatever you tell it to.
