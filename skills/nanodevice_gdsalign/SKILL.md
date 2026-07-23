---
name: nanodevice_gdsalign
description: Align microscope stack images to a GDS fabrication template using lithographic marker detection. Computes image鈫扜DS transform and commits warped image + material contours to KLayout.
---

# nanodevice_gdsalign 鈥?GDS Template Alignment

Align microscope images to a GDS fabrication template by detecting lithographic markers in both domains and computing a similarity transform.

> **Coordinate system after gdsalign**: Coordinates in `traces_gds.json` are in the **GDS reference frame** (floating-point micrometers). When using them in `execute_script`, convert to dbu via `int(value_um / layout.dbu)`. **Do NOT apply the flakedetect_commit image-center transform** 鈥?gdsalign already handles the coordinate mapping.

## Prerequisites

- Conda env `instrMCPdev` with opencv, numpy, scipy, gdstk
- Template GDS file with L5/0 alignment markers (4 cross pairs)
- flakedetect output: `traces.json` with material contours in pixel coordinates
- All scripts: `${PYTHON_PATH:-conda run -n instrMCPdev python} <script>`

## Default Output Directory

**If the user does not specify an output path, default to `<stack_image_dir>/output/gdsalign/`** (the directory containing the source images). Never use `/tmp` as the default output.

---

## Agent Workflow

```
1. extract_markers 鈥?Parse GDS, find 4 innermost L5/0 marker pairs
   鈫?gds_markers.json (pair centers + bounding boxes in um)

2. detect_markers 鈥?Find marker crosses in microscope image
   鈫?image_markers.json (detected centers in pixel coordinates)

3. align_gds 鈥?Match GDS鈫攊mage marker pairs, compute reflected similarity
   鈫?gds_warp.npy (2x3 affine), gds_alignment_report.json

4. commit_gds 鈥?Warp image + transform contours, commit to KLayout
   鈫?full_stack_gds.png, traces_gds.json, image_placement.json
```

---

## Scripts Reference

### extract_markers.py

```bash
${PYTHON_PATH:-conda run -n instrMCPdev python} skills/nanodevice_gdsalign/scripts/extract_markers.py \
    --gds Template.gds --output-dir output/gdsalign/
```

Parses GDS, finds all L5/0 polygons, selects the 8 closest to grid center, groups into 4 pairs by proximity, labels NE/NW/SE/SW.

**Outputs**: `gds_markers.json`

### detect_markers.py

```bash
${PYTHON_PATH:-conda run -n instrMCPdev python} skills/nanodevice_gdsalign/scripts/detect_markers.py \
    --image stack.png --pixel-size <um/px> \
    --gds-markers output/gdsalign/gds_markers.json \
    --output-dir output/gdsalign/
```

Renders marker-pair templates from `gds_markers.json`, then runs multi-method template matching (grayscale, inverted, CLAHE, edge) with geometric consistency filtering to find the 4 marker pairs.

**Outputs**: `image_markers.json`, `01_template.png`, `03_detections.png`

**Verification**: After running, view `03_detections.png`. The 4 detected markers (colored circles) should form a roughly square pattern, ~300-400 um apart (a few thousand pixels at 0.087 um/px), centered in the image near the flake region. The `pixel_size` parameter is critical for correct template rendering 鈥?use the value specified in the task instructions (typically 0.087 um/px). If detections look wrong (markers on image edges, or only 2-3 found), check that pixel_size matches the actual microscope magnification.

### align_gds.py

```bash
${PYTHON_PATH:-conda run -n instrMCPdev python} skills/nanodevice_gdsalign/scripts/align_gds.py \
    --gds-markers output/gdsalign/gds_markers.json \
    --image-markers output/gdsalign/image_markers.json \
    --output-dir output/gdsalign/
```

Exhaustive enumeration over 2-point correspondences finds the best reflected similarity (image Y-down 鈫?GDS Y-up), then least-squares refinement over all inliers. Automatically resolves rotational ambiguity (90掳/180掳/270掳) for symmetric marker patterns by preferring the solution closest to 0掳 rotation.

**Outputs**: `gds_warp.npy` (2脳3 affine matrix), `gds_alignment_report.json`

### commit_gds.py

```bash
${PYTHON_PATH:-conda run -n instrMCPdev python} skills/nanodevice_gdsalign/scripts/commit_gds.py \
    --warp output/gdsalign/gds_warp.npy \
    --traces output/combine/traces.json \
    --image full_stack_raw.jpg --pixel-size <um/px> \
    --gds Template.gds --output-dir output/gdsalign/ [--warp-only]
```

Warps the microscope image and material contours into GDS coordinates using the affine from `align_gds.py`. Without `--warp-only`, also loads the GDS template into KLayout, adds the warped image as a background overlay, and inserts material polygons on layers 10鈥?3.

| Flag | Description |
|------|-------------|
| `--warp` | Path to `gds_warp.npy` (2脳3 affine matrix) |
| `--traces` | Path to `traces.json` from flakedetect (with `contour_um` fields) |
| `--image` | Full-stack microscope image |
| `--pixel-size` | Image pixel size in um/px |
| `--gds` | Template GDS file (loaded into KLayout during commit) |
| `--output-dir` | Optional output directory for warp results (default: current working directory). Set this to the agent's workspace directory to keep warp outputs scoped. Files written: `traces_gds.json`, `full_stack_gds.png`, `image_placement.json` |
| `--warp-only` | Only produce warped files, skip KLayout commit |
| `--mcp-config` | Path to MCP config JSON. Fallback chain: `.mcp.json` in CWD 鈫?`mcp_config.json` in project root 鈫?default `127.0.0.1:8765`. **In Docker**, pass a config pointing to `http://host.docker.internal:8765/mcp` |
| `--align-report` | Path to `gds_alignment_report.json` for the registration gate. Default: auto-located next to `--warp`. |
| `--gds-markers` | Path to `gds_markers.json` for the marker-field frame check. Default: auto-located next to `--warp`. |
| `--skip-registration-check` | **Escape hatch 鈥?strongly discouraged.** Bypasses the registration self-validation gate. Only for an intentional hand-built transform. |

**Outputs**: `full_stack_gds.png`, `traces_gds.json`, `image_placement.json`

> ### 鈿狅笍 Validated-commit contract 鈥?commit flakes ONLY through `commit_gds.py`
>
> `commit_gds.py` is the **only sanctioned way** to place flake polygons in the
> GDS frame. **Do NOT** hand-place flakes via `execute_script` with a
> hand-built transform (a centered shift / Y-flip / "1:1 at origin"). That is
> the dominant failure mode (HM08/QH06/AH06): the device looks perfect against
> the agent's own flakes but lands hundreds of um off the real marker field and
> scores ~0.
>
> Before committing, `commit_gds.py` runs a **registration self-validation
> gate** (no ground truth 鈥?only the alignment report, the marker grid, and your
> own warped contours). It hard-fails (exit 3) when:
> - there is no `gds_alignment_report.json` next to the warp (alignment was
>   skipped / hand-rolled),
> - the alignment is bad (`< 3` inliers or mean residual `> 5 um`),
> - the `--warp` matrix does not match the alignment report (a stale/hand-built
>   transform), or
> - the warped flakes land outside the L5 marker field.
>
> **If the gate fails, FIX the alignment 鈥?do not bypass it.** Almost always the
> cause is a wrong `pixel_size`: re-run `validate_pixel_size`, then
> `detect_markers` 鈫?`align_gds`, and commit the resulting `gds_warp.npy`. Only
> pass `--skip-registration-check` if you are deliberately committing a
> hand-built transform and understand it will likely be off-frame.

---

## Acceptance Thresholds

| Metric | Pass | Fail |
|--------|------|------|
| Markers detected | >= 3 | < 3 |
| Inliers (align_gds) | >= 2 | < 2 |
| Mean residual | < 5.0 um | >= 5.0 um |

**Fail on any metric**: Do NOT commit to KLayout. Check diagnostic images and retry.

## Known Behaviour

- **Rotational ambiguity**: Square marker grids have rotational symmetry 鈥?胃, 胃+90掳, 胃+180掳, 胃+270掳 can yield similar residuals. `align_gds.py` automatically tries all four companions and picks the one with rotation closest to 0掳.
- **PNG orientation**: The warped PNG (`full_stack_gds.png`) is vertically flipped on save so that row 0 holds GDS-north data. KLayout's `pya.Image` renders PNG row 0 at the high-Y edge of the placement bbox, so the image ends up right-side-up when placed at `origin_um = (x_min, y_min)` with identity rotation. If you inspect the PNG in a file viewer it will already look correct (north at top).
- **Image 鈫?contour consistency**: The warped image and the transformed contours (`traces_gds.json`) are produced from the same affine. If you re-run the alignment at a different `pixel_size`, you **must** also re-run `commit_gds.py` with the same `pixel_size`. Mixing a `gds_warp.npy` produced at one pixel size with `commit_gds.py --pixel-size` at another will place the image at the wrong scale (the scale factor in the warp matrix compensates for the pixel_size used during alignment).
- **Destructive commit**: `commit_gds.py` calls `layout.clear()` before loading the template into KLayout, so any pre-existing geometry in the current tab is wiped. This is intentional 鈥?it avoids cell-name collisions in `Layout.read()` that otherwise leave orphaned cell indices. Run `commit_gds.py` against a fresh tab (or use the `create_layout` MCP tool first) if you have ongoing work you don't want to lose.

---

## Pixel Size Validation

The `pixel_size` parameter (um/px) is critical for correct template rendering in `detect_markers.py` and image placement in `commit_gds.py`. Always validate the pixel size before running the pipeline.

**Common pixel_size values by objective magnification:**

| Objective | Typical pixel_size (um/px) |
|-----------|---------------------------|
| 100x | 0.05 |
| 100x (alt) | 0.087 |
| 50x | 0.1 |
| 20x | 0.25 |
| 10x | 0.5 |

Use `validate_pixel_size` to confirm the value before running the alignment pipeline. If the pixel size is wrong, marker detection will fail or produce incorrect transforms.

---

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Error (missing file, insufficient markers, transform failure) |
| 3 | `commit_gds.py` registration self-validation failed (skipped/hand-rolled/off-frame transform) 鈥?fix the alignment, do not bypass |
