---
name: nanodevice_flakedetect_combine
description: Transform detection results into the common full_stack coordinate system, build unified traces.json, and draw overlay images. Use after align and detect steps are complete.
---

# nanodevice_flakedetect_combine 鈥?Coordinate Transforms & Overlays

Transforms all per-material detections into the full_stack coordinate system, produces the unified `traces.json`, and draws contour overlays on raw/LUT images.

## Prerequisites

- Conda env `instrMCPdev` with opencv, numpy, shapely (`rank_candidate_pairs.py` uses shapely)
- Completed align step (warp matrices, footprint mask)
- Completed detect step (per-material masks/contours, detections.json)
- All scripts run via `${PYTHON_PATH:-conda run -n instrMCPdev python} <script>`

## Scripts

### ecc_register.py 鈥?ECC raw-to-LUT translation alignment

```bash
${PYTHON_PATH:-conda run -n instrMCPdev python} skills/nanodevice_flakedetect_combine/scripts/ecc_register.py \
    --raw <full_stack_raw_image> \
    --lut <full_stack_lut_image> \
    --output-dir <path>
```

- `--raw` 鈥?Full stack raw image
- `--lut` 鈥?Full stack LUT (color-enhanced) image
- `--output-dir` 鈥?Output directory

Computes ECC translation alignment between raw and LUT images. The LUT image typically has a spatial offset (~71px dx, ~56px dy) from raw.

**Outputs:**
- Creates `combine_report.json` with `raw2lut` section: dx, dy, ecc_correlation

**Note:** If no LUT image is available, skip this script. The overlay script handles the absence gracefully.

### transform.py 鈥?Coordinate transforms for all materials

```bash
${PYTHON_PATH:-conda run -n instrMCPdev python} skills/nanodevice_flakedetect_combine/scripts/transform.py \
    --detections <detect/detections.json> \
    --align-dir <align/> \
    --image <full_stack_raw_image> \
    --pixel-size <um_per_px> \
    --output-dir <path> \
    [--min-area 500]
```

- `--detections` 鈥?Path to detections.json from detect step
- `--align-dir` 鈥?Directory containing warp matrices and footprint from align step
- `--image` 鈥?Full stack raw image (for size reference)
- `--pixel-size` 鈥?Microns per pixel
- `--output-dir` 鈥?Output directory
- `--min-area` 鈥?Minimum component area in pixels for `keep_largest_n` on the warped/clipped graphene mask. Default `500` px (鈮?5.6 碌m虏 at 0.106 碌m/px). Lower this when working at finer pixel sizes or with intentionally small flakes; raise it to reject more aggressive noise.

**Transform rules by material:**

| Material | Coordinate System | Transform |
|----------|------------------|-----------|
| graphite | bottom_part | Invert `warp_sift_bottom.npy`, apply to contour |
| graphene | top_part (mirrored) | Apply `warp_top.npy` to mask (INTER_NEAREST), clip to footprint, morph clean, re-extract contour |
| bottom_hBN | full_stack | Pass through (already in target coords) |
| top_hBN | full_stack | Pass through (= footprint) |

All materials get `smooth_material()` applied after transform.

**Input files read from `--align-dir`:**
- `warp_sift_bottom.npy` 鈥?inverted before use (bottom_part鈫抐ull_stack)
- `warp_top.npy` 鈥?applied directly (top_part鈫抐ull_stack)
- `footprint_mask.png` 鈥?for graphene clipping

**Outputs:**
- `traces.json` 鈥?unified traces with all contours in full_stack pixel coordinates
- `graphite_full.png`, `graphene_full.png`, `bottom_hbn_full.png`, `top_hbn_full.png` 鈥?transformed masks
- Appends `transform_summary` section to `combine_report.json`
- Appends `transform_diagnostics` section to `combine_report.json` 鈥?per-material per-stage pixel counts (see below)

**Per-stage diagnostics (`transform_diagnostics`):**

Every material reports its pixel count at each pipeline stage so the orchestrator can tell the difference between "detect produced nothing" and "transform zeroed a valid input at stage X". Stage keys vary by material:

| Material | Stage keys |
|----------|------------|
| graphite | `input_pixels`, `post_keep_largest_pixels` |
| graphene | `input_pixels`, `post_warp_pixels`, `post_bitwise_and_pixels`, `post_morph_pixels`, `post_keep_largest_pixels` |
| bottom_hBN, top_hBN | `input_pixels`, `post_keep_largest_pixels` |

Example success entry:
```json
"transform_diagnostics": {
  "graphene": {
    "input_pixels": 18234,
    "post_warp_pixels": 17890,
    "post_bitwise_and_pixels": 12044,
    "post_morph_pixels": 11820,
    "post_keep_largest_pixels": 11820
  }
}
```

**Refusal on zeroed valid input:** if `input_pixels > 0` but the mask drops to zero pixels at any stage, `transform.py` exits non-zero (code 3) with the partial counts persisted to `combine_report.json` under both `transform_diagnostics.<material>` and a top-level `transform_error` block:

```json
"transform_error": {
  "material": "graphene",
  "dropped_at_stage": "bitwise_and",
  "stage_counts": { "input_pixels": 18234, "post_warp_pixels": 17890, "post_bitwise_and_pixels": 0 }
}
```

`dropped_at_stage` is one of `warp`, `bitwise_and`, `morph`, `keep_largest`. The orchestrator can then re-run detect with a different cluster choice or escalate to vision-review without re-deriving which stage failed.

This refusal composes with the alignment-status refusal: `transform.py` already exits with code 2 when `alignment_report.status` (or `footprint.status`) is `failed` / `needs_rotation_selection`. Per-stage diagnostics only run after that gate passes.

### overlay.py 鈥?Contour overlay visualization

```bash
${PYTHON_PATH:-conda run -n instrMCPdev python} skills/nanodevice_flakedetect_combine/scripts/overlay.py \
    --traces <combine/traces.json> \
    --raw <full_stack_raw_image> \
    [--lut <full_stack_lut_image>] \
    [--combine-report <combine/combine_report.json>] \
    --output-dir <path>
```

- `--traces` 鈥?Path to traces.json from transform step
- `--raw` 鈥?Full stack raw image
- `--lut` 鈥?(optional) Full stack LUT image
- `--combine-report` 鈥?(optional) Path to combine_report.json (for raw鈫扡UT shift)
- `--output-dir` 鈥?Output directory

Draws material contours on desaturated background images using the BGR color palette:
- top_hBN: green (0, 200, 0)
- graphene: red (0, 0, 255)
- bottom_hBN: blue-ish (255, 100, 0)
- graphite: yellow (0, 200, 255)

For LUT overlay: reads dx, dy from combine_report.json and shifts contours before drawing.

**Outputs:**
- `overlay_raw.png` 鈥?all material contours on raw image
- `overlay_lut.png` 鈥?all material contours on LUT image (if --lut provided)
- `mask_composite.png` 鈥?all material masks color-coded at 50% alpha
- Appends `overlay_files` section to `combine_report.json`

### rank_candidate_pairs.py 鈥?rank material pairs by overlap (default graphene 脳 graphite)

Ranks every pair across two detected material lists by intersection area. Useful whenever a downstream design step requires a material pair with non-trivial overlap and the rank-0 detections do not always satisfy that constraint. Defaults to `--material-a graphene --material-b graphite` for the vdW-Hall-bar workflow, but any two material keys that exist in `traces.json` will work (e.g. `--material-a top_hBN --material-b bottom_hBN` to rank encapsulation pairs).

```bash
${PYTHON_PATH:-conda run -n instrMCPdev python} skills/nanodevice_flakedetect_combine/scripts/rank_candidate_pairs.py \
    --traces <combine/traces.json> \
    [--output candidate_ranking.json] \
    [--top-k 5] \
    [--material-a graphene] [--material-b graphite]
```

- `--traces` 鈥?Path to traces.json (works on both pre- and post-gdsalign traces: uses `contour_gds` if present, falls back to `contour_um`)
- `--output` 鈥?Path to the ranking JSON (default: alongside traces.json)
- `--top-k` 鈥?How many top pairs to echo on stdout (default 5). The JSON file always contains all pairs.
- `--material-a`, `--material-b` 鈥?Material keys to pair (default graphene 脳 graphite)

Computes polygon intersection area between every (material_a, material_b) candidate pair, ranks by overlap area descending, and writes an ordered list.

**Outputs:**
- `candidate_ranking.json` 鈥?ordered list with fields: `{rank, graphene_id, graphite_id, overlap_um2, graphene_area_um2, graphite_area_um2, centroid_distance_um}`

**When to run:** When a first-pass design session discovers the auto-picked material-A 脳 material-B pair has `overlap_um2 鈮?0`. The ranking surfaces a pair with non-trivial overlap without brute-force iteration. (Observed failure mode: ml09 / ml11 benchmarks where default graphene 脳 graphite had no overlap.)

## Workflow

Run in order:
1. `ecc_register.py` (if LUT image available)
2. `transform.py`
3. `overlay.py`
4. `rank_candidate_pairs.py` 鈥?only when the auto-picked pair has zero overlap; otherwise skip.

The `combine_report.json` is a multi-writer file: ecc_register creates it, transform adds to it, overlay adds to it. Each script reads the existing file and appends its section. `rank_candidate_pairs.py` writes a separate `candidate_ranking.json` and does not touch `combine_report.json`.

## Output Files

All outputs go in the combine output directory:
- `traces.json` 鈥?the main pipeline output consumed by review and commit
- `combine_report.json` 鈥?metadata for diagnostics
- `candidate_ranking.json` 鈥?(optional) ranking of candidate material pairs
- Per-material transformed masks and overlay images
