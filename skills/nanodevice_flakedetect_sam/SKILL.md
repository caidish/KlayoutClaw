---
name: nanodevice_flakedetect_sam
description: SAM2-prompt assisted flake detection. Wraps the standard four flake detectors, emits prompt candidate images, optionally refines the selected candidate through local SAM2, and preserves the normal flakedetect output contract.
---

# nanodevice_flakedetect_sam - Prompt Candidate Detect

Use this skill when the user asks for SAM-assisted flake detection, candidate click/point images, or a detect skill derived from `nanodevice_flakedetect_detect` that lets Codex inspect candidates and choose one.

## Contract

The skill provides four detector scripts with the same CLI/output contract as `nanodevice_flakedetect_detect`:

- `scripts/graphite.py`
- `scripts/graphene.py`
- `scripts/bottom_hbn.py`
- `scripts/top_hbn.py`

SAM-assisted detection uses the same align products as the non-SAM flow. The upstream `footprint.py` diff must be the full_stack LAB diff against bottom_part warped into full_stack, with non-overlap warp pixels filled by the full_stack four-corner low-saturation gray mean. Do not substitute black fill, top/source average fill, or SAM candidate colors for this diff background; SAM only refines selected material masks after the shared align step.

Graphite and graphene first delegate to the corresponding baseline detector,
then write prompt-candidate diagnostics:

- `<material>_candidate_01_on_grid.png` ... `<material>_candidate_09_on_grid.png`
- `<material>_candidate_montage.png` (3x3 panel for agent visual selection)
- `<material>_prompt_candidates.json`

Candidate images must show the raw/source image with a coordinate grid, prompt
points, and the SAM-produced mask overlay. The grid is mandatory because the
agent uses it to correct prompt locations. Candidate overlays must draw the
mask in red so it remains visually distinct from blue/cyan hBN flakes; positive
prompt points are green and negative prompt points are orange. When a graphene
run has a graphite-on-top prior, every `graphene_candidate_##_on_grid.png` and
`graphene_candidate_montage.png` must also show the graphite prior as a thin
yellow contour, and the title text should identify yellow as `graphite prior`.
This contour is visual-only: it must not be used to constrain SAM, clip
`graphene_mask.png`, or change the downstream combine contract. Candidate 9 is
the original baseline/refined mask, not a SAM run. For graphene, this must be the exact
`graphene_mask.png` selected by the baseline detector from
`00_graphene_candidates.png` (including an explicit `--cluster-id` choice when
supplied), and it must be written as `graphene_candidate_09_on_grid.png` even
when manual prompts are used.

Candidate filename numbering is one-based, while `--prompt-rank` is zero-based:
`graphite_candidate_06_on_grid.png` means `--prompt-rank 5`, and
`graphite_candidate_09_on_grid.png` means `--prompt-rank 8`. When fewer than
eight manual prompt candidates were supplied, the montage must still include
the actual files written, including candidate 9 if present; do not infer that a
blank seventh/eighth tile is the baseline.

For SAM/manual runs, candidates 01-08 are the SAM prompt candidates and must be
reviewed first. Candidate 09/rank 8 is baseline/refined fallback only, not a SAM
prompt result. Do not choose rank 8 merely because its outline is smoother,
cleaner, or more continuous. Choose rank 8 only if every reviewed SAM candidate
01-08 has a clear visual failure such as hBN flooding, full-image/FOV
border or corner artifacts,
tiny fragments, missing the target material, or selecting the wrong material.
When rank 8 is selected, the run log or selection JSON must explicitly state
why the SAM candidates were rejected.

Exception: `bottom_hbn.py` and `top_hbn.py` delegate to the baseline detector
only. Bottom hBN is host-only and top hBN is the align footprint, so neither
wrapper writes candidate images, writes prompt JSON, or refines with SAM2.

Default selection is rank `0`. Use `--prompt-rank N` to select a different prompt candidate after visual review.

**Candidate review is mandatory:** every time candidate images or
`*_prompt_candidates.json` are written, Codex/Claude/Qlaybot or another capable
agent must inspect the images or read the candidate metadata and explicitly
choose one prompt rank. SAM2 score and automatic rank are hints only; do not
accept rank 0 or the highest score without review when candidate diagnostics
exist. For both graphite and graphene, treat masks on the full source image's
outer border/corners or crop/FOV edge artifacts as usually wrong, treat very small isolated masks as usually
wrong, and treat masks that simply cover all or nearly all of the relevant hBN
flake (`bottom_hBN` for graphite, `top_hBN` for graphene) as usually wrong.

## SAM2

By default the scripts look for SAM2 in this order:

1. `SAM2_ROOT` environment variable, if set.
2. Repository-local `tools/sam2-main`.
3. Legacy local path `D:/Users/liyiz/desktop_backup/shixi/sam2-main`.

and use:

- config: `configs/sam2.1/sam2.1_hiera_b+.yaml`
- checkpoint: `model/sam2.1_hiera_base_plus.pt`

For GitHub sharing, put the SAM2 source tree at
`tools/sam2-main`. The checkpoint path expected by the wrappers is
`tools/sam2-main/model/sam2.1_hiera_base_plus.pt`. The checkpoint is
larger than GitHub's normal 100 MB file limit, so commit it only through Git
LFS or distribute it separately and instruct users to place it at that path.

Run with `--use-sam2` to attempt SAM2 refinement using the selected
positive/negative points. For graphene, `00_graphene_candidates.png` and
`--cluster-id` are baseline diagnostics only; they do not replace the manual
on-grid prompt loop. In the current full-rerun workflow, start graphene from
the clean mirrored source grid without the graphite overlay, write
`graphene_manual_prompts.json`, run with
`--manual-prompts-json <json> --use-sam2`, inspect the fresh
`graphene_candidate_##_on_grid.png` files with the graphite prior visible,
then rerun with explicit `--prompt-rank N`. Use `--cluster-id` only when the
task explicitly chooses to constrain the baseline diagnostic before manual
prompt generation.

## Grid-first Manual Prompt Selection

For graphite/backgate, use only the grid-first manual prompt loop. The graphite
wrapper must not auto-generate SAM prompt points from the baseline mask. If
`graphite.py` is run without `--manual-prompts-json`, it only writes
`graphite_source_grid_80px.png`, keeps the baseline mask as rank 8, and records
`manual_grid_prompts_required` in `graphite_prompt_candidates.json`.

This is an intermediate review state, not a completed graphite SAM run. When
the SAM skill is selected, the agent must continue immediately: inspect the
grid, create `graphite_manual_prompts.json`, and rerun graphite with both
`--manual-prompts-json` and `--use-sam2`. Do not assemble `detections.json`,
run combine/gdsalign, or report a score while
`graphite_prompt_candidates.json` still contains
`manual_grid_prompts_required` or while no `graphite_candidate_*_on_grid.png`
files were produced. A graphite result that only has
`refined_candidates.png`/`graphite_mask.png` is baseline detection, not SAM.

For other ambiguous SAM runs, prefer the same grid-first prompt selection loop
instead of trusting automatically placed points.

1. Run the SAM wrapper once without `--manual-prompts-json`. The wrapper writes
   `<material>_source_grid_80px.png` by default. Use `--grid-step 80` unless
   the user asks for another spacing.
2. Inspect the grid image and the raw source image. For graphite/backgate,
   search inside the bottom hBN / host hBN flake first, then choose the
   visually darkest long strip-like backgate candidate within that hBN host.
   Do not prefer isolated dark strips outside the host hBN merely because they
   are darker or cleaner. A dark, narrow, strip-like region inside the bottom
   hBN host is a stronger prompt target than a broad hBN-colored sheet, even
   if the baseline mask or automatic candidate points prefer the sheet. Do not
   reject a candidate just because it lies visually within a blue/cyan hBN
   overlay region: that color is a mask/display cue, not optical evidence that
   the darker strip is non-graphite. Graphite/backgate is often the relatively
   darker, continuous vertical strip surrounded by or embedded in that hBN host.
   Graphite/backgate may bend, kink, or have an elbow; choose the connected
   long strip as one object even when its centreline is not straight. Place
   prompt hypotheses that can recover the whole nearby graphite/backgate strip,
   not only a locally dark short segment. Do not reject a graphite candidate
   just because a fold makes its PCA/aspect score look worse. Graphite can
   sometimes look substrate-like in color, but it is not expected to be a tiny
   isolated speck; prefer a connected, physically plausible long strip over
   small high-contrast fragments.
3. Write a manual prompt JSON with six to eight candidates. Each candidate has
   `rank`, `positive_points`, and `negative_points` in source-image pixel
   coordinates:

```json
[
  {
    "rank": 0,
    "positive_points": [[900, 500]],
    "negative_points": [[760, 500], [1040, 500], [900, 760]]
  },
  {
    "rank": 1,
    "positive_points": [[905, 760]],
    "negative_points": [[760, 760], [1040, 760], [905, 1040]]
  }
]
```

4. For a vertical strip, put positives down its centerline and left/right negatives
   on neighboring material. Include a stem-local hypothesis with an end negative
   at the stem-to-pad transition and a complete hypothesis with a pad positive and
   an end negative beyond it. The local mask is a control, not automatically better. Never place positives on
   small full-image-border/corner fragments, crop/FOV edge artifacts, or
   unconnected flake-corner tips merely because they are dark or sharp.
   Positive points should fall on the main connected graphite/backgate strip
   inside the host hBN; use those full-image-border/corner artifacts or
   unconnected flake-corner fragments as negatives when they are not visibly
   connected to that strip.
5. Re-run graphite with `--manual-prompts-json <json> --use-sam2`. The wrapper writes
   only `<material>_candidate_XX_on_grid.png` files plus the montage. These
   diagnostics must start from the raw/source image plus grid and prompt
   points; if SAM2 succeeds, overlay the SAM mask itself. Do not use baseline,
   refined-candidate, or previously detected masks as the on-grid background
   except for candidate 9, which is explicitly the baseline/refined mask.
6. Inspect every candidate image and choose visually. For graphite/backgate,
   prefer the candidate that isolates the darkest narrow strip; do not choose a
   larger whole-flake mask just because SAM2 gives it a higher score. In
   particular, reject candidates that are just full-image-border/corner or
   crop/FOV edge artifacts,
   tiny isolated specks, or masks that occupy all or nearly all of the
   bottom_hBN host instead of isolating the graphite/backgate strip. Do not
   use "blue means not graphite" as a hard rule; blue/cyan is hBN overlay
   color, while the right graphite/backgate target may be the darker vertical
   strip inside the blue host hBN. Do not
   select a small-area graphite mask sitting on the full image border/corners
   or crop/FOV edge artifacts, even if it is dark, sharp, or high-scoring;
   graphite/backgate should
   be the physically plausible connected strip inside the host hBN region. When
   comparing candidates on the same graphite/backgate line, choose the mask that
   covers the whole usable continuous strip, including nearby connected
   segments, over a cleaner but shorter local fragment. When a candidate follows
   a connected long strip with a bend or elbow, count it as a better graphite
   candidate than a straighter but tiny fragment. Prefer SAM candidates 01-08
   over candidate 09 whenever any SAM candidate is visually acceptable;
   candidate 09 is fallback only after documenting why all SAM candidates
   failed.

7. Record the selected visual candidate/rank and the reason in the run log.
   If SAM2 is unavailable, record the import/load failure explicitly and mark
   the run as SAM fallback; never silently treat the no-prompt baseline as a
   completed SAM run.

Batch-run behavior: when `run_flake_detect_batch.py` is used with SAM visual
selection enabled, it must stop instead of continuing downstream whenever the
manual visual step has not been completed. If `<material>_manual_prompts.json`
is missing after the grid-first pass, mark the sample
`needs_visual_prompts` and write
`<material>_visual_selection_required.json`. If SAM candidate images exist but
no visual rank has been recorded, mark the sample
`needs_visual_candidate_selection` and require
`<material>_visual_selection.json` with `prompt_rank` (zero-based) or
`candidate_number` (one-based), plus a short visual reason. Do not continue to
`detections.json`, combine, gdsalign, or scoring until this file exists.
The SAM wrapper should preserve candidate diagnostics across the visual
selection pause. Candidate images and `<material>_candidate_masks.npz` may be
reused when the generation signature is unchanged (same image, manual prompt
JSON contents, baseline mask, SAM2 settings, grid step, and mirror state). Do
not delete and regenerate candidate images merely because the second run adds a
visual `--prompt-rank`; in that case reuse the cached candidate mask to update
the final `*_mask.png`/`*_contour.npy` and only refresh the selected-border
diagnostics. Clear stale candidate images only when the generation signature
changes.

Exception to the reuse rule: when the user explicitly asks for a fresh rerun,
fresh point selection, or says not to reuse old selections, the agent must
delete or ignore all prior `<material>_manual_prompts.json`,
`<material>_visual_selection.json`, legacy `graphene_manual_selection.json`,
candidate images, prompt sidecars, and candidate mask caches for the requested
scope before starting the new prompt cycle. Alignment products may still be
reused when the user allows align reuse. In that fresh-rerun mode, old points
and old selected ranks are invalid evidence even if the image and generation
signature are unchanged.

Graphene graphite-prior ordering: generate `graphite_on_top_mask.png` only
after the graphite SAM/manual workflow has completed and the final selected
`graphite_mask.png` has been written. Do not compute the graphene prior from
the first-pass baseline graphite mask, and do not recompute it repeatedly during
the graphite prompt loop. This keeps the graphene prior tied to the final
graphite choice and avoids wasting time on stale masks.

### Mandatory clean-grid-first graphene overlap loop

When `graphite_on_top_mask.png` is available, use this sequence exactly:

1. For the first prompt attempt, inspect only the clean
   `graphene_source_grid_80px.png` and the mirrored raw top-part image. Do not
   inspect `graphene_source_grid_80px_graphite_prior.png` before writing the
   first `graphene_manual_prompts.json`; the initial points must come from the
   optical graphene evidence without graphite-prior bias.
2. Generate fresh graphene SAM candidates. Review the montage and candidate
   images with the thin yellow graphite contour visible, then select the best
   visual graphene candidate.
3. Compute the exact pixel intersection between the selected candidate mask
   and `graphite_on_top_mask.png`.
4. If the intersection is greater than zero pixels, record the overlap count
   and selected rank, write the selected candidate alone as
   `graphene_mask.png`, and allow the workflow to continue.
5. If the intersection is exactly zero pixels, reject that selection and stop
   all downstream combine, gdsalign, and scoring work. Now inspect
   `graphene_source_grid_80px_graphite_prior.png`, rewrite the manual prompts,
   regenerate fresh candidates, review them with the yellow prior, and compute
   the intersection again. On this retry, include at least one positive point
   on visually plausible graphene inside the graphite-overlap region; an
   arbitrary yellow-contour, graphite-only, background, or artifact pixel is
   not valid.
6. Repeat step 5 for every zero-overlap selection. Do not accept a zero-overlap
   candidate and do not continue downstream merely because align is complete,
   time is limited, or a previous candidate had a high score. Each retry must
   replace the prior prompt hypotheses and regenerate candidates; do not reuse
   the stale zero-overlap selection.

The graphite-on-top contour/prior is therefore hidden during first-attempt
point placement, shown during candidate review, and used for point placement
only after a selected candidate fails the pixel-overlap gate.

The graphite contour is an input cue, never graphene output. `graphene_mask.png`
must contain only the graphene region returned by the selected graphene
segmentation. Never union, paste, copy, or otherwise write
`graphite_on_top_mask.png` pixels into `graphene_mask.png`, its contour, or
downstream graphene geometry. The graphite-overlap positive tells SAM where
overlapping graphene is present; it does not declare the whole graphite contour
to be graphene.

For the manual SAM workflow, graphene must also use grid-first prompts. The
automatic mask-centered/free-anchor points are only a baseline diagnostic; the
agent must first inspect the clean mirrored top-part grid and raw image, write
`graphene_manual_prompts.json`, and rerun with
`--manual-prompts-json <json> --use-sam2`. Every graphene prompt candidate must
include positive points in the visually correct layered region. A graphite-overlap
positive in the graphene region that overlaps the yellow graphite contour is
required on retries after a zero-overlap selection, not before the first
candidate generation. Every candidate must also include at least one
negative point on nearby non-graphene top hBN/top-flake material when visible.
Do not continue to combine/gdsalign or report a manual-point SAM result while
the prompt JSON is missing or while `graphene_prompt_candidates.json` reports
that automatic points were used without the manual prompt file.

Identify graphene in this order before placing prompts:

1. Locate the top-flake/top-hBN footprint against the surrounding background.
2. Inside that footprint, find stable color or brightness-step boundaries that
   persist along a coherent region; do not use glare, dust, residue, or a single
   saturated patch as the boundary evidence.
3. Trace the connected extent on the graphene side of those boundaries, then
   decide whether the target is local, large, elongated, or crosses a visible
   flake edge. Include adjacent graphene-like pieces when they are touching or
   visibly connected by the same local layered/overlap region. Base the extent
   on observed boundaries and continuity; use size and aspect ratio only as
   secondary review signals. Classify each outward direction separately as a
   real flake edge, a continuing layered region, or a transition into uniform
   hBN before deciding where negatives belong.

Build six to eight manual graphene prompt candidates that represent different
segmentation hypotheses, not small coordinate perturbations of one hypothesis.
The reviewed candidate set must include at least these four classes:

- **Local:** positives inside a visually coherent local graphene region, with
  negatives just outside its decisive internal boundaries.
- **Complete extent:** two or three well-separated positives along the same
  connected graphene region. Use this for a large or elongated target so SAM
  does not return only a short fragment.
- **Larger connected:** positives distributed across the plausible connected
  extent, with negatives across the first visible boundary that the mask must
  not cross. This tests whether separated-looking contrast zones are one
  continuous graphene region.
- **Baseline-preserving expansion:** inspect every substantial baseline component
  in the same local stacked-flake footprint. Include at least one candidate with
  a positive in the larger visual target and a positive in each such baseline
  component; add a bridge/overlap positive when visible. Put negatives beyond
  the combined outer boundary, never between these regions.
- **Baseline:** treat local components as strong positive priors despite
  brightness, position, shape, or low contrast. Exclude one only for a clear
  physical gap to an unrelated object, not saturation or raggedness.

### Edge-aware graphene negative placement

Apply this rule direction by direction; do not surround graphene with negatives
mechanically:

- When coherent graphene reaches a **real top-flake/top-hBN edge**, leave that
  outward side unconstrained. Do not place a closure negative beyond or just
  inside that edge. First distinguish the physical flake edge from a full-image
  border, crop/FOV edge, glare boundary, or other imaging artifact.
- Treat a nearby region with similar color or brightness as part of the same
  graphene hypothesis when layered texture, overlap structure, or boundary
  continuity remains uninterrupted. Preserve that continuous similar region by
  default: extend positives into it when needed, or leave it neutral; do not
  place negatives between the regions merely to make the mask smaller.
- When the outward continuation progressively loses the graphene-like layered
  contrast and becomes smooth, uniform hBN, use that local transition as the
  exclusion boundary. Place one or more negative points immediately on the hBN
  side of the transition, following its local shape with separate `(x, y)`
  coordinates when necessary. This remains required even if the transition is
  gradual rather than a sharp line.
- If a first candidate floods into uniform hBN, move the hBN-side negatives
  closer to that transition or add another local negative there. Do not repair
  the flood by adding a negative on the genuine flake-edge side or between
  visibly continuous graphene-like regions.

Never place a negative on any substantial local baseline component unless a
clear physical gap proves it is unrelated. Brightness, bottom position, or
fragmented contrast is insufficient. For both graphite and graphene, select every prompt as a
local `(x, y)` pair: when points are placed at different `y` positions along a
tilted, curved, kinked, or widening target, adjust `x` to follow the local
centerline or region interior. Reuse one `x` column only when the visible target
is actually vertical at all selected `y` positions.

Coordinate-system rule: when graphene is run with `--mirror`, SAM2 must receive the horizontally mirrored `top_part` image and must write `graphene_mask.png` in mirrored-top_part coordinates. `warp_top.npy` maps mirrored-top_part to full_stack directly; writing an unmirrored SAM mask will look correct in detect diagnostics but will be clipped/misplaced during combine.

Graphene-specific tuning:

- For graphene, `--sam-target-frac 0.4` is the usual first choice: it steers SAM2 toward a substantial graphene region without selecting the whole top flake.
- Use a smaller value only when visual review shows the real graphene is a small internal patch.
- Use a much larger value, e.g. `--sam-target-frac 0.95`, only when visual review clearly shows that the graphene target is nearly the entire top flake; do not use `0.95` as the default.
- Visually, graphene is often in relatively light-colored regions where many semi-transparent layers overlap, with complex interference colors/brightness steps and several visible layer boundaries. It may cover or cross the visible flake edge, and it can be fairly large. A very tiny isolated mask is almost certainly not the desired graphene. Prefer light, layered, translucent overlap regions over a single uniform hBN-looking sheet or pure white glare/reflection.
- When several plausible graphene patches appear within the same layered/overlap
  region, prefer the larger connected graphene-like area that captures the
  coherent extent of that region, including neighboring graphene pieces that are
  adjacent or visibly continuous. Do not choose a smaller fragment merely
  because it has a sharper local boundary, higher SAM score, or sits near the
  first positive point; choose the small fragment only when the raw image shows
  the true graphene is visibly small and separated.
- If multiple visible graphene pieces appear to belong to the same local
  graphene/overlap region, prefer the candidate mask that includes more of
  those graphene pieces and has the larger physically coherent graphene area,
  provided it does not leak into whole top hBN/top-flake or cross clear
  non-graphene boundaries.
- Do not merge distant graphene-like pieces just because they share similar
  brightness or color. Graphene selection should cover only a local connected
  graphene block, or very nearby pieces with visible continuity/overlap
  evidence between them. If two graphene-like blocks are separated by a clear
  gap, intervening hBN/top-flake material, background, glare, tape, or an
  unrelated flake boundary, treat the distant block as a separate object and
  place negative points between/across it. A candidate that swallows a far-away
  graphene-like patch is worse than one that covers the intended nearby
  connected block cleanly.
- For graphene, reject candidates that are merely on the full image's corners,
  outer image border, or crop/FOV edge artifacts unless the raw image clearly
  shows graphene there. Do not apply this as a ban on graphene at the flake's
  own edge; graphene may genuinely cover or cross a flake edge. Reject tiny
  isolated specks/fragments, and reject masks that cover all or nearly all of
  the top_hBN/top-flake region; those are usually hBN/top-flake masks, not
  graphene.
- `00_graphene_candidates.png` is a baseline diagnostic. Use `--cluster-id N`
  only when one panel contains a visually plausible graphene region; otherwise
  skip the cluster constraint and place manual positives from the grid-first
  visual identification above. Do not allocate candidates by a fixed count of
  mask-centered versus free-anchor points.
- For graphene prompt points, do not place positive points on overly white or
  saturated bright areas when the white patch is isolated glare, residue, dust,
  marker reflection, or another non-graphene artifact. If the white/bright spot
  lies inside a coherent graphene-like region or is part of the same connected
  layered graphene area, it is acceptable for the final mask to include it; do
  not reject an otherwise good graphene candidate solely because it contains
  such an internal bright spot.
- For graphene prompt points, place negative points on visually clear non-graphene regions inside the same top hBN/top-flake footprint, not only on distant background. Use these in-footprint negatives to tell SAM2 "this part of top hBN is not graphene" and prevent masks from expanding to the whole top flake.
- Automatic SAM2 scores are only a first-pass ranking. The agent must inspect `graphene_candidate_01.png` ... `graphene_candidate_08.png` against the microscope image and choose the candidate that visually matches the real graphene region; do not choose solely because its numeric score is highest. If the highest-scoring candidate is visually wrong, re-run with `--prompt-rank N`.
- Graphene candidate selection should avoid thin strip-like masks and tiny isolated masks. Unlike graphite, strip shape is not a positive feature for graphene. When comparing SAM2 candidates, prefer substantial light-colored compact or flake-like masks, including masks that cover/cross a flake edge, over long narrow slivers or tiny specks unless the microscope image clearly shows the real graphene is a narrow strip on the flake itself. If automatic scoring is ambiguous, choose a `--prompt-rank` whose candidate is not a thin strip and not too small.
- Graphite uses strip geometry as evidence (`s_strip` / high aspect ratio), but graphene should treat high aspect ratio as a warning sign. Do not transfer graphite's strip preference to graphene.

## Visual Review Failure Modes

Use these rules after inspecting the candidate montages and source-grid images;
they are visual failure-mode checks, not sample-name shortcuts.

### Graphite/backgate

- Do not pick a local stem segment only because it is thin, dark, or clean. A
  widened terminal/pad that shares the strip's axis, material continuity, and
  outline junction is part of the graphite/backgate, not flooding.
- Do not choose small-area graphite candidates sitting on the full image
  corners, outer image border, crop/FOV edge artifacts, or unconnected flake
  corner tips. These fragments are negative evidence for graphite/backgate
  selection unless they are visibly connected to the main long backgate strip
  inside the host hBN.
- When several candidates lie along the same vertical/backgate line, compare
  their full continuous extent, including a connected bottom/top widening.
  Reject a cap, middle sliver, or stem-only mask when another candidate preserves
  the same stem plus its terminal. A broad hBN-like sheet is still wrong, but a
  localized terminal widening must not be confused with whole-host flooding.
- If the candidate montage has fewer than nine visible panels, open the
  individual `graphite_candidate_*_on_grid.png` files and the
  `graphite_prompt_candidates.json` list before deciding. Candidate 9 is the
  baseline/refined mask when present and may be omitted from a stale or
  incorrectly assembled montage.

### Graphene

- Do not classify a larger candidate as host-flake flooding from area alone.
  Treat it as flooding only when it crosses a clear internal non-graphene
  boundary or expands to nearly the entire top hBN/top-flake footprint.
- When a small candidate is nested inside a larger plausible candidate, first
  trace the same boundary-defined region above and below it. If the layered
  texture continues without a clear transverse non-graphene boundary, treat
  the small mask as a thickness, wrinkle, or brightness subregion of the larger
  graphene body. A compact closed outline or sharper local boundary does not
  justify truncating that longitudinal continuation.
- Compare competing candidates in this order: graphene body consistency,
  complete connected extent, compliance with clear non-graphene boundaries,
  then local boundary sharpness. When several independently prompted
  candidates converge on the same larger coherent body, treat that agreement
  as positive evidence for the larger body rather than as evidence of flooding.
  Low-contrast or ragged portions of that shared outline are secondary when the
  candidates still follow the same connected upper, middle, and lower extent.
- Do not accept a candidate that selects nearly the whole top hBN/top-flake
  region unless the raw image shows graphene genuinely covers that full region.
- Do not accept a small compact bright patch or isolated glare-like block as
  graphene merely because it is light and cleanly closed. First check whether
  it is the bright middle lobe of a longer translucent layered region.
- Prefer regions with layer-step texture, partial transparency, and coherent
  flake-like extent. A correct graphene candidate may sit inside the lower,
  central, or right-side portion of top hBN; position alone is not enough.
- If multiple graphene-like candidate masks occupy the same visual region, pick
  the larger connected mask that covers the coherent graphene-like extent,
  unless that mask leaks into whole top hBN/top-flake or crosses a visible
  non-graphene boundary.
- When several visible graphene pieces sit in the same region, treat a mask
  that includes more of those pieces as better than a small single-piece mask,
  as long as the larger mask still respects the visible graphene boundaries.
- Prefer a single SAM candidate that preserves the main visual region and all
  substantial baseline components in the same local stack. If none does, rerun
  with positives on the main region, central baseline lobe, and lower
  ribbon/tongue components. Do not finalize a partial mask or place negatives
  on those components merely because they are bright, dark, ragged, or low.
- Do not reject a graphene candidate only because a white/bright spot appears
  inside the selected graphene region. Reject bright spots when they are
  isolated artifacts outside the graphene region or when they cause the mask to
  flood across non-graphene boundaries.
- Do not include distant graphene-like pieces in the same mask unless the raw
  image shows they are connected by nearby continuous layered contrast. Prefer
  the candidate that tightly covers the intended local/nearby graphene block
  over a candidate that jumps across a gap to collect a far-away patch.
- When placing or judging SAM prompts, require at least one negative point on a
  nearby non-graphene part of top hBN when such an area is visible. This is the
  clearest way to separate graphene from the surrounding hBN/top-flake material.
  Put it just across a transition into uniform hBN; do not satisfy this check by
  closing a side where graphene reaches a real flake edge or by splitting a
  visibly continuous similar layered region.
- When the montage candidates are dominated by whole-flake masks, go back to
  `00_graphene_candidates.png` and choose the baseline cluster that best
  localizes the layered graphene region before generating SAM prompts.

If SAM2 cannot be imported or loaded, the script falls back to the baseline mask and records the failure in `<material>_prompt_candidates.json` without breaking the pipeline.

## Usage

Use `instrMCPdev` unless the user explicitly asks for another environment.

On Windows/PowerShell, always set UTF-8 Python I/O before running these scripts:

```powershell
$env:PYTHONIOENCODING='utf-8'
```

This is a required runtime guard, not an algorithm choice. Some detectors print
area units such as `碌m虏`; the default GBK console encoding can crash on those
characters. Treat that crash as an encoding/runtime issue, not a detection,
alignment, SAM, or scoring failure.

```powershell
conda run -n instrMCPdev python skills\nanodevice_flakedetect_sam\scripts\graphite.py `
  --image <bottom_part.jpg> --pixel-size <um/px> --output-dir <detect_dir>

conda run -n instrMCPdev python skills\nanodevice_flakedetect_sam\scripts\graphene.py `
  --image <top_part.jpg> --pixel-size <um/px> --mirror --output-dir <detect_dir>

conda run -n instrMCPdev python skills\nanodevice_flakedetect_sam\scripts\bottom_hbn.py `
  --image <bottom_part.jpg> --warp-matrix <align/warp_sift_bottom.npy> `
  --target-image <full_stack_raw.jpg> --pixel-size <um/px> --output-dir <detect_dir>

conda run -n instrMCPdev python skills\nanodevice_flakedetect_sam\scripts\top_hbn.py `
  --footprint-mask <align/footprint_mask.png> --footprint-contour <align/footprint_contour.npy> `
  --image <full_stack_raw.jpg> --pixel-size <um/px> --output-dir <detect_dir>
```

Top hBN remains the align footprint. The SAM wrapper exists only for interface
compatibility and must not create top-hBN prompt candidates.

For a visual/SAM pass:

1. Run the detector once without `--use-sam2`.
2. For graphite, treat this first pass as grid generation only. Inspect
   `graphite_source_grid_80px.png` and `bottom_part.jpg`, write
   `graphite_manual_prompts.json` with six to eight visually grounded
   candidates, then rerun graphite with
   `--manual-prompts-json <path> --use-sam2`. Do not continue to downstream
   stages after the first pass.
3. For graphene, inspect the mirrored top-part grid/raw image in this order:
   locate the top-flake, find stable internal color/brightness-step boundaries,
   then trace the connected graphene extent. Write six to eight candidates in
   `graphene_manual_prompts.json` covering local, complete-extent, and larger-
   connected hypotheses; retain the baseline as the fourth comparison class.
   For a large or elongated target, use two or three well-separated positives
   along the same connected region. Leave a genuine flake-edge side open and
   preserve continuous similar layered regions without negatives between them.
   Where the continuation becomes uniform hBN, put negatives immediately on
   the hBN side of that local transition; also use negatives on glare, residue,
   or dust when present. At different `y` positions, adjust each point's `x` to
   follow the local target interior instead of copying one vertical `x` value.
   Do not use distant background negatives as the only separation signal.
   `00_graphene_candidates.png` may be checked as a diagnostic, but it is not
   the selection surface for the manual workflow. On the first attempt, use
   only the clean `graphene_source_grid_80px.png`; do not inspect the
   graphite-prior grid before placing these points.
4. Re-run graphene with `--manual-prompts-json <json> --use-sam2`. Inspect the
   resulting on-grid candidates and override `--prompt-rank` by visual match.
   If a graphite-on-top prior is available, inspect candidate images and the
   montage with the thin yellow graphite contour visible. Compute the selected
   candidate's exact intersection with `graphite_on_top_mask.png`. If it is
   zero pixels, inspect the graphite-prior grid, replace the manual prompts,
   regenerate candidates, and select again; repeat until the selected candidate
   has positive overlap. During each retry, place at least one positive on
   visually plausible graphene inside the overlap. Do not add the yellow prior
   region to the selected graphene mask or downstream outputs.
   Automatic points alone do not satisfy the manual SAM workflow.
5. Inspect `graphite_candidate_montage.png` and every individual graphite
   on-grid candidate before selecting graphite. Then inspect
   `graphene_candidate_montage.png` first, followed by the individual
   `graphene_candidate_01_on_grid.png` ... files as needed. Compare the mask
   shape to the microscope image and the visual clue that graphene is usually relatively
   light-colored, often sits where many semi-transparent layers overlap, may
   cover/cross a flake edge, and is unlikely to be a very tiny isolated patch.
6. Pick the graphite prompt rank whose mask follows the continuous strip, and
   pick the graphene rank whose mask best describes the desired flake. Override
   automatic ranks when the visual match is better even if its numeric score is
   lower.
7. Re-run with the selected graphite prompt rank and selected graphene
   `--prompt-rank M --use-sam2`; set `--sam-target-frac` to steer graphene
   selection by visual size when needed.
8. Continue the normal combine and gdsalign pipeline only after the graphite
   SAM completion check passes and the selected graphene candidate has passed
   the positive-pixel graphite-overlap gate.

Normal detector runs do not write per-candidate mask PNGs; only the selected mask is emitted through the standard `*_mask.png` output.

## Notes

- The baseline detector output remains valid even when SAM2 is unavailable.
- The output masks, contours, result JSON filenames, coordinate systems, and downstream combine contract are unchanged.
