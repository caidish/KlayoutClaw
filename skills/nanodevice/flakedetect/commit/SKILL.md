---
name: nanodevice:flakedetect:commit
description: Insert detected flake polygons into KLayout as GDS geometry using the geometry skill's add_polygon.py. Use after combine completes to commit traces.json contours as polygons on the correct layers.
---

# nanodevice:flakedetect:commit — KLayout Polygon Insertion

Pure agent workflow using existing skills and MCP tools. No custom scripts needed.

## When to Use

After the combine step completes. Requires `traces.json` from `<out>/combine/`.

## Prerequisites

- KLayout running with KlayoutClaw plugin (v0.5+)
- A layout open (use `create_layout` MCP tool if needed)
- Background image loaded (use `image` skill's `add_image.py`)
- `traces.json` produced by the combine step

## Commit Protocol

### Step 1: Load background image

**NOTE**: The `add_image.py` script uses the MCP client which does NOT work from inside a Docker container. Instead, use `execute_script` directly to load the image:

```python
filepath = "/path/to/full_stack_raw.jpg"
pixel_size = 0.087  # um/px

img = pya.Image(filepath)
img.visible = True
w_um = img.width() * pixel_size
h_um = img.height() * pixel_size
img.set_pixel_width(pixel_size)
img.set_pixel_height(pixel_size)
# Center the image at (0, 0)
img.trans = pya.DCplxTrans(1.0, 0, False, pya.DVector(-w_um / 2.0, -h_um / 2.0))
_layout_view.insert_image(img)
_refresh_view()
result = f"Image loaded: {w_um:.1f} x {h_um:.1f} um, centered at origin"
```

### Step 2: Read traces.json

Load `<out>/combine/traces.json`. Extract:
- `image_size_um` — `[w_um, h_um]` for coordinate transform
- `layer_map` — material name → `"layer/datatype"` string
- `materials` — per-material contour lists with `contour_um` coordinates

### Step 3: Coordinate transform

Contours in traces.json use **image-origin coordinates** (0,0 at top-left, y-axis pointing down, units in microns).

KLayout uses **centered coordinates** (0,0 at image center, y-axis pointing up, units in microns).

Transform each point:
```
kl_x = img_x_um - w_um / 2
kl_y = h_um / 2 - img_y_um
```

The **y-axis sign flip** (`h_um/2 - img_y_um`) is the most common source of errors.

### Step 4: Add polygons via execute_script

**NOTE**: The `add_polygon.py` script uses the MCP client which does NOT work from inside a Docker container. Instead, use `execute_script` to add polygons directly:

```python
import json

with open("<out>/combine/traces.json") as f:
    traces = json.load(f)

w_um, h_um = traces["image_size_um"]
top_cell = _layout.top_cell()

for material in traces["stack"]:
    layer_str = traces["layer_map"][material]  # e.g. "10/0"
    layer_num, dt = [int(x) for x in layer_str.split("/")]
    li = _layout.layer(layer_num, dt)

    for region in traces["materials"][material]:
        pts = []
        for x_um, y_um in region["contour_um"]:
            kl_x = x_um - w_um / 2.0
            kl_y = h_um / 2.0 - y_um  # Y-axis flip!
            pts.append(pya.DPoint(kl_x, kl_y))
        top_cell.shapes(li).insert(pya.DPolygon(pts))

_refresh_view()
result = f"Committed polygons for {len(traces['stack'])} materials"
```

This is faster and more reliable than calling `add_polygon.py` for each material, and works from inside Docker containers.

### Step 5: Verify with screenshot

Use the MCP `screenshot` tool to capture the viewport and visually confirm polygons align with the background image:

```
mcp__klayoutclaw__screenshot → view the PNG
```

## Layer Assignments

| Material | Layer | Color |
|----------|-------|-------|
| top_hBN | 10/0 | green |
| graphene | 11/0 | red |
| bottom_hBN | 12/0 | blue-ish |
| graphite | 13/0 | yellow |

These defaults come from `traces.json` `layer_map` — always use the values from the JSON, not hardcoded.

## Common Pitfalls

1. **Y-axis flip**: `kl_y = h_um/2 - img_y_um` — forgetting the sign flip puts polygons upside down.
2. **MCP client scripts fail in Docker**: `add_polygon.py` and `add_image.py` connect to KLayout MCP on localhost, which doesn't work from inside a container. Always use `execute_script` instead (shown above).
3. **Cell name**: When using `execute_script`, use `_layout.top_cell()` to get the current top cell — don't hardcode a cell name.
4. **Background centering**: The image must be centered at KLayout's (0,0) origin so that the coordinate transform `kl_x = x_um - w_um/2` aligns correctly.
5. **Large contours**: Some materials (bottom_hBN) can have 5000+ contour points. Use `execute_script` with the full polygon — it handles large point arrays efficiently.
