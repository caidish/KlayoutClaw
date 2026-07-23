# Contract And Runtime

## SAM2 Lookup

Wrappers look for SAM2 in this order:

1. `SAM2_ROOT`
2. repository-local `tools/sam2-main`
3. `D:/Users/liyiz/desktop_backup/shixi/sam2-main`

Default files:

- config: `configs/sam2.1/sam2.1_hiera_b+.yaml`
- checkpoint: `model/sam2.1_hiera_base_plus.pt`

Run with `--use-sam2` to attempt SAM2 refinement. If import or checkpoint
loading fails, the wrapper falls back to the baseline mask and records the
failure in `<material>_prompt_candidates.json`.

## Shared Align Input

SAM-assisted detection uses the same align products as the non-SAM flow. The
upstream footprint diff must be the `full_stack` LAB diff against `bottom_part`
warped into `full_stack`, with non-overlap warp pixels filled by the full-stack
four-corner low-saturation gray mean. Do not substitute black fill, top/source
average fill, or SAM candidate colors for this diff background.

## Candidate Diagnostics

Candidate overlays must show the raw/source image with coordinate grid, prompt
points, and the SAM mask. Mask overlay is red, positive prompt points are green,
negative prompt points are orange. For graphene candidates after graphite prior
exists, also show the graphite prior as a thin yellow contour.

Candidate 09 is the baseline/refined fallback. It is not a SAM prompt result.

## Batch Stop Conditions

Batch runners must stop instead of continuing downstream when:

- a required manual prompt JSON is missing;
- `*_prompt_candidates.json` says manual grid prompts are required;
- candidate images exist but no `*_visual_selection.json` exists;
- selected graphene has zero pixel intersection with `graphite_on_top_mask.png`.

When stopped, write a request file such as
`<material>_visual_selection_required.json` with images to inspect and the
schema for `<material>_visual_selection.json`.

## Fresh Reruns

For fresh reruns or fresh point selection, delete or ignore stale:

- `<material>_manual_prompts.json`
- `<material>_visual_selection.json`
- legacy `graphene_manual_selection.json`
- candidate images and montage
- `*_prompt_candidates.json`
- `*_candidate_masks.npz`

Alignment products may be reused only when the user allows align reuse.