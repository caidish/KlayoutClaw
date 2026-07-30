---
name: nanodevice_flakedetect
description: Use when detecting flakes from multiple microscope images, aligning cross-substrate van der Waals stacks, segmenting hBN/graphene/graphite, committing polygons to KLayout, aligning detections to a GDS template, or producing scored result.gds outputs.
---

# nanodevice_flakedetect 鈥?Stack Detection Pipeline

Detect material boundaries in van der Waals heterostructure stacks from optical microscope images and commit them as polygons to KLayout.

**This is an orchestration skill.** You run it by dispatching subagents for each step. Each step has its own sub-skill SKILL.md with full instructions 鈥?the subagent reads that skill and executes autonomously.

## Before You Start

### Gather information from the user

You need these before dispatching any subagent:

1. **Source images** 鈥?Ask the user for paths to:
   - `bottom_part` 鈥?bottom hBN flake on SiO2 (before transfer)
   - `top_part` 鈥?top flake on PDMS (before transfer)
   - `full_stack_raw` 鈥?assembled stack on SiO2
   - `full_stack_lut` 鈥?(optional) color-enhanced version of full_stack
2. **Pixel size** 鈥?microns per pixel (e.g., 0.087 for 100x objective). Ask the user or check the image metadata.
3. **Mirror** 鈥?Does the top_part need mirroring? Yes if it was transferred from PDMS (the transfer flips it horizontally). Ask the user if unsure.
4. **Output directory** 鈥?Where to write results. **If the user does not specify an output path, default to `<stack_image_dir>/output/`** (the directory containing the source images). Never use `/tmp` as the default output.

### Set up the output directory

```
<out>/
鈹溾攢鈹€ align/    鈫?warp matrices, footprint, alignment diagnostics
鈹溾攢鈹€ detect/   鈫?per-material masks, contours, detection diagnostics
鈹斺攢鈹€ combine/  鈫?traces.json, overlay images, combine_report.json
```

Commit and review don't write to disk 鈥?they use KLayout directly.

### First Full Task Contract

Use this contract for a first complete flake-detection task. A first run is
not a reuse-align rerun: start from source images and produce a fresh complete
result in a new output root.

When the user says "full rerun", "complete rerun", or asks to rerun a sample
"including align", treat it as this same full task contract for that sample:
delete or overwrite that sample's old `align/`, `detect/`, `combine/`,
`gdsalign/`, `output/`, prompt-rank records, summaries, scores, and result
files before starting. Recreate the manual prompt JSON files as part of the
detect step after inspecting the fresh on-grid source images; do not treat old
prompt files or old rank selections as outside the rerun.

1. Create or choose a fresh `<out>/` for this task. Do not reuse old `detect/`,
   `combine/`, `gdsalign/`, `output/`, prompt JSON, rank JSON, scores, or
   result files from another run.
2. Run `align/` from the raw source images before detection. The only time an
   existing `align/` may be reused is when the user explicitly asks for a
   reuse-align rerun.
3. Detect tasks default to the SAM wrapper flow for graphite and graphene. The
   baseline detectors still run inside that flow to create source grids,
   baseline/refined fallback candidate 09, and non-SAM material outputs, but an
   agent must not treat the baseline-only graphite/graphene masks as complete.
   Graphite and graphene both require manual grid-first prompt selection:
   - Graphite: inspect `graphite_source_grid_80px.png` and `bottom_part.jpg`,
     search for the graphite/backgate candidate inside the bottom hBN / host
     hBN first. Do not reject a target merely because it is visually within a
     blue/cyan hBN mask or overlay area: that color is a display cue, not
    evidence against graphite. The real backgate may be the relatively darker,
    continuous slender vertical strip surrounded by or embedded in that host
    hBN; do not broaden this rule into a generic dark region. Write
     `graphite_manual_prompts.json`, rerun with
     `--manual-prompts-json ... --use-sam2`, inspect candidate images, then
     rerun with the visually selected `--prompt-rank`.
   - Graphene: use the same grid-first manual prompt loop as graphite. The SAM wrapper
     forces `<detect_dir>/../align/footprint_mask.png` for the baseline/grid pass,
     SAM candidate generation, and final `--prompt-rank` rerun. Inspect
     the mirrored `graphene_source_grid_80px.png` and mirrored `top_part`
     image, write `graphene_manual_prompts.json`, rerun with
     `--manual-prompts-json ... --use-sam2`, inspect only the fresh
     `graphene_candidate_##_on_grid.png` images for visual selection, then
     rerun with the visually selected `--prompt-rank`. `00_graphene_candidates`
     and automatic cluster/score outputs are baseline diagnostics, not a
     substitute for manual on-grid point selection. For graphene, cover only
     the intended local connected block, or very nearby graphene pieces with
     visible continuity/overlap evidence. Do not include distant graphene-like
     pieces just because their color/brightness is similar; if a clear gap or
     intervening hBN/top-flake material separates them, use negatives to keep
     the far block out. Judge each side independently: do not suppress a side
     where coherent graphene reaches a real flake edge, and preserve a
     continuous similar-color/brightness layered region without negatives
     between it and the target. If that continuation gradually loses layered
     contrast and becomes uniform hBN, place negatives immediately on the hBN
     side of the local transition. Saturated white / high-exposure glare
     regions are invalid graphene targets regardless of size or connectivity;
     treat them as negative regions and do not choose a candidate whose main
     area is a washed-out white/pink reflection.
4. Do not assemble `detections.json`, run combine, run gdsalign, write
   `output/result.gds`, or report a score until both graphite and graphene have
   fresh manual prompt JSON files, fresh on-grid candidate images, and recorded
   visual rank choices from those candidate images.
5. After detect is complete, run downstream stages from the fresh outputs:
   `combine/`, then `gdsalign/` when a GDS template is supplied, then write the
   required `output/` artifacts and run the scorer/evaluator when the task
   provides one.
6. Record the chosen graphite and graphene prompt ranks and visual reasons in
   the run notes/log so the result is reproducible. Record baseline cluster IDs
   only when a non-manual baseline diagnostic was intentionally used.

### Candidate Selection Rule

Whenever any step writes candidate outputs (`candidate_*.png`,
`*_candidates.png`, `*_candidate_*.png`, `*_candidates.json`, prompt-candidate
JSON, or a ranked candidate panel), Codex/Claude/Qlaybot or another capable
agent must inspect the candidate images or read the candidate metadata and
explicitly choose one. Do not silently accept rank 0, the lowest cost, or the
highest numeric score when candidate diagnostics exist. Record the chosen
rank/ID through the relevant flag (`--cluster-id`, `--prompt-rank`,
`--rot-hint`, `--candidate-rank`) or in the run notes/log.

For SAM/manual graphite and graphene runs, candidate overlays must use red for
the mask, green for positive prompt points, and orange for negative prompt
points. Candidate 09/rank 8 is the baseline/refined fallback, not a SAM prompt
result. Review all candidates using the same visual evidence standard; choose
rank 8 when it is visually the best mask for the target material, and record
the reason explicitly.

For graphite/backgate visual choice, never reason from overlay color alone:
blue/cyan hBN mask does not mean "not graphite". The target should be judged by
the raw microscope structure: a relatively darker, continuous slender vertical
or strip-like object physically surrounded by/inside the host hBN is the
preferred graphite/backgate hypothesis over isolated outside dark fragments or
broad dark regions.

---

## Pipeline Workflow

```
1. align   鈹€鈹€鈫?2. detect  鈹€鈹€鈫?3. combine  鈹€鈹€鈫?4. commit  鈹€鈹€鈫?5. review
   鈹?               鈹?             鈹?              鈹?             鈹?   鈹?subagent       鈹?subagent     鈹?subagent       鈹?subagent     鈹?subagent
   鈹?reads align/   鈹?reads detect/鈹?reads combine/ 鈹?reads commit/鈹?reads review/
   鈹?SKILL.md       鈹?SKILL.md     鈹?SKILL.md       鈹?SKILL.md     鈹?SKILL.md
   鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹粹攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹粹攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹粹攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?```

**Each step is executed by a subagent.** You (the orchestrator) dispatch subagents sequentially, passing the required context (image paths, pixel size, output dir) to each one. Wait for each subagent to complete before dispatching the next 鈥?each step depends on the previous step's outputs.

---

For first full tasks that include a GDS template, `output/result.gds`, or a
score/evaluator requirement, Step 3b is part of the required pipeline and runs
after combine before final commit/review/reporting.

### Step 1: align

**Goal:** Register all source images to the full_stack coordinate system.

**Dispatch a subagent** with this prompt:
> Read `skills/nanodevice_flakedetect_align/SKILL.md` and follow its workflow. Register the source images to the full_stack coordinate system.
> - bottom_part: `<path>` (same-substrate, use SIFT)
> - top_part: `<path>` (cross-substrate, use Chamfer pipeline, `--mirror`)
> - full_stack_raw: `<path>`
> - pixel_size: `<value>` um/px
> - output_dir: `<out>/align`

**What the subagent does:** Runs SIFT for bottom_part, runs the full Chamfer pipeline (source_contour 鈫?footprint 鈫?sweep 鈫?pick rotation 鈫?refine) for top_part. Makes its own rotation selection decision by viewing candidate images. If source_mask and footprint_mask are different visible crops and normal footprint/refine attempts fail, the subagent MUST run `partial_fov_edge_align.py --write-warp-top` and use its replacement `warp_top.npy`; it must not accept the best bad full-mask refine result or continue downstream with that bad warp. **IMPORTANT**: refine.py takes 10-15 min 鈥?the subagent MUST run it as a foreground blocking Bash command with timeout=1200000. It must NOT use run_in_background or sleep/poll loops.

**What it produces:** `warp_sift_bottom.npy`, `warp_top.npy`, `footprint_mask.png`, `footprint_contour.npy`, `alignment_report.json`

**Before moving on:** Check `alignment_report.json` status is `"complete"`. If SIFT inliers < 20 or Chamfer IoU < 0.5, the subagent should have flagged the issue. If the issue is source/footprint visible-crop mismatch, `partial_fov_edge_align.py --write-warp-top` is mandatory before moving on.

---

### Step 2: detect

**Goal:** Segment each material from its optimal source image.

**Dispatch a subagent** with this prompt:
> Read `skills/nanodevice_flakedetect_detect/SKILL.md` and `skills/nanodevice_flakedetect_sam/SKILL.md`, then use the SAM wrapper workflow for graphite and graphene by default. Detect all 4 materials and assemble `detections.json`.
> - bottom_part: `<path>` (for graphite)
> - top_part: `<path>` (for graphene, `--mirror`)
> - full_stack_raw: `<path>` (for bottom_hBN)
> - footprint_mask: `<out>/align/footprint_mask.png` (required for graphene, top_hBN, and bottom_hBN)
> - footprint_contour: `<out>/align/footprint_contour.npy` (for top_hBN)
> - pixel_size: `<value>` um/px
> - output_dir: `<out>/detect`

**What the subagent does:** Runs all 4 detect scripts. For a first full detect
task, graphite and graphene use the SAM wrapper grid-first manual prompt flow:
run the baseline/grid pass, inspect the
source grid images, write `graphite_manual_prompts.json` and
`graphene_manual_prompts.json`, rerun graphene with `--use-sam2`; the wrapper forces `<detect_dir>/../align/footprint_mask.png`. Visually inspect the
candidate images, record the chosen ranks, and rerun with explicit
`--prompt-rank` before assembling `detections.json`; the wrapper keeps the footprint fixed. Candidate 09 remains the
baseline/refined fallback inside the SAM candidate set; choose it by the same
visual-review rules as the SAM candidates and record the reason.
For bottom_hBN, inspect `low_confidence` / `winner_score` in the result JSON
and escalate to vision-review when those signal a poor pick.

**What it produces:** Per-material masks/contours/result.json files, `detections.json`

**Before moving on:** Verify `detections.json` exists and has entries for all 4 materials.

---

### Step 3: combine

**Goal:** Transform all detections into full_stack coordinates and produce `traces.json`.

**Dispatch a subagent** with this prompt:
> Read `skills/nanodevice_flakedetect_combine/SKILL.md` and follow its workflow. Transform detections and produce overlay images.
> - full_stack_raw: `<path>`
> - full_stack_lut: `<path>` (or "not available")
> - detections: `<out>/detect/detections.json`
> - align_dir: `<out>/align`
> - pixel_size: `<value>` um/px
> - output_dir: `<out>/combine`

**What the subagent does:** Runs ecc_register (if LUT available), transform, and overlay scripts in order. Fully automatic 鈥?no agent decisions.

**What it produces:** `traces.json`, `combine_report.json`, `overlay_raw.png`, `overlay_lut.png`, `mask_composite.png`

**Before moving on:** Read `overlay_raw.png` to visually confirm all 4 materials are present and properly aligned.

---

### Step 3b: gdsalign and output

**Goal:** Map the combined traces into the GDS template frame and write task
outputs when the task provides a template GDS, requires `output/result.gds`, or
will be scored by an evaluator.

Skip this step only when the user is doing a microscope-image/KLayout review
with no GDS template and no scoring/output requirement.

**Dispatch a subagent** with this prompt:
> Read `skills/nanodevice_gdsalign/SKILL.md` and follow its workflow. Align the microscope stack to the GDS template, commit traces in the GDS frame, and write the required output artifacts.
> - template_gds: `<Template.gds>` (or the task-provided GDS)
> - stack_image: `<full_stack_raw or full_stack_lut path>`
> - traces: `<out>/combine/traces.json`
> - output_dir: `<out>/gdsalign`
> - final_output_dir: `<out>/output`

**What the subagent does:** Extracts GDS markers, detects image markers,
computes the image-to-GDS transform, writes GDS-frame traces, commits polygons
to the template frame, and saves `output/result.gds` plus any task-required
`result.json` or score/log files. It must not use stale `gdsalign/` or
`output/` artifacts from a previous run.

**What it produces:** GDS alignment reports, `traces_gds.json` when supported,
`output/result.gds`, and scorer/evaluator artifacts when the task provides an
evaluator.

**Before moving on:** Verify `output/result.gds` exists and has nonzero size
when the task requires a GDS result. If a scorer is available, run it and record
the score.

---

### Step 4: commit

**Goal:** Insert the detected material polygons into KLayout.

**Dispatch a subagent** with this prompt:
> Read `skills/nanodevice_flakedetect_commit/SKILL.md` and follow its workflow. Commit the traces to KLayout.
> - traces: `<out>/gdsalign/traces_gds.json` if Step 3b ran and produced it; otherwise `<out>/combine/traces.json`
> - full_stack_raw: `<path>` (for background image)
> - pixel_size: `<value>` um/px

**What the subagent does:** Loads the background image and adds polygons using `execute_script` (NOT `add_image.py` or `add_polygon.py` 鈥?those scripts use MCP client which doesn't work from Docker containers). Reads traces.json, transforms coordinates (image-origin 鈫?KLayout centered with Y-flip), and inserts polygons directly via pya API. Takes a screenshot to verify.

**What it produces:** Polygons on layers 10/0-13/0 in KLayout, background image loaded.

**Before moving on:** View the screenshot. Polygons should be visible on the correct layers overlaid on the microscope image.

---

### Step 5: review

**Goal:** Validate that the committed polygons are correct.

**Dispatch a subagent** with this prompt:
> Read `skills/nanodevice_flakedetect_review/SKILL.md` and follow its workflow. Review the committed polygons in KLayout.
> - traces: `<out>/gdsalign/traces_gds.json` if Step 3b ran and produced it; otherwise `<out>/combine/traces.json`
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
| Wrong material detected (graphene) | Step 2 (detect) | Adjust cluster selection with `--cluster-id` |
| Wrong material detected (graphite / bottom_hBN) | Step 2 (detect) | Inspect `winner_score` + `low_confidence` in result JSON; spawn vision-review or refit priors. The C3 ensemble has no `--cluster-id` knob. |
| Polygons flipped or offset | Step 4 (commit) | Check coordinate transform formula |

**Max 2 retries per stage.** If still failing after 2 retries, report to the user for manual intervention.

---

## Conventions

- **Conda env:** `instrMCPdev` (has opencv, numpy, scipy, sklearn)
- **Contour .npy format:** shape (N,2), dtype float64
- **Masks:** uint8, values 0 or 255
- **Warp matrices:** 2x3 float64 for cv2.warpAffine
- **Mirror:** `--mirror` flag for PDMS transfers; applies `cv2.flip(image, 1)` before processing
- **Script errors:** exit 0 = success, exit 1 = failure, `ERROR:` printed to stderr
