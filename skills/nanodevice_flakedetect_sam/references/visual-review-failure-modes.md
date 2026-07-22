# Visual Review Failure Modes

## How To Read This File

Read this file before accepting graphite or graphene prompt ranks from candidate
images. These are visual failure-mode checks, not sample-name shortcuts.

Use only the material section that applies:

- Selecting graphite/backgate: read `Shared Rules` and `Graphite / Backgate`.
- Selecting graphene: read `Shared Rules` and `Graphene`; read
  `graphene-overlap-prompts.md` first if a graphite prior or overlap gate is in
  play.

After reading this file, inspect the candidate montage and individual
`*_candidate_*_on_grid.png` files, choose the visual rank, and record the reason.

## Shared Rules

- Candidate review is mandatory whenever candidate images or
  `*_prompt_candidates.json` are written.
- SAM2 score and automatic rank are hints only.
- Do not accept rank 0 or the highest score without visual review.
- Treat masks on full source image borders/corners or crop/FOV edge artifacts as
  usually wrong.
- Treat very small isolated masks as usually wrong.
- Treat masks that cover all or nearly all of the relevant hBN flake as usually
  wrong: `bottom_hBN` for graphite, `top_hBN` for graphene.

## Graphite / Backgate

- Search inside the bottom hBN / host hBN flake first. Do not prefer isolated
  dark strips outside the host hBN merely because they are darker or cleaner.
- Prefer a dark, narrow, connected strip-like region inside the bottom hBN host
  over a broad hBN-colored sheet.
- Do not use blue/cyan overlay color as optical evidence that a darker strip is
  non-graphite.
- Do not pick a local stem segment only because it is thin, dark, or clean. A
  widened terminal/pad sharing the strip axis, material continuity, and outline
  junction is part of the graphite/backgate, not flooding.
- Do not choose small-area candidates on full-image corners, outer image border,
  crop/FOV edge artifacts, or unconnected flake-corner tips unless they are
  visibly connected to the main long backgate strip inside host hBN.
- When several candidates lie along the same line, compare full continuous
  extent, including connected bottom/top widening. Reject a cap, middle sliver,
  or stem-only mask when another candidate preserves the same stem plus terminal.
- Candidate 9 is fallback only after documenting why all SAM candidates failed.
- If the montage has fewer than nine visible panels, open the individual
  `graphite_candidate_*_on_grid.png` files and
  `graphite_prompt_candidates.json` before deciding.

## Graphene

- Graphene is often in relatively light-colored regions where semi-transparent
  layers overlap, with complex interference colors, brightness steps, and
  visible layer boundaries. It may cover or cross a flake edge and can be fairly
  large.
- Do not classify a larger candidate as host-flake flooding from area alone.
  Treat it as flooding only when it crosses a clear internal non-graphene
  boundary or expands to nearly the entire top hBN/top-flake footprint.
- When a small candidate is nested inside a larger plausible candidate, trace
  the same boundary-defined region above and below it. If layered texture
  continues without a clear transverse non-graphene boundary, treat the small
  mask as a subregion of the larger graphene body.
- Compare candidates in this order: graphene body consistency, complete
  connected extent, compliance with clear non-graphene boundaries, then local
  boundary sharpness.
- Prefer the larger connected mask that covers a coherent graphene-like extent,
  unless that mask leaks into whole top hBN/top-flake or crosses a visible
  non-graphene boundary.
- Do not accept a small compact bright patch or isolated glare-like block as
  graphene merely because it is light and cleanly closed.
- Do not reject a candidate only because a white/bright spot appears inside an
  otherwise coherent graphene region. Reject bright spots when they are isolated
  artifacts outside graphene or cause flooding across non-graphene boundaries.
- Do not include distant graphene-like pieces in the same mask unless the raw
  image shows nearby continuous layered contrast connecting them.
- Require at least one negative point on nearby non-graphene top hBN when such a
  region is visible. Put it just across a transition into uniform hBN.
- When montage candidates are dominated by whole-flake masks, go back to
  `00_graphene_candidates.png` and choose the baseline cluster that best
  localizes the layered graphene region before generating SAM prompts.
- Unlike graphite, high aspect ratio is a warning sign for graphene. Do not
  transfer graphite's strip preference to graphene.
