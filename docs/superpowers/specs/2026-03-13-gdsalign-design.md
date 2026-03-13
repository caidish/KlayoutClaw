# GDS Align — Image-to-GDS Marker Alignment Skill

**Date**: 2026-03-13
**Status**: Design
**Skill path**: `skills/nanodevice/gdsalign/`

## Problem

The flakedetect pipeline produces material contours in microscope image coordinates (um). To integrate these into a fabrication layout, we need a transform from image coordinates to GDS coordinates, anchored by lithographic alignment markers visible in both domains.

## Approach

Fully automatic alignment using the **4 innermost L5/0 marker pairs** (8 individual square markers) from the GDS template. These markers are lithographically defined on the substrate and visible in microscope images. Their shape is invariant — detection uses **multi-scale template matching** of the marker pair pattern against the microscope image.

## GDS Marker Reference

Template: `/Volumes/RandomData/Stacks/Template.gds`
Layer: 5/0
Center of marker grid: (775, 775) um

The 4 inner pairs form a 75x75 um square. Each pair consists of two 3x3 um **square** markers (4 vertices each) touching at a corner, forming a 6x6 um diagonal pattern. The 4 pairs have different diagonal orientations (each rotated 90 deg from its neighbor):

| Pair | Shared corner (um) | Marker A bbox | Marker B bbox | Diagonal direction |
|------|--------------------|---------------|---------------|--------------------|
| NE   | (812.5, 812.5)     | (809.5,809.5)-(812.5,812.5) | (812.5,812.5)-(815.5,815.5) | lower-left → upper-right |
| NW   | (737.5, 812.5)     | (737.5,809.5)-(740.5,812.5) | (734.5,812.5)-(737.5,815.5) | lower-right → upper-left |
| SW   | (737.5, 737.5)     | (737.5,737.5)-(740.5,740.5) | (734.5,734.5)-(737.5,737.5) | upper-right → lower-left |
| SE   | (812.5, 737.5)     | (809.5,737.5)-(812.5,740.5) | (812.5,734.5)-(815.5,737.5) | upper-left → lower-right |

At 0.087 um/px (100x objective), each 3x3 um square is ~34x34 px. A pair (6x6 um) is ~69x69 px.

## Pipeline

### Scripts

All scripts in `skills/nanodevice/gdsalign/scripts/`. Run with `conda run -n base python <script>`.
Error convention: exit 0 on success, exit 1 on failure, `ERROR: <msg>` to stderr. JSON reports use `"status": "complete"` or `"status": "failed"`.

#### 1. `extract_markers.py`

Parse Template.gds (via gdstk), extract the 4 inner L5/0 marker pair centroids. Selects the 8 L5/0 polygons closest to the grid center (775, 775), groups them into pairs by proximity, and outputs pair centroids.

**Input**: `--gds <path>` (Template.gds), `--output-dir <path>`
**Output**: `gds_markers.json`

```json
{
  "source_gds": "Template.gds",
  "layer": "5/0",
  "pairs": [
    {
      "label": "NE",
      "center_um": [812.5, 812.5],
      "markers": [
        {"bbox": [[809.5, 809.5], [812.5, 812.5]]},
        {"bbox": [[812.5, 812.5], [815.5, 815.5]]}
      ]
    },
    {"label": "NW", "center_um": [737.5, 812.5], "markers": [...]},
    {"label": "SW", "center_um": [737.5, 737.5], "markers": [...]},
    {"label": "SE", "center_um": [812.5, 737.5], "markers": [...]}
  ]
}
```

#### 2. `detect_markers.py`

Detect marker pair positions in the microscope image using multi-scale template matching.

**Input**: `--image <path>` (full_stack_raw.jpg), `--pixel-size <um/px>` (0.087), `--gds-markers <path>` (gds_markers.json), `--output-dir <path>`
**Output**: `image_markers.json`, diagnostic PNGs

**Algorithm**:
1. **Render pair template from GDS geometry**: Using the marker bounding boxes from `gds_markers.json`, render one pair (two 3x3 um squares touching at a corner) as a small binary image at the expected pixel scale. This is the template unit — a 6x6 um diagonal pattern (~69x69 px).
2. Convert the full_stack image to grayscale.
3. **Multi-scale + multi-rotation sweep**: The 4 pairs have 4 different diagonal orientations, so rotation sweep [0, 90, 180, 270] degrees is needed to match all pair types. Scale sweep [0.8, 0.9, 1.0, 1.1, 1.2] handles pixel-size uncertainty up to 20%.
   - For each (rotation, scale) combination:
     - Rotate + scale the template.
     - Run `cv2.matchTemplate(image, template, TM_CCOEFF_NORMED)`.
     - Collect peaks above threshold.
4. **Non-maximum suppression**: Merge nearby detections (within 1 pair diameter). Each detection records its best-match rotation (which indicates the pair type: NE/NW/SW/SE).
5. Output detected pair centroids in pixels and um.

Each detection's rotation tells us which GDS pair it corresponds to, simplifying the correspondence problem in `align_gds.py`.

```json
{
  "source_image": "full_stack_raw.jpg",
  "pixel_size_um": 0.087,
  "best_scale": 1.0,
  "detections": [
    {"id": 0, "center_px": [x, y], "center_um": [x_um, y_um], "score": 0.85, "rotation_deg": 0, "scale": 1.0, "pair_type": "NE"},
    ...
  ],
  "diagnostic_images": ["01_template.png", "02_correlation_map.png", "03_detections.png"]
}
```

#### 3. `align_gds.py`

Compute the similarity transform from image um to GDS um via exhaustive point-pattern matching.

**Input**: `--gds-markers <path>` (gds_markers.json), `--image-markers <path>` (image_markers.json), `--output-dir <path>`
**Output**: `gds_warp.npy` (2x3 affine), `gds_alignment_report.json`

**Algorithm**:
1. Load 4 GDS pair centroids and N detected image pair centroids.
2. **Exhaustive enumeration** (not RANSAC — with only 4 GDS reference points, exhaustive is fast and exact): For each 2-pair correspondence between detected centroids and GDS centroids, compute the similarity transform (4 DOF: rotation + scale + tx + ty). Evaluate the residual on all remaining correspondences. The search space is C(N,2) x C(4,2) x 2 (both orderings) = at most C(8,2) x 6 x 2 = 336 trials.
3. Select the transform with the most inliers (residual < 1.0 um) and lowest total residual.
4. Refine: Least-squares fit on all inliers.
5. Output the 2x3 affine matrix (image_um → GDS_um) and quality metrics.

The `pair_type` from `detect_markers.py` can optionally constrain correspondences (NE detection → NE GDS pair), further reducing the search space.

```json
{
  "status": "complete",
  "transform": {
    "rotation_deg": 12.3,
    "scale": 1.002,
    "translation_um": [745.2, 701.8]
  },
  "quality": {
    "inliers": 4,
    "total_detected": 5,
    "mean_residual_um": 0.15,
    "max_residual_um": 0.32
  },
  "warp_file": "gds_warp.npy"
}
```

#### 4. `commit_gds.py`

Apply the warp to both the microscope image and the material contours, then commit to KLayout.

**Input**: `--warp <path>` (gds_warp.npy), `--traces <path>` (traces.json), `--image <path>` (full_stack_raw.jpg), `--pixel-size <um/px>`, `--gds <path>` (Template.gds), `--output-dir <path>`
**Output**: Warped image file + KLayout polygons committed

**Steps**:
1. Check if Template.gds is already loaded in KLayout; if not, load it via `execute_script`.
2. Warp `full_stack_raw.jpg` into GDS coordinates using `gds_warp.npy` + `cv2.warpAffine`. Save as `full_stack_gds.png`.
3. Load warped image as background overlay in KLayout via `image` skill.
4. For each material in `traces.json`:
   - Transform contour points: image_um → GDS_um via `gds_warp.npy`.
   - Insert polygon on the designated layer via `geometry` skill.
   - Note: `gds_warp.npy` maps image_um (Y-down) directly to GDS_um (Y-up). The Y-flip is baked into the transform. No additional flip needed.
5. Take screenshot for verification.

## Data Flow

```
Template.gds ──→ extract_markers ──→ gds_markers.json ─┐
full_stack_raw.jpg ──→ detect_markers ──→ image_markers.json ─┤
                                                              ▼
                                                        align_gds
                                                           │
                                                    gds_warp.npy
                                                      ┌────┴────┐
                                                      ▼         ▼
                                               warp image   warp contours
                                                      │         │
                                                      ▼         ▼
                                              image skill   geometry skill
                                              (background)  (polygons on L10-13)
```

## Output Directory Convention

All intermediate files go to a single output directory (default: `<sample>/output/gdsalign/`):

```
output/gdsalign/
├── gds_markers.json
├── image_markers.json
├── gds_warp.npy
├── gds_alignment_report.json
├── full_stack_gds.png
├── 01_template.png
├── 02_correlation_map.png
└── 03_detections.png
```

## File Structure

```
skills/nanodevice/gdsalign/
├── SKILL.md              # Orchestrator instructions (name: nanodevice:gdsalign)
└── scripts/
    ├── extract_markers.py
    ├── detect_markers.py
    ├── align_gds.py
    └── commit_gds.py
```

## Dependencies

- `gdstk` — GDS parsing (conda base)
- `cv2` — Template matching, warp (conda base)
- `numpy`, `scipy` — Transform computation (conda base)
- Existing skills: `image` (background overlay), `geometry` (polygon commit)
- Shared: `skills/nanodevice/flakedetect/scripts/core.py` for `warp_contour`, `invert_warp`

## Coordinate Systems

| Domain | Origin | Y direction | Units |
|--------|--------|-------------|-------|
| Image pixels | Top-left | Down | px |
| Image um | Top-left | Down | um (px * pixel_size) |
| GDS / KLayout | Absolute | Up | um |

The `gds_warp.npy` affine maps image_um → GDS_um. Since image Y-down and GDS Y-up, the transform inherently includes the Y-flip. No additional coordinate conversion needed during polygon insertion.

## Acceptance Criteria

- Detect >= 3 of the 4 inner marker pairs in the image.
- Mean alignment residual < 1.0 um.
- Transformed contours visually align with the optical image in KLayout.
- Background image visually aligns with the GDS template markers.

## Fallback: Marker Occlusion

If fewer than 3 inner pairs are detected (e.g., flake stack covers markers), fall back to the **next ring of markers** — 12 pairs at ~198 um from center, each with 5+7.5 um squares. These are larger (more distinctive for template matching) and farther from center (less likely to be occluded by the flake). The same algorithm applies with a wider template and updated GDS reference positions.

## Limitations

- Assumes at least 3 of the 4 inner L5/0 marker pairs are visible in the microscope FOV.
- Scale sweep [0.8, 1.2] assumes pixel_size is accurate to within 20%.
- Rotation sweep at 90 degree increments handles pair orientation; fine rotation is recovered by the similarity transform fit.
