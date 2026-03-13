# flakedetect_v1 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build flakedetect_v1 as 5 independent sub-skills (align, detect, combine, commit, review) for van der Waals heterostructure stack detection.

**Architecture:** Each sub-skill has its own SKILL.md and scripts/ directory under `skills/flakedetect_v1/`. A shared `core.py` provides morphological and contour utilities. Scripts communicate via files in an output directory with subdirectories per stage. The orchestrating agent calls scripts sequentially and assembles intermediate JSON files.

**Tech Stack:** Python 3, OpenCV, NumPy, SciPy (differential_evolution, KDTree, minimize), scikit-learn (KMeans). Conda env `instrMCPdev`.

**Spec:** `docs/superpowers/specs/2026-03-13-flakedetect-v1-design.md`

---

## Chunk 1: Scaffold and Shared Infrastructure

### Task 0: Create directory structure and shared core.py

**Files:**
- Create: `skills/flakedetect_v1/scripts/core.py`
- Create: `skills/flakedetect_v1/align/SKILL.md`
- Create: `skills/flakedetect_v1/align/scripts/` (empty dir)
- Create: `skills/flakedetect_v1/detect/SKILL.md`
- Create: `skills/flakedetect_v1/detect/scripts/` (empty dir)
- Create: `skills/flakedetect_v1/combine/SKILL.md`
- Create: `skills/flakedetect_v1/combine/scripts/` (empty dir)
- Create: `skills/flakedetect_v1/review/SKILL.md`
- Create: `skills/flakedetect_v1/review/scripts/` (empty dir)
- Create: `skills/flakedetect_v1/commit/SKILL.md`
- Create: `skills/flakedetect_v1/commit/scripts/` (empty dir)
- Reference: `skills/flakedetect/scripts/core.py` (copy and verify)

- [x] **Step 1: Create directory tree** *(done — committed at 95b5fe2)*

```bash
mkdir -p skills/flakedetect_v1/{scripts,align/scripts,detect/scripts,combine/scripts,review/scripts,commit/scripts}
```

- [x] **Step 2: Copy core.py from existing flakedetect** *(done — committed at 95b5fe2)*

Read `skills/flakedetect/scripts/core.py`. Copy it verbatim to `skills/flakedetect_v1/scripts/core.py`. No modifications needed — all functions are already correct per the spec §2.1.

- [x] **Step 3: Write align/SKILL.md** *(done — committed at 95b5fe2)*

Read the spec §6.1 for the align sub-skill specification. Write the SKILL.md following the `nanodevice:routing` SKILL.md pattern (at `skills/nanodevice/routing/SKILL.md`). Must include:
- YAML frontmatter: `name: flakedetect_v1:align`
- Description and trigger phrases for alignment tasks
- Prerequisites section (KLayout, conda env, images)
- Two-phase workflow documentation: Phase 1 (source_contour → footprint → sweep) and Phase 2 (agent picks rotation → refine)
- SIFT auto-detection fallback documentation
- CLI reference for all 5 scripts with argument descriptions
- Diagnostic image descriptions so the agent knows what to look for

- [x] **Step 4: Write detect/SKILL.md** *(done — committed at 95b5fe2)*

Read spec §6.2. Write SKILL.md with:
- YAML frontmatter: `name: flakedetect_v1:detect`
- Trigger phrases for material detection tasks
- CLI reference for all 4 scripts
- Note about `detections.json` assembly by the orchestrating agent, including the template from spec §7.2

- [x] **Step 5: Write combine/SKILL.md** *(done — committed at 95b5fe2)*

Read spec §6.3. Write SKILL.md with:
- YAML frontmatter: `name: flakedetect_v1:combine`
- Trigger phrases for combining/transforming detections
- 3-step workflow: ecc_register → transform → overlay
- CLI reference for all 3 scripts
- Note about `combine_report.json` multi-writer pattern (ecc_register creates, transform adds, overlay adds)

- [x] **Step 6: Write commit/SKILL.md** *(done — committed at 95b5fe2)*

Read spec §6.4. Write SKILL.md with:
- YAML frontmatter: `name: flakedetect_v1:commit`
- The commit workflow: script generates pya code, agent submits via MCP
- Coordinate transform formula: `kl_x = img_x_um - w_um/2`, `kl_y = h_um/2 - img_y_um`
- Layer assignments table
- CLI reference for `commit.py`
- Reference to existing `commit.py::build_pya_code_traces()` as the code template

- [x] **Step 7: Write review/SKILL.md** *(done — committed at 95b5fe2)*

Read spec §6.5. Write SKILL.md with:
- YAML frontmatter: `name: flakedetect_v1:review`
- Note: review runs AFTER commit (agent examines committed polygons in KLayout)
- The complete agent review protocol (structured vision prompts, decision tree, retry logic)
- Copy the structured questions verbatim from spec §6.5
- CLI reference for `review.py`
- Vision feedback principles from alignment lessons §5

- [x] **Step 8: Commit scaffold** *(done — committed at 95b5fe2)*

```bash
git add skills/flakedetect_v1/
git commit -m "feat(flakedetect_v1): scaffold directory structure with SKILL.md files and core.py"
```

---

## Chunk 2: align sub-skill (Agent 1)

The most complex sub-skill. 5 scripts implementing SIFT auto-detection, shape-guided footprint construction, coarse rotation sweep, and fine optimization.

**Reference code the agent MUST read before starting:**
- `skills/flakedetect/scripts/align.py` — existing SIFT, ECC, `make_warp()`, `warp_contour()` utilities to port (use grep to find function definitions)
- `/tmp/ML09_chamfer_v8.py` — working Chamfer+DE reference (contains cost function, DE optimization, GrabCut footprint). **Note:** This is in `/tmp/` and may not survive reboots. If missing, the same algorithm is documented in `docs/alignment-lessons-2026-03-13.md` §1.5 and §1.6 with enough detail to reconstruct.
- `docs/alignment-lessons-2026-03-13.md` — algorithm design and lessons learned
- `docs/superpowers/specs/2026-03-13-flakedetect-v1-design.md` §3 (conventions), §5 (data flow), §6.1 (align spec), §7.1 (alignment_report.json schema)

### Task 1: Implement source_contour.py

**Files:**
- Create: `skills/flakedetect_v1/align/scripts/source_contour.py`
- Reference: `/tmp/ML09_chamfer_v8.py` (top contour extraction section)

- [x] **Step 1: Read reference code**

Read `/tmp/ML09_chamfer_v8.py` and find the top contour extraction section. This shows the hardcoded extraction: `gray > 40 and sat > 15`. The new script must generalize this with auto-segmentation.

Also read `skills/flakedetect/scripts/core.py` for `morph_clean`, `keep_largest_n`, `flood_fill_holes`, `mask_centroid`.

- [x] **Step 2: Implement source_contour.py**

Write the script with:
- CLI: `--image`, `--mirror`, `--output-dir` (argparse)
- `sys.path.insert` to import from `../../scripts/core.py`
- If `--mirror`: `cv2.flip(image, 1)` before processing
- Segmentation: convert to grayscale + HSV, threshold on brightness (gray > 40) AND saturation (hsv_s > 15), morph_clean(close=15, open=11), keep_largest_n(n=1, min_area=5000), flood_fill_holes
- Extract contour: `cv2.findContours`, take largest by area
- Save outputs per spec §3.2: `np.save("source_contour.npy", contour.reshape(-1,2).astype(np.float64))`
- Save source_mask.png: `cv2.imwrite`
- Draw diagnostic: contour overlay on original image → `01_source_contour.png`
- Write/update `alignment_report.json` with `"source"` section: `contour_file`, `mask_file`, `mirrored` (per spec §7.1)
- Print summary: area, point count
- Error handling per spec §1.4

- [x] **Step 3: Test with a real image**

```bash
conda run -n instrMCPdev python skills/flakedetect_v1/align/scripts/source_contour.py \
    --image /Volumes/RandomData/Stacks/ML09/top_part.jpg \
    --mirror \
    --output-dir /tmp/flakedetect_v1_test/align
```

Verify: `source_contour.npy` exists and has shape (N, 2), `source_mask.png` is a valid binary mask, `01_source_contour.png` shows a reasonable contour on the flake.

- [x] **Step 4: Commit**

```bash
git add skills/flakedetect_v1/align/scripts/source_contour.py
git commit -m "feat(flakedetect_v1:align): implement source_contour.py"
```

### Task 2: Implement footprint.py

**Files:**
- Create: `skills/flakedetect_v1/align/scripts/footprint.py`
- Reference: `/tmp/ML09_chamfer_v8.py` (GrabCut footprint section)
- Reference: `docs/alignment-lessons-2026-03-13.md` §1.4 (shape-guided cluster selection)

- [x] **Step 1: Read reference code and design**

Read `/tmp/ML09_chamfer_v8.py` and find the GrabCut footprint section. Note: cluster IDs [2,4,6] and cl5_clean are ML09-specific. The new script must auto-select clusters using shape matching.

Read `docs/alignment-lessons-2026-03-13.md` §1.4.1 for the shape-guided approach: Hu moments + convexity + solidity comparison against the source flake shape.

- [x] **Step 2: Implement footprint.py**

Write the script with:
- CLI: `--source`, `--target`, `--mirror`, `--pixel-size`, `--output-dir`
- `sys.path.insert` to import from `../../scripts/core.py`

**Source shape extraction** (for shape reference):
- If `--mirror`: flip source image
- Segment source flake same way as source_contour.py (gray+sat threshold)
- Compute shape descriptors: `cv2.HuMoments`, convexity = contour_area / convex_hull_area, solidity, aspect ratio

**K-means clustering on target**:
- Convert target to LAB, reshape to (N,3), `KMeans(n_clusters=8, n_init=10, random_state=42)`
- Save diagnostic `02_cluster_map.png`: each cluster a different color

**Cluster enumeration**:
- For each cluster, compute: mean saturation, area as fraction of image
- Filter out: substrate clusters (mean saturation < 20), tiny clusters (< 5% of image area)
- Enumerate subsets of size 2-5 from remaining clusters
- For each subset: merge masks → morph_clean(close=35, open=11) → keep_largest_n(n=1) → flood_fill_holes
- Filter: require merged area within 0.3×-2.0× of source flake area (accounting for scale range)
- Compute shape distance: `cv2.matchShapes(source_contour, candidate_contour, cv2.CONTOURS_MATCH_I1, 0)` + `|convexity_diff|` + `|solidity_diff|`
- Rank by shape distance, save top 3 as diagnostic `03_footprint_candidates.png`

**GrabCut refinement** (on best candidate):
- erode(25) = GC_FGD, as-is = GC_PR_FGD, dilate(35) = GC_PR_BGD, rest = GC_BGD
- `cv2.grabCut(target, gc_mask, None, bgd, fgd, 8, cv2.GC_INIT_WITH_MASK)`
- Extract result, morph_clean(close=15, open=7), keep_largest_n, flood_fill_holes
- Save `footprint_mask.png`, extract contour → save `footprint_contour.npy`
- Write/update `alignment_report.json` with `"footprint"` section: `cluster_ids`, `shape_distance`, `grabcut_area_px`, `mask_file`, `contour_file` (per spec §7.1)
- Save diagnostic `04_footprint_grabcut.png`

- [x] **Step 3: Test with real images**

```bash
conda run -n instrMCPdev python skills/flakedetect_v1/align/scripts/footprint.py \
    --source /Volumes/RandomData/Stacks/ML09/top_part.jpg \
    --target /Volumes/RandomData/Stacks/ML09/full_stack_raw.jpg \
    --mirror \
    --pixel-size 0.077 \
    --output-dir /tmp/flakedetect_v1_test/align
```

Verify: footprint_mask.png is a reasonable footprint, footprint_contour.npy has shape (N, 2), diagnostics show cluster map and candidates.

- [x] **Step 4: Commit**

```bash
git add skills/flakedetect_v1/align/scripts/footprint.py
git commit -m "feat(flakedetect_v1:align): implement footprint.py with shape-guided cluster selection"
```

### Task 3: Implement sweep.py

**Files:**
- Create: `skills/flakedetect_v1/align/scripts/sweep.py`
- Reference: `/tmp/ML09_chamfer_v8.py` (cost function + DE optimization sections)
- Reference: `skills/flakedetect/scripts/align.py` — grep for `make_warp` and `warp_contour` function definitions

- [x] **Step 1: Read reference code**

Read `/tmp/ML09_chamfer_v8.py` and find the cost function and DE optimization sections.
Read `skills/flakedetect/scripts/align.py` and find `make_warp()` and `warp_contour()` — port these as local functions or import from a shared align utilities module.

- [x] **Step 2: Implement sweep.py**

Write the script with:
- CLI: `--source-contour`, `--source-mask`, `--footprint-contour`, `--footprint-mask`, `--target-image`, `--pixel-size`, `--output-dir`
- Port `make_warp()` and `warp_contour()` from existing align.py (copy into the script or into a shared module within align/scripts/)

**Cost function** `chamfer_contained(params)`:
- Implement exactly per spec §6.1 Cost Function and the cost function section in `/tmp/ML09_chamfer_v8.py`
- Use `scipy.spatial.KDTree` on footprint contour points (subsample to ~800 points)
- Subsample source contour to ~600 points for speed
- `fwd_chamfer_squared + 3000 * outside_fraction + 500 * oob_penalty`

**Coarse rotation sweep**:
- Loop `rot` from -180 to 168 in 12° steps (30 steps total)
- For each rotation: `differential_evolution(cost, bounds=[(rot, rot), (0.3, 2.0), (-W/2, W/2), (-H/2, H/2)], maxiter=80, popsize=15, seed=42)`
- Note: rotation is fixed per step, DE optimizes scale + dx + dy only (3 free parameters)
- Record (rot, scale, dx, dy, cost)
- Sort by cost, take top 5-8 candidates

**Candidate visualization**:
- For each candidate: warp source contour, draw on desaturated target image → `candidate_01.png`, `candidate_02.png`, ...
- Create grid of all candidates → `05_sweep_grid.png`

**Write alignment_report.json**:
- Set `"status": "needs_rotation_selection"`
- Populate `sweep_candidates` array with rank, rotation, scale, cost, image filename

- [x] **Step 3: Test with outputs from steps 1-2**

```bash
conda run -n instrMCPdev python skills/flakedetect_v1/align/scripts/sweep.py \
    --source-contour /tmp/flakedetect_v1_test/align/source_contour.npy \
    --source-mask /tmp/flakedetect_v1_test/align/source_mask.png \
    --footprint-contour /tmp/flakedetect_v1_test/align/footprint_contour.npy \
    --footprint-mask /tmp/flakedetect_v1_test/align/footprint_mask.png \
    --target-image /Volumes/RandomData/Stacks/ML09/full_stack_raw.jpg \
    --pixel-size 0.077 \
    --output-dir /tmp/flakedetect_v1_test/align
```

Verify: candidate images exist, alignment_report.json has status "needs_rotation_selection", sweep_candidates has 5+ entries. For ML09, correct rotation should be ~115° — check if it appears in the candidates.

- [x] **Step 4: Commit**

```bash
git add skills/flakedetect_v1/align/scripts/sweep.py
git commit -m "feat(flakedetect_v1:align): implement sweep.py coarse rotation search"
```

### Task 4: Implement refine.py

**Files:**
- Create: `skills/flakedetect_v1/align/scripts/refine.py`
- Reference: `/tmp/ML09_chamfer_v8.py` (DE + L-BFGS-B + multi-restart optimization pipeline)

- [x] **Step 1: Read reference code**

Read `/tmp/ML09_chamfer_v8.py` and find the optimization pipeline sections: DE → L-BFGS-B → multi-restart.

- [x] **Step 2: Implement refine.py**

Write the script with:
- CLI: `--source-contour`, `--source-mask`, `--footprint-contour`, `--footprint-mask`, `--target-image`, `--rot-hint` (required), `--scale-hint` (optional), `--pixel-size`, `--output-dir`
- Same cost function as sweep.py (share via import or copy)
- Same `make_warp()` / `warp_contour()` utilities

**Bounds**:
- rotation: rot_hint ± 15°
- scale: scale_hint ± 0.1 if provided, else best_from_sweep ± 0.2 (clamp to [0.3, 2.0])
- dx, dy: [-W/2, W/2], [-H/2, H/2]

**Optimization pipeline**:
1. `differential_evolution(cost, bounds, maxiter=500, popsize=50, tol=1e-5, seed=42, mutation=(0.5, 1.5), recombination=0.9, polish=False)`
2. `minimize(cost, x0=de.x, method='L-BFGS-B', bounds=bounds, options={'maxiter': 1000})`
3. 150 multi-restart: perturb best by `randn * [4.0, 0.03, 12.0, 12.0]`, clip to bounds, `minimize` with L-BFGS-B

**Evaluation** (per v8 evaluation section):
- Forward Chamfer: mean, median, 90th percentile (in um)
- IoU: intersection / union of warped mask and footprint mask
- Containment: intersection / warped_mask_area
- Outside fraction: (warped AND NOT footprint) / warped_area

**Outputs**:
- `warp_top.npy`: final 2×3 warp matrix
- `20_best_overlay_raw.png`: warped contour (yellow) + footprint contour (green) on raw image with metrics text
- `21_mask_overlap.png`: green=overlap, red=footprint-only, blue=warped-only, with IoU and outside_frac text
- `22_chamfer_heatmap.png`: distance-coded warped contour (green=close, red=far) with mean chamfer text
- Update `alignment_report.json`: set `"status": "complete"`, fill in `alignments.top` with all metrics

- [x] **Step 3: Test with sweep output**

```bash
conda run -n instrMCPdev python skills/flakedetect_v1/align/scripts/refine.py \
    --source-contour /tmp/flakedetect_v1_test/align/source_contour.npy \
    --source-mask /tmp/flakedetect_v1_test/align/source_mask.png \
    --footprint-contour /tmp/flakedetect_v1_test/align/footprint_contour.npy \
    --footprint-mask /tmp/flakedetect_v1_test/align/footprint_mask.png \
    --target-image /Volumes/RandomData/Stacks/ML09/full_stack_raw.jpg \
    --rot-hint 115 \
    --pixel-size 0.077 \
    --output-dir /tmp/flakedetect_v1_test/align
```

Verify: warp_top.npy exists (2,3 shape), alignment_report.json shows status "complete", diagnostic images look reasonable. For ML09 reference: expect rot~115°, scale~1.04, fwd_chamfer~2um, IoU~0.74.

- [x] **Step 4: Commit**

```bash
git add skills/flakedetect_v1/align/scripts/refine.py
git commit -m "feat(flakedetect_v1:align): implement refine.py fine optimization"
```

### Task 5: Implement sift_align.py

**Files:**
- Create: `skills/flakedetect_v1/align/scripts/sift_align.py`
- Reference: `skills/flakedetect/scripts/align.py` — grep for `align_sift` and `align_ecc` function definitions

- [x] **Step 1: Read reference code**

Read `skills/flakedetect/scripts/align.py` and find `align_sift()` and `align_ecc()` functions. Port them, wrapping in a CLI script.

- [x] **Step 2: Implement sift_align.py**

Write the script with:
- CLI: `--source`, `--target`, `--pixel-size`, `--output-dir`
- Port `align_sift()` from existing align.py (SIFT + BFMatcher + Lowe's ratio + RANSAC)
- Port `align_ecc()` as fallback for <10 matches

**SIFT threshold logic** (per spec §6.1):
- ≥50 inliers: quality="good"
- 20-49 inliers: quality="warning"
- <20 inliers: print message that Chamfer pipeline should be used, exit with code 2 (special code meaning "fall back to Chamfer")

**Outputs**:
- `warp_sift_bottom.npy`: the 2×3 warp matrix (direction: full_stack→source, per spec §3.1)
- Update `alignment_report.json`: create file or update `alignments.bottom` section
- `01_sift_matches.png`: diagnostic showing matched keypoints between images

- [x] **Step 3: Test with same-substrate images**

```bash
conda run -n instrMCPdev python skills/flakedetect_v1/align/scripts/sift_align.py \
    --source /Volumes/RandomData/Stacks/ML09/bottom_part.jpg \
    --target /Volumes/RandomData/Stacks/ML09/full_stack_raw.jpg \
    --pixel-size 0.077 \
    --output-dir /tmp/flakedetect_v1_test/align
```

Verify: warp_sift_bottom.npy exists, alignment_report.json has `alignments.bottom` with n_inliers > 50 and quality "good".

- [x] **Step 4: Commit**

```bash
git add skills/flakedetect_v1/align/scripts/sift_align.py
git commit -m "feat(flakedetect_v1:align): implement sift_align.py same-substrate alignment"
```

---

## Chunk 3: detect sub-skill (Agent 2)

4 material detection scripts. Each operates independently on its source image.

**Testing dependency note:** `bottom_hbn.py` and `top_hbn.py` require `footprint_mask.png` from align. If the align sub-skill hasn't been run yet, create a placeholder by running `footprint.py` directly, or use any binary mask file as a stand-in for initial smoke testing. The E2E test in Chunk 5 validates the full pipeline.

**detections.json assembly:** After all 4 detect scripts complete, the orchestrating agent assembles `detect/detections.json` by hand using the template in spec §7.2. Each script just writes its own mask/contour/diagnostic files. See spec §5.1 for the assembly protocol.

**Reference code the agent MUST read before starting:**
- `skills/flakedetect/scripts/detect.py` — existing detection functions to port: grep for `detect_graphite()`, `detect_graphene()`, `detect_bottom_hbn()` definitions
- `skills/flakedetect/scripts/core.py` — morph utilities
- `docs/superpowers/specs/2026-03-13-flakedetect-v1-design.md` §3 (conventions), §5 (data flow), §6.2 (detect spec), §7.2 (detections.json schema)

### Task 6: Implement graphite.py

**Files:**
- Create: `skills/flakedetect_v1/detect/scripts/graphite.py`
- Reference: `skills/flakedetect/scripts/detect.py` function `detect_graphite()`

- [x] **Step 1: Read reference code** *(done)*

- [x] **Step 2: Implement graphite.py** *(done — K-means sub-clustering within hBN flake, two-pass candidate workflow with `--cluster-id` override)*

- [x] **Step 3: Test** *(done — tested on ML08 and ML09, graphite correctly detected with agent cluster override)*

- [x] **Step 4: Commit**

### Task 7: Implement graphene.py

**Files:**
- Create: `skills/flakedetect_v1/detect/scripts/graphene.py`
- Reference: `skills/flakedetect/scripts/detect.py` function `detect_graphene()`

- [x] **Step 1: Read reference code** *(done)*

- [x] **Step 2: Implement graphene.py** *(done — K-means sub-clustering within flake, two-pass candidate workflow with `--cluster-id` override)*

- [x] **Step 3: Test** *(done — tested on ML08 and ML09)*

- [x] **Step 4: Commit**

### Task 8: Implement bottom_hbn.py

**Files:**
- Create: `skills/flakedetect_v1/detect/scripts/bottom_hbn.py`
- Reference: `skills/flakedetect/scripts/detect.py` function `detect_bottom_hbn()`

- [x] **Step 1: Read reference code** *(done)*

- [x] **Step 2: Implement bottom_hbn.py** *(done — independent K-means with footprint exclusion, connected component selection)*

- [x] **Step 3: Test** *(done — tested on ML08 and ML09)*

- [x] **Step 4: Commit**

### Task 9: Implement top_hbn.py

**Files:**
- Create: `skills/flakedetect_v1/detect/scripts/top_hbn.py`

- [x] **Step 1: Implement top_hbn.py** *(done — footprint pass-through with optional contour extraction)*

- [x] **Step 2: Test** *(done — tested on ML08 and ML09)*

- [x] **Step 3: Commit**

---

## Chunk 4: combine sub-skill (Agent 3)

3 scripts: ECC registration, coordinate transforms, and overlay drawing.

**Testing dependency note:** `transform.py` requires outputs from both align and detect. `ecc_register.py` is fully independent (only needs raw + LUT images). For `transform.py` and `overlay.py` testing, use outputs from align and detect test runs, or create minimal stub files (a dummy `detections.json` with placeholder mask paths, a dummy `warp_sift_bottom.npy` as `np.eye(2,3)`, etc.).

**Reference code the agent MUST read before starting:**
- `skills/flakedetect/scripts/combine.py` — existing `transform_contour()`, `build_masks()`, `extract_contours()`, `build_traces_json()`
- `skills/flakedetect/scripts/commit.py` — existing `draw_overlay()`, `draw_overlay_on_lut()`
- `skills/flakedetect/scripts/align.py` — grep for `align_ecc()` function definition
- `docs/superpowers/specs/2026-03-13-flakedetect-v1-design.md` §3 (conventions), §5 (data flow), §6.3 (combine spec), §7.3-7.4 (JSON schemas)

### Task 10: Implement ecc_register.py

**Files:**
- Create: `skills/flakedetect_v1/combine/scripts/ecc_register.py`
- Reference: `skills/flakedetect/scripts/align.py` — grep for `align_ecc()` function definition

- [ ] **Step 1: Read reference code**

Read `align_ecc()` in existing align.py (use grep to find it). It uses histogram equalization + ECC with `cv2.MOTION_TRANSLATION`.

- [ ] **Step 2: Implement ecc_register.py**

Write the script with:
- CLI: `--raw`, `--lut`, `--output-dir`
- Convert both images to grayscale, equalize histograms
- `cv2.findTransformECC(eq_raw, eq_lut, warp_matrix, cv2.MOTION_TRANSLATION, criteria)`
- Extract dx, dy from the 2×3 warp matrix (warp[0,2] and warp[1,2])
- Create `combine_report.json` with `raw2lut` section: dx, dy, rotation_deg=0.0, scale=1.0, ecc_correlation, method="ecc_translation"
- Print summary: dx, dy, correlation

- [ ] **Step 3: Test**

```bash
conda run -n instrMCPdev python skills/flakedetect_v1/combine/scripts/ecc_register.py \
    --raw /Volumes/RandomData/Stacks/ML09/full_stack_raw.jpg \
    --lut /Volumes/RandomData/Stacks/ML09/full_stack_w_LUT.jpg \
    --output-dir /tmp/flakedetect_v1_test/combine
```

Verify: combine_report.json exists with dx~71, dy~56.

- [ ] **Step 4: Commit**

```bash
git add skills/flakedetect_v1/combine/scripts/ecc_register.py
git commit -m "feat(flakedetect_v1:combine): implement ecc_register.py raw-LUT translation"
```

### Task 11: Implement transform.py

**Files:**
- Create: `skills/flakedetect_v1/combine/scripts/transform.py`
- Reference: `skills/flakedetect/scripts/combine.py` (all functions)

- [ ] **Step 1: Read reference code**

Read existing combine.py thoroughly. Understand `transform_contour()`, `build_masks()`, `extract_contours()`, `build_traces_json()`.

- [ ] **Step 2: Implement transform.py**

Write the script with:
- CLI: `--detections` (JSON path), `--align-dir` (dir path), `--image` (full_stack), `--pixel-size`, `--output-dir`
- `sys.path.insert` to import from `../../scripts/core.py` (2 levels up: `combine/scripts/` → `flakedetect_v1/scripts/`)

**Load inputs** per spec §6.3 input contracts:
- From align-dir: load `warp_sift_bottom.npy`, `warp_top.npy`, `footprint_mask.png`
- From detections JSON: load mask/contour files for each material (paths relative to detect dir)

**Transform each material**:
- graphite (coordinate_system="bottom_part"): invert warp_sift_bottom, apply to contour, draw filled mask
- graphene (coordinate_system="top_part"): warpAffine mask with warp_top (INTER_NEAREST), clip to footprint, morph_clean(close=15, open=7), keep_largest_n, flood_fill_holes, then **re-extract contour via `cv2.findContours`** on the transformed mask (don't warp the contour directly — interpolation artifacts make mask-warp + re-extract more reliable)
- bottom_hBN (coordinate_system="full_stack"): pass through
- top_hBN (coordinate_system="full_stack"): pass through

**Post-transform**: apply `core.smooth_material()` to each material's contour

**Build traces.json** per spec §7.3: call equivalent of `build_traces_json()` from existing combine.py

**Save outputs**:
- `traces.json`
- Per-material full_stack masks: `graphite_full.png`, `graphene_full.png`, `bottom_hbn_full.png`, `top_hbn_full.png`
- Append `transform_summary` section to `combine_report.json` (read existing, add, rewrite)

- [ ] **Step 3: Test**

Requires outputs from align and detect. Create a minimal `detections.json` manually or use outputs from earlier tests.

```bash
conda run -n instrMCPdev python skills/flakedetect_v1/combine/scripts/transform.py \
    --detections /tmp/flakedetect_v1_test/detect/detections.json \
    --align-dir /tmp/flakedetect_v1_test/align \
    --image /Volumes/RandomData/Stacks/ML09/full_stack_raw.jpg \
    --pixel-size 0.077 \
    --output-dir /tmp/flakedetect_v1_test/combine
```

- [ ] **Step 4: Commit**

```bash
git add skills/flakedetect_v1/combine/scripts/transform.py
git commit -m "feat(flakedetect_v1:combine): implement transform.py coordinate transforms"
```

### Task 12: Implement overlay.py

**Files:**
- Create: `skills/flakedetect_v1/combine/scripts/overlay.py`
- Reference: `skills/flakedetect/scripts/commit.py` — grep for `draw_overlay` and `draw_overlay_on_lut` function definitions

- [ ] **Step 1: Read reference code**

Read `draw_overlay()` and `draw_overlay_on_lut()` in existing commit.py (use grep to find them).

- [ ] **Step 2: Implement overlay.py**

Write the script with:
- CLI: `--traces`, `--raw`, `--lut` (optional), `--combine-report`, `--output-dir`
- `sys.path.insert` to import `desaturate` from `../../scripts/core.py` (2 levels up: `combine/scripts/` → `flakedetect_v1/scripts/`)

**Raw overlay**:
- Desaturate raw image (factor=0.4)
- For each material in traces, draw contours with exact BGR colors from spec §3.4
- Add material name labels near centroids
- Save `overlay_raw.png`

**LUT overlay** (if --lut provided):
- Read dx, dy from combine_report.json `raw2lut` section
- Desaturate LUT image
- For each contour point: shift by (dx, dy) before drawing
- Save `overlay_lut.png`

**Mask composite**:
- Create blank image same size as raw
- For each material: fill mask region with its BGR color at 50% alpha
- Save `mask_composite.png`

**Update combine_report.json**: append `overlay_files` section

- [ ] **Step 3: Test**

```bash
conda run -n instrMCPdev python skills/flakedetect_v1/combine/scripts/overlay.py \
    --traces /tmp/flakedetect_v1_test/combine/traces.json \
    --raw /Volumes/RandomData/Stacks/ML09/full_stack_raw.jpg \
    --lut /Volumes/RandomData/Stacks/ML09/full_stack_w_LUT.jpg \
    --combine-report /tmp/flakedetect_v1_test/combine/combine_report.json \
    --output-dir /tmp/flakedetect_v1_test/combine
```

- [ ] **Step 4: Commit**

```bash
git add skills/flakedetect_v1/combine/scripts/overlay.py
git commit -m "feat(flakedetect_v1:combine): implement overlay.py contour visualization"
```

---

## Chunk 5: commit + review scripts and E2E test

Implements the commit and review scripts, then validates the full pipeline end-to-end.

### Task 13: Implement commit.py

**Files:**
- Create: `skills/flakedetect_v1/commit/scripts/commit.py`
- Reference: `skills/flakedetect/scripts/commit.py` — grep for `build_pya_code_traces` function definition

- [ ] **Step 1: Read reference code**

Read `build_pya_code_traces()` in existing commit.py (use grep to find it). This handles the coordinate transform and pya Region-based merging.

- [ ] **Step 2: Implement commit.py**

Write the script with:
- CLI: `--traces` (path to traces.json), `--output-dir`
- `sys.path.insert` to import from `../../scripts/core.py` if needed
- Read traces.json, iterate over materials
- For each material contour, apply coordinate transform:
  - `kl_x = img_x_um - w_um/2`
  - `kl_y = h_um/2 - img_y_um`
- Generate pya code string that:
  - Creates a cell per material (or uses existing cell)
  - Uses pya.Region to merge overlapping polygons
  - Assigns to correct layer from `layer_map` (top_hBN→10/0, graphene→11/0, bottom_hBN→12/0, graphite→13/0)
- Output the pya code to stdout (agent captures and submits via MCP `execute_script`)
- Also save the generated code to `commit/commit_code.py` in output-dir for debugging

- [ ] **Step 3: Test**

```bash
conda run -n instrMCPdev python skills/flakedetect_v1/commit/scripts/commit.py \
    --traces /tmp/flakedetect_v1_test/combine/traces.json \
    --output-dir /tmp/flakedetect_v1_test/commit
```

Verify: pya code is printed to stdout, `commit_code.py` exists in output dir.

- [ ] **Step 4: Commit**

```bash
git add skills/flakedetect_v1/commit/scripts/commit.py
git commit -m "feat(flakedetect_v1:commit): implement commit.py pya code generator"
```

### Task 14: Implement review.py

**Files:**
- Create: `skills/flakedetect_v1/review/scripts/review.py`

- [ ] **Step 1: Implement review.py**

Write the script with:
- CLI: `--traces` (path to traces.json), `--combine-report` (path to combine_report.json), `--alignment-report` (path to alignment_report.json), `--output-dir`
- `sys.path.insert` to import from `../../scripts/core.py` if needed
- Quantitative checks:
  - graphene containment: is graphene contour fully inside top_hBN contour? Compute overlap percentage.
  - Area sanity: are material areas within plausible ranges? (e.g., graphene area < top_hBN area)
  - Alignment quality: read alignment_report.json metrics (IoU, chamfer, containment)
- Output `review_report.json` with:
  - Per-material: `{"name": "graphene", "pass": true, "checks": {"containment": 0.98, ...}}`
  - Overall: `{"verdict": "pass|fail", "issues": [...]}`
- Print summary to stdout

- [ ] **Step 2: Test**

```bash
conda run -n instrMCPdev python skills/flakedetect_v1/review/scripts/review.py \
    --traces /tmp/flakedetect_v1_test/combine/traces.json \
    --combine-report /tmp/flakedetect_v1_test/combine/combine_report.json \
    --alignment-report /tmp/flakedetect_v1_test/align/alignment_report.json \
    --output-dir /tmp/flakedetect_v1_test/review
```

- [ ] **Step 3: Commit**

```bash
git add skills/flakedetect_v1/review/scripts/review.py
git commit -m "feat(flakedetect_v1:review): implement review.py quantitative checks"
```

### Task 15: End-to-end pipeline test

- [ ] **Step 1: Run the full pipeline manually**

Run all scripts in order on the ML09 sample data to verify the full data flow:

```bash
OUT=/tmp/flakedetect_v1_e2e/ML09
IMG=/Volumes/RandomData/Stacks/ML09

# 1. align
conda run -n instrMCPdev python skills/flakedetect_v1/align/scripts/sift_align.py \
    --source $IMG/bottom_part.jpg --target $IMG/full_stack_raw.jpg \
    --pixel-size 0.077 --output-dir $OUT/align

conda run -n instrMCPdev python skills/flakedetect_v1/align/scripts/source_contour.py \
    --image $IMG/top_part.jpg --mirror --output-dir $OUT/align

conda run -n instrMCPdev python skills/flakedetect_v1/align/scripts/footprint.py \
    --source $IMG/top_part.jpg --target $IMG/full_stack_raw.jpg \
    --mirror --pixel-size 0.077 --output-dir $OUT/align

conda run -n instrMCPdev python skills/flakedetect_v1/align/scripts/sweep.py \
    --source-contour $OUT/align/source_contour.npy \
    --source-mask $OUT/align/source_mask.png \
    --footprint-contour $OUT/align/footprint_contour.npy \
    --footprint-mask $OUT/align/footprint_mask.png \
    --target-image $IMG/full_stack_raw.jpg \
    --pixel-size 0.077 --output-dir $OUT/align

# (Agent examines candidates, picks rotation ~115°)

conda run -n instrMCPdev python skills/flakedetect_v1/align/scripts/refine.py \
    --source-contour $OUT/align/source_contour.npy \
    --source-mask $OUT/align/source_mask.png \
    --footprint-contour $OUT/align/footprint_contour.npy \
    --footprint-mask $OUT/align/footprint_mask.png \
    --target-image $IMG/full_stack_raw.jpg \
    --rot-hint 115 --pixel-size 0.077 --output-dir $OUT/align

# 2. detect
conda run -n instrMCPdev python skills/flakedetect_v1/detect/scripts/graphite.py \
    --image $IMG/bottom_part.jpg --pixel-size 0.077 --output-dir $OUT/detect

conda run -n instrMCPdev python skills/flakedetect_v1/detect/scripts/graphene.py \
    --image $IMG/top_part.jpg --pixel-size 0.077 --mirror --output-dir $OUT/detect

conda run -n instrMCPdev python skills/flakedetect_v1/detect/scripts/bottom_hbn.py \
    --image $IMG/full_stack_raw.jpg --footprint-mask $OUT/align/footprint_mask.png \
    --pixel-size 0.077 --output-dir $OUT/detect

conda run -n instrMCPdev python skills/flakedetect_v1/detect/scripts/top_hbn.py \
    --footprint-mask $OUT/align/footprint_mask.png \
    --footprint-contour $OUT/align/footprint_contour.npy \
    --image $IMG/full_stack_raw.jpg --pixel-size 0.077 --output-dir $OUT/detect

# (Agent assembles detections.json)

# 3. combine
conda run -n instrMCPdev python skills/flakedetect_v1/combine/scripts/ecc_register.py \
    --raw $IMG/full_stack_raw.jpg --lut $IMG/full_stack_w_LUT.jpg \
    --output-dir $OUT/combine

conda run -n instrMCPdev python skills/flakedetect_v1/combine/scripts/transform.py \
    --detections $OUT/detect/detections.json --align-dir $OUT/align \
    --image $IMG/full_stack_raw.jpg --pixel-size 0.077 --output-dir $OUT/combine

conda run -n instrMCPdev python skills/flakedetect_v1/combine/scripts/overlay.py \
    --traces $OUT/combine/traces.json \
    --raw $IMG/full_stack_raw.jpg --lut $IMG/full_stack_w_LUT.jpg \
    --combine-report $OUT/combine/combine_report.json \
    --output-dir $OUT/combine

# 4. commit
conda run -n instrMCPdev python skills/flakedetect_v1/commit/scripts/commit.py \
    --traces $OUT/combine/traces.json \
    --output-dir $OUT/commit

# (Agent submits generated pya code via MCP execute_script)

# 5. review
conda run -n instrMCPdev python skills/flakedetect_v1/review/scripts/review.py \
    --traces $OUT/combine/traces.json \
    --combine-report $OUT/combine/combine_report.json \
    --alignment-report $OUT/align/alignment_report.json \
    --output-dir $OUT/review
```

- [ ] **Step 2: Verify outputs**

Check that all expected files exist in the output directory structure per spec §4. View overlay images to verify visual correctness. Verify review_report.json shows passing checks.

- [ ] **Step 3: Final commit**

```bash
git add skills/flakedetect_v1/
git commit -m "feat(flakedetect_v1): complete implementation of all sub-skills"
```
