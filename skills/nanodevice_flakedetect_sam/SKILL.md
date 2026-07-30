---
name: nanodevice_flakedetect_sam
description: SAM2-prompt assisted flake detection. Wraps the standard four flake detectors, emits prompt candidate images, optionally refines the selected candidate through local SAM2, and preserves the normal flakedetect output contract.
---

# nanodevice_flakedetect_sam

Use this skill as the default graphite/backgate and graphene detect flow for
flake-detect tasks, prompt candidate images, manual point selection, or a SAM
rerun of the normal `nanodevice_flakedetect_detect` flow. Baseline detector
passes remain part of this flow for source grids and candidate 09 fallback.

## Required References

Before taking an action, read the matching reference first:

| Before you... | Read this reference |
| --- | --- |
| Run any SAM wrapper script, use batch-runner SAM mode, handle stale outputs, or interpret candidate sidecars | `references/contract-and-runtime.md` |
| Create or rewrite `graphite_manual_prompts.json` | `references/graphite-prompts.md` |
| Generate graphite SAM candidates after manual prompts | `references/contract-and-runtime.md` and `references/graphite-prompts.md` |
| Choose or freeze a graphite prompt rank | `references/graphite-prompts.md` and `references/visual-review.md` |
| Generate `graphite_on_top_mask.png` or prepare graphene with a graphite prior | `references/contract-and-runtime.md` and `references/graphene-prompts.md` |
| Create or rewrite `graphene_manual_prompts.json` | `references/graphene-prompts.md` |
| Generate graphene SAM candidates after manual prompts | `references/contract-and-runtime.md` and `references/graphene-prompts.md` |
| Choose or freeze a graphene prompt rank, including zero-overlap retries | `references/graphene-prompts.md` and `references/visual-review.md` |
| Continue to `detections.json`, combine, gdsalign, output GDS, or score | `references/contract-and-runtime.md`, `references/visual-review.md`, and the material prompt references for any material whose selection changed |

If the user asks for a fresh rerun, fresh point selection, or says not to reuse
old selections, old prompt JSON, visual selection JSON, candidate images,
candidate mask caches, and prompt sidecars are invalid evidence. Alignment
products may still be reused only when the user explicitly allows align reuse.

## Core Flow

1. Reuse completed align products only if allowed by the user.
2. Run graphite once to produce `graphite_source_grid_80px.png`.
3. Write fresh `graphite_manual_prompts.json`.
4. Rerun graphite through the SAM wrapper with
   `--manual-prompts-json ... --use-sam2` (batch runners should pass this
   internally; users should not need a separate `--use-sam` mode flag).
5. Inspect `graphite_candidate_montage.png` and individual candidates.
6. Write `graphite_visual_selection.json` with one frozen rank and reason.
7. Rerun graphite with the selected rank so final `graphite_mask.png` exists.
8. Generate `graphite_on_top_mask.png` only after final graphite is frozen.
9. Run graphene grid-first from clean mirrored top image; the wrapper forces `<detect_dir>/../align/footprint_mask.png` in code.
10. Write fresh `graphene_manual_prompts.json`.
11. Rerun graphene through the SAM wrapper with
    `--manual-prompts-json ... --use-sam2`; the wrapper injects the required footprint
    internally, and users should not need a separate `--use-sam` mode flag.
12. Inspect candidates with the yellow graphite prior visible.
13. Select graphene only after positive pixel overlap with
    `graphite_on_top_mask.png`; if overlap is zero, replace prompts and rerun.
14. Continue to `detections.json`, combine, gdsalign, output, and score only
    after both material visual selections are frozen.

## Hard Gates

- Candidate review is mandatory whenever candidate images, montage, or
  `*_prompt_candidates.json` exist. For graphene, every baseline/grid pass,
  SAM candidate generation, and final `--prompt-rank` rerun must use the wrapper-forced
  `<detect_dir>/../align/footprint_mask.png`; do not rely on a manual `--footprint-mask` argument. SAM2 score and automatic rank are hints only.
- Candidate filename numbering is one-based; `--prompt-rank` is zero-based.
  `candidate_01` means rank `0`; `candidate_09` means rank `8`.
- Candidate 09 is baseline/refined fallback, not a SAM prompt result. Review it
  with the same visual evidence standard as the SAM candidates, and document
  why it was selected when choosing rank 8.
- Do not assemble `detections.json`, combine, gdsalign, or score while graphite
  or graphene still needs manual prompts or visual rank selection.
- Do not accept a zero-overlap graphene candidate when
  `graphite_on_top_mask.png` is available. Rewrite prompts and regenerate
  candidates until the selected graphene mask intersects the graphite prior.
- Do not union, paste, copy, or otherwise write graphite prior pixels into
  `graphene_mask.png`; the prior is an input cue, never graphene output.
- If SAM2 cannot import or load, record the failure explicitly in the sidecar
  and mark the run as SAM fallback. Never silently treat the no-prompt baseline
  as a completed SAM run.
- If the user says score only once, freeze all prompt ranks before scoring and
  do not rerun based on score.

## Scripts

Use the four wrapper scripts with the same output contract as the baseline
material detectors:

- `scripts/graphite.py`
- `scripts/graphene.py`
- `scripts/bottom_hbn.py`
- `scripts/top_hbn.py`

Top hBN and bottom hBN delegate to baseline behavior. They do not create SAM
prompt candidates.

## Windows Runtime

Use the Python environment requested by the user. If not specified, use
`instrMCPdev`.

In PowerShell, set UTF-8 I/O before detector scripts:

```powershell
$env:PYTHONIOENCODING='utf-8'
```

When writing manual prompt JSON on Windows, write UTF-8 without BOM. The wrapper
loads prompt JSON with strict `utf-8`.

## Typical Commands

```powershell
conda run -n instrMCPdev python skills\nanodevice_flakedetect_sam\scripts\graphite.py `
  --image <bottom_part.jpg> --pixel-size <um/px> --output-dir <detect_dir>

conda run -n instrMCPdev python skills\nanodevice_flakedetect_sam\scripts\graphite.py `
  --image <bottom_part.jpg> --pixel-size <um/px> --output-dir <detect_dir> `
  --manual-prompts-json <detect_dir>\graphite_manual_prompts.json --use-sam2 `
  --prompt-rank <rank>

conda run -n instrMCPdev python skills\nanodevice_flakedetect_sam\scripts\graphene.py `
  --image <top_part.jpg> --pixel-size <um/px> --mirror `
  --output-dir <detect_dir> --manual-prompts-json <detect_dir>\graphene_manual_prompts.json `
  --use-sam2 --sam-target-frac 0.4 --prompt-rank <rank>
```

## Output Contract

Normal detector output names are unchanged:

- `graphite_mask.png`, `graphite_contour.npy`, `graphite_result.json`
- `graphene_mask.png`, `graphene_contour.npy`, `graphene_result.json`
- `bottom_hbn_mask.png`, `bottom_hbn_contour.npy`, `bottom_hbn_result.json`
- `top_hbn_mask.png`, `top_hbn_contour.npy`, `top_hbn_result.json`
- `detections.json`

Candidate diagnostics:

- `<material>_source_grid_80px.png`
- `<material>_candidate_01_on_grid.png` ... `<material>_candidate_09_on_grid.png`
- `<material>_candidate_montage.png`
- `<material>_prompt_candidates.json`
- `<material>_candidate_masks.npz`
- `<material>_visual_selection.json`
