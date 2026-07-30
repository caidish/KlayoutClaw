# Graphene Prompt Selection

Graphene also uses grid-first manual prompts. Automatic points and
`00_graphene_candidates.png` are diagnostics only.

## Clean-Grid-First With Graphite Prior

When `graphite_on_top_mask.png` exists:

1. First inspect only `graphene_source_grid_80px.png` and the mirrored raw
   `top_part` image. Do not inspect the graphite-prior grid before first prompt
   placement.
2. Generate graphene SAM candidates.
3. Review candidates with the thin yellow graphite prior visible.
4. Compute exact pixel intersection between the selected candidate mask and
   `graphite_on_top_mask.png`.
5. If intersection is zero, reject the selection, inspect the prior grid, write
   new prompts with at least one valid graphene positive in the overlap region,
   regenerate candidates, and repeat.

Never accept zero-overlap graphene when a graphite prior is available.

## Identify Graphene

Inspect in this order:

1. Locate the top-flake/top-HBN footprint.
2. Inside it, find stable color or brightness-step boundaries that persist
   along a coherent region.
3. Trace connected graphene-like extent using layered texture, overlap
   structure, transparency, and boundary continuity.

Graphene is often light-colored, translucent, and in multi-layer overlap
regions. It may cover or cross a real flake edge. Tiny isolated specks, pure
glare, or thin strip artifacts are usually wrong.

Saturated white / high-exposure regions are not graphene targets. This is true
even when the region is large, connected, inside the top-flake footprint, or has
some weak internal texture. Use them as negative regions. A valid bright
graphene target must be non-saturated and show translucent layered texture or
stable physical boundaries in the raw/mirrored top image.

## Prompt Hypotheses

Build six to eight different hypotheses, not tiny perturbations:

- local graphene region;
- complete extent with separated positives along the same connected region;
- larger connected region, with negatives across clear non-graphene boundaries;
- baseline-preserving expansion that includes substantial baseline components;
- baseline/local components as positive priors unless a clear physical gap
  proves they are unrelated.
- high-exposure rejection hypothesis with negatives on saturated white/pink
  glare regions near the candidate.

Use negatives on nearby non-graphene top HBN/top-flake material when visible.
Distant background negatives alone are not enough.

## Edge-Aware Negatives

Apply per direction:

- if graphene reaches a real top-flake edge, leave that side open;
- preserve continuous similar layered regions by extending positives or leaving
  them neutral;
- when contrast fades into uniform HBN, put negatives just on the HBN side of
  that transition;
- never place a negative on a substantial local baseline component unless a
  clear physical gap proves it is unrelated.

Use local `(x, y)` points. Do not reuse one x-column unless the target is
actually vertical at all selected y positions.

## Graphene-Specific Tuning

Use `--sam-target-frac 0.4` as the usual first choice. Use smaller values only
for visibly small graphene and much larger values only when graphene clearly
covers nearly the whole top flake.

Coordinate rule: with `--mirror`, SAM2 receives the horizontally mirrored
`top_part` and writes `graphene_mask.png` in mirrored top-part coordinates.
`warp_top.npy` maps mirrored top-part to full-stack directly.
