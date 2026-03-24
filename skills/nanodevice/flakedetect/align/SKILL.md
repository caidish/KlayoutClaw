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
   → View 01_source_contour.png: contour must trace the full flake boundary.

3. Run footprint.py [--mirror]
   → *** CRITICAL: Verify footprint before proceeding ***
   → View 03_footprint_candidates.png — it shows multiple candidates side by side.
   → Compare EACH candidate against the source contour shape from step 2.
   → The default candidate (#1) is often WRONG — it may grab debris/satellite
     flakes instead of the PDMS stamp. Candidates #2 or #3 are often better.
   → If 04_footprint_grabcut.png does NOT match the source flake shape:
     Re-run with --candidate-rank 2 (or 3). Do NOT proceed with a bad footprint.
   → **Do NOT rely on shape_distance alone to pick candidates.** A candidate with
     slightly worse shape_distance may produce much better IoU after sweep+refine
     because the sweep optimizes position, rotation, AND scale. When all candidates
     have shape_distance > 0.5 (none clearly good), run sweep+refine on at least
     the top 2 candidates and compare final IoU.

4. Run sweep.py
   → Produces candidate overlay images

5. *** PAUSE: Select rotation ***
   View 05_sweep_grid.png and individual candidate_NN.png files.
   Pick the candidate where the contour best matches the flake.
   IGNORE cost ranking — the lowest cost is often wrong.

6. Run refine.py --rot-hint <degrees>
   → **Runtime**: refine.py takes 10-15 minutes on 2-CPU machines (differential
     evolution optimizer).
     **MANDATORY EXECUTION METHOD**: Run refine.py as a FOREGROUND BLOCKING
     command with a long timeout. Use the Bash tool with timeout=1200000
     (20 minutes). Example:
       Bash(command="conda run -n base python .../refine.py ...", timeout=1200000)
     Do NOT use run_in_background=true. Do NOT launch it as a background
     process with &. Do NOT poll with sleep loops. Do NOT check for output
     files in a loop. Just run the single blocking command and wait for it
     to return. The Bash tool will hold until the process exits or the
     timeout is reached.
   → Check metrics against acceptance thresholds (see below)
   → If accepted: DONE. warp_top.npy is ready.
   → If FAILED: Go to step 7.

7. *** RETRY LOOP (max 2 retries) ***
   NEVER retry refine.py with the same footprint. Fix the INPUT first.

   Retry 1: Re-run footprint.py with --candidate-rank 2
            → then sweep.py → select rotation → refine.py

   Retry 2: Re-run footprint.py with --candidate-rank 3 or --n-clusters 24
            → then sweep.py → select rotation → refine.py

   If still failing after 2 retries → STOP. Report failure.
```

> **IMPORTANT**: Never retry refine.py more than once with the same footprint. If refine fails, the problem is the footprint or rotation selection, not refine's optimizer. Go back to step 3 and try a different `--candidate-rank`.

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
| Footprint grabs entire flake assembly + debris/satellite flakes | Default candidate (#1) picked up too much | **Re-run with `--candidate-rank 2`** (or 3). Always check `03_footprint_candidates.png` first — a better candidate likely exists |
| Footprint too large (includes bottom hBN) | Wrong clusters selected | Re-run with `--n-clusters 20`, `--n-clusters 24` for finer segmentation |
| Footprint too small (misses edges) | GrabCut too aggressive | Re-run with `--candidate-rank 2` or `--candidate-rank 3` |
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

> **Rule**: NEVER retry refine.py with the same footprint. If refine fails, fix the footprint first.
> **Time budget**: Each refine.py attempt takes 10-15 min. Budget max 2 full attempts (footprint→sweep→refine). If 2 attempts fail and the best IoU is above 0.5, accept it and proceed — an imperfect alignment that lets you complete the pipeline is better than a perfect alignment that times out.
> **Execution reminder**: ALWAYS run refine.py as a foreground blocking Bash command with timeout=1200000. NEVER use run_in_background or sleep/poll loops.

```
Attempt 1: footprint (default) → sweep → select rotation → refine
  → If refine FAILS (IoU < 0.50):
Attempt 2: footprint --candidate-rank 2 → sweep → select rotation → refine
  → If still FAILS (IoU < 0.50):
Accept the best result from attempts 1-2 and proceed. Do NOT run a 3rd refine.
Max refine.py invocations: 2. Each takes 10-15 min — 3 would consume 45 min.
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
- `--candidate-rank N` — use the Nth-ranked candidate instead of the default (#1). **Always check `03_footprint_candidates.png`** — candidate #1 is often wrong. Try `--candidate-rank 2` or `--candidate-rank 3` on retry.

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
