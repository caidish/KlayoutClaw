# Visual Review

Candidate review is mandatory. Numeric score and automatic rank are hints only.

## Graphite/backgate

Prefer the SAM candidate that follows the physically plausible continuous,
slender interior graphite/backgate strip, not a broad dark region.

Reject:

- masks on image border, crop/FOV edge, or unconnected flake corners;
- masks that are only a crack, fold edge, material seam, or HBN edge;
- tiny isolated specks/fragments;
- broad HBN/host slabs;
- whole or nearly whole bottom HBN host masks;
- short clean fragments when another candidate preserves the usable continuous
  strip.

Candidate 09 is the baseline/refined fallback, not a SAM prompt result. Review
it with the same visual evidence standard as the SAM candidates. If most
candidates are wrong because prompts targeted the wrong visual object, rewrite
prompts instead of selecting the least-bad mask.

If candidates recover only a very thick vertical band, check whether positives
were centered on a broad contrast zone or negatives were too close. Move
positives to centerline, reduce points, and move negatives farther outside the
full dark-band envelope.

## Graphene

Prefer candidates that capture the coherent graphene-like body, complete
connected extent, and clear non-graphene boundaries.

Reject:

- tiny isolated specks or long narrow strip artifacts;
- nearly whole top HBN/top-flake masks unless raw image supports it;
- saturated white / high-exposure glare regions of any size, even if they are
  connected, large, or appear inside the top-flake footprint;
- small glare-like bright patches without layered texture;
- candidates that jump across a physical gap to distant unrelated patches;
- candidates that flood into uniform HBN.

High exposure is a hard visual reject for graphene selection. Do not choose a
candidate whose main area is a white/pink saturated reflection, washed-out
highlight, or bloom region. Treat those pixels as non-graphene and place
negative prompts just outside/inside them to keep SAM from growing into them.
Only choose a bright graphene candidate when the raw image shows non-saturated
translucent layered texture and stable physical boundaries.

Do not reject a larger graphene candidate from area alone. Treat it as flooding
only when it crosses a clear internal non-graphene boundary or expands to nearly
the whole top flake.

When several candidates occupy the same visual region, pick the larger connected
mask that covers the coherent graphene-like extent, unless it leaks across clear
non-graphene boundaries.
