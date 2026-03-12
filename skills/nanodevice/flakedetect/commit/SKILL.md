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

Use the `image` skill to load the full_stack image as a centered background:

```bash
python skills/image/scripts/add_image.py <full_stack_raw.jpg> --pixel-size <um/px> --center
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

### Step 4: Add polygons using the geometry skill

For each material, call `add_polygon.py` with the transformed coordinates:

```bash
python skills/geometry/scripts/add_polygon.py <cell_name> <layer> <datatype> <x1,y1> <x2,y2> ...
```

Example for top_hBN (layer 10/0):
```bash
python skills/geometry/scripts/add_polygon.py ML08_stack 10 0 -79.257,37.410 -84.042,25.404 ...
```

Repeat for each material in the `stack` order. Parse `layer_map` values to get the layer and datatype numbers (e.g., `"10/0"` → layer=10, datatype=0).

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
2. **Coordinate format**: `add_polygon.py` takes points as `x,y` pairs (comma-separated, no spaces within a pair).
3. **Cell name**: Must match the top cell name in the current layout (from `create_layout`).
4. **Background centering**: Use `--center` with `add_image.py` so the image origin aligns with KLayout's (0,0) center.
