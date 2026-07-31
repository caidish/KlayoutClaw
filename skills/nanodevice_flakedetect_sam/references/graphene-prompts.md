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

Uniform top hBN is not graphene. Reject broad, smooth, low-texture regions that
share the top hBN color/brightness and lack an internal graphene boundary, even
when they sit inside the footprint or overlap the graphite prior. Graphene
should have a distinct translucent sheet body, an edge/contrast transition, or
layered texture that separates it from surrounding hBN.

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
- hBN-flood rejection hypothesis with negatives placed on nearby uniform top
  hBN just outside the graphene boundary, plus positives kept inside the
  textured/translucent graphene body.

Use negatives on nearby non-graphene top HBN/top-flake material when visible.
Distant background negatives alone are not enough.

## Edge-Aware Negatives

Apply per direction:

- if graphene reaches a real top-flake edge, leave that side open;
- preserve continuous similar layered regions by extending positives or leaving
  them neutral;
- when contrast fades into uniform HBN, put negatives just on the HBN side of
  that transition;
- when a candidate tends to flood across a faint graphene-to-hBN boundary, add
  several negatives along the hBN side of that boundary instead of only one
  far-away background negative;
- keep positives away from broad uniform hBN interiors, smooth host-flake
  slabs, and saturated highlights even if those areas overlap the graphite
  prior;
- never place a negative on a substantial local baseline component unless a
  clear physical gap proves it is unrelated.

Use local `(x, y)` points. Do not reuse one x-column unless the target is
actually vertical at all selected y positions.

## Graphene-Specific Tuning

Use `--sam-target-frac 0.4` as the usual first choice. Use smaller values only
for visibly small graphene and much larger values only when graphene clearly
covers nearly the whole top flake.

Do not increase `--sam-target-frac` to make a weak candidate reach the graphite
prior if the expansion floods into top hBN. In that case, rewrite prompts around
the graphene boundary and add hBN-side negatives before regenerating
candidates.

## HBN Flood Rejection

Before freezing a graphene rank, inspect both the candidate image and the raw or
mirrored top image. Reject the candidate and rewrite prompts when any of these
are true:

- the mask expands into a broad uniform top hBN/top-flake slab with no stable
  graphene boundary;
- the mask covers most of the top hBN footprint while the visible graphene body
  is only a smaller textured/translucent subregion;
- the mask crosses a clear graphene-to-hBN contrast transition and keeps
  growing through smooth hBN;
- the mask mainly follows the footprint/top-hBN outline rather than the
  graphene sheet boundary;
- the selected overlap with `graphite_on_top_mask.png` is achieved only by
  flooding hBN, not by covering a visible graphene region over the prior.

If every SAM candidate either misses the graphite prior or floods into hBN,
prefer another prompt round over selecting candidate 09 or the least-bad flood.
Candidate 09 / the baseline-refined fallback is a diagnostic fallback, not an
escape hatch from hBN-flood rejection. Do not freeze rank 8 or 09 when its
useful graphite-prior overlap comes from broad uniform top hBN, a smooth
top-flake slab, or a mask that mainly follows the hBN footprint. Use positives
inside the most plausible graphene-over-prior region and place negatives on the
adjacent hBN side of each leak, then regenerate candidates.

Before selecting rank 8 / candidate 09, `graphene_visual_selection.json` must
include a `rank8_hbn_flood_gate` object with all of these fields:

- `visible_graphene_boundary`: the concrete raw/mirrored-image boundary the
  mask follows;
- `adjacent_hbn_region`: the neighboring hBN/top-flake region the mask does
  not enter;
- `why_prior_overlap_is_graphene`: why the overlap with
  `graphite_on_top_mask.png` is visible graphene-over-prior rather than broad
  hBN flooding;
- `non_flood_reason`: why the mask is not mainly following the hBN footprint.

Generic phrases such as "only visually plausible", "clean fallback", "best
overlap", or "non-flooded" do not satisfy this gate. If any field cannot be
filled from the raw or mirrored top image, treat candidate 09 as rejected and
run another prompt round.

Coordinate rule: with `--mirror`, SAM2 receives the horizontally mirrored
`top_part` and writes `graphene_mask.png` in mirrored top-part coordinates.
`warp_top.npy` maps mirrored top-part to full-stack directly.
