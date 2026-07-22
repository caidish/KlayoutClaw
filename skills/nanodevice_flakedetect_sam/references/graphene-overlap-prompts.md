# Graphene Overlap Prompts

## How To Read This File

Read this file whenever selecting graphene in SAM mode. It is mandatory when
`graphite_on_top_mask.png` exists, when a selected graphene candidate has zero
overlap with graphite, or when writing `graphene_manual_prompts.json`.

Read this after `manual-prompt-workflow.md` and before final graphene rank
selection. Then read `visual-review-failure-modes.md` to judge candidate images.

Do not read this file for graphite-only selection, bottom hBN, or top hBN.

## Graphite Prior Ordering

Generate `graphite_on_top_mask.png` only after the graphite SAM/manual workflow
has completed and the final selected `graphite_mask.png` has been written. Do
not compute the graphene prior from the first-pass baseline graphite mask.

The prior is tied to the final graphite choice and avoids wasting time on stale
masks.

## Mandatory Clean-Grid-First Overlap Loop

When `graphite_on_top_mask.png` is available, use this sequence exactly:

1. For the first prompt attempt, inspect only the clean
   `graphene_source_grid_80px.png` and the mirrored raw top-part image. Do not
   inspect `graphene_source_grid_80px_graphite_prior.png` before writing the
   first `graphene_manual_prompts.json`.
2. Generate fresh graphene SAM candidates. Review the montage and candidate
   images with the thin yellow graphite contour visible, then select the best
   visual graphene candidate.
3. Compute the exact pixel intersection between the selected candidate mask and
   `graphite_on_top_mask.png`.
4. If the intersection is greater than zero pixels, record the overlap count and
   selected rank, write the selected candidate alone as `graphene_mask.png`, and
   allow the workflow to continue.
5. If the intersection is exactly zero pixels, reject that selection and stop
   all downstream combine, gdsalign, and scoring work. Inspect
   `graphene_source_grid_80px_graphite_prior.png`, rewrite the manual prompts,
   regenerate candidates, review them with the yellow prior, and compute the
   intersection again.
6. Repeat step 5 for every zero-overlap selection. Do not accept a zero-overlap
   candidate because align is complete, time is limited, or a previous candidate
   had a high score.

On every zero-overlap retry, include at least one positive point on visually
plausible graphene inside the graphite-overlap region. An arbitrary
yellow-contour, graphite-only, background, or artifact pixel is not valid.

The graphite contour is an input cue, never graphene output. Never union, paste,
copy, or otherwise write `graphite_on_top_mask.png` pixels into
`graphene_mask.png`, its contour, or downstream graphene geometry.

## Graphene Prompt Hypotheses

Build six to eight manual graphene prompt candidates that represent different
segmentation hypotheses, not small coordinate perturbations of one hypothesis.

Include at least these classes:

- **Local:** positives inside a visually coherent local graphene region, with
  negatives just outside decisive internal boundaries.
- **Complete extent:** two or three well-separated positives along the same
  connected graphene region.
- **Larger connected:** positives distributed across the plausible connected
  extent, with negatives across the first visible boundary that the mask must
  not cross.
- **Baseline-preserving expansion:** inspect every substantial baseline
  component in the same local stacked-flake footprint. Include positives in the
  larger visual target and in each such baseline component; put negatives beyond
  the combined outer boundary, never between these regions.
- **Baseline:** treat local components as strong positive priors despite
  brightness, position, shape, or low contrast. Exclude one only for a clear
  physical gap to an unrelated object.

## Edge-Aware Negative Placement

Apply this direction by direction; do not surround graphene with negatives
mechanically:

- When coherent graphene reaches a real top-flake/top-hBN edge, leave that
  outward side unconstrained. Do not place a closure negative beyond or just
  inside that edge.
- Treat a nearby region with similar color or brightness as part of the same
  graphene hypothesis when layered texture, overlap structure, or boundary
  continuity remains uninterrupted.
- When the outward continuation progressively loses graphene-like layered
  contrast and becomes smooth, uniform hBN, place negatives immediately on the
  hBN side of that transition.
- If a first candidate floods into uniform hBN, move hBN-side negatives closer
  to that transition or add another local negative there.

Never place a negative on any substantial local baseline component unless a
clear physical gap proves it is unrelated.

For both graphite and graphene, select every prompt as a local `(x, y)` pair.
When points are placed at different `y` positions along a tilted, curved,
kinked, or widening target, adjust `x` to follow the local centerline or region
interior.

## Coordinate And Tuning Rules

When graphene is run with `--mirror`, SAM2 must receive the horizontally
mirrored `top_part` image and must write `graphene_mask.png` in mirrored-top_part
coordinates. `warp_top.npy` maps mirrored-top_part to full_stack directly.

For graphene, `--sam-target-frac 0.4` is the usual first choice. Use a smaller
value only when visual review shows the real graphene is a small internal patch.
Use a much larger value, such as `--sam-target-frac 0.95`, only when visual
review clearly shows that graphene genuinely occupies nearly the entire top
flake.
