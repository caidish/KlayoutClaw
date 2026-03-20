---
name: nanodevice:gdsalign
description: Align microscope stack images to a GDS fabrication template using lithographic marker detection. Computes image→GDS transform and commits warped image + material contours to KLayout.
---

# nanodevice:gdsalign — GDS Template Alignment

Align microscope images to a GDS fabrication template by detecting lithographic markers in both domains and computing a similarity transform.

## Prerequisites

- Conda env `base` with opencv, numpy, scipy, gdstk
- Template GDS file with L5/0 alignment markers (4 cross pairs)
- flakedetect output: `traces.json` with material contours in pixel coordinates
- All scripts: `conda run -n base python <script>`

## Default Output Directory

**If the user does not specify an output path, default to `<stack_image_dir>/output/gdsalign/`** (the directory containing the source images). Never use `/tmp` as the default output.

---

## Agent Workflow

```
1. extract_markers — Parse GDS, find 4 innermost L5/0 marker pairs
   → gds_markers.json (pair centers + bounding boxes in um)

2. detect_markers — Find marker crosses in microscope image
   → image_markers.json (detected centers in pixel coordinates)

3. align_gds — Match GDS↔image marker pairs, compute reflected similarity
   → gds_warp.npy (2x3 affine), gds_alignment_report.json

4. commit_gds — Warp image + transform contours, commit to KLayout
   → full_stack_gds.png, traces_gds.json, image_placement.json
```

---

## Scripts Reference

### extract_markers.py

```bash
conda run -n base python skills/nanodevice/gdsalign/scripts/extract_markers.py \
    --gds Template.gds --output-dir output/gdsalign/
```

Parses GDS, finds all L5/0 polygons, selects the 8 closest to grid center, groups into 4 pairs by proximity, labels NE/NW/SE/SW.

**Outputs**: `gds_markers.json`

### detect_markers.py

```bash
conda run -n base python skills/nanodevice/gdsalign/scripts/detect_markers.py \
    --image stack.png --pixel-size <um/px> \
    --gds-markers output/gdsalign/gds_markers.json \
    --output-dir output/gdsalign/
```

Renders marker-pair templates from `gds_markers.json`, then runs multi-method template matching (grayscale, inverted, CLAHE, edge) with geometric consistency filtering to find the 4 marker pairs.

**Outputs**: `image_markers.json`, `01_template.png`, `03_detections.png`

### align_gds.py

```bash
conda run -n base python skills/nanodevice/gdsalign/scripts/align_gds.py \
    --gds-markers output/gdsalign/gds_markers.json \
    --image-markers output/gdsalign/image_markers.json \
    --output-dir output/gdsalign/
```

Exhaustive enumeration over 2-point correspondences finds the best reflected similarity (image Y-down → GDS Y-up), then least-squares refinement over all inliers. Automatically resolves rotational ambiguity (90°/180°/270°) for symmetric marker patterns by preferring the solution closest to 0° rotation.

**Outputs**: `gds_warp.npy` (2×3 affine matrix), `gds_alignment_report.json`

### commit_gds.py

```bash
conda run -n base python skills/nanodevice/gdsalign/scripts/commit_gds.py \
    --warp output/gdsalign/gds_warp.npy \
    --traces output/combine/traces.json \
    --image full_stack_raw.jpg --pixel-size <um/px> \
    --gds Template.gds --output-dir output/gdsalign/ [--warp-only]
```

Warps the microscope image and material contours into GDS coordinates using the affine from `align_gds.py`. Without `--warp-only`, also loads the GDS template into KLayout, adds the warped image as a background overlay, and inserts material polygons on layers 10–13.

| Flag | Description |
|------|-------------|
| `--warp` | Path to `gds_warp.npy` (2×3 affine matrix) |
| `--traces` | Path to `traces.json` from flakedetect (with `contour_um` fields) |
| `--image` | Full-stack microscope image |
| `--pixel-size` | Image pixel size in um/px |
| `--gds` | Template GDS file (loaded into KLayout during commit) |
| `--warp-only` | Only produce warped files, skip KLayout commit |

**Outputs**: `full_stack_gds.png`, `traces_gds.json`, `image_placement.json`

---

## Acceptance Thresholds

| Metric | Pass | Fail |
|--------|------|------|
| Markers detected | >= 3 | < 3 |
| Inliers (align_gds) | >= 2 | < 2 |
| Mean residual | < 5.0 um | >= 5.0 um |

**Fail on any metric**: Do NOT commit to KLayout. Check diagnostic images and retry.

## Known Behaviour

- **Rotational ambiguity**: Square marker grids have rotational symmetry — θ, θ+90°, θ+180°, θ+270° can yield similar residuals. `align_gds.py` automatically tries all four companions and picks the one with rotation closest to 0°.
- **PNG orientation**: The warped PNG (`full_stack_gds.png`) has GDS south at the top (standard for GDS→image rendering). KLayout overlay places it correctly via `origin_um = (x_min, y_min)`.

---

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Error (missing file, insufficient markers, transform failure) |
