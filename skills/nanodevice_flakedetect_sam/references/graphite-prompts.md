# Graphite Prompt Selection

Graphite/backgate uses grid-first manual prompt selection only. The wrapper
must not auto-generate SAM prompt points from the baseline mask.

## Identify The Target

Inspect `graphite_source_grid_80px.png` and `bottom_part.jpg`.

Search inside the bottom hBN / host hBN flake first. Prefer a connected,
physically plausible long dark strip inside the host over:

- isolated darker strips outside the host;
- hBN-colored broad sheets;
- image border or crop/FOV artifacts;
- cracks, folds, or cross-material seams;
- tiny sharp fragments.

Before placing points, explicitly decide whether a dark line is an interior
object or only a host-flake boundary. A line coincident with the edge between
blue/cyan hBN and green/gray material is weak graphite evidence even when it is
dark and sharp.

## Prompt Shape

Use six to eight hypotheses, but each hypothesis should be sparse:

- put positive points on the optical centerline, not on the strip edge;
- use two to four well-placed centerline positives for one hypothesis;
- follow the local centerline if the strip tilts, bends, kinks, or widens;
- place negatives on clearly neighboring material, far enough away that they
  cannot fall inside the full dark-band envelope;
- if uncertain whether a negative is inside the dark band, omit it or move it
  farther away;
- avoid dense columns of points and mechanical left/right negatives at every y;
- include distinct physical hypotheses: full stem, stem plus terminal, interior
  narrow core, and one boundary-control hypothesis if a tempting seam exists.

A negative inside the graphite/backgate band is worse than no negative point.

## Candidate JSON

Each candidate has `rank`, `positive_points`, and `negative_points` in source
image pixel coordinates:

```json
[
  {
    "rank": 0,
    "positive_points": [[910, 180], [912, 520], [914, 850]],
    "negative_points": [[790, 520], [1035, 520], [910, 1085]]
  }
]
```

On Windows, write this JSON as UTF-8 without BOM.

## Rerun Rule

If the montage mostly selects hBN edge, a broad blue host slab, a crack, a seam,
or a thick optical band rather than the intended narrow interior object, treat
the point set as bad. Rewrite prompts and regenerate candidates before any
combine, gdsalign, or scoring step.