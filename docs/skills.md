# KlayoutClaw Skills Reference

Skills are Claude Code plugins that wrap KlayoutClaw MCP tools into task-oriented CLIs. They live in the `skills/` directory of this repository.

## Installation

```bash
# Add KlayoutClaw as a Claude Code plugin marketplace
/plugin marketplace add caidish/KlayoutClaw

# Install the plugin
/plugin install klayoutclaw@klayoutclaw
```

After installation, skills are available as `/klayoutclaw:geometry`, `/klayoutclaw:display`, `/klayoutclaw:image`, and `/klayoutclaw:visual`. Claude also loads them automatically when relevant.

All scripts share a common MCP client (`skills/scripts/mcp_client.py`) that connects to KLayout at `127.0.0.1:8765`.

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

## Image

Load reference images (microscope photos, SEM, optical) as background overlays for design alignment.

### add_image.py

Load an image as a background overlay in KLayout.

```bash
python skills/image/scripts/add_image.py <filepath> [--pixel-size 0.1] [--scale-bar <um> <px>] [--x 0] [--y 0] [--center]
```

| Flag | Default | Description |
|------|---------|-------------|
| `filepath` | (required) | Path to image file (JPG, PNG, BMP) |
| `--pixel-size` | `1.0` | Microns per pixel |
| `--scale-bar` | — | Derive pixel size from scale bar: `<um> <pixels>` |
| `--x` | `0` | X position offset in microns |
| `--y` | `0` | Y position offset in microns |
| `--center` | off | Center image at given position |

```bash
# Set pixel size directly
python add_image.py ~/photos/graphene.jpg --pixel-size 0.1

# Derive from scale bar: 20 um bar = 153 pixels → 0.1307 um/px
python add_image.py ~/photos/graphene.jpg --scale-bar 20 153 --center

# Center at (50, 25) um
python add_image.py ~/photos/flake.png --pixel-size 0.05 --x 50 --y 25 --center
```

### list_images.py

List all background images in the current view.

```bash
python skills/image/scripts/list_images.py
```

### remove_image.py

Remove background image(s) by ID or remove all.

```bash
python skills/image/scripts/remove_image.py <image_id | all>
```

```bash
python remove_image.py 12     # remove specific image
python remove_image.py all    # remove all images
```

### Estimating pixel-size

If the image has a scale bar of `S` microns spanning `P` pixels: `pixel-size = S / P`.

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

## GDS Alignment (nanodevice:gdsalign)

Align microscope stack images to a GDS fabrication template by detecting lithographic markers and computing a similarity transform. Commits warped image + material contours to KLayout.

### Pipeline

```
1. extract_markers.py → gds_markers.json (4 inner L5/0 marker pair centroids)
2. detect_markers.py  → image_markers.json (template-matched detections in image)
3. align_gds.py       → gds_warp.npy + gds_alignment_report.json (similarity transform)
4. commit_gds.py      → full_stack_gds.png + traces_gds.json (warped outputs)
```

### Scripts

#### extract_markers.py

Parse GDS template, extract the 4 innermost L5/0 marker pairs (8 squares total).

```bash
conda run -n base python skills/nanodevice/gdsalign/scripts/extract_markers.py \
    --gds Template.gds --output-dir output/gdsalign/
```

#### detect_markers.py

Detect marker pairs in microscope image via multi-scale, multi-rotation template matching.

```bash
conda run -n base python skills/nanodevice/gdsalign/scripts/detect_markers.py \
    --image full_stack_raw.jpg --pixel-size 0.087 \
    --gds-markers output/gdsalign/gds_markers.json --output-dir output/gdsalign/
```

#### align_gds.py

Exhaustive 2-point correspondence enumeration to compute similarity transform (image_um → GDS_um).

```bash
conda run -n base python skills/nanodevice/gdsalign/scripts/align_gds.py \
    --gds-markers output/gdsalign/gds_markers.json \
    --image-markers output/gdsalign/image_markers.json \
    --output-dir output/gdsalign/
```

#### commit_gds.py

Apply warp to image + contours, commit to KLayout. Use `--warp-only` for offline testing.

```bash
conda run -n base python skills/nanodevice/gdsalign/scripts/commit_gds.py \
    --warp output/gdsalign/gds_warp.npy --traces output/combine/traces.json \
    --image full_stack_raw.jpg --pixel-size 0.087 \
    --gds Template.gds --output-dir output/gdsalign/ [--warp-only]
```

### Acceptance Thresholds

| Metric | Pass | Fail |
|--------|------|------|
| Markers detected | >= 3 | < 3 |
| Mean residual | < 1.0 um | >= 1.0 um |
| Max residual | < 2.0 um | >= 2.0 um |

### Dependencies

- `gdstk` — GDS parsing
- `opencv-python-headless` — template matching, image warp
- `numpy`, `scipy` — transform computation, least-squares refinement

Conda env: `base` (all deps pre-installed)

### Full Documentation

See `skills/nanodevice/gdsalign/SKILL.md` for orchestrator workflow and `docs/superpowers/specs/2026-03-13-gdsalign-design.md` for design spec.

---

## Flake Detection (nanodevice:flakedetect)

Agent-orchestrated pipeline for detecting van der Waals heterostructure material boundaries from optical microscope images. Detects hBN, graphene, and graphite from multi-source images and commits polygons to KLayout.

### Architecture

Split into 5 sub-skills, each executed by a subagent:

| Sub-skill | Purpose | Scripts |
|-----------|---------|---------|
| `align` | Register source images to full_stack coords | sift_align, source_contour, footprint, sweep, refine |
| `detect` | Per-material segmentation | graphite, graphene, bottom_hbn, top_hbn |
| `combine` | Coordinate transforms + overlays | ecc_register, transform, overlay |
| `commit` | Insert polygons into KLayout | (pure agent workflow, uses geometry skill) |
| `review` | Visual validation protocol | (pure agent workflow, uses display skill) |

### Pipeline

```
1. align → 2. detect → 3. combine → 4. commit → 5. review
```

Each step runs as a subagent that reads its SKILL.md from `skills/nanodevice/flakedetect/<step>/SKILL.md`.

### Dependencies

- `opencv-python-headless` — image processing, contour extraction
- `numpy` — array operations
- `scipy` — KDTree, optimization (Chamfer alignment)
- `scikit-learn` — k-means clustering

Conda env: `base` (all deps pre-installed)

### Full Documentation

See `skills/nanodevice/flakedetect/SKILL.md` for the orchestrator workflow, and each sub-skill's SKILL.md for detailed script references and tuning guides.

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
