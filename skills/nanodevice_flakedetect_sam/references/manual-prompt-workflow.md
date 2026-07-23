# Manual Prompt Workflow

## How To Read This File

Read this file whenever running `nanodevice_flakedetect_sam` for graphite or
graphene. Read it after `runtime-and-contract.md` and before creating any
`<material>_manual_prompts.json` file.

Use only the material sections that apply:

- Selecting graphite/backgate: read `Grid-First Rule`, `Graphite Prompt Loop`,
  and `Batch And Cache Behavior`; then read
  `visual-review-failure-modes.md` before choosing `--prompt-rank`.
- Selecting graphene: read `Grid-First Rule`, `Graphene Prompt Loop`, and
  `Batch And Cache Behavior`; then read `graphene-overlap-prompts.md` and
  `visual-review-failure-modes.md` before choosing `--prompt-rank`.
- Running both graphite and graphene: read the whole file once, then follow the
  graphite loop before the graphene loop so the final graphite mask can provide
  the graphene prior.

After reading this file, the agent should know whether it must stop for manual
prompts, which prompt JSON to create, which script to rerun with `--use-sam2`,
and which reference to read before visual rank selection.

## Grid-First Rule

For graphite/backgate, use only the grid-first manual prompt loop. The graphite
wrapper must not auto-generate SAM prompt points from the baseline mask.

If `graphite.py` is run without `--manual-prompts-json`, it only writes
`graphite_source_grid_80px.png`, keeps the baseline mask as rank 8, and records
`manual_grid_prompts_required` in `graphite_prompt_candidates.json`.

This is an intermediate review state, not a completed graphite SAM run. Continue
immediately: inspect the grid, create `graphite_manual_prompts.json`, and rerun
graphite with both `--manual-prompts-json` and `--use-sam2`.

Do not assemble `detections.json`, run combine/gdsalign, or report a score while
`graphite_prompt_candidates.json` still contains `manual_grid_prompts_required`
or while no `graphite_candidate_*_on_grid.png` files were produced.

For other ambiguous SAM runs, prefer the same grid-first prompt selection loop
instead of trusting automatically placed points.

## Graphite Prompt Loop

1. Run the SAM wrapper once without `--manual-prompts-json`. The wrapper writes
   `<material>_source_grid_80px.png` by default. Use `--grid-step 80` unless
   the user asks for another spacing.
2. Inspect the grid image and the raw source image. Search inside the bottom hBN
   / host hBN flake first, then choose the visually darkest long strip-like
   backgate candidate within that hBN host.
3. Write a manual prompt JSON with six to eight candidates. Each candidate has
   `rank`, `positive_points`, and `negative_points` in source-image pixel
   coordinates.
4. For a vertical strip, put positives down its centerline and left/right
   negatives on neighboring material. Include a stem-local hypothesis with an
   end negative at the stem-to-pad transition and a complete hypothesis with a
   pad positive and an end negative beyond it.
5. Re-run graphite with `--manual-prompts-json <json> --use-sam2`.
6. Inspect every candidate image and choose visually. Prefer the candidate that
   follows the complete continuous graphite/backgate strip, including connected
   bends, elbows, and terminal widenings.
7. Record the selected visual candidate/rank and the reason in the run log.

Example prompt JSON:

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

## Graphene Prompt Loop

For graphene, inspect the mirrored top-part grid/raw image in this order:

1. Locate the top-flake/top-hBN footprint against the surrounding background.
2. Find stable internal color or brightness-step boundaries that persist along a
   coherent region.
3. Trace the connected graphene extent.
4. Decide whether the target is local, large, elongated, or crosses a visible
   flake edge.

Write six to eight candidates in `graphene_manual_prompts.json` covering local,
complete-extent, larger-connected, and baseline-preserving hypotheses. Use two
or three well-separated positives for large or elongated targets. Put negatives
immediately on the uniform hBN side of visible non-graphene transitions; do not
use distant background negatives as the only separation signal.

`00_graphene_candidates.png` may be checked as a diagnostic, but it is not the
selection surface for the manual workflow. On the first attempt, use only the
clean `graphene_source_grid_80px.png`; do not inspect the graphite-prior grid
before placing the first points.

After rerunning with `--manual-prompts-json <json> --use-sam2`, inspect the
on-grid candidates and override `--prompt-rank` by visual match.

## Batch And Cache Behavior

When `run_flake_detect_batch.py` is used with SAM visual selection enabled, it
must stop instead of continuing downstream whenever the manual visual step has
not been completed.

If `<material>_manual_prompts.json` is missing after the grid-first pass, mark
the sample `needs_visual_prompts` and write
`<material>_visual_selection_required.json`.

If SAM candidate images exist but no visual rank has been recorded, mark the
sample `needs_visual_candidate_selection` and require
`<material>_visual_selection.json` with `prompt_rank` (zero-based) or
`candidate_number` (one-based), plus a short visual reason.

Do not continue to `detections.json`, combine, gdsalign, or scoring until the
required selection file exists.

Preserve candidate diagnostics across the visual selection pause. Candidate
images and `<material>_candidate_masks.npz` may be reused when the generation
signature is unchanged: same image, manual prompt JSON contents, baseline mask,
SAM2 settings, grid step, and mirror state.

Clear stale candidate images only when the generation signature changes.

When the user explicitly asks for a fresh rerun, fresh point selection, or no
reuse, delete or ignore all prior prompt JSONs, visual selection JSONs,
candidate images, prompt sidecars, and candidate mask caches for the requested
scope before starting a new prompt cycle. Alignment products may still be reused
when the user allows align reuse.
