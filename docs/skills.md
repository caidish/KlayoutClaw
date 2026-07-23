# KlayoutClaw Skills Reference

Skills are Claude Code plugins that wrap KlayoutClaw MCP tools into task-oriented CLIs. They live in the `skills/` directory of this repository.

## Installation

```bash
# Add KlayoutClaw as a Claude Code plugin marketplace
/plugin marketplace add caidish/KlayoutClaw

# Install the plugin
/plugin install klayoutclaw@klayoutclaw
```

After installation the four core skills are directly invocable as `/klayoutclaw:{geometry,display,image,visual}`. The nanodevice pipelines (`nanodevice_flakedetect`, `nanodevice_gdsalign`, `nanodevice_routing`, `nanodevice_e2e_design`) and `klayout_gds_import` auto-load by description when the user's request matches — Claude picks them without a slash command.

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

## GDS Alignment (nanodevice_gdsalign)

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
conda run -n instrMCPdev python skills/nanodevice_gdsalign/scripts/extract_markers.py \
    --gds Template.gds --output-dir output/gdsalign/
```

#### detect_markers.py

Detect marker pairs in microscope image via multi-scale, multi-rotation template matching.

```bash
conda run -n instrMCPdev python skills/nanodevice_gdsalign/scripts/detect_markers.py \
    --image full_stack_raw.jpg --pixel-size 0.087 \
    --gds-markers output/gdsalign/gds_markers.json --output-dir output/gdsalign/
```

#### align_gds.py

Exhaustive 2-point correspondence enumeration to compute similarity transform (image_um → GDS_um).

```bash
conda run -n instrMCPdev python skills/nanodevice_gdsalign/scripts/align_gds.py \
    --gds-markers output/gdsalign/gds_markers.json \
    --image-markers output/gdsalign/image_markers.json \
    --output-dir output/gdsalign/
```

#### commit_gds.py

Apply warp to image + contours, commit to KLayout. Use `--warp-only` for offline testing.

```bash
conda run -n instrMCPdev python skills/nanodevice_gdsalign/scripts/commit_gds.py \
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

Conda env: `instrMCPdev` (all deps pre-installed)

### Full Documentation

See `skills/nanodevice_gdsalign/SKILL.md` for orchestrator workflow and `docs/superpowers/specs/2026-03-13-gdsalign-design.md` for design spec.

---

## GDS Import (klayout_gds_import)

Safe GDS import that avoids the `Layout.read()` pitfalls documented in `CLAUDE.md`: cell-name collisions leaving orphaned cell indices, dangling `CellView.cell` after read, and multi-top-cell states after merging.

### Script

#### import_gds.py

```bash
python skills/klayout_gds_import/scripts/import_gds.py \
    --filepath /abs/path/to/template.gds \
    --flatten \
    --merge-into-current
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--filepath` | string | — | **Required.** Absolute path to a `.gds` file |
| `--flatten` / `--no-flatten` | bool | `true` | Flatten hierarchy into the top cell via `cell.flatten(-1, True)` |
| `--merge-into-current` / `--no-merge-into-current` | bool | `true` | Merge into the currently focused layout (vs. open a fresh tab) |

**Returns (stdout JSON):**
```json
{"status": "ok", "top_cell": "TOP", "shapes_added": 1342}
```

### Behavior

1. Reads the GDS into the current layout (or a fresh tab via `mw.create_layout(1)` if `--no-merge-into-current`)
2. Flattens hierarchy so there are no subcells left
3. Collapses any residual multi-top-cell state into a single top cell (largest bbox wins; other tops' shapes are moved in layer-by-layer via `shapes().each() + insert()`)
4. Rebinds `CellView.cell` to the resolved top, zooms fit, and refreshes the layer panel

### Dependencies

- `skills/scripts/mcp_client.py` (shared MCP client)
- KLayout running with the KlayoutClaw plugin (the script dispatches pya code via `execute_script`)

### Full Documentation

See `skills/klayout_gds_import/SKILL.md`.

---

## Flake Detection (nanodevice_flakedetect)

Agent-orchestrated pipeline for detecting van der Waals heterostructure material boundaries from optical microscope images. Detects hBN, graphene, and graphite from multi-source images and commits polygons to KLayout.

### Architecture

Split into 5 sub-skills, each executed by a subagent:

| Sub-skill | Purpose | Scripts |
|-----------|---------|---------|
| `align` | Register source images to full_stack coords | sift_align, source_contour, footprint, sweep, refine |
| `detect` | Per-material segmentation | graphite, graphene, bottom_hbn, top_hbn |
| `combine` | Coordinate transforms + overlays | ecc_register, transform, overlay, rank_candidate_pairs |
| `commit` | Insert polygons into KLayout | (pure agent workflow, uses geometry skill) |
| `review` | Visual validation protocol | (pure agent workflow, uses display skill) |

### Pipeline

```
1. align → 2. detect → 3. combine → 4. commit → 5. review
```

Each step runs as a subagent that reads its SKILL.md from `skills/nanodevice_flakedetect_<step>/SKILL.md`.

### Dependencies

- `opencv-python-headless` — image processing, contour extraction
- `numpy` — array operations
- `scipy` — KDTree, optimization (Chamfer alignment)
- `scikit-learn` — k-means clustering
- `shapely` — polygon ops in `rank_candidate_pairs.py`

Conda env: `instrMCPdev` (all deps pre-installed)

### Full Documentation

See `skills/nanodevice_flakedetect/SKILL.md` for the orchestrator workflow, and each sub-skill's SKILL.md for detailed script references and tuning guides.


---

## SAM-Assisted Flake Detection (nanodevice_flakedetect_sam)

Optional wrappers around the standard graphite, graphene, bottom hBN, and top hBN detectors. These scripts keep the normal `nanodevice_flakedetect_detect` outputs while adding prompt candidate images and JSON sidecars that can be refined by a local SAM2 checkout.

### Scripts

```bash
conda run -n instrMCPdev python skills/nanodevice_flakedetect_sam/scripts/graphene.py \
    --image top_part.jpg --pixel-size 0.087 --output-dir output/detect/
```

Graphene emits candidate overlays such as `graphene_candidate_01_on_grid.png` plus `graphene_prompt_candidates.json`; graphite emits a source grid for manual prompt selection. Use `--use-sam2` only after installing PyTorch, adding the SAM2 source under `tools/sam2-main` or setting `SAM2_ROOT`, and placing the checkpoint at `tools/sam2-main/model/sam2.1_hiera_base_plus.pt`.

If SAM2 is unavailable, the wrapper does not block the pipeline: it records the reason and returns the baseline detector result.

Device selection defaults to CUDA, then Apple Metal/MPS, then CPU. Override it
with `--sam-device cuda|mps|cpu|auto` or `SAM2_DEVICE`. MPS inference enables
PyTorch's CPU operator fallback for Metal operations that are unavailable.

Download the optional pieces from:

- SAM2 source: https://github.com/facebookresearch/sam2
- SAM2.1 base-plus checkpoint: https://huggingface.co/facebook/sam2.1-hiera-base-plus/tree/main (`sam2.1_hiera_base_plus.pt`)

### Dependencies

- Baseline: same `instrMCPdev` stack as `nanodevice_flakedetect`
- Optional refinement: PyTorch, local SAM2 source checkout, SAM2 checkpoint

### Full Documentation

See `skills/nanodevice_flakedetect_sam/SKILL.md`.

---

## Nanodevice Routing (nanodevice_routing)

Multi-window EBL routing: place bonding pads around the field perimeter, then run two-pass routing (inner fine + outer coarse + boundary patches) to connect device contacts to pads.

### Scripts

#### place_pads.py

```bash
python skills/nanodevice_routing/scripts/place_pads.py \
    --field 2000 --pad-size 80 --pads-per-edge 12 [--layer 2/0] [--margin 60]
```

Places bonding pads around the EBL write-field perimeter and drops pin markers on a companion layer for the router.

#### route_multiwindow.py

```bash
python skills/nanodevice_routing/scripts/route_multiwindow.py \
    --pin-contacts 100/0 --pin-pads 101/0 \
    --inner-window 800 --outer-window 2000 \
    --inner-width 0.5 --outer-width 1.0 \
    --inner-layer 3/0 --outer-layer 4/0 --patch-layer 5/0 \
    --obstacle-layers 1/0
```

Two-pass router: contacts → boundary (fine), boundary patches, boundary → pads (coarse).

#### clear_routes.py

```bash
python skills/nanodevice_routing/scripts/clear_routes.py 3/0 4/0 5/0
```

Removes shapes from the listed layers so you can re-route without touching device geometry.

### Dependencies

- `numpy`, `scikit-image`, `klayout` — backing `auto_route` subprocess
- Conda env: `instrMCPdev`

### Full Documentation

See `skills/nanodevice_routing/SKILL.md` for the multi-window workflow, the `python_path` fallback for hosts without `instrMCPdev`, and manual-route fallbacks for pairs `auto_route` can't solve.

---

## End-to-End Device Design (nanodevice_e2e_design)

Device-agnostic methodology for designing nanodevices on material regions in KLayout. The agent follows a 7-step reasoning pipeline, deriving device-specific physics rules from context and user input. No device type is hardcoded.

This is a pure-text orchestrator skill with no scripts directory. The agent dispatches sub-skills and MCP tools at each step.

### Pipeline Steps
1. QUERY — gather device type, material regions, layer assignments
2. PREPARE — run flake detection + GDS alignment (optional, only if microscope images provided)
3. ANALYZE — study material regions, compute overlaps/exclusions
4. DESIGN — create device geometry via `execute_script`
5. ROUTE — connect contacts to bonding pads via `auto_route`; for dense layouts start with `dry_run=true` to inspect the ordered-loop assignment and re-call with `pin_pairs_override` when the desired device topology differs. Inspect each route with `route_inspect` to surface crossings.
6. EVALUATE — run the nanodevice DRC + metric evaluator with device-appropriate checks. Newer primitives worth knowing: `bulk_containment`, `arm_material_class`, `material_overlap_report`. The response's `next_step_suggestion` names the specific follow-up tool for any check scoring below 0.8.
7. SAVE — export GDS + write result.json

Steps are conditional: QUERY is skipped if all info is provided, PREPARE is skipped if no images are given, ROUTE is skipped if the device has no external contacts.

### Recovery: MCP Wedged

MCP is the only path by design -- there is no standalone subprocess fallback. If an MCP call hangs partway through the pipeline, restart KLayout (`pkill -f klayout`, then `open /Applications/klayout.app`, poll `http://127.0.0.1:8765/mcp` until ready, then re-run the failed call). Full procedure in the **MCP Wedged? Restart KLayout** section of `skills/nanodevice_e2e_design/SKILL.md`.

### Full Documentation

See `skills/nanodevice_e2e_design/SKILL.md` for the complete methodology, gate conditions, retry protocol, and check primitives.

---

## Tests

The canonical test suites live at the repo root in `tests/`:

- `tests/test_phase*.py` — pytest-based functional tests (phase 0-4). Run: `pytest -m mcp tests/`.
- `tests/test_e2e_*.sh` — shell-driven E2E bundles (regression, alt-device, crossing_pairs, material_overlap, route_override, non-Hall-bar, heavy-script). Run: `bash tests/test_e2e_regression.sh` to sweep every phase sequentially.
- `tests/test_connection.sh`, `tests/test_hallbar.sh`, `tests/test_autoroute.sh` — legacy single-purpose E2E scripts.

All suites require KLayout running with the KlayoutClaw plugin at `127.0.0.1:8765`.
