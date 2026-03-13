# flakedetect_v1 — Split Workflow Design

**Date**: 2026-03-13
**Context**: Rebuild the monolithic flakedetect pipeline into 5 independent sub-skills, each invocable separately with file-based data flow. Designed for 5 parallel implementation agents.

---

## 1. Overview

### 1.1 Motivation

The current flakedetect skill runs everything in one `pipeline.py` — alignment, detection, combination, validation, overlay, commit. This makes it:
- Hard to debug individual stages
- Impossible for the agent to intervene mid-pipeline (e.g., rotation quadrant selection)
- Difficult to retry one stage without re-running everything

flakedetect_v1 splits the pipeline into 5 sub-skills that the agent orchestrates sequentially.

### 1.2 Workflow Order

```
1. align   → warp matrices, footprint mask, diagnostic images
2. detect  → per-material masks and contours
3. combine → coordinate transforms, traces.json, overlays on raw/LUT
4. commit  → agent inserts polygons into KLayout via MCP tools
5. review  → agent examines committed result in KLayout, pass/fail/retry
```

### 1.3 Dependencies

Required Python packages (available in conda env `instrMCPdev`). All scripts must be run via `conda run -n instrMCPdev python <script>`.

- `opencv-python-headless`
- `numpy`
- `scipy` (differential_evolution, KDTree, minimize)
- `scikit-learn` (KMeans)

### 1.4 Error Conventions

All scripts:
- Exit 0 on success, exit 1 on failure
- Print `ERROR: <message>` to stderr on failure
- If partial results are available (e.g., footprint found but GrabCut failed), write them and set `"status": "partial"` in the report JSON
- Print a concise summary to stdout on success

---

## 2. Directory Structure

```
skills/flakedetect_v1/
├── scripts/
│   └── core.py                        # Shared utilities
├── align/
│   ├── SKILL.md                       # name: flakedetect_v1:align
│   └── scripts/
│       ├── sift_align.py              # Same-substrate SIFT+RANSAC
│       ├── footprint.py               # Shape-guided K-means + GrabCut
│       ├── sweep.py                   # Coarse rotation sweep (Chamfer+DE)
│       ├── refine.py                  # Fine DE + L-BFGS-B + multi-restart
│       └── source_contour.py          # Top flake segmentation from source
├── detect/
│   ├── SKILL.md                       # name: flakedetect_v1:detect
│   └── scripts/
│       ├── graphite.py                # Graphite from bottom_part (LAB L)
│       ├── graphene.py                # Graphene from top_part (K-means brightest)
│       ├── bottom_hbn.py              # Bottom hBN from full_stack (K-means)
│       └── top_hbn.py                 # Top hBN = footprint from align
├── combine/
│   ├── SKILL.md                       # name: flakedetect_v1:combine
│   └── scripts/
│       ├── transform.py               # All coord transforms (detect → full_stack)
│       ├── overlay.py                 # Contour overlays on raw/LUT images
│       └── ecc_register.py            # ECC raw↔LUT (translation only)
├── commit/
│   ├── SKILL.md                       # name: flakedetect_v1:commit
│   └── scripts/
│       └── commit.py                  # Commit traces to KLayout via MCP
└── review/
    ├── SKILL.md                       # name: flakedetect_v1:review
    └── scripts/
        └── review.py                  # Review committed result, pass/fail
```

### 2.1 Shared Module: core.py

Located at `skills/flakedetect_v1/scripts/core.py`. All sub-skill scripts add this directory to `sys.path` and import from here. Carries over from existing `skills/flakedetect/scripts/core.py`:

- `morph_clean(mask, close_k, open_k)` — morphological close then open
- `flood_fill_holes(mask)` — fill interior holes
- `keep_largest_n(mask, n, min_area)` — keep N largest connected components
- `mask_centroid(mask)` — centroid via image moments
- `desaturate(image, factor)` — reduce saturation for overlay backgrounds
- `smooth_contour_polygon(contour, epsilon)` — Douglas-Peucker simplification
- `smooth_contour_gaussian(contour, sigma)` — Gaussian smooth closed contour
- `smooth_material(contour, material_name)` — material-appropriate smoothing

---

## 3. Shared Conventions

### 3.1 Warp Matrix Convention

All stored warp matrices are 2×3 float64 arrays compatible with `cv2.warpAffine`. The direction convention:

- **`warp_sift_bottom.npy`**: maps full_stack (ref) coordinates → bottom_part (mov) coordinates. To transform a point FROM bottom_part TO full_stack, use `cv2.invertAffineTransform()`.
- **`warp_top.npy`**: maps source (top_part, possibly mirrored) coordinates → full_stack coordinates. Apply directly to transform top_part contours/masks into full_stack space.

The difference in direction is because SIFT returns ref→mov (OpenCV convention), while the Chamfer+DE pipeline builds source→target directly via `make_warp()`.

### 3.2 Contour .npy Format

All `.npy` contour files use shape `(N, 2)` with dtype `float64`. Scripts must reshape and cast on save:
```python
np.save(path, contour.reshape(-1, 2).astype(np.float64))
```
Consuming scripts should handle `(N, 1, 2)` input gracefully by reshaping on load.

### 3.3 Mirror Convention

When `--mirror` is used, `cv2.flip(image, 1)` is applied before any processing. The resulting contours and masks are in the **mirrored** coordinate system. The `warp_top.npy` matrix from align already incorporates this flip — downstream consumers (combine, detect) apply the warp directly without an additional flip.

### 3.4 Material Color Palette (BGR)

Exact values carried over from existing `commit.py`:

| Material | BGR | Visual |
|----------|-----|--------|
| top_hBN | (0, 200, 0) | green |
| graphene | (0, 0, 255) | red |
| bottom_hBN | (255, 100, 0) | blue-ish |
| graphite | (0, 200, 255) | yellow |

---

## 4. Output Directory Convention

Each workflow writes to a subdirectory under a shared sample output directory:

```
<output-dir>/                          # e.g., /tmp/flakedetect_v1/ML09/
├── align/
│   ├── warp_sift_bottom.npy           # full_stack → bottom_part (2×3); invert for bottom→full
│   ├── warp_top.npy                   # top_part(mirrored) → full_stack (2×3)
│   ├── footprint_mask.png             # binary mask (uint8, 0/255)
│   ├── footprint_contour.npy          # (N,2) float64 contour of footprint
│   ├── source_contour.npy             # (N,2) float64 contour of source flake
│   ├── source_mask.png                # binary mask of source flake (uint8, 0/255)
│   ├── alignment_report.json          # metrics, params, status
│   ├── 01_source_contour.png          # top flake segmentation on source
│   ├── 02_cluster_map.png             # K-means 8-cluster visualization
│   ├── 03_footprint_candidates.png    # top-3 cluster combos with shape distances
│   ├── 04_footprint_grabcut.png       # GrabCut footprint on target image
│   ├── 05_sweep_grid.png              # coarse rotation candidates grid
│   ├── candidate_01.png              # individual candidate overlays (no numeric prefix,
│   ├── candidate_02.png              #   variable count avoids collision with final images)
│   ├── ...
│   ├── 20_best_overlay_raw.png        # final contours on raw image
│   ├── 21_mask_overlap.png            # green=overlap, red=target-only, blue=warped-only
│   └── 22_chamfer_heatmap.png         # distance-coded warped contour
├── detect/
│   ├── detections.json                # per-material detection results (assembled by agent)
│   ├── 01_graphite_on_bottom.png      # graphite detection overlay on bottom_part
│   ├── 02_graphene_on_top.png         # graphene detection overlay on top_part
│   ├── 03_bottom_hbn_on_full.png      # bottom hBN overlay on full_stack
│   ├── 04_top_hbn_footprint.png       # top hBN = footprint overlay on full_stack
│   ├── graphite_mask.png              # binary masks for each material
│   ├── graphite_contour.npy           # (N,2) float64 contours
│   ├── graphite_result.json           # sidecar: area_px, area_um2
│   ├── graphene_mask.png
│   ├── graphene_contour.npy
│   ├── graphene_result.json
│   ├── bottom_hbn_mask.png
│   ├── bottom_hbn_contour.npy
│   ├── bottom_hbn_result.json
│   ├── top_hbn_mask.png
│   ├── top_hbn_contour.npy
│   └── top_hbn_result.json
├── combine/
│   ├── traces.json                    # unified traces in full_stack coords
│   ├── combine_report.json            # includes raw2lut dx/dy
│   ├── graphite_full.png              # transformed masks in full_stack coords
│   ├── graphene_full.png
│   ├── bottom_hbn_full.png
│   ├── top_hbn_full.png
│   ├── overlay_raw.png                # all materials on full_stack_raw
│   ├── overlay_lut.png                # all materials on full_stack_LUT
│   └── mask_composite.png             # all material masks color-coded
├── commit/
│   └── commit_code.py               # generated pya code for KLayout
└── review/
    └── review_report.json            # quantitative review metrics + verdict
```

---

## 5. Data Flow Contract

This table maps every output file to its producing script and consuming script(s). This is the authoritative reference for cross-skill data flow.

| File | Produced By | Consumed By | Format | Notes |
|------|-------------|-------------|--------|-------|
| `align/warp_sift_bottom.npy` | `sift_align.py` | `transform.py` | (2,3) float64 | Direction: full_stack→bottom_part. Invert before applying to bottom_part contours. |
| `align/warp_top.npy` | `refine.py` | `transform.py` | (2,3) float64 | Direction: top_part(mirrored)→full_stack. Apply directly. |
| `align/footprint_mask.png` | `footprint.py` | `sweep.py`, `refine.py`, `top_hbn.py`, `transform.py` | uint8, 0/255 | Binary mask in full_stack coordinates |
| `align/footprint_contour.npy` | `footprint.py` | `sweep.py`, `refine.py`, `top_hbn.py` | (N,2) float64 | Contour of footprint boundary |
| `align/source_contour.npy` | `source_contour.py` | `sweep.py`, `refine.py` | (N,2) float64 | Contour of source flake (mirrored if --mirror) |
| `align/source_mask.png` | `source_contour.py` | `sweep.py`, `refine.py` | uint8, 0/255 | Binary mask of source flake (mirrored if --mirror) |
| `align/alignment_report.json` | `source_contour.py`, `footprint.py`, `sift_align.py`, `sweep.py`, `refine.py` | agent orchestration | JSON | See schema §7.1. Scripts write/update their own sections. |
| `detect/graphite_mask.png` | `graphite.py` | `transform.py` | uint8, 0/255 | In bottom_part coordinate system |
| `detect/graphite_contour.npy` | `graphite.py` | `transform.py` | (N,2) float64 | In bottom_part coordinate system |
| `detect/graphene_mask.png` | `graphene.py` | `transform.py` | uint8, 0/255 | In mirrored top_part coordinate system |
| `detect/graphene_contour.npy` | `graphene.py` | `transform.py` | (N,2) float64 | In mirrored top_part coordinate system |
| `detect/bottom_hbn_mask.png` | `bottom_hbn.py` | `transform.py` | uint8, 0/255 | Already in full_stack coordinate system |
| `detect/bottom_hbn_contour.npy` | `bottom_hbn.py` | `transform.py` | (N,2) float64 | Already in full_stack coordinate system |
| `detect/top_hbn_mask.png` | `top_hbn.py` | `transform.py` | uint8, 0/255 | Already in full_stack coordinate system (= footprint) |
| `detect/top_hbn_contour.npy` | `top_hbn.py` | `transform.py` | (N,2) float64 | Already in full_stack coordinate system |
| `detect/graphite_result.json` | `graphite.py` | agent (assembly) | JSON | `{"area_px": ..., "area_um2": ...}` |
| `detect/graphene_result.json` | `graphene.py` | agent (assembly) | JSON | `{"area_px": ..., "area_um2": ...}` |
| `detect/bottom_hbn_result.json` | `bottom_hbn.py` | agent (assembly) | JSON | `{"area_px": ..., "area_um2": ...}` |
| `detect/top_hbn_result.json` | `top_hbn.py` | agent (assembly) | JSON | `{"area_px": ..., "area_um2": ...}` |
| `detect/detections.json` | agent (assembled from sidecars) | `transform.py` | JSON | See schema §7.2. Agent constructs after running all detect scripts. |
| `combine/traces.json` | `transform.py` | `overlay.py`, review agent, commit agent | JSON | See schema §7.3. All contours in full_stack coords. |
| `combine/combine_report.json` | `ecc_register.py` (creates with `raw2lut`), `transform.py` (adds `transform_summary`), `overlay.py` (adds `overlay_files`) | review agent | JSON | See schema §7.4. Scripts append their sections in order. |

### 5.1 detections.json Assembly

The `detections.json` file is assembled by the orchestrating agent after running all four detect scripts. Each detect script outputs its own files independently, including a `<material>_result.json` sidecar with `area_px` and `area_um2`. The agent constructs `detections.json` by filling in the template from §7.2, pointing `mask_file` and `contour_file` fields to the actual output files in the detect directory, and copying `area_px` and `area_um2` from each sidecar JSON. This keeps individual detect scripts simple and decoupled while making assembly reliable (no stdout parsing needed).

---

## 6. Sub-Skill Specifications

### 6.1 align (flakedetect_v1:align)

The most complex sub-skill. Registers source images to the target (full_stack) coordinate system.

#### Method Auto-Detection

1. Try SIFT first (`sift_align.py`)
2. If ≥50 inliers → same-substrate, SIFT result is final
3. If 20-49 inliers → SIFT result is accepted but log `"quality": "warning"` in alignment_report.json
4. If <20 inliers → cross-substrate, enter Chamfer+DE pipeline

#### Two-Phase Invocation (Chamfer path)

**Phase 1 — sweep**: Agent calls scripts in order:
1. `source_contour.py` → produces `source_contour.npy`, `source_mask.png`
2. `footprint.py` → produces `footprint_mask.png`, `footprint_contour.npy`
3. `sweep.py` → consumes the above, produces candidate images + `alignment_report.json` with `"status": "needs_rotation_selection"`

**Phase 2 — refine**: Agent views candidate images, picks best rotation, runs:
4. `refine.py --rot-hint <degrees>` → consumes same contour/mask files, produces `warp_top.npy` + final diagnostics

For SIFT path (same-substrate), only `sift_align.py` is needed.

#### Scripts

**sift_align.py**
```
conda run -n instrMCPdev python sift_align.py \
    --source <image> --target <image> \
    --pixel-size <um/px> \
    --output-dir <path>
```
- Uses SIFT keypoints + BFMatcher + Lowe's ratio test + RANSAC
- Falls back to ECC translation if <10 SIFT matches
- Outputs: `warp_sift_bottom.npy`, updates `alignment_report.json`
- Diagnostic: `01_sift_matches.png` showing matched keypoints

**footprint.py**
```
conda run -n instrMCPdev python footprint.py \
    --source <image>              # source image (for shape reference)
    --target <image>              # target image (full_stack, for K-means)
    --mirror                      # mirror source before shape extraction
    --pixel-size <um/px> \
    --output-dir <path>
```
Shape-guided footprint construction per alignment lessons §1.4:
1. Segment top flake from source (largest saturated region) → compute Hu moments, convexity, solidity
2. K-means (8 clusters) on target in LAB color space
3. Enumerate viable cluster subsets (2-5 clusters), filter by: exclude low-saturation substrate clusters, exclude tiny clusters (<5% image area), require merged area within 2× of source flake area (adjusted for plausible scale range 0.3-2.0)
4. For each viable subset: merge → morph_clean → largest component → cv2.matchShapes() + convexity/solidity distance to source flake
5. Select best combination
6. GrabCut refinement: erode = definite FGD, as-is = probable FGD, dilate = probable BGD, rest = definite BGD
7. Outputs: `footprint_mask.png`, `footprint_contour.npy`, diagnostic images (`02_cluster_map.png`, `03_footprint_candidates.png`, `04_footprint_grabcut.png`)

**source_contour.py**
```
conda run -n instrMCPdev python source_contour.py \
    --image <image>               # source image (e.g., top_part)
    --mirror                      # horizontal flip (PDMS transfers)
    --output-dir <path>
```
- Extracts the largest flake contour from the **source** image (e.g., top_part on PDMS). This produces a shape reference used by `sweep.py` and `refine.py` to match against the footprint on the target image.
- Auto-segments: grayscale + saturation thresholding → morph clean → largest component → contour
- If `--mirror`: applies `cv2.flip(image, 1)` before segmentation
- Outputs: `source_contour.npy`, `source_mask.png`, `01_source_contour.png` (diagnostic overlay)

**Note:** `source_contour.py` and `footprint.py` both segment flakes but from different images for different purposes. `source_contour.py` segments the source image (e.g., top_part on PDMS) to get the flake's shape as a contour for alignment matching. `footprint.py` segments the **target** image (full_stack on SiO2) using K-means + GrabCut to find where that same flake landed after transfer. The source shape is used by `footprint.py` only as a shape-similarity reference (Hu moments, convexity) to guide cluster selection — it does its own independent segmentation on the target.

**sweep.py**
```
conda run -n instrMCPdev python sweep.py \
    --source-contour <align/source_contour.npy> \
    --source-mask <align/source_mask.png> \
    --footprint-contour <align/footprint_contour.npy> \
    --footprint-mask <align/footprint_mask.png> \
    --target-image <full_stack_raw.jpg>   # for drawing candidate overlays
    --pixel-size <um/px> \
    --output-dir <path>
```
- Coarse rotation sweep: 12° steps over [-180°, 180°]
- For each step: quick DE over (scale, dx, dy) with chamfer_contained cost
- Cost function: `fwd_chamfer_squared + 3000 * outside_fraction + 500 * oob_penalty`
- Scale bounds: [0.3, 2.0], translation bounds: [-W/2, W/2], [-H/2, H/2]
- Saves top 5-8 candidate overlay images (`candidate_01.png`, ...) + sweep grid (`05_sweep_grid.png`)
- Writes `alignment_report.json` with `"status": "needs_rotation_selection"` and `sweep_candidates` array

**refine.py**
```
conda run -n instrMCPdev python refine.py \
    --source-contour <align/source_contour.npy> \
    --source-mask <align/source_mask.png> \
    --footprint-contour <align/footprint_contour.npy> \
    --footprint-mask <align/footprint_mask.png> \
    --target-image <full_stack_raw.jpg>   # for diagnostic overlays
    --rot-hint <degrees>          # from agent's visual selection
    --scale-hint <value>          # (optional) narrow scale range
    --pixel-size <um/px> \
    --output-dir <path>
```
- Narrow bounds: rot ±15° around hint, scale from sweep result ±0.1 (or ±0.2 if no scale-hint)
- Full DE (popsize=50, maxiter=500)
- L-BFGS-B from DE solution
- 100-150 multi-restart with small perturbations
- Outputs: `warp_top.npy`, `20_best_overlay_raw.png`, `21_mask_overlap.png`, `22_chamfer_heatmap.png`
- Updates `alignment_report.json` with final metrics and `"status": "complete"`

#### Cost Function

Identical to the proven v8 recipe (alignment lessons §1.5):

```
cost = fwd_chamfer_squared + λ_contain * outside_fraction + λ_oob * oob_penalty
```

- `fwd_chamfer_squared`: mean(d²) where d = KDTree nearest distance from warped source contour points to target footprint contour points
- `outside_fraction`: count(warped_mask AND NOT footprint_mask) / count(warped_mask)
- `oob_penalty`: count(warped contour points outside image bounds) / total points
- Weights: λ_contain = 3000, λ_oob = 500
- Early exit: if >30% of warped points are out of bounds, return 1e6

#### Key Design Decisions

- **No hardcoded rotation/scale ranges**: ML04 needed rot~-42°, scale~0.55. ML09 needed rot~+115°, scale~1.04. The sweep covers full [-180°, 180°] with scale [0.3, 2.0].
- **Mirror is a search parameter**: test both flipped and unflipped when `--mirror` flag is set.
- **Agent selects rotation quadrant**: Chamfer cost alone has deceptive minima (ML09: wrong rotation had 40% lower cost). The coarse sweep saves candidate images for the agent to judge visually.

#### Reference Code

- Working ML09 script: `/tmp/ML09_chamfer_v8.py`
- Existing align.py: `skills/flakedetect/scripts/align.py` (SIFT, ECC, make_warp, warp_contour utilities)
- Alignment lessons: `docs/alignment-lessons-2026-03-13.md`

---

### 6.2 detect (flakedetect_v1:detect)

Per-material detection from optimal source images. Each material has a dedicated script. Each script writes its own outputs independently, including a `<material>_result.json` sidecar with `area_px` and `area_um2` for reliable `detections.json` assembly. The orchestrating agent assembles `detections.json` after all scripts complete (see §5.1).

#### Scripts

**graphite.py**
```
conda run -n instrMCPdev python graphite.py \
    --image <bottom_part.jpg> \
    --pixel-size <um/px> \
    --output-dir <path>
```
- Detection method: LAB L-channel, low percentile thresholding (darkest regions)
- Morph clean with light parameters (close=7, open=3) — graphite strips are thin
- Outputs: `graphite_mask.png`, `graphite_contour.npy`, `01_graphite_on_bottom.png` (diagnostic), `graphite_result.json` (sidecar with `area_px`, `area_um2`)

**graphene.py**
```
conda run -n instrMCPdev python graphene.py \
    --image <top_part.jpg> \
    --pixel-size <um/px> \
    --mirror                      # match align's mirror setting
    --output-dir <path>
```
- Detection method: K-means sub-clustering of the brightest region
- If `--mirror`: applies `cv2.flip(image, 1)` before detection. Output contour/mask are in mirrored coordinates.
- Outputs: `graphene_mask.png`, `graphene_contour.npy`, `02_graphene_on_top.png` (diagnostic), `graphene_result.json` (sidecar with `area_px`, `area_um2`)

**bottom_hbn.py**
```
conda run -n instrMCPdev python bottom_hbn.py \
    --image <full_stack_raw.jpg> \
    --footprint-mask <align/footprint_mask.png>   # to exclude footprint region
    --pixel-size <um/px> \
    --output-dir <path>
```
- Detection method: runs its own independent K-means clustering in LAB/HSV space. Uses `--footprint-mask` to exclude the top hBN region from bottom hBN candidates.
- Outputs: `bottom_hbn_mask.png`, `bottom_hbn_contour.npy`, `03_bottom_hbn_on_full.png` (diagnostic), `bottom_hbn_result.json` (sidecar with `area_px`, `area_um2`)

**top_hbn.py**
```
conda run -n instrMCPdev python top_hbn.py \
    --footprint-mask <align/footprint_mask.png>   # from align
    --footprint-contour <align/footprint_contour.npy>  # (optional) from align
    --image <full_stack_raw.jpg>                  # for overlay drawing
    --pixel-size <um/px> \
    --output-dir <path>
```
- Top hBN IS the footprint from align — this script copies `footprint_mask.png` → `top_hbn_mask.png` and copies `footprint_contour.npy` → `top_hbn_contour.npy` (preserving the exact same contour, no re-extraction). Draws the diagnostic overlay.
- Outputs: `top_hbn_mask.png`, `top_hbn_contour.npy`, `04_top_hbn_footprint.png` (diagnostic), `top_hbn_result.json` (sidecar with `area_px`, `area_um2`)

#### Reference Code

- Existing detect.py: `skills/flakedetect/scripts/detect.py` (detect_graphite, detect_graphene, detect_bottom_hbn, detect_footprint)

---

### 6.3 combine (flakedetect_v1:combine)

Transforms all detection results into the full_stack coordinate system, produces the unified traces JSON and overlay images.

#### Scripts

**transform.py**
```
conda run -n instrMCPdev python transform.py \
    --detections <detect/detections.json> \
    --align-dir <align/>              # directory containing warp matrices + footprint
    --image <full_stack_raw.jpg>      # reference image (for size)
    --pixel-size <um/px> \
    --output-dir <path>
```
Input contract — reads from `--align-dir`:
- `warp_sift_bottom.npy` — for graphite contour transform (inverted before use)
- `warp_top.npy` — for graphene mask/contour transform (applied directly)
- `footprint_mask.png` — for graphene clipping (graphene must be inside top hBN)

Input contract — reads from `--detections` JSON:
- `materials.*.mask_file` and `materials.*.contour_file` paths (relative to detect dir)
- `materials.*.coordinate_system` to determine which warp to apply

Transforms:
- graphite: load contour → invert `warp_sift_bottom` → apply inverse to transform bottom_part→full_stack
- graphene: load mask → apply `warp_top` directly (already includes mirror) → clip to footprint → morph clean
- bottom_hBN: already in full_stack coords, pass through
- top_hBN: already in full_stack coords (= footprint), pass through
- All materials: apply `core.smooth_material()` after transform

Outputs: `traces.json`, per-material transformed masks (`graphite_full.png`, `graphene_full.png`, `bottom_hbn_full.png`, `top_hbn_full.png`) — all in full_stack coords in the combine output dir

**ecc_register.py**
```
conda run -n instrMCPdev python ecc_register.py \
    --raw <full_stack_raw.jpg> \
    --lut <full_stack_w_LUT.jpg> \
    --output-dir <path>
```
- Computes ECC **translation** alignment (`cv2.MOTION_TRANSLATION`) between raw and LUT images
- Outputs dx, dy to `combine_report.json`. Rotation and scale are included but always ~0 and ~1.0 for this translation-only model.
- Typically: dx~71, dy~56

**overlay.py**
```
conda run -n instrMCPdev python overlay.py \
    --traces <combine/traces.json> \
    --raw <full_stack_raw.jpg> \
    --lut <full_stack_w_LUT.jpg> \           # (optional)
    --combine-report <combine/combine_report.json>  # for raw2lut dx/dy
    --output-dir <path>
```
- Reads dx, dy from `combine_report.json` `raw2lut` section (no manual plumbing needed)
- Draws material contours on desaturated background images using exact BGR palette from §3.4
- For LUT overlay: shifts each contour point by (dx, dy) before drawing
- Outputs: `overlay_raw.png`, `overlay_lut.png`, `mask_composite.png`

#### Reference Code

- Existing combine.py: `skills/flakedetect/scripts/combine.py` (transform_contour, build_masks, extract_contours, build_traces_json)
- Existing commit.py: `skills/flakedetect/scripts/commit.py` (draw_overlay, draw_overlay_on_lut)

---

### 6.4 commit (flakedetect_v1:commit)

Commits detected traces to KLayout as polygons on the appropriate layers.

#### Script

**commit.py**
```
conda run -n instrMCPdev python commit.py \
    --traces <combine/traces.json> \
    --output-dir <path>
```
- Reads `traces.json` and generates pya code to insert polygons into KLayout
- Coordinate transform: image-origin (0,0 top-left, y-down) → KLayout-centered (0,0 center, y-up):
  - `kl_x = img_x_um - w_um/2`
  - `kl_y = h_um/2 - img_y_um`
- Uses pya.Region for merging overlapping polygons per material
- Layer assignments from traces.json `layer_map`:
  - top_hBN → 10/0
  - graphene → 11/0
  - bottom_hBN → 12/0
  - graphite → 13/0
- Outputs the pya code string to stdout (or a file) for the agent to submit via MCP `execute_script`
- The agent calls this script, then passes the generated code to the MCP tool

#### Reference Code

- Existing commit.py: `skills/flakedetect/scripts/commit.py` (build_pya_code_traces, lines 395-450). Port the coordinate transform and Region-based merging. The y-axis sign flip (`h_um/2 - img_y_um`) is the most common source of errors.

---

### 6.5 review (flakedetect_v1:review)

Reviews the committed result. Review runs **after** commit so the agent can examine the actual polygons in KLayout, not just the overlay images.

#### Script

**review.py**
```
conda run -n instrMCPdev python review.py \
    --traces <combine/traces.json> \
    --combine-report <combine/combine_report.json> \
    --alignment-report <align/alignment_report.json> \
    --output-dir <path>
```
- Computes quantitative review metrics from the committed traces: area ratios, containment checks (graphene inside top_hBN), alignment quality scores
- Outputs `review_report.json` with per-material pass/fail and overall verdict
- The agent uses this report plus its own visual inspection (KLayout screenshot + overlay images) to make the final pass/fail decision

#### Agent Review Protocol

1. Agent takes a KLayout screenshot (via MCP `screenshot` tool) to see the committed polygons overlaid on the background image
2. Agent also reads `combine/overlay_raw.png` and `combine/overlay_lut.png` for comparison
3. Agent visually inspects using structured questions:
   - "Do the green (top_hBN, BGR 0,200,0) contours follow the flake boundary in the full_stack image?"
   - "Is the red (graphene, BGR 0,0,255) contour fully contained within the green (top_hBN) contour?"
   - "Does the yellow (graphite, BGR 0,200,255) contour align with the dark strip visible in the image?"
   - "Do the committed KLayout polygons match the overlay images?"
   - "Rate this result: excellent / good / needs work."
4. Decision:
   - **PASS**: Done — stack detection complete
   - **FAIL — alignment issue**: Re-run align with different parameters (wider bounds, different rotation quadrant), then redo detect → combine → commit → review
   - **FAIL — detection issue**: Re-run specific detect script with adjusted parameters, then redo combine → commit → review
   - **FAIL — commit issue**: Re-run commit (coordinate transform or layer assignment error)
5. Agent documents decision and reasoning

#### Vision Feedback Principles (per alignment lessons §5)

- **Structured prompts, not open-ended.** Multiple-choice or specific questions get reliable answers.
- **Show raw image as context.** The vision model needs to see the actual flake, not just contour outlines.
- **Budget calls.** Max ~10 vision calls per full workflow cycle.
- **Vision for coarse judgment, metrics for precision.** Reliably distinguishes "completely wrong" from "roughly right."
- **The source images are always available as ground truth reference.** Include them in comparisons.

---

## 7. JSON Schemas

### 7.1 alignment_report.json

```json
{
  "status": "complete | needs_rotation_selection | partial",
  "alignments": {
    "bottom": {
      "method": "sift",
      "quality": "good | warning",
      "warp_file": "warp_sift_bottom.npy",
      "n_inliers": 196,
      "scale": 1.002,
      "rotation_deg": -0.3
    },
    "top": {
      "method": "chamfer",
      "warp_file": "warp_top.npy",
      "rotation_deg": 115.4,
      "scale": 1.041,
      "dx_px": -18.9,
      "dy_px": -34.7,
      "mirror": true,
      "fwd_chamfer_um": 2.02,
      "iou": 0.745,
      "top_containment": 0.942,
      "outside_fraction": 0.058,
      "cost": 42.3
    }
  },
  "sweep_candidates": [
    {
      "rank": 1,
      "rotation_deg": 115.0,
      "scale": 1.04,
      "cost": 45.2,
      "image": "candidate_01.png"
    }
  ],
  "footprint": {
    "cluster_ids": [2, 4, 6],
    "shape_distance": 0.023,
    "grabcut_area_px": 548000,
    "mask_file": "footprint_mask.png",
    "contour_file": "footprint_contour.npy"
  },
  "source": {
    "contour_file": "source_contour.npy",
    "mask_file": "source_mask.png",
    "mirrored": true
  },
  "pixel_size_um": 0.077,
  "source_image": "/path/to/top_part.jpg",
  "target_image": "/path/to/full_stack_raw.jpg"
}
```

### 7.2 detections.json

Assembled by the orchestrating agent after running all detect scripts. Template:

```json
{
  "pixel_size_um": 0.077,
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
      "area_px": 12500,
      "area_um2": 74.1,
      "coordinate_system": "bottom_part",
      "mirrored": false
    },
    "graphene": {
      "mask_file": "graphene_mask.png",
      "contour_file": "graphene_contour.npy",
      "area_px": 45000,
      "area_um2": 266.8,
      "coordinate_system": "top_part",
      "mirrored": true
    },
    "bottom_hBN": {
      "mask_file": "bottom_hbn_mask.png",
      "contour_file": "bottom_hbn_contour.npy",
      "area_px": 180000,
      "area_um2": 1067.2,
      "coordinate_system": "full_stack",
      "mirrored": false
    },
    "top_hBN": {
      "mask_file": "top_hbn_mask.png",
      "contour_file": "top_hbn_contour.npy",
      "area_px": 548000,
      "area_um2": 3249.5,
      "coordinate_system": "full_stack",
      "mirrored": false
    }
  }
}
```

Note: `mask_file` and `contour_file` paths are relative to the detect output directory. `coordinate_system` tells `transform.py` which warp to apply. When `mirrored: true`, the `warp_top.npy` matrix already incorporates the flip — apply it directly without additional flipping.

### 7.3 traces.json

```json
{
  "image": "/path/to/full_stack_raw.jpg",
  "pixel_size_um": 0.077,
  "image_size_px": [2592, 1944],
  "image_size_um": [199.584, 149.688],
  "stack": ["top_hBN", "graphene", "bottom_hBN", "graphite"],
  "layer_map": {
    "top_hBN": "10/0",
    "graphene": "11/0",
    "bottom_hBN": "12/0",
    "graphite": "13/0"
  },
  "materials": {
    "top_hBN": [
      {
        "id": 1,
        "contour_px": [[x1, y1], [x2, y2], "..."],
        "contour_um": [[x1, y1], [x2, y2], "..."],
        "area_um2": 3249.5,
        "num_points": 142
      }
    ],
    "graphene": ["..."],
    "bottom_hBN": ["..."],
    "graphite": ["..."]
  }
}
```

All contours in `traces.json` are in full_stack image coordinates (pixel origin top-left, y-down). The commit step converts to KLayout coordinates.

### 7.4 combine_report.json

```json
{
  "raw2lut": {
    "dx": 71.2,
    "dy": 56.1,
    "rotation_deg": 0.0,
    "scale": 1.0,
    "ecc_correlation": 0.97,
    "method": "ecc_translation"
  },
  "transform_summary": {
    "graphite": "bottom_part → full_stack via inverted warp_sift_bottom",
    "graphene": "top_part(mirrored) → full_stack via warp_top (direct)",
    "bottom_hBN": "already in full_stack coords (pass-through)",
    "top_hBN": "already in full_stack coords (= footprint, pass-through)"
  },
  "traces_file": "traces.json",
  "overlay_files": {
    "raw": "overlay_raw.png",
    "lut": "overlay_lut.png",
    "composite": "mask_composite.png"
  }
}
```

---

## 8. Cross-References

| Document | Purpose |
|----------|---------|
| `docs/alignment-lessons-2026-03-13.md` | Detailed alignment algorithm design, cost function derivation, lessons learned |
| `/tmp/ML09_chamfer_v8.py` | Working reference implementation for Chamfer+DE alignment |
| `skills/flakedetect/scripts/align.py` | Existing SIFT, ECC, make_warp, warp_contour utilities |
| `skills/flakedetect/scripts/detect.py` | Existing per-material detection functions |
| `skills/flakedetect/scripts/combine.py` | Existing coordinate transform and trace building |
| `skills/flakedetect/scripts/commit.py` | Existing overlay drawing and KLayout commit (build_pya_code_traces lines 395-450) |
| `skills/flakedetect/scripts/core.py` | Existing shared utilities |
