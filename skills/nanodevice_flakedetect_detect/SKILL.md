---
name: nanodevice_flakedetect_detect
description: Detect individual material layers (graphite, graphene, bottom hBN, top hBN) from their optimal source images. Use when segmenting specific materials in a van der Waals heterostructure stack from microscope images.
---

# nanodevice_flakedetect_detect — Per-Material Detection

Detect each material from its optimal source image. Four independent scripts, one per material.

- **graphite** (or backgate-metal) — `graphite.py`. Single adaptive pipeline that produces a ranked list of candidates and lets the agent pick which one is graphite via `--cluster-id` (vision-required). All other parameters are knobs the agent can tune ONLY when the candidate the agent wanted to pick isn't in the top-N panel.
- **graphene** — adaptive signed-contrast candidates inside the top-flake mask. Emits a ranked candidate panel and lets the agent pick which one is graphene via `--cluster-id` when the default is wrong.
- **bottom_hBN** — multi-K (4/6/8) K-means + HSV-gate union candidates over GT-fitted priors. Picks the highest-scoring candidate.
- **top_hBN** — copies the footprint from the align step.

### Never-empty fallback

`graphite.py` always emits a real-sized blob. If a stack produces zero candidates after scoring, the script writes an empty mask with `low_confidence: true`. Orchestrators should treat `low_confidence: true` as a signal to escalate to vision-review rather than as a hard failure.

## Prerequisites

- Conda env with opencv, numpy, scikit-learn
- Source images for each material
- For `graphite.py`: just `bottom_part.jpg` + pixel size. The script runs its own substrate / host detection — no upstream dependencies.
- For `bottom_hbn.py`: `warp_sift_bottom.npy` from the align step + `full_stack_raw.jpg` for warp target.
- For `top_hbn.py`: `footprint_mask.png` from the align step.
- All scripts: `${PYTHON_PATH:-conda run -n instrMCPdev python} <script>`

---

## Agent Workflow

All four detectors are independent — `bottom_hbn.py`, `graphene.py`, `top_hbn.py`, and `graphite.py` can run in parallel.

```
1. Run bottom_hbn.py on bottom_part (needs warp_sift_bottom.npy + full_stack_raw)
   → Writes bottom_hbn_mask.png (full_stack coords) + bottom_hbn_mask_bp.png
     (bottom_part coords).
   → Inspect bottom_hbn_result.json: check `low_confidence`, `fallback_source`.

2. Run graphene.py on top_part [--mirror]
   → Review 00_graphene_candidates.png and `graphene_result.json.top_candidates`;
     override with --cluster-id <N> if the selected rank is not graphene.

3. Run top_hbn.py (copies footprint from align) → 04_top_hbn_footprint.png.

4. Run graphite.py on bottom_part:
   → First run with defaults; review refined_candidates.png.
   → Pick the candidate that is visually graphite via --cluster-id <rank>.
   → If graphite isn't in the top-8 panel, tune the other parameters
     described below (--top-n, --refine-iters, --min-cc-um2, --refine-lambda).

5. Assemble detections.json (see template below).
```

---

## Graphite Detection — `graphite.py`

One adaptive pipeline. Every per-stack threshold is data-driven — no priors, no sample-tuned constants. The script produces a ranked list of candidates (`refined_candidates.png`) and the agent picks which one is the graphite via `--cluster-id`.

**Pipeline overview** (each step is internally adaptive):

1. **Substrate**: pick `mu_sub` from the joint LAB histogram `H_corners × H_image × L`. Per-corner means fail when corners are mixed; histogram modes plus the L-brightness factor pick the brightest material that appears in BOTH the corners and the image at large.
2. **Host mask** = pixels with LAB distance to `mu_sub` above a plateau-midpoint T*. Multi-scale local-baseline peak detection on `dA(T)/dT`; T* lives in the valley between the substrate peak and the first-flake peak.
3. **Codex ridge map**: `ms_min` gradient → percentile clip + sqrt gamma → MAX of Frangi + Sato + Meijering → hysteresis (in-host p82, p96) → adaptive `remove_small_objects` (plateau in the CC-area distribution).
4. **Carve** `host \ dilated(codex_edge)` → connected components.
5. **K-union**: K-means at `K ∈ [3 .. n_ccs]`; group same-cluster + spatially-adjacent CCs; union across K with IoU dedupe.
6. **Score** each merged candidate:
   - `s_strip = 1 − √(λ_min / λ_max)` from PCA on pixel positions (graphite is a strip)
   - `s_central = mean(distance_transform) / max(dt)` over the candidate (real flakes deposit toward host centre)
   - `s_gray = 1 − chroma / 30` from the candidate's mean a,b
   - `s_contrast = min(1, dist_to_bulk_mode / 50)`
   - `s_cohere` = largest-CC fraction
   - `score = 0.3·strip + 0.3·central + 0.15·gray + 0.15·contrast + 0.1·cohere`
7. **Refine** each candidate via 5 iterations of local-mean region grow (frontier pixel admitted if its LAB is close to the LOCAL mean of nearby refined pixels, gated against bulk by `d_local < λ · d_bulk`).
8. **Output** the top-N panel + selected mask.

### Parameters

| Flag | Default | Range | When to touch |
|---|---|---|---|
| **`--cluster-id`** | `0` | `0 .. top_n-1` | **Most frequently used.** The agent inspects `refined_candidates.png` visually and sets this to the rank that IS the graphite. The auto-pick (rank 0) is the highest-scoring strip-shaped non-bulk candidate; agent vision is the ground truth. |
| `--top-n` | `8` | `1 .. 12` | Show MORE candidates in the panel when graphite isn't in the default top-8 list (rare). The JSON sidecar also reports `top_n` candidates so the agent can read scores even outside the panel. |
| `--refine-iters` | `5` | `1 .. 10` | Increase ONLY if the refined contour visibly under-grows (truncated at the rough region's boundary) — usually because the flake has a gradual colour gradient that needs more iterations to walk through. Decrease only if a candidate is over-growing into bulk (rare with current λ). |
| `--min-cc-um2` | `20` | `10 .. 50` | Lower when a small graphite gets thrown out at the carving step — typical signal: the desired candidate appears in `01_graphite_on_bottom.png` but not in `refined_candidates.png`. Raise only when many tiny noise CCs clutter the panel. |
| `--refine-lambda` | `0.5` | `0.3 .. 1.0` | Lower (0.3) if a candidate is flooding into bulk during refinement. Raise (0.7+) ONLY if the candidate is under-growing AND `refine-iters` already at max — the candidate's colour is unusually close to bulk and needs the LAB test relaxed. |

`--cluster-id` is the only parameter the agent uses every run. **Everything else is fallback** for the case where the agent looked at `refined_candidates.png` and the graphite it wants to pick isn't there — at which point the agent reasons about *why* (too small? excluded by area floor? not refined enough? flooded?) and adjusts the appropriate parameter.

### Usage

```bash
# First pass — auto-pick top, generate refined_candidates.png for review
${PYTHON_PATH:-conda run -n instrMCPdev python} graphite.py \
    --image <bottom_part.jpg> --pixel-size <um/px> --output-dir <path>

# Agent reviewed the panel — graphite is rank #2 in the panel
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
| `01_graphite_on_bottom.png` | Selected candidate's contour over a desaturated image — quick visual sanity check on the final outline. |

### Reading `graphite_result.json`

- `selected.rank` / `selected.score` — the candidate written to `graphite_mask.png`
- `low_confidence: true` when `score < 0.40` or when carving produced no candidates — the orchestrator should escalate to vision-review
- `top_candidates[]` — full list (length up to `top_n`) with `score`, `s_strip`, `s_central`, `s_gray`, `s_contrast`, `s_cohere`, `aspect`, `chroma`, `contrast_vs_bulk`, `area_um2`, `refined_area_um2`, `source_Ks` (which K values produced the candidate), `lab_ids` (which carved CCs were merged)
- `host.mu_bulk_lab` — mode of host LAB (the "bulk hBN colour" reference used by `s_contrast`)
- `substrate.mu_lab` — substrate sample from the joint histogram

---

## Graphene Detection — Tuning Guide

**Method**: Isolates the top flake, generates signed bright/dark contrast candidates in LAB space, ranks them by area/contrast/shape plus footprint containment when available, then writes a candidate panel for agent review.

**Key insight**: The auto-selection is rank #0, but the final authority is the candidate panel. Use `--cluster-id <rank>` when the highlighted candidate is an artifact, hBN region, or only a fragment while another panel isolates graphene better.

### What to look for in 00_graphene_candidates.png

| What you see | What's wrong | Action |
|-------------|-------------|--------|
| One panel highlights the graphene region within the flake | Correct | Use `--cluster-id <N>` if not auto-selected |
| Auto-selected panel includes artifacts/reflections along with graphene | Wrong ranked candidate | Override with a panel that shows just the graphene region |
| Graphene region is split or partial | Selected seed is only a fragment | Prefer a panel with fuller graphene coverage; if none exists, keep default and note low confidence |
| No panel clearly isolates graphene | Candidate pool is ambiguous | Inspect `graphene_result.json.top_candidates`; retry only if the image/footprint inputs are wrong |

### Important: --mirror flag

If the align step used `--mirror` for the top_part, **you must also pass --mirror here**. The graphene detection must operate in the same coordinate system as the alignment warp.

```bash
# Pass 1: auto-detect + review
${PYTHON_PATH:-conda run -n instrMCPdev python} graphene.py \
    --image <top_part.jpg> --pixel-size <um/px> --mirror --output-dir <path>

# Pass 2: override after reviewing 00_graphene_candidates.png
${PYTHON_PATH:-conda run -n instrMCPdev python} graphene.py \
    --image <top_part.jpg> --pixel-size <um/px> --mirror \
    --cluster-id 0 --output-dir <path>
```

**Outputs**: `graphene_mask.png`, `graphene_contour.npy`, `graphene_result.json` (`selected`, `selected_rank`, `top_candidates[]`), `00_graphene_candidates.png` (ranked candidate panel), `02_graphene_on_top.png` (final selected overlay)

---

## Bottom hBN Detection

**Method**: shares the first step of `graphite.py` to get a non-substrate host prior, then classifies the hBN subregion inside that host before warping it to full_stack coordinates.

1. Substrate sample `mu_sub` = LAB peak of `H_corners × H_image × L` (joint histogram mode of corner + image pixels, weighted by brightness).
2. Host mask = pixels with LAB distance to `mu_sub` above plateau-midpoint `T*` (multi-scale local-baseline peak detection on `dA(T)/dT`, midpoint of the valley between substrate peak and first-flake peak).
3. Morph clean → keep largest CC → 4-corner flood-fill-holes.
4. Warp from bottom_part to full_stack coords via the SIFT-derived affine matrix from align step.
5. Final fixed 1.5 µm dilation to match the current GT-dilation convention.

`compute_host` is imported directly from `graphite.py` — single source of truth for substrate detection. No priors, no fitted thresholds, no `bottom_hbn_shape_priors.json` dependency.

### Edge cases

| Symptom | Likely cause | Action |
|---|---|---|
| Contour traces the whole visible cyan region but GT polygon is smaller | The visible flake is bottom_hBN ∪ graphite/gold merged in 2D — `combine.py` doesn't mind because graphite is detected independently | None; the union is the right answer for downstream alignment |
| Contour offset from visible flake | SIFT warp inaccurate | Check inliers reported by `sift_align.py`; rerun with adjusted parameters |
| Empty / very small mask (`low_confidence: true`) | `T*` landed at the top of its sweep (no flake peak detected) — bottom_part has no clearly-separable foreground material | Inspect `bottom_hbn_result.json` for `t_star` and `substrate.mu_lab`; if `t_star ≈ 80` the algorithm couldn't find a flake peak. May need vision-review |
| Host extends across bare substrate (gold-backgate stacks like HM05) | Gold backgate is correctly classified as non-substrate and gets included | Expected — `combine.py` aligns the union (hBN + gold), and `graphite.py` independently localises the gold |

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

**Method**: Copies the footprint from the align step. No detection is performed — top hBN IS the footprint.

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
      "area_px": 103546,
      "area_um2": 783.74,
      "coordinate_system": "bottom_part",
      "mirrored": false
    },
    "graphene": {
      "mask_file": "graphene_mask.png",
      "contour_file": "graphene_contour.npy",
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
3. Set `mirrored: true` for graphene if `--mirror` was used
4. All `mask_file` and `contour_file` paths are relative to the detect output directory
5. Write to `<detect_output_dir>/detections.json`

---

## Coordinate Systems

Each detect script operates in its source image's native coordinate system. The combine step handles all transforms.

| Material | Source Image | Detection Coords | Output Coords | Mirror |
|----------|-------------|-----------------|---------------|--------|
| graphite | bottom_part | bottom_part | bottom_part | no |
| graphene | top_part | top_part | top_part (mirrored if --mirror) | depends |
| bottom_hBN | bottom_part | bottom_part → warped to full_stack | full_stack | no |
| top_hBN | full_stack_raw | full_stack | full_stack | no |
