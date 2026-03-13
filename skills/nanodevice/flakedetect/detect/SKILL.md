---
name: nanodevice:flakedetect:detect
description: Detect individual material layers (graphite, graphene, bottom hBN, top hBN) from their optimal source images. Use when segmenting specific materials in a van der Waals heterostructure stack from microscope images.
---

# nanodevice:flakedetect:detect — Per-Material Detection

Detect each material from its optimal source image using K-means sub-clustering within the flake region. Four independent scripts, one per material.

Graphite and graphene use a **two-pass candidate workflow**: first run auto-selects, saves candidate images for review; agent overrides with `--cluster-id` if the auto-selection is wrong.

## Prerequisites

- Conda env with opencv, numpy, scikit-learn
- Source images for each material
- For bottom_hbn and top_hbn: `footprint_mask.png` from the align step
- All scripts: `conda run -n base python <script>`

---

## Agent Workflow

All 4 scripts are independent — run them in any order (or parallel). Then review and assemble.

```
1. Run graphite.py on bottom_part
   → Review 00_graphite_candidates.png — is the auto-selected cluster correct?
   → If wrong: re-run with --cluster-id <N>

2. Run graphene.py on top_part [--mirror]
   → Review 00_graphene_candidates.png — is the auto-selected cluster correct?
   → If wrong: re-run with --cluster-id <N>

3. Run bottom_hbn.py on full_stack_raw (needs footprint_mask from align)
   → Review 03_bottom_hbn_on_full.png

4. Run top_hbn.py (copies footprint from align)
   → Review 04_top_hbn_footprint.png

5. Assemble detections.json (see template below)
```

---

## Graphite Detection — Tuning Guide

**Method**: Isolates the hBN flake via HSV thresholding, then K-means sub-clusters (default 4) within the flake in LAB color space. Auto-selects the darkest sub-cluster.

**Key insight**: The graphite strip is typically the **2nd darkest** sub-cluster, NOT the absolute darkest. The darkest cluster is usually edge artifacts, folds, or shadow regions. Always review the candidates.

### What to look for in 00_graphite_candidates.png

| What you see | What's wrong | Action |
|-------------|-------------|--------|
| One panel shows the dark elongated strip in the center of the flake | That's the graphite | Use `--cluster-id <N>` for that panel if not auto-selected |
| Auto-selected panel shows scattered edges/folds | Darkest cluster is artifacts, not graphite | Override with the panel that shows the coherent dark strip |
| No panel shows a clear graphite strip | Sub-clusters too coarse | Re-run with `--n-sub-clusters 6` for finer segmentation |
| Graphite split across multiple panels | Sub-clusters too fine | Re-run with `--n-sub-clusters 3` |

### Expected graphite characteristics

- **Shape**: Elongated strip or wedge, aspect ratio typically 3-5+
- **Area**: 50-800 um² (varies widely by sample)
- **L value**: Darker than main hBN (L difference typically 20-60), but NOT the absolute darkest feature in the image

```bash
# Pass 1: auto-detect + review
conda run -n base python graphite.py \
    --image <bottom_part.jpg> --pixel-size <um/px> --output-dir <path>

# Pass 2: override after reviewing 00_graphite_candidates.png
conda run -n base python graphite.py \
    --image <bottom_part.jpg> --pixel-size <um/px> \
    --cluster-id 3 --output-dir <path>
```

**Outputs**: `graphite_mask.png`, `graphite_contour.npy`, `graphite_result.json`, `00_graphite_candidates.png`, `01_graphite_on_bottom.png`

---

## Graphene Detection — Tuning Guide

**Method**: Isolates the flake on PDMS via brightness+saturation thresholding, then K-means sub-clusters (default 3) within the flake in LAB space. Auto-selects the brightest sub-cluster.

**Key insight**: On PDMS, the flake has multiple brightness zones. Graphene is the brightest, but the auto-selection can grab overexposed artifacts or bright hBN instead. Always review the candidates.

### What to look for in 00_graphene_candidates.png

| What you see | What's wrong | Action |
|-------------|-------------|--------|
| One panel highlights the graphene region within the flake | Correct | Use `--cluster-id <N>` if not auto-selected |
| Auto-selected panel includes bright artifacts/reflections along with graphene | Brightest cluster includes non-graphene | Override with a panel that shows just the graphene region |
| Graphene region is split or partial | Sub-clusters too fine | Re-run with `--n-sub-clusters 2` |
| No panel clearly isolates graphene | Sub-clusters too coarse or graphene too subtle | Re-run with `--n-sub-clusters 5` for finer segmentation |

### Important: --mirror flag

If the align step used `--mirror` for the top_part, **you must also pass --mirror here**. The graphene detection must operate in the same coordinate system as the alignment warp.

```bash
# Pass 1: auto-detect + review
conda run -n base python graphene.py \
    --image <top_part.jpg> --pixel-size <um/px> --mirror --output-dir <path>

# Pass 2: override after reviewing 00_graphene_candidates.png
conda run -n base python graphene.py \
    --image <top_part.jpg> --pixel-size <um/px> --mirror \
    --cluster-id 0 --output-dir <path>
```

**Outputs**: `graphene_mask.png`, `graphene_contour.npy`, `graphene_result.json`, `00_graphene_candidates.png`, `02_graphene_on_top.png`

---

## Bottom hBN Detection — Tuning Guide

**Method**: Independent K-means (8 clusters) in LAB space on the full_stack image. Selects clusters with hBN-like color signature (high saturation, blue hue) that are NOT predominantly inside the top hBN footprint. Unions with footprint and keeps the connected component overlapping it.

This script usually works well without tuning. The footprint mask from align effectively separates top and bottom hBN.

### When it fails

| What you see in 03_bottom_hbn_on_full.png | What's wrong | Action |
|------------------------------------------|-------------|--------|
| Contour traces the full bottom hBN boundary | Nothing | Proceed |
| Contour is much larger than expected (includes substrate) | Substrate cluster misidentified as hBN | Re-run with `--n-clusters 12` for finer color separation |
| Contour misses parts of the bottom hBN | Not enough hBN clusters selected | Re-run with `--n-clusters 12` (more clusters = finer hBN boundary) |
| Contour includes part of the top hBN | Footprint mask incomplete | Go back and improve the align footprint |

```bash
conda run -n base python bottom_hbn.py \
    --image <full_stack_raw.jpg> \
    --footprint-mask <align/footprint_mask.png> \
    --pixel-size <um/px> --output-dir <path>
```

**Outputs**: `bottom_hbn_mask.png`, `bottom_hbn_contour.npy`, `bottom_hbn_result.json`, `03_bottom_hbn_on_full.png`

---

## Top hBN Detection

**Method**: Copies the footprint from the align step. No detection is performed — top hBN IS the footprint.

If the top hBN detection looks wrong, the fix is in the **align** step (re-run footprint.py or adjust Chamfer alignment), not here.

```bash
conda run -n base python top_hbn.py \
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

| Material | Source Image | Coordinate System | Mirror |
|----------|-------------|-------------------|--------|
| graphite | bottom_part | bottom_part | no |
| graphene | top_part | top_part (mirrored if --mirror) | depends |
| bottom_hBN | full_stack_raw | full_stack | no |
| top_hBN | full_stack_raw | full_stack | no |
