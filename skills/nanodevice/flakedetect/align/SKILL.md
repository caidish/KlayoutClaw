---
name: nanodevice:flakedetect:align
description: Register source microscope images to the full_stack coordinate system using SIFT (same-substrate) or Chamfer+DE (cross-substrate) alignment. Use when aligning bottom_part, top_part, or other source images to the full_stack reference image for van der Waals stack detection.
---

# nanodevice:flakedetect:align — Image Alignment

Register source microscope images to the full_stack target coordinate system.

- **SIFT path**: Same-substrate images (e.g., bottom_part → full_stack). Fast, automatic.
- **Chamfer path**: Cross-substrate images (e.g., top_part on PDMS → full_stack on SiO2). Requires agent rotation selection.

## Prerequisites

- Conda env `base` with opencv, numpy, scipy, scikit-learn
- Source images and full_stack reference image
- All scripts: `conda run -n base python <script>`

---

## Agent Workflow

Runs **fully autonomously** except for one mandatory pause: **rotation selection** after the coarse sweep.

### Step-by-Step

```
1. Determine alignment type
   ├─ Same-substrate? → Run sift_align.py → DONE (if ≥20 inliers)
   └─ Cross-substrate? → Continue to step 2

2. Run source_contour.py [--mirror]
   → Check: Does the contour in 01_source_contour.png trace the flake?

3. Run footprint.py [--mirror]
   → Check: Does 04_footprint_grabcut.png match the flake shape from step 2?

4. Run sweep.py
   → Produces candidate overlay images

5. *** PAUSE: Select rotation ***
   View 05_sweep_grid.png and individual candidate_NN.png files.
   Pick the candidate where the contour best matches the flake.
   IGNORE cost ranking — the lowest cost is often wrong.

6. Run refine.py --rot-hint <degrees>
   → Check metrics against acceptance thresholds (see below)
   → If accepted: DONE. warp_top.npy is ready.
   → If rejected: see "Adjusting Parameters" below.
```

---

## Acceptance Thresholds (refine.py)

Auto-accept when ALL pass:

| Metric | Pass | Borderline | Fail |
|--------|------|------------|------|
| fwd_chamfer_mean | < 2.5 um | 2.5-4.0 um | > 4.0 um |
| IoU | > 0.70 | 0.50-0.70 | < 0.50 |
| top_containment | > 0.90 | 0.80-0.90 | < 0.80 |
| outside_fraction | < 0.10 | 0.10-0.20 | > 0.20 |

**Borderline**: Accept but log a warning. Check diagnostic images.
**Fail on any metric**: Do NOT accept. Adjust parameters and retry.

---

## Adjusting Parameters: Feedback → Action

This is the core skill — reading diagnostic outputs and knowing which knob to turn.

### After source_contour.py

**Goal**: The contour must capture the **entire largest bright region** — the full flake outline, including any very bright sub-regions (reflections, thin areas). A contour that misses the bright center but traces only the dim edges is wrong.

**Common failure**: Otsu auto-threshold can split the flake into "bright" and "very bright" regions, discarding the very bright part. In 01_source_contour.png, look for holes or missing chunks in the center of the flake — that means the threshold excluded the brightest pixels.

| What you see in 01_source_contour.png | What's wrong | Action |
|---------------------------------------|-------------|--------|
| Contour traces the full flake boundary | Nothing | Proceed |
| Contour has a hole or missing center (very bright area excluded) | Otsu split the flake — bright part was thresholded out | Re-run with `--gray-only` to skip saturation threshold |
| Contour is too small / misses edges | Threshold too aggressive | Check if the image is very dark or low-contrast |
| Contour includes substrate/debris | Threshold too loose | Usually means the flake isn't the largest bright region — check source image quality |
| No contour found (area=0) | Flake not detected | Image may need manual inspection; verify it's the right file |

### After footprint.py

| What you see in diagnostics | What's wrong | Action |
|-----------------------------|-------------|--------|
| 04_footprint_grabcut.png matches source flake shape | Nothing | Proceed |
| Footprint too large (includes bottom hBN) | Wrong clusters selected | Re-run with `--n-clusters 20`, `--n-clusters 24` for finer segmentation |
| Footprint too small (misses edges) | GrabCut too aggressive | Check 03_footprint_candidates.png — the #2 or #3 candidate may be better |
| Footprint is completely wrong shape | Shape matching failed | The source and target may look too different; check if `--mirror` is correct |
| shape_distance > 0.5 in stdout | Poor shape match | Continue anyway — GrabCut may still produce a usable footprint |

### After sweep.py — Choosing Rotation

| What you see in candidates | Guidance |
|---------------------------|----------|
| One candidate clearly matches | Use its rotation as --rot-hint |
| Two candidates look similar | Try the one where long edges align with visible flake edges |
| No candidate looks right | Footprint is likely wrong — go back to step 3 |
| Contour is right shape but shifted | Rotation is correct but translation is off — refine.py will fix this |

**Key judgment**: Look for **edge alignment**, not just overlap. The contour's straight edges should line up with the flake's crystallographic edges in the target image.

### After refine.py — When Metrics Fail

| Failed Metric | What it means | Adjustment |
|--------------|--------------|------------|
| outside_fraction > 0.20 | Warped flake extends beyond footprint | **Wrong rotation.** Try the next-best sweep candidate. |
| IoU < 0.4 | Poor overlap between masks | **Scale is wrong.** Add `--scale-hint` with a value from the sweep candidate, ±0.1. |
| fwd_chamfer > 5 um | Contour edges don't align | **Rotation off by a few degrees.** Widen: re-run with `--rot-hint ±5°` from current. |
| top_containment < 0.80 | Much of warped flake is outside footprint | **Footprint too small or rotation wrong.** Check 21_mask_overlap.png: blue regions = warped-only = problem areas. |
| All metrics fail badly | Fundamentally wrong alignment | **Start over.** Re-examine footprint, try different rotation candidate, or check if `--mirror` is correct. |

### Retry Strategy

```
Attempt 1: Run full pipeline with best sweep candidate
  → If FAIL on rotation metrics (outside_frac, containment):
Attempt 2: Re-run refine.py with 2nd-best sweep candidate rotation
  → If FAIL on scale metrics (IoU, chamfer):
Attempt 3: Re-run refine.py with --scale-hint from attempt 1's result ±0.1
  → If still FAIL:
Attempt 4: Re-run footprint.py with --n-clusters 10, then full pipeline
  → If still FAIL:
STOP: Report failure with diagnostic images for human review
Max attempts: 4
```

---

## Scripts Reference

### sift_align.py

```bash
conda run -n base python skills/nanodevice/flakedetect/align/scripts/sift_align.py \
    --source <image> --target <image> --pixel-size <um/px> --output-dir <path>
```

| Exit code | Meaning | Agent action |
|-----------|---------|-------------|
| 0, ≥50 inliers | Good alignment | Done. Use warp_sift_bottom.npy |
| 0, 20-49 inliers | Marginal alignment | Accept with warning. Check 01_sift_matches.png |
| 2 | Too few matches | Switch to Chamfer pipeline |
| 1 | Error | Check stderr |

**Outputs**: `warp_sift_bottom.npy`, `01_sift_matches.png`, `01_sift_overlay.png` (magenta-tinted warped source on desaturated target), updates `alignment_report.json`

### source_contour.py

```bash
conda run -n base python skills/nanodevice/flakedetect/align/scripts/source_contour.py \
    --image <image> [--mirror] [--gray-only] --output-dir <path>
```

Optional: `--gray-only` — use grayscale Otsu only, skip saturation intersection. **Use this when the flake has very bright/overexposed areas** that appear white (low saturation). Without this flag, bright areas are excluded by the saturation threshold.

**Outputs**: `source_contour.npy`, `source_mask.png`, `01_source_contour.png`, updates `alignment_report.json`

### footprint.py

```bash
conda run -n base python skills/nanodevice/flakedetect/align/scripts/footprint.py \
    --source <image> --target <image> [--mirror] \
    [--source-contour <out>/align/source_contour.npy] \
    [--source-mask <out>/align/source_mask.png] \
    --pixel-size <um/px> --output-dir <path>
```

Optional:
- `--source-contour` + `--source-mask` — use pre-computed contour/mask from source_contour.py instead of re-segmenting internally. **Recommended**: ensures footprint uses the same source shape as sweep/refine.
- `--n-clusters 16` (default; increase to 24 for finer segmentation on retry)

**Outputs**: `footprint_mask.png`, `footprint_contour.npy`, `02_cluster_map.png`, `03_footprint_candidates.png`, `04_footprint_grabcut.png`, updates `alignment_report.json`

### sweep.py

```bash
conda run -n base python skills/nanodevice/flakedetect/align/scripts/sweep.py \
    --source-contour <.npy> --source-mask <.png> \
    --footprint-contour <.npy> --footprint-mask <.png> \
    --target-image <image> --pixel-size <um/px> --output-dir <path>
```

**Outputs**: `candidate_01.png` ... `candidate_NN.png`, `05_sweep_grid.png`, updates `alignment_report.json` with `"status": "needs_rotation_selection"`

**Auto re-sweep**: If all top-8 candidates have scale < 0.75 (degenerate small-scale minimum), sweep.py automatically re-runs with scale floor raised to 0.75. This adds ~50s but avoids passing degenerate scales to refine.

### refine.py

```bash
conda run -n base python skills/nanodevice/flakedetect/align/scripts/refine.py \
    --source-contour <.npy> --source-mask <.png> \
    --footprint-contour <.npy> --footprint-mask <.png> \
    --target-image <image> \
    --rot-hint <degrees> [--scale-hint <value>] \
    --pixel-size <um/px> --output-dir <path>
```

**Auto scale hint**: When `--scale-hint` is omitted, refine.py reads `alignment_report.json` and uses the scale from the sweep candidate closest to `--rot-hint`. This constrains the search to ±0.1 around the sweep's estimate, avoiding the degenerate small-scale minimum.

**Outputs**: `warp_top.npy`, `20_best_overlay_raw.png`, `21_mask_overlap.png`, `22_chamfer_heatmap.png`, updates `alignment_report.json` with `"status": "complete"`

---

## Warp Matrix Convention

- **`warp_sift_bottom.npy`**: full_stack → bottom_part direction. Use `cv2.invertAffineTransform()` to go bottom_part → full_stack.
- **`warp_top.npy`**: source (top_part, possibly mirrored) → full_stack direction. Apply directly.
