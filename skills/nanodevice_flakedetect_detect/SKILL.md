---
name: nanodevice_flakedetect_detect
description: Detect individual material layers (graphite, graphene, bottom hBN, top hBN) from their optimal source images. Use when segmenting specific materials in a van der Waals heterostructure stack from microscope images.
---

# nanodevice_flakedetect_detect 鈥?Per-Material Detection

Detect each material from its optimal source image. For flake-detect benchmark
tasks, this is not a baseline-only path: graphite/backgate and graphene use the
SAM wrapper by default.

## Default SAM Path For Flake-Detect Tasks

When the task is a flake-detect benchmark or a full stack pipeline, read
`skills/nanodevice_flakedetect_sam/SKILL.md` and run graphite/backgate plus
graphene from `skills/nanodevice_flakedetect_sam/scripts/` by default. Do not
ask the user for a separate `--use-sam` mode flag.

The normal baseline detectors still run inside the wrapper to create source
grids, fallback candidate 09, and non-SAM diagnostics. Baseline-only
graphite/graphene masks are not complete results for these tasks unless the
user explicitly disables SAM or SAM2 cannot import/load; record that fallback
in the sidecar/run notes.

Use this split:

| Material | Default script path | Selection rule |
|---|---|---|
| graphite/backgate | `skills/nanodevice_flakedetect_sam/scripts/graphite.py` | baseline/grid pass, manual prompts, SAM2 candidates, visual rank |
| graphene | `skills/nanodevice_flakedetect_sam/scripts/graphene.py` | baseline/grid pass with code-forced align footprint, manual prompts, SAM2 candidates, visual rank |
| bottom_hBN | `skills/nanodevice_flakedetect_sam/scripts/bottom_hbn.py` or baseline equivalent | automatic, inspect low-confidence sidecar |
| top_hBN | `skills/nanodevice_flakedetect_sam/scripts/top_hbn.py` or baseline equivalent | copy align footprint |

Minimal command shape:

```bash
# Graphite/backgate grid pass: creates graphite_source_grid_80px.png.
${PYTHON_PATH:-conda run -n instrMCPdev python} skills/nanodevice_flakedetect_sam/scripts/graphite.py \
  --image <bottom_part.jpg> --pixel-size <um/px> --output-dir <detect_dir>

# After inspecting the grid, write <detect_dir>/graphite_manual_prompts.json.
# Then generate SAM candidates and final mask with the selected zero-based rank.
${PYTHON_PATH:-conda run -n instrMCPdev python} skills/nanodevice_flakedetect_sam/scripts/graphite.py \
  --image <bottom_part.jpg> --pixel-size <um/px> --output-dir <detect_dir> \
  --manual-prompts-json <detect_dir>/graphite_manual_prompts.json --use-sam2 \
  --prompt-rank <rank>

# Graphene uses the same grid-first flow; pass --mirror when align did.
# Always pass the align footprint so baseline candidates and fallback 09 use the same spatial prior.
${PYTHON_PATH:-conda run -n instrMCPdev python} skills/nanodevice_flakedetect_sam/scripts/graphene.py \
  --image <top_part.jpg> --pixel-size <um/px> --mirror --output-dir <detect_dir> \
  --manual-prompts-json <detect_dir>/graphene_manual_prompts.json --use-sam2 \
  --sam-target-frac 0.4 --prompt-rank <rank>
```

Before assembling `detections.json`, graphite and graphene must each have:

- `<material>_manual_prompts.json`
- `<material>_prompt_candidates.json`
- `<material>_candidate_montage.png`
- `<material>_candidate_##_on_grid.png`
- `<material>_visual_selection.json` or equivalent run note with selected rank
- final `<material>_mask.png`, `<material>_contour.npy`, and
  `<material>_result.json` from the selected rank

Candidate numbering is one-based in filenames and zero-based in
`--prompt-rank`: `candidate_01` means rank `0`, and `candidate_09` means rank
`8`. Candidate 09 is the baseline/refined fallback, not a SAM prompt result;
review it with the same visual evidence standard as the SAM candidates and
record why it was selected.

## Baseline Detector Role

The bullets below describe the detector behavior. For graphite/backgate and
graphene in flake-detect tasks, treat those scripts as the baseline/fallback
logic wrapped by the SAM path above.

- **graphite** (or backgate-metal) 鈥?`graphite.py`. Single adaptive pipeline that produces a ranked list of candidates and lets the agent pick which one is graphite via `--cluster-id` (vision-required). All other parameters are knobs the agent can tune ONLY when the candidate the agent wanted to pick isn't in the top-N panel.
- **graphene** 鈥?adaptive signed-contrast candidates inside the top-flake mask. Emits a ranked candidate panel; the agent must inspect the candidate image and pick the visually correct graphene region via `--cluster-id`, not choose solely by the highest numeric score. Graphene is usually in relatively light-colored regions and may cover/extend across flake edges; it can be fairly large, but a very tiny region is almost certainly not the desired graphene. Avoid selecting thin strip artifacts even when they rank highly.
- **bottom_hBN** 鈥?multi-K (4/6/8) K-means + HSV-gate union candidates over GT-fitted priors. Picks the highest-scoring candidate.
- **top_hBN** 鈥?copies the footprint from the align step.

### Never-empty fallback

`graphite.py` always emits a real-sized blob. If a stack produces zero candidates after scoring, the script writes an empty mask with `low_confidence: true`. Orchestrators should treat `low_confidence: true` as a signal to escalate to vision-review rather than as a hard failure.

## Prerequisites

Default flake-detect runs use the SAM wrapper scripts for graphite/backgate and
graphene: `skills/nanodevice_flakedetect_sam/scripts/graphite.py` and
`skills/nanodevice_flakedetect_sam/scripts/graphene.py`.

- Conda env with opencv, numpy, scikit-learn
- Source images for each material
- For `graphite.py`: just `bottom_part.jpg` + pixel size. The script runs its own substrate / host detection 鈥?no upstream dependencies.
- For `bottom_hbn.py`: `warp_sift_bottom.npy` from the align step + `full_stack_raw.jpg` for warp target.
- For `top_hbn.py`: `footprint_mask.png` from the align step.
- All scripts: `${PYTHON_PATH:-conda run -n instrMCPdev python} <script>`
- On Windows/PowerShell, set `$env:PYTHONIOENCODING='utf-8'` before running
  detector scripts. Some scripts print area units such as `碌m虏`; a GBK console
  can crash on those characters. Treat that as an encoding/runtime issue, not
  an algorithm, detection, or material-segmentation failure.

---

## Agent Workflow

All four detectors are independent 鈥?`bottom_hbn.py`, `graphene.py`, `top_hbn.py`, and `graphite.py` can run in parallel.

Default SAM ordering overrides the legacy baseline-only parallel note above:
run graphite/backgate first, freeze its selected mask/rank, then run graphene
with the same mirror setting as alignment. The wrapper forces the sibling
`align/footprint_mask.png` from `--output-dir`. Bottom hBN and top hBN can run
independently after align outputs exist.

**Candidate review is mandatory:** whenever a detector emits a candidate panel,
candidate images, or candidate JSON, Codex/Claude/Qlaybot or another capable
agent must inspect those outputs and choose the rank/ID. Numeric score is a
hint, not the authority. Do not leave rank 0 selected only because it is the
default; if another candidate is visually correct, re-run with `--cluster-id`
or the detector's equivalent selection flag.

**Graphite SAM completion is mandatory for flake-detect tasks.** The first
graphite pass without `--use-sam2` only creates the source grid and baseline
candidates; batch runners should perform this baseline/grid pass inside the
default SAM workflow. The agent must inspect `graphite_source_grid_80px.png`
against `bottom_part.jpg`, create `graphite_manual_prompts.json` with six to
eight visual candidates, and rerun through the SAM wrapper with
`--manual-prompts-json <path> --use-sam2`. Put positives along the continuous
graphite/backgate strip and negatives on neighboring material to prevent
flooding. Do not proceed to `detections.json`, combine, gdsalign, or scoring
until `graphite_prompt_candidates.json` contains the actual SAM candidate list
and `graphite_candidate_*_on_grid.png` files are present.
`manual_grid_prompts_required` means the run is unfinished, not a valid
baseline to score as SAM.

**Graphene manual SAM handoff is also mandatory for flake-detect tasks.** After
reviewing the mirrored top-part grid/raw image, the agent must write
`graphene_manual_prompts.json` with six to eight candidates. Each candidate
must put positives in the correct layered graphene region and negatives on
nearby non-graphene top hBN/top-flake material when visible. For each outward
direction, leave the side open when coherent graphene reaches a real flake
edge; preserve continuous similar-color/brightness layered regions without
putting negatives between them; when the continuation gradually becomes
uniform hBN, put negatives immediately on the hBN side of that transition.
Rerun through the SAM wrapper with
`--manual-prompts-json <path> --use-sam2`; the wrapper forces the align footprint. Automatic
mask-centered points alone do not satisfy this workflow. Do not assemble,
combine, gdsalign, or score until the graphene prompt sidecar records the
manual prompt path and the candidate files have been visually reviewed.

```
1. Run bottom_hbn.py on bottom_part (needs warp_sift_bottom.npy + full_stack_raw)
   鈫?Writes bottom_hbn_mask.png (full_stack coords) + bottom_hbn_mask_bp.png
     (bottom_part coords).
   鈫?Inspect bottom_hbn_result.json: check `low_confidence`, `fallback_source`.

2. Run graphene.py on top_part [--mirror]
   鈫?Review 00_graphene_candidates.png and `graphene_result.json.top_candidates`;
     pick by visual inspection, not score alone. Override with --cluster-id <N>
     if the selected rank is not graphene or is a thin strip artifact.

3. Run top_hbn.py (copies footprint from align) 鈫?04_top_hbn_footprint.png.

4. Run graphite.py on bottom_part:
   鈫?First run with defaults; review refined_candidates.png.
   鈫?Pick the candidate that is visually graphite/backgate via --cluster-id <rank>.
   鈫?Graphite/backgate is expected to be a long, narrow strip; strip-like
     geometry is strong positive evidence for graphite, while broad hBN-like
     regions are usually wrong even if their numeric score is high.
     Graphite/backgate may bend, kink, or have an elbow. Prefer a connected
     long strip even when the centreline is not straight or a PCA/aspect score
     is lower. Do not let a fold split the visual decision into small
     fragments. Graphite can sometimes look substrate-like in color, but it
     should not be a tiny isolated mask.
   鈫?If several candidates are similarly thin/strip-like, do not choose only
     by score: prioritize the darker strip first; if darkness is comparable,
     prefer the more physically central strip.
   鈫?If graphite isn't in the top-8 panel, tune the other parameters
     described below (--top-n, --refine-iters, --min-cc-um2, --refine-lambda).

5. Assemble detections.json (see template below).
```

---

## Graphite Detection 鈥?`graphite.py`

One adaptive pipeline. Every per-stack threshold is data-driven 鈥?no priors, no sample-tuned constants. The script produces a ranked list of candidates (`refined_candidates.png`) and the agent picks which one is the graphite via `--cluster-id`. Graphite/backgate should usually look like a connected long, narrow strip; treat that strip geometry as a required visual check before accepting a candidate. The strip may bend or have an elbow, so do not over-penalize non-straight long connected candidates. A graphite candidate can be somewhat substrate-like in color, but it should not be a tiny isolated speck.

**Pipeline overview** (each step is internally adaptive):

1. **Substrate**: pick `mu_sub` from the joint LAB histogram `H_corners 脳 H_image 脳 L`. Per-corner means fail when corners are mixed; histogram modes plus the L-brightness factor pick the brightest material that appears in BOTH the corners and the image at large.
2. **Host mask** = pixels with LAB distance to `mu_sub` above a plateau-midpoint T*. Multi-scale local-baseline peak detection on `dA(T)/dT`; T* lives in the valley between the substrate peak and the first-flake peak.
3. **Codex ridge map**: `ms_min` gradient 鈫?percentile clip + sqrt gamma 鈫?MAX of Frangi + Sato + Meijering 鈫?hysteresis (in-host p82, p96) 鈫?adaptive `remove_small_objects` (plateau in the CC-area distribution).
4. **Carve** `host \ dilated(codex_edge)` 鈫?connected components.
5. **K-union**: K-means at `K 鈭?[3 .. n_ccs]`; group same-cluster + spatially-adjacent CCs; union across K with IoU dedupe.
6. **Score** each merged candidate:
   - `s_strip = 1 鈭?鈭?位_min / 位_max)` from PCA on pixel positions (graphite is a strip)
   - `s_central = mean(distance_transform) / max(dt)` over the candidate (real flakes deposit toward host centre)
   - `s_gray = 1 鈭?chroma / 30` from the candidate's mean a,b
   - `s_contrast = min(1, dist_to_bulk_mode / 50)`
   - `s_cohere` = largest-CC fraction
   - `score = 0.3路strip + 0.3路central + 0.15路gray + 0.15路contrast + 0.1路cohere`
7. **Refine** each candidate via 5 iterations of local-mean region grow (frontier pixel admitted if its LAB is close to the LOCAL mean of nearby refined pixels, gated against bulk by `d_local < 位 路 d_bulk`).
8. **Output** the top-N panel + selected mask.

### Parameters

| Flag | Default | Range | When to touch |
|---|---|---|---|
| **`--cluster-id`** | `0` | `0 .. top_n-1` | **Most frequently used.** The agent inspects `refined_candidates.png` visually and sets this to the rank that IS the graphite. The auto-pick (rank 0) is the highest-scoring strip-shaped non-bulk candidate; agent vision is the ground truth. |
| `--top-n` | `8` | `1 .. 12` | Show MORE candidates in the panel when graphite isn't in the default top-8 list (rare). The JSON sidecar also reports `top_n` candidates so the agent can read scores even outside the panel. |
| `--refine-iters` | `5` | `1 .. 10` | Increase ONLY if the refined contour visibly under-grows (truncated at the rough region's boundary) 鈥?usually because the flake has a gradual colour gradient that needs more iterations to walk through. Decrease only if a candidate is over-growing into bulk (rare with current 位). |
| `--min-cc-um2` | `20` | `10 .. 50` | Lower when a small graphite gets thrown out at the carving step 鈥?typical signal: the desired candidate appears in `01_graphite_on_bottom.png` but not in `refined_candidates.png`. Raise only when many tiny noise CCs clutter the panel. |
| `--refine-lambda` | `0.5` | `0.3 .. 1.0` | Lower (0.3) if a candidate is flooding into bulk during refinement. Raise (0.7+) ONLY if the candidate is under-growing AND `refine-iters` already at max 鈥?the candidate's colour is unusually close to bulk and needs the LAB test relaxed. |

`--cluster-id` is the only parameter the agent uses every run. **Everything else is fallback** for the case where the agent looked at `refined_candidates.png` and the graphite it wants to pick isn't there 鈥?at which point the agent reasons about *why* (too small? excluded by area floor? not refined enough? flooded?) and adjusts the appropriate parameter.

When there are several graphite-like candidates, the final choice is a vision decision: compare their connected length, physical plausibility, darkness, and position in `refined_candidates.png` against `bottom_part.jpg`, and set `--cluster-id` to the connected long graphite/backgate strip first. A bent connected strip beats a small straight fragment. If multiple connected long strips are plausible, prioritize the darker strip; if darkness is comparable, prefer the more physically central strip. Do not change scoring weights just to force this case unless the desired strip is consistently absent from the top-N list or repeatedly ranks below visually wrong candidates across many stacks.

### Visual Failure Modes From Candidate Review

Use these checks when reviewing `refined_candidates.png`; they are meant to
explain why a rank is wrong, not to hard-code a sample answer.

| What you see | Why it is risky | Action |
|---|---|---|
| A tiny thin segment gets selected while a longer connected strip on the same material is visible | The score over-favored local strip/aspect quality and ignored usable electrode extent | Prefer the longer coherent strip/backgate segment, even if its PCA/aspect score is slightly worse |
| A selected mask captures only a cap, short middle sliver, or disconnected fragment of the vertical strip | It is too small to be the intended graphite/backgate | Choose a candidate that covers the continuous physically plausible strip extent, or expose more candidates with `--top-n` |
| A candidate covers a broad hBN-like sheet | It is hBN/host, not graphite | Reject it even if it has high centrality or gray score |
| Several candidates are along the same backgate line | Numeric rank alone cannot disambiguate the correct extent | Compare against `bottom_part.jpg` and choose the mask with connected length, plausible width, and correct placement |

### Usage

```bash
# First pass 鈥?auto-pick top, generate refined_candidates.png for review
${PYTHON_PATH:-conda run -n instrMCPdev python} graphite.py \
    --image <bottom_part.jpg> --pixel-size <um/px> --output-dir <path>

# Agent reviewed the panel 鈥?graphite is rank #2 in the panel
${PYTHON_PATH:-conda run -n instrMCPdev python} graphite.py \
    --image <bottom_part.jpg> --pixel-size <um/px> --output-dir <path> \
    --cluster-id 2

# Graphite not in top-8: raise top-n + lower area floor to expose more candidates
${PYTHON_PATH:-conda run -n instrMCPdev python} graphite.py \
    --image <bottom_part.jpg> --pixel-size <um/px> --output-dir <path> \
    --top-n 12 --min-cc-um2 10

# Refined contour visibly truncated at the rough region's boundary
${PYTHON_PATH:-conda run -n instrMCPdev python} graphite.py \
    --image <bottom_part.jpg> --pixel-size <um/px> --output-dir <path> \
    --cluster-id 0 --refine-iters 8
```

### Outputs

| File | Purpose |
|---|---|
| **`refined_candidates.png`** | Top-N panel with refined masks, scores, aspect, gray/strip/central components. The selected `--cluster-id` rank is bordered yellow. Inspect this first. |
| `graphite_mask.png` | Final binary mask (uint8, bottom_part coords) of the selected candidate. |
| `graphite_contour.npy` | (N, 2) float64 contour points in bottom_part px. |
| `graphite_result.json` | Sidecar: selected rank/score/area, top-N list with all score components, host area + bulk mu_LAB, substrate corner + mu_LAB, T*, `low_confidence` flag, params used. |
| `01_graphite_on_bottom.png` | Selected candidate's contour over a desaturated image 鈥?quick visual sanity check on the final outline. |

### Reading `graphite_result.json`

- `selected.rank` / `selected.score` 鈥?the candidate written to `graphite_mask.png`
- `low_confidence: true` when `score < 0.40` or when carving produced no candidates 鈥?the orchestrator should escalate to vision-review
- `top_candidates[]` 鈥?full list (length up to `top_n`) with `score`, `s_strip`, `s_central`, `s_gray`, `s_contrast`, `s_cohere`, `aspect`, `chroma`, `contrast_vs_bulk`, `area_um2`, `refined_area_um2`, `source_Ks` (which K values produced the candidate), `lab_ids` (which carved CCs were merged)
- `host.mu_bulk_lab` 鈥?mode of host LAB (the "bulk hBN colour" reference used by `s_contrast`)
- `substrate.mu_lab` 鈥?substrate sample from the joint histogram

---

## Graphene Detection 鈥?Tuning Guide

**Method**: Isolates the top flake, generates signed bright/dark contrast candidates in LAB space, ranks them by area/contrast/shape plus footprint containment, then writes a candidate panel for agent review. For benchmark/full-stack runs, the wrapper requires `<detect_dir>/../align/footprint_mask.png` and injects it internally for graphene baseline, SAM candidate generation, and prompt-rank reruns.

**Key insight**: The auto-selection is rank #0, but the final authority is the candidate panel. The agent must read `00_graphene_candidates.png` and decide which candidate visually matches the graphene flake; numeric score is only a hint. Graphene is generally a relatively light-colored region, can cover or cross the visible flake edge, and can be large; a very small isolated speck/patch is usually not the target graphene. Use `--cluster-id <rank>` when the highlighted candidate is an artifact, hBN region, a thin strip, or only a fragment while another panel isolates graphene better.

### What to look for in 00_graphene_candidates.png

| What you see | What's wrong | Action |
|-------------|-------------|--------|
| One panel highlights the graphene region within the flake | Correct | Use `--cluster-id <N>` if not auto-selected |
| Auto-selected panel includes artifacts/reflections along with graphene | Wrong ranked candidate | Override with a panel that shows just the graphene region |
| Auto-selected panel is a long thin strip but the real graphene is broader/flake-shaped | Score favored an edge/stripe artifact | Do not accept it just because it ranks high; choose the visually correct broader graphene candidate |
| Candidate is a tiny isolated speck/patch | Too small to be the intended graphene | Prefer a larger light-colored graphene-like region, including one that reaches or crosses an edge |
| Candidate is a small bright/glare-like block with no layered texture | Brightness was mistaken for graphene contrast | Reject it unless the raw image shows layer boundaries/overlap around that block |
| Candidate's main area is saturated white/pink high-exposure glare, even if large or connected | Overexposed reflection was mistaken for graphene | Reject it; use negatives on the glare and choose a non-saturated translucent/layered region |
| Candidate covers nearly all of top hBN/top flake | It is usually an hBN/top-flake mask, not graphene | Prefer a medium/large translucent layered subregion with coherent boundaries |
| Graphene region is split or partial | Selected seed is only a fragment | Prefer a panel with fuller graphene coverage; if none exists, keep default and note low confidence |
| No panel clearly isolates graphene | Candidate pool is ambiguous | Inspect `graphene_result.json.top_candidates`; retry only if the image/footprint inputs are wrong |

When escalating graphene to SAM prompts, put positive points on the visually
correct layered graphene region and put negative points on nearby top hBN/top
flake areas that are clearly not graphene. In-footprint negative points are more
useful than distant background points for preventing SAM from selecting the
whole top hBN region. Do not close a genuine flake-edge side or split a visibly
continuous similar layered region with negatives. If that continuation loses
layered contrast and becomes uniform hBN, place the negative immediately across
that local transition on the hBN side.

### Important: --mirror and forced footprint

If the align step used `--mirror` for the top_part, **you must also pass --mirror here**. The graphene detection must operate in the same coordinate system as the alignment warp. For benchmark/full-stack runs, the wrapper always forces `<detect_dir>/../align/footprint_mask.png` on every graphene baseline, SAM, and `--prompt-rank` rerun so `00_graphene_candidates.png`, fallback candidate 09, and the final mask are generated with the same spatial prior.

```bash
# Pass 1: auto-detect + review
${PYTHON_PATH:-conda run -n instrMCPdev python} graphene.py \
    --image <top_part.jpg> --pixel-size <um/px> --mirror \
    --output-dir <path>

# Pass 2: override after reviewing 00_graphene_candidates.png
${PYTHON_PATH:-conda run -n instrMCPdev python} graphene.py \
    --image <top_part.jpg> --pixel-size <um/px> --mirror \
    --cluster-id 0 --output-dir <path>
```

**Outputs**: `graphene_mask.png`, `graphene_contour.npy`, `graphene_result.json` (`selected`, `selected_rank`, `top_candidates[]`), `00_graphene_candidates.png` (ranked candidate panel), `02_graphene_on_top.png` (final selected overlay)

---

## Bottom hBN Detection

**Method**: shares the first step of `graphite.py` to get a non-substrate host prior, then classifies the hBN subregion inside that host before warping it to full_stack coordinates.

1. Substrate sample `mu_sub` = LAB peak of `H_corners 脳 H_image 脳 L` (joint histogram mode of corner + image pixels, weighted by brightness).
2. Host mask = pixels with LAB distance to `mu_sub` above plateau-midpoint `T*` (multi-scale local-baseline peak detection on `dA(T)/dT`, midpoint of the valley between substrate peak and first-flake peak).
3. Morph clean 鈫?keep largest CC 鈫?4-corner flood-fill-holes.
4. Warp from bottom_part to full_stack coords via the SIFT-derived affine matrix from align step.
5. Final fixed 1.5 碌m dilation to match the current GT-dilation convention.

`compute_host` is imported directly from `graphite.py` 鈥?single source of truth for substrate detection. No priors, no fitted thresholds, no `bottom_hbn_shape_priors.json` dependency.

### Edge cases

| Symptom | Likely cause | Action |
|---|---|---|
| Contour traces the whole visible cyan region but GT polygon is smaller | The visible flake is bottom_hBN 鈭?graphite/gold merged in 2D 鈥?`combine.py` doesn't mind because graphite is detected independently | None; the union is the right answer for downstream alignment |
| Contour offset from visible flake | SIFT warp inaccurate | Check inliers reported by `sift_align.py`; rerun with adjusted parameters |
| Empty / very small mask (`low_confidence: true`) | `T*` landed at the top of its sweep (no flake peak detected) 鈥?bottom_part has no clearly-separable foreground material | Inspect `bottom_hbn_result.json` for `t_star` and `substrate.mu_lab`; if `t_star 鈮?80` the algorithm couldn't find a flake peak. May need vision-review |
| Host extends across bare substrate (gold-backgate stacks like HM05) | Gold backgate is correctly classified as non-substrate and gets included | Expected 鈥?`combine.py` aligns the union (hBN + gold), and `graphite.py` independently localises the gold |

```bash
${PYTHON_PATH:-conda run -n instrMCPdev python} bottom_hbn.py \
    --image <bottom_part.jpg> \
    --warp-matrix <align/warp_sift_bottom.npy> \
    --target-image <full_stack_raw.jpg> \
    --pixel-size <um/px> --output-dir <path>
```

**Outputs**: `bottom_hbn_mask.png` (full_stack coords), `bottom_hbn_mask_bp.png` (bottom_part coords; kept for backward compat), `bottom_hbn_contour.npy`, `bottom_hbn_result.json` (area + `substrate.{corner, mu_lab, t_star}` + `dilation_um: 1.5` + `low_confidence`), `03_bottom_hbn_on_full.png`

---

## Top hBN Detection

**Method**: Copies the footprint from the align step. No detection is performed 鈥?top hBN IS the footprint.

If the top hBN detection looks wrong, the fix is in the **align** step (re-run footprint.py or adjust Chamfer alignment), not here.

```bash
${PYTHON_PATH:-conda run -n instrMCPdev python} top_hbn.py \
    --footprint-mask <align/footprint_mask.png> \
    --footprint-contour <align/footprint_contour.npy> \
    --image <full_stack_raw.jpg> \
    --pixel-size <um/px> --output-dir <path>
```

**Outputs**: `top_hbn_mask.png`, `top_hbn_contour.npy`, `top_hbn_result.json`, `04_top_hbn_footprint.png`

---

## Assembling detections.json

After all 4 scripts complete, assemble `detections.json` by reading each `*_result.json` sidecar. This file is consumed by `combine::transform.py`.

**Template** (fill in paths and values from script outputs):

```json
{
  "pixel_size_um": 0.087,
  "source_images": {
    "graphite": "/path/to/bottom_part.jpg",
    "graphene": "/path/to/top_part.jpg",
    "bottom_hBN": "/path/to/full_stack_raw.jpg",
    "top_hBN": "/path/to/full_stack_raw.jpg"
  },
  "materials": {
    "graphite": {
      "mask_file": "graphite_mask.png",
      "contour_file": "graphite_contour.npy",
      "candidate_masks_file": "graphite_candidate_masks.npz",
      "selected_rank": 0,
      "selected_score": 0.83,
      "area_px": 103546,
      "area_um2": 783.74,
      "coordinate_system": "bottom_part",
      "mirrored": false
    },
    "graphene": {
      "mask_file": "graphene_mask.png",
      "contour_file": "graphene_contour.npy",
      "candidate_masks_file": "graphene_candidate_masks.npz",
      "selected_rank": 0,
      "selected_score": 0.57,
      "area_px": 105507,
      "area_um2": 798.58,
      "coordinate_system": "top_part",
      "mirrored": true
    },
    "bottom_hBN": {
      "mask_file": "bottom_hbn_mask.png",
      "contour_file": "bottom_hbn_contour.npy",
      "area_px": 916400,
      "area_um2": 6936.23,
      "coordinate_system": "full_stack",
      "mirrored": false
    },
    "top_hBN": {
      "mask_file": "top_hbn_mask.png",
      "contour_file": "top_hbn_contour.npy",
      "area_px": 476472,
      "area_um2": 3606.42,
      "coordinate_system": "full_stack",
      "mirrored": false
    }
  }
}
```

**Assembly steps:**
1. Read `graphite_result.json`, `graphene_result.json`, `bottom_hbn_result.json`, `top_hbn_result.json` from the detect output directory
2. Copy `area_px` and `area_um2` from each sidecar into the template
3. For graphite and graphene, also copy `candidate_masks_file`, `selected_rank`, and `selected.score` as `selected_score` when present
4. Set `mirrored: true` for graphene if `--mirror` was used
5. All mask/contour/candidate paths are relative to the detect output directory
6. Write to `<detect_output_dir>/detections.json`

---

## Coordinate Systems

Each detect script operates in its source image's native coordinate system. The combine step handles all transforms.

| Material | Source Image | Detection Coords | Output Coords | Mirror |
|----------|-------------|-----------------|---------------|--------|
| graphite | bottom_part | bottom_part | bottom_part | no |
| graphene | top_part | top_part | top_part (mirrored if --mirror) | depends |
| bottom_hBN | bottom_part | bottom_part 鈫?warped to full_stack | full_stack | no |
| top_hBN | full_stack_raw | full_stack | full_stack | no |
## Shared Align Diff Input

The detect step consumes align outputs. For top hBN/graphene footprint context, use the `footprint.py` result generated from the align diff image where bottom_part is warped into full_stack and non-overlap pixels are filled with the full_stack four-corner low-saturation gray mean before LAB diff. This is shared by SAM and non-SAM flows; material detectors should not recompute that diff with black fill or source/top average fill.
