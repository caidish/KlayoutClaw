---
name: nanodevice_flakedetect_align
description: Register source microscope images to the full_stack coordinate system using SIFT (same-substrate) or Chamfer+DE (cross-substrate) alignment. Use when aligning bottom_part, top_part, or other source images to the full_stack reference image for van der Waals stack detection.
---

# nanodevice_flakedetect_align 鈥?Image Alignment

Register source microscope images to the full_stack target coordinate system.

- **SIFT path**: Same-substrate images (e.g., bottom_part 鈫?full_stack). Fast, automatic.
- **Chamfer path**: Cross-substrate images (e.g., top_part on PDMS 鈫?full_stack on SiO2). Requires agent rotation selection.

## Prerequisites

- Conda env `instrMCPdev` with opencv, numpy, scipy, scikit-learn
- Source images and full_stack reference image
- All scripts: `${PYTHON_PATH:-conda run -n instrMCPdev python} <script>`

---

## Agent Workflow

Runs **fully autonomously** except for one mandatory pause: **rotation selection** after the coarse sweep.

### Step-by-Step

```
1. Determine alignment type
   鈹溾攢 Same-substrate? 鈫?Run sift_align.py 鈫?DONE (if 鈮?0 inliers)
   鈹斺攢 Cross-substrate? 鈫?Continue to step 2

2. Run source_contour.py [--mirror]
   鈫?View 01_source_contour.png: contour must trace the full flake boundary.

3. Run footprint.py [--mirror] [--bottom <bottom_part>]
   鈫?Use --bottom when bottom_part image is available (diff mode, preferred).
   鈫?In diff mode, footprint detection is performed on the LAB diff image between
     full_stack_raw and bottom_part warped into full_stack coordinates. The
     --target image still defines the full_stack coordinate system and overlay
     background.
   鈫?*** CRITICAL: Verify footprint before proceeding ***
   鈫?View 03_footprint_candidates.png 鈥?it shows multiple candidates side by side.
   鈫?Compare EACH candidate against the source contour shape from step 2.
   鈫?The default candidate (#1) is often WRONG 鈥?it may grab debris/satellite
     flakes instead of the PDMS stamp. Candidates #2 or #3 are often better.
   鈫?If 04_footprint_grabcut.png does NOT match the source flake shape:
     Re-run with --candidate-rank 2 (or 3). Do NOT proceed with a bad footprint.
   鈫?**Do NOT rely on shape_distance alone to pick candidates.** A candidate with
     slightly worse shape_distance may produce much better IoU after sweep+refine
     because the sweep optimizes position, rotation, AND scale. When all candidates
     have shape_distance > 0.5 (none clearly good), run sweep+refine on at least
     the top 2 candidates and compare final IoU.

4. Run sweep.py
   鈫?Produces candidate overlay images

5. *** PAUSE: Select rotation ***
   View 05_sweep_grid.png and individual candidate_NN.png files.
   Pick the candidate where the contour best matches the flake.
   IGNORE cost ranking 鈥?the lowest cost is often wrong.

6. Run refine.py --rot-hint <degrees>
   鈫?**Runtime**: refine.py takes 10-15 minutes on 2-CPU machines (differential
     evolution optimizer).
     **MANDATORY EXECUTION METHOD**: Run refine.py as a FOREGROUND BLOCKING
     command with a long timeout. Use the Bash tool with timeout=1200000
     (20 minutes). Example:
       Bash(command="${PYTHON_PATH:-conda run -n instrMCPdev python} .../refine.py ...", timeout=1200000)
     Do NOT use run_in_background=true. Do NOT launch it as a background
     process with &. Do NOT poll with sleep loops. Do NOT check for output
     files in a loop. Just run the single blocking command and wait for it
     to return. The Bash tool will hold until the process exits or the
     timeout is reached.
   鈫?Check metrics against acceptance thresholds (see below)
   鈫?If accepted: DONE. warp_top.npy is ready.
   鈫?If FAILED: Go to step 7.

7. *** RETRY LOOP (max 2 retries) ***
   NEVER retry refine.py with the same footprint. Fix the INPUT first.

   Retry 1: Re-run footprint.py with --candidate-rank 2
            鈫?then sweep.py 鈫?select rotation 鈫?refine.py

   Retry 2: Re-run footprint.py with --candidate-rank 3 or --n-clusters 24
            鈫?then sweep.py 鈫?select rotation 鈫?refine.py

   Partial-FOV fallback: If final IoU/outside_fraction are bad AND the chosen
            sweep/refine scale is far from 1, suspect that source_mask and
            footprint_mask are different visible crops of the same flake.
            This is a hard stop for the normal Chamfer path: do not keep
            forcing full-mask IoU, do not accept the best bad refine result,
            and do not continue downstream until `partial_fov_edge_align.py`
            has been run. Extract the longest visible non-border flake edges
            from source and footprint, find two intersecting long edges in
            each, build a partial V/corner shape, and re-align from that
            edge-corner geometry.

   Batch-run behavior: `run_flake_detect_batch.py` must run normal top
            alignment first. After `refine.py`, automatically inspect the
            top alignment metrics. If the result is clearly failed
            (IoU < 0.70, outside_fraction > 0.10, top_containment < 0.90,
            or scale outside [0.85, 1.15]), run
            `partial_fov_edge_align.py --write-warp-top` and use the generated
            long-edge/corner transform as the replacement `warp_top.npy`.
            Before replacement, preserve the normal refine warp as
            `warp_top_refine_before_partial_fov.npy`.

            The batch runner must not silently select the lowest-cost sweep
            candidate when running in visual mode. After `sweep.py`, if
            `align_visual_selection.json` is missing, stop the sample with
            status `needs_visual_selection` and write
            `align_visual_selection_required.json`. The agent must inspect
            `05_sweep_grid.png` and the individual `candidate_NN.png` images,
            then write `align_visual_selection.json` with either
            `candidate_rank` (one-based) or `rotation_deg`, plus a short
            visual reason. Only then may `refine.py` run.

   If still failing after 2 retries 鈫?STOP. Report failure.
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

## Adjusting Parameters: Feedback 鈫?Action

This is the core skill 鈥?reading diagnostic outputs and knowing which knob to turn.

### After source_contour.py

**Goal**: The contour must capture the **entire largest bright region** 鈥?the full flake outline, including any very bright sub-regions (reflections, thin areas). A contour that misses the bright center but traces only the dim edges is wrong.

**Common failure**: Otsu auto-threshold can split the flake into "bright" and "very bright" regions, discarding the very bright part. In 01_source_contour.png, look for holes or missing chunks in the center of the flake 鈥?that means the threshold excluded the brightest pixels.

| What you see in 01_source_contour.png | What's wrong | Action |
|---------------------------------------|-------------|--------|
| Contour traces the full flake boundary | Nothing | Proceed |
| Contour has a hole or missing center (very bright area excluded) | Otsu split the flake 鈥?bright part was thresholded out | Re-run with `--gray-only` to skip saturation threshold |
| Contour is too small / misses edges | Threshold too aggressive | Check if the image is very dark or low-contrast |
| Contour includes substrate/debris | Threshold too loose | Usually means the flake isn't the largest bright region 鈥?check source image quality |
| No contour found (area=0) | Flake not detected | Image may need manual inspection; verify it's the right file |

### After footprint.py

| What you see in diagnostics | What's wrong | Action |
|-----------------------------|-------------|--------|
| 04_footprint_grabcut.png matches source flake shape | Nothing | Proceed |
| Footprint grabs entire flake assembly + debris/satellite flakes | Default candidate (#1) picked up too much | **Re-run with `--candidate-rank 2`** (or 3). Always check `03_footprint_candidates.png` first 鈥?a better candidate likely exists |
| Footprint too large (includes bottom hBN) | Wrong clusters selected | Re-run with `--n-clusters 20`, `--n-clusters 24` for finer segmentation |
| Footprint too small (misses edges) | GrabCut too aggressive | Re-run with `--candidate-rank 2` or `--candidate-rank 3` |
| Footprint is completely wrong shape | Shape matching failed | The source and target may look too different; check if `--mirror` is correct |
| shape_distance > 0.5 in stdout | Poor shape match | Continue anyway 鈥?GrabCut may still produce a usable footprint |

### After sweep.py 鈥?Choosing Rotation

| What you see in candidates | Guidance |
|---------------------------|----------|
| One candidate clearly matches | Use its rotation as --rot-hint |
| Two candidates look similar | Try the one where long edges align with visible flake edges |
| No candidate looks right | Footprint is likely wrong 鈥?go back to step 3 |
| Contour is right shape but shifted | Rotation is correct but translation is off 鈥?refine.py will fix this |
| A low-cost candidate has scale < 0.75 while another candidate has scale near 1 and visually matches | Prefer the visually correct near-1 scale candidate; the cost includes a low-scale penalty, but agent visual judgment is still required |

**Key judgment**: Look for **edge alignment**, not just overlap. The contour's straight edges should line up with the flake's crystallographic edges in the target image.

### After refine.py 鈥?When Metrics Fail

| Failed Metric | What it means | Adjustment |
|--------------|--------------|------------|
| outside_fraction > 0.20 | Warped flake extends beyond footprint | **Wrong rotation.** Try the next-best sweep candidate. |
| IoU < 0.4 | Poor overlap between masks | **Scale is wrong.** Add `--scale-hint` with a value from the sweep candidate, 卤0.1. |
| fwd_chamfer > 5 um | Contour edges don't align | **Rotation off by a few degrees.** Widen: re-run with `--rot-hint 卤5掳` from current. |
| top_containment < 0.80 | Much of warped flake is outside footprint | **Footprint too small or rotation wrong.** Check 21_mask_overlap.png: blue regions = warped-only = problem areas. |
| IoU/outside fail badly and scale is far from 1 | Full source/footprint shapes are probably not comparable, often because one image has insufficient field of view or a cropped flake | Use the Partial-FOV Edge/Corner Fallback below instead of accepting a small-scale full-mask fit. |
| All metrics fail badly | Fundamentally wrong alignment | **Start over.** Re-examine footprint, try different rotation candidate, or check if `--mirror` is correct. |

### Partial-FOV Edge/Corner Fallback

This is a mandatory fallback, not an optional heuristic. Use
`partial_fov_edge_align.py` when the full source and footprint masks have
genuinely different visible shapes because the microscope field of view clipped
one image. When these conditions are met, the agent must run
`partial_fov_edge_align.py --write-warp-top` before accepting alignment or
continuing to detect/combine/GDS output. The trigger is the combination of poor
final metrics and an implausible scale:

- IoU is low or outside_fraction/top_containment is poor after refine.
- The selected sweep/refine scale is far from 1, especially a degenerate small
  scale that makes a partial region overlap.
- Visual inspection shows source_mask and footprint_mask are different crops
  of the same flake, not simply a bad footprint candidate.

Hard rule: if `source_mask.png` and `footprint_mask.png` are not comparable
full-flake masks after normal footprint retries, and `refine.py` cannot produce
acceptable metrics, run `partial_fov_edge_align.py`. Do not mark alignment
complete, do not use the low-quality `warp_top.npy`, and do not proceed
downstream until the partial-FOV method has produced a replacement
`warp_top.npy` or has failed with explicit diagnostic artifacts.

Fallback rule:

1. Do not fabricate a complete flake outline. Build a partial alignment shape
   only from edges that are visible in the current images.
2. In both `source_contour` and `footprint_contour`, extract long straight
   flake edges with polygon approximation, Hough, or RANSAC line fitting.
3. Reject or strongly downweight image-border and field-of-view crop edges.
   A straight line that coincides with the image boundary is not a flake edge.
4. Do not independently choose "the longest two source edges" and "the longest
   two footprint edges" and then force them to match. That often pairs different
   physical flake edges. Instead, enumerate multiple reliable long non-border
   edge pairs in both images, then jointly rank source/footprint pair matches by:
   similar included angle, consistent edge directions, scale close to 1, and
   mask/edge overlap after the induced transform.
5. Prefer crystallographic-looking edge pairs with stable angles and clear
   visual support in the raw/source image. If a numerically long segment is an
   internal intensity boundary, a crop boundary, a shadow, or a weak noisy line,
   downweight it even if its length is large.
   A simplified polygon edge is only reliable when enough original contour
   samples lie close to that segment along its length. Reject long chords that
   merely connect two contour vertices while crossing a concave gap, hole, or
   missing/cropped region.
6. Use the two edge directions to estimate rotation, the intersection point as
   the anchor for translation, and visible edge lengths only as a weak scale
   cue. Keep a strong prior that scale should be close to 1 unless the imaging
   setup truly changed.
7. Construct a local V/corner or wedge-shaped partial mask from those two
   visible edges, then re-run alignment from this edge-corner initialization.
   Judge success by edge/corner agreement and plausible scale, not by full-mask
   IoU alone.
8. If a single selected edge pair is unstable, enumerate all reliable long-edge
   pairs. Extend each pair to its intersection, infer the mask interior side for
   each line, and fill the resulting local wedge/half-plane intersection only to
   generate affine candidates. Do not select the final transform by partial
   wedge overlap. After each candidate affine transform is computed, warp the
   complete original source mask and score it against the complete original
   footprint mask using full IoU, outside fraction, containment, plausible scale,
   and contour edge agreement. Output the edge list, wedge candidates, match
   candidates, and selected overlay so the failure mode is visually inspectable.
9. After the matched vertex and edge directions are determined, refine with a
   vertex-anchored homothety/similarity transform: keep the vertex fixed, keep
   the rotation from the edge directions, and vary only the uniform scale
   (equivalently, the distance from the vertex to points along the edge). Do not
   introduce free translation, shear, or arbitrary affine distortion unless
   explicitly needed. Pick the scale where the complete original source mask has
   the best full-mask IoU, outside fraction, containment, and contour-edge
   distance against the complete original footprint mask. Output the scale search
   candidates and the final refined overlay.

This fallback is for partial field-of-view mismatch. If the bad metrics are due
to a wrong footprint candidate, wrong mirror setting, debris, or a satellite
flake, fix those inputs first instead of using edge/corner fallback.

### Retry Strategy

> **Rule**: NEVER retry refine.py with the same footprint. If refine fails, fix the footprint first.
> **Time budget**: Each refine.py attempt takes 10-15 min. Budget max 2 full attempts (footprint鈫抯weep鈫抮efine). If 2 attempts fail because source_mask and footprint_mask are different visible crops, run `partial_fov_edge_align.py --write-warp-top`; do not accept the best bad full-mask refine result.
> **Execution reminder**: ALWAYS run refine.py as a foreground blocking Bash command with timeout=1200000. NEVER use run_in_background or sleep/poll loops.

```
Attempt 1: footprint (default) 鈫?sweep 鈫?select rotation 鈫?refine
  鈫?If refine FAILS (IoU < 0.50):
Attempt 2: footprint --candidate-rank 2 鈫?sweep 鈫?select rotation 鈫?refine
  鈫?If still FAILS (IoU < 0.50):
If attempts 1-2 fail because source_mask and footprint_mask are different
visible crops, run `partial_fov_edge_align.py --write-warp-top` and use its
replacement `warp_top.npy`. Do NOT run a 3rd normal refine and do NOT continue
downstream with the best bad full-mask refine result.
Max refine.py invocations: 2. Each takes 10-15 min 鈥?3 would consume 45 min.
```

---

## Scripts Reference

### sift_align.py

```bash
${PYTHON_PATH:-conda run -n instrMCPdev python} skills/nanodevice_flakedetect_align/scripts/sift_align.py \
    --source <image> --target <image> --pixel-size <um/px> --output-dir <path> \
    [--min-inliers 20] [--scalebar-bottom 0.08] [--scalebar-right 0.20]
```

Optional:
- `--min-inliers N` 鈥?minimum RANSAC inliers for "sufficient" quality (default: 20). Thresholds: good 鈮?max(50, 2N), warning 鈮?N, insufficient < N. Lower to 10 for images with few substrate features.
- `--scalebar-bottom F` 鈥?fraction of image height to mask from bottom to exclude scalebar (default: 0.08). Set to 0 to disable.
- `--scalebar-right F` 鈥?fraction of image width to mask from right to exclude scalebar (default: 0.20). Set to 0 to disable.

| Exit code | Meaning | Agent action |
|-----------|---------|-------------|
| 0, 鈮?0 inliers | Good alignment | Done. Use warp_sift_bottom.npy |
| 0, 鈮in-inliers | Marginal alignment | Accept with warning. Check 01_sift_matches.png |
| 2 | Too few matches (<min-inliers) | Try `--min-inliers 10`. If still fails, switch to Chamfer pipeline |
| 1 | Error | Check stderr |

**Outputs**: `warp_sift_bottom.npy`, `01_sift_matches.png`, `01_sift_overlay.png` (magenta-tinted warped source on desaturated target), updates `alignment_report.json`

### source_contour.py

```bash
${PYTHON_PATH:-conda run -n instrMCPdev python} skills/nanodevice_flakedetect_align/scripts/source_contour.py \
    --image <image> [--mirror] [--gray-only] --output-dir <path>
```

Optional: `--gray-only` 鈥?use grayscale Otsu only, skip saturation intersection. **Use this when the flake has very bright/overexposed areas** that appear white (low saturation). Without this flag, bright areas are excluded by the saturation threshold.

**Outputs**: `source_contour.npy`, `source_mask.png`, `01_source_contour.png`, updates `alignment_report.json`

### footprint.py

SIFT-aligns bottom_part to target, computes a LAB diff image, and finds the footprint on that diff image with K-means on diff intensity. `--target <full_stack_raw>` defines the full_stack coordinate system and background image; in diff mode the actual segmentation target is the diff image made from full_stack_raw minus bottom_part warped into full_stack coordinates. After warping bottom_part to full_stack size, pixels not covered by the warped bottom image are filled with the mean BGR of low-saturation gray pixels from the four full_stack corners before LAB diff is computed. Do not fill non-overlap with black, top/source average, or bottom average. Splits disconnected blobs within clusters into sub-clusters before enumeration, so spatially separate flakes sharing the same intensity are treated independently.

```bash
${PYTHON_PATH:-conda run -n instrMCPdev python} skills/nanodevice_flakedetect_align/scripts/footprint.py \
    --source <top_part> --target <full_stack_raw> \
    --bottom <bottom_part> [--mirror] \
    [--source-contour <out>/align/source_contour.npy] \
    [--source-mask <out>/align/source_mask.png] \
    --pixel-size <um/px> --output-dir <path>
```

Optional:
- `--source-contour` + `--source-mask` 鈥?use pre-computed contour/mask from source_contour.py instead of re-segmenting internally. **Recommended**: ensures footprint uses the same source shape as sweep/refine.
- `--n-clusters N` 鈥?number of K-means clusters (default: 12; increase for finer segmentation on retry)
- `--candidate-rank N` 鈥?use the Nth-ranked candidate instead of the default (#1). **Always check `03_footprint_candidates.png`** 鈥?candidate #1 is often wrong. Try `--candidate-rank 2` or `--candidate-rank 3` on retry.
- `--warp <path-to-warp_sift_bottom.npy>` 鈥?reuse the SIFT warp produced by `sift_align.py` instead of re-running SIFT internally (issue #31). If omitted, auto-resolves `<output-dir>/warp_sift_bottom.npy` then `<source-parent>/../align/warp_sift_bottom.npy`. Internal SIFT only runs when neither path exists. Sibling scripts (`sweep.py`, `refine.py`, `source_contour.py`) do not run SIFT internally and need no equivalent flag.

**Outputs**: `footprint_mask.png`, `footprint_contour.npy`, `02_diff_image.png`, `02_cluster_map.png`, `03_footprint_candidates.png`, `04_footprint_grabcut.png`, updates `alignment_report.json`

### sweep.py

```bash
${PYTHON_PATH:-conda run -n instrMCPdev python} skills/nanodevice_flakedetect_align/scripts/sweep.py \
    --source-contour <.npy> --source-mask <.png> \
    --footprint-contour <.npy> --footprint-mask <.png> \
    --target-image <image> --pixel-size <um/px> --output-dir <path>
```

**Outputs**: `candidate_01.png` ... `candidate_NN.png`, `05_sweep_grid.png`, updates `alignment_report.json` with `"status": "needs_rotation_selection"`

**Auto re-sweep**: If all top-8 candidates have scale < 0.75 (degenerate small-scale minimum), sweep.py automatically re-runs with scale floor raised to 0.75. This adds ~50s but avoids passing degenerate scales to refine.

### refine.py

```bash
${PYTHON_PATH:-conda run -n instrMCPdev python} skills/nanodevice_flakedetect_align/scripts/refine.py \
    --source-contour <.npy> --source-mask <.png> \
    --footprint-contour <.npy> --footprint-mask <.png> \
    --target-image <image> \
    --rot-hint <degrees> [--scale-hint <value>] \
    --pixel-size <um/px> --output-dir <path>
```

**Auto scale hint**: When `--scale-hint` is omitted, refine.py reads `alignment_report.json` and uses the scale from the sweep candidate closest to `--rot-hint`. This constrains the search to 卤0.1 around the sweep's estimate, avoiding the degenerate small-scale minimum.

**Outputs**: `warp_top.npy`, `20_best_overlay_raw.png`, `21_mask_overlap.png`, `22_chamfer_heatmap.png`, updates `alignment_report.json` with `"status": "complete"`

---

## Warp Matrix Convention

- **`warp_sift_bottom.npy`**: full_stack 鈫?bottom_part direction. Use `cv2.invertAffineTransform()` to go bottom_part 鈫?full_stack.
- **`warp_top.npy`**: source (top_part, possibly mirrored) 鈫?full_stack direction. Apply directly.
