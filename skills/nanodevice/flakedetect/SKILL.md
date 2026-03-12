---
name: nanodevice:flakedetect
description: Detect and map van der Waals heterostructure flake boundaries from microscope images into KLayout polygons. Split workflow with 5 sub-skills (align, detect, combine, commit, review) for agent-orchestrated stack detection. Use this skill when the user wants to detect flakes from multiple source images, align cross-substrate images, segment hBN/graphene/graphite stacks, or run the full stack detection pipeline.
---

# nanodevice:flakedetect — Stack Detection Pipeline

Detect material boundaries in van der Waals heterostructure stacks from optical microscope images and commit them as polygons to KLayout.

**This is an orchestration skill.** You run it by dispatching subagents for each step. Each step has its own sub-skill SKILL.md with full instructions — the subagent reads that skill and executes autonomously.

## Before You Start

### Gather information from the user

You need these before dispatching any subagent:

1. **Source images** — Ask the user for paths to:
   - `bottom_part` — bottom hBN flake on SiO2 (before transfer)
   - `top_part` — top flake on PDMS (before transfer)
   - `full_stack_raw` — assembled stack on SiO2
   - `full_stack_lut` — (optional) color-enhanced version of full_stack
2. **Pixel size** — microns per pixel (e.g., 0.087 for 100x objective). Ask the user or check the image metadata.
3. **Mirror** — Does the top_part need mirroring? Yes if it was transferred from PDMS (the transfer flips it horizontally). Ask the user if unsure.
4. **Output directory** — Where to write results. A good default: `<image_dir>/output/`

### Set up the output directory

```
<out>/
├── align/    ← warp matrices, footprint, alignment diagnostics
├── detect/   ← per-material masks, contours, detection diagnostics
└── combine/  ← traces.json, overlay images, combine_report.json
```

Commit and review don't write to disk — they use KLayout directly.

---

## Pipeline Workflow

```
1. align   ──→ 2. detect  ──→ 3. combine  ──→ 4. commit  ──→ 5. review
   │                │              │               │              │
   │ subagent       │ subagent     │ subagent       │ subagent     │ subagent
   │ reads align/   │ reads detect/│ reads combine/ │ reads commit/│ reads review/
   │ SKILL.md       │ SKILL.md     │ SKILL.md       │ SKILL.md     │ SKILL.md
   └────────────────┴──────────────┴───────────────┴──────────────┘
```

**Each step is executed by a subagent.** You (the orchestrator) dispatch subagents sequentially, passing the required context (image paths, pixel size, output dir) to each one. Wait for each subagent to complete before dispatching the next — each step depends on the previous step's outputs.

---

### Step 1: align

**Goal:** Register all source images to the full_stack coordinate system.

**Dispatch a subagent** with this prompt:
> Read `skills/nanodevice/flakedetect/align/SKILL.md` and follow its workflow. Register the source images to the full_stack coordinate system.
> - bottom_part: `<path>` (same-substrate, use SIFT)
> - top_part: `<path>` (cross-substrate, use Chamfer pipeline, `--mirror`)
> - full_stack_raw: `<path>`
> - pixel_size: `<value>` um/px
> - output_dir: `<out>/align`

**What the subagent does:** Runs SIFT for bottom_part, runs the full Chamfer pipeline (source_contour → footprint → sweep → pick rotation → refine) for top_part. Makes its own rotation selection decision by viewing candidate images.

**What it produces:** `warp_sift_bottom.npy`, `warp_top.npy`, `footprint_mask.png`, `footprint_contour.npy`, `alignment_report.json`

**Before moving on:** Check `alignment_report.json` status is `"complete"`. If SIFT inliers < 20 or Chamfer IoU < 0.5, the subagent should have flagged the issue.

---

### Step 2: detect

**Goal:** Segment each material from its optimal source image.

**Dispatch a subagent** with this prompt:
> Read `skills/nanodevice/flakedetect/detect/SKILL.md` and follow its workflow. Detect all 4 materials and assemble `detections.json`.
> - bottom_part: `<path>` (for graphite)
> - top_part: `<path>` (for graphene, `--mirror`)
> - full_stack_raw: `<path>` (for bottom_hBN)
> - footprint_mask: `<out>/align/footprint_mask.png` (for top_hBN and bottom_hBN)
> - footprint_contour: `<out>/align/footprint_contour.npy` (for top_hBN)
> - pixel_size: `<value>` um/px
> - output_dir: `<out>/detect`

**What the subagent does:** Runs all 4 detect scripts, reviews candidate images for graphite/graphene, re-runs with `--cluster-id` if needed, assembles `detections.json`.

**What it produces:** Per-material masks/contours/result.json files, `detections.json`

**Before moving on:** Verify `detections.json` exists and has entries for all 4 materials.

---

### Step 3: combine

**Goal:** Transform all detections into full_stack coordinates and produce `traces.json`.

**Dispatch a subagent** with this prompt:
> Read `skills/nanodevice/flakedetect/combine/SKILL.md` and follow its workflow. Transform detections and produce overlay images.
> - full_stack_raw: `<path>`
> - full_stack_lut: `<path>` (or "not available")
> - detections: `<out>/detect/detections.json`
> - align_dir: `<out>/align`
> - pixel_size: `<value>` um/px
> - output_dir: `<out>/combine`

**What the subagent does:** Runs ecc_register (if LUT available), transform, and overlay scripts in order. Fully automatic — no agent decisions.

**What it produces:** `traces.json`, `combine_report.json`, `overlay_raw.png`, `overlay_lut.png`, `mask_composite.png`

**Before moving on:** Read `overlay_raw.png` to visually confirm all 4 materials are present and properly aligned.

---

### Step 4: commit

**Goal:** Insert the detected material polygons into KLayout.

**Dispatch a subagent** with this prompt:
> Read `skills/nanodevice/flakedetect/commit/SKILL.md` and follow its workflow. Commit the traces to KLayout.
> - traces: `<out>/combine/traces.json`
> - full_stack_raw: `<path>` (for background image)
> - pixel_size: `<value>` um/px

**What the subagent does:** Creates a layout, loads the background image (using the `image` skill), reads traces.json, transforms coordinates, and adds polygons (using the `geometry` skill's `add_polygon.py`). Takes a screenshot to verify.

**What it produces:** Polygons on layers 10/0-13/0 in KLayout, background image loaded.

**Before moving on:** View the screenshot. Polygons should be visible on the correct layers overlaid on the microscope image.

---

### Step 5: review

**Goal:** Validate that the committed polygons are correct.

**Dispatch a subagent** with this prompt:
> Read `skills/nanodevice/flakedetect/review/SKILL.md` and follow its workflow. Review the committed polygons in KLayout.
> - traces: `<out>/combine/traces.json`
> - overlay_raw: `<out>/combine/overlay_raw.png`
> - overlay_lut: `<out>/combine/overlay_lut.png` (or "not available")
> - alignment_report: `<out>/align/alignment_report.json`
> - combine_report: `<out>/combine/combine_report.json`

**What the subagent does:** Takes screenshots, isolates layers (using the `display` skill), compares KLayout polygons against overlay images, answers structured assessment questions, checks quantitative metrics, and returns a PASS/FAIL verdict.

**What it produces:** A verdict with reasoning.

**If PASS:** Stack detection complete. Report results to the user.

**If FAIL:** The subagent will specify which step to retry and what to change. Dispatch a new subagent for that step with adjusted parameters, then re-run all subsequent steps.

---

## Retry Protocol

| Problem | Retry from | What to change |
|---------|------------|----------------|
| Polygon boundaries don't match flake edges | Step 1 (align) | Try different rotation or wider scale range |
| Wrong material detected | Step 2 (detect) | Adjust cluster selection with `--cluster-id` |
| Polygons flipped or offset | Step 4 (commit) | Check coordinate transform formula |

**Max 2 retries per stage.** If still failing after 2 retries, report to the user for manual intervention.

---

## Conventions

- **Conda env:** `base` (has opencv, numpy, scipy, sklearn)
- **Contour .npy format:** shape (N,2), dtype float64
- **Masks:** uint8, values 0 or 255
- **Warp matrices:** 2x3 float64 for cv2.warpAffine
- **Mirror:** `--mirror` flag for PDMS transfers; applies `cv2.flip(image, 1)` before processing
- **Script errors:** exit 0 = success, exit 1 = failure, `ERROR:` printed to stderr
