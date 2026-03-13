---
name: nanodevice:flakedetect:combine
description: Transform detection results into the common full_stack coordinate system, build unified traces.json, and draw overlay images. Use after align and detect steps are complete.
---

# nanodevice:flakedetect:combine — Coordinate Transforms & Overlays

Transforms all per-material detections into the full_stack coordinate system, produces the unified `traces.json`, and draws contour overlays on raw/LUT images.

## Prerequisites

- Conda env `base` with opencv, numpy
- Completed align step (warp matrices, footprint mask)
- Completed detect step (per-material masks/contours, detections.json)
- All scripts run via `conda run -n base python <script>`

## Scripts

### ecc_register.py — ECC raw-to-LUT translation alignment

```bash
conda run -n base python skills/nanodevice/flakedetect/combine/scripts/ecc_register.py \
    --raw <full_stack_raw_image> \
    --lut <full_stack_lut_image> \
    --output-dir <path>
```

- `--raw` — Full stack raw image
- `--lut` — Full stack LUT (color-enhanced) image
- `--output-dir` — Output directory

Computes ECC translation alignment between raw and LUT images. The LUT image typically has a spatial offset (~71px dx, ~56px dy) from raw.

**Outputs:**
- Creates `combine_report.json` with `raw2lut` section: dx, dy, ecc_correlation

**Note:** If no LUT image is available, skip this script. The overlay script handles the absence gracefully.

### transform.py — Coordinate transforms for all materials

```bash
conda run -n base python skills/nanodevice/flakedetect/combine/scripts/transform.py \
    --detections <detect/detections.json> \
    --align-dir <align/> \
    --image <full_stack_raw_image> \
    --pixel-size <um_per_px> \
    --output-dir <path>
```

- `--detections` — Path to detections.json from detect step
- `--align-dir` — Directory containing warp matrices and footprint from align step
- `--image` — Full stack raw image (for size reference)
- `--pixel-size` — Microns per pixel
- `--output-dir` — Output directory

**Transform rules by material:**

| Material | Coordinate System | Transform |
|----------|------------------|-----------|
| graphite | bottom_part | Invert `warp_sift_bottom.npy`, apply to contour |
| graphene | top_part (mirrored) | Apply `warp_top.npy` to mask (INTER_NEAREST), clip to footprint, morph clean, re-extract contour |
| bottom_hBN | full_stack | Pass through (already in target coords) |
| top_hBN | full_stack | Pass through (= footprint) |

All materials get `smooth_material()` applied after transform.

**Input files read from `--align-dir`:**
- `warp_sift_bottom.npy` — inverted before use (bottom_part→full_stack)
- `warp_top.npy` — applied directly (top_part→full_stack)
- `footprint_mask.png` — for graphene clipping

**Outputs:**
- `traces.json` — unified traces with all contours in full_stack pixel coordinates
- `graphite_full.png`, `graphene_full.png`, `bottom_hbn_full.png`, `top_hbn_full.png` — transformed masks
- Appends `transform_summary` section to `combine_report.json`

### overlay.py — Contour overlay visualization

```bash
conda run -n base python skills/nanodevice/flakedetect/combine/scripts/overlay.py \
    --traces <combine/traces.json> \
    --raw <full_stack_raw_image> \
    [--lut <full_stack_lut_image>] \
    [--combine-report <combine/combine_report.json>] \
    --output-dir <path>
```

- `--traces` — Path to traces.json from transform step
- `--raw` — Full stack raw image
- `--lut` — (optional) Full stack LUT image
- `--combine-report` — (optional) Path to combine_report.json (for raw→LUT shift)
- `--output-dir` — Output directory

Draws material contours on desaturated background images using the BGR color palette:
- top_hBN: green (0, 200, 0)
- graphene: red (0, 0, 255)
- bottom_hBN: blue-ish (255, 100, 0)
- graphite: yellow (0, 200, 255)

For LUT overlay: reads dx, dy from combine_report.json and shifts contours before drawing.

**Outputs:**
- `overlay_raw.png` — all material contours on raw image
- `overlay_lut.png` — all material contours on LUT image (if --lut provided)
- `mask_composite.png` — all material masks color-coded at 50% alpha
- Appends `overlay_files` section to `combine_report.json`

## Workflow

Run in order:
1. `ecc_register.py` (if LUT image available)
2. `transform.py`
3. `overlay.py`

The `combine_report.json` is a multi-writer file: ecc_register creates it, transform adds to it, overlay adds to it. Each script reads the existing file and appends its section.

## Output Files

All outputs go in the combine output directory:
- `traces.json` — the main pipeline output consumed by review and commit
- `combine_report.json` — metadata for diagnostics
- Per-material transformed masks and overlay images
