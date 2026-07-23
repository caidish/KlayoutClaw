#!/usr/bin/env python
"""Top hBN detection 鈥?footprint as geometry + material evidence validation.

The alignment footprint remains the geometry source for the top hBN mask.
Phase 4 adds image-evidence validation: the script checks that the footprint
region genuinely contains hBN-like contrast vs the substrate before emitting
the mask. It does NOT discard the footprint on low confidence 鈥?it annotates
the result JSON with a quality signal so the agent can make informed decisions.

Design rationale:
  The footprint already achieves IoU=0.87鈥?.97 across mlxx stacks. Replacing
  it would risk regression. Instead, we add a parallel evidence computation
  path whose output is diagnostic, not gating.

Validation steps:
  1. Sample substrate LAB from image corners (10 % margin on each side).
  2. Compute per-pixel LAB distance from substrate over the whole image.
  3. validate_hbn_contrast(): mean distance inside footprint / mean distance
     over the whole image 鈫?evidence_score in [0, 1].  Higher = more
     material-like inside the footprint.
  4. Otsu on the in-footprint LAB-distance histogram: bimodal 鈫?clean
     material/substrate separation 鈫?high confidence.  Unimodal (no clean
     split) 鈫?low_confidence = True.
  5. Enumerate all contours above an adaptive area floor (1 % 脳 footprint_area)
     in the result JSON (contour_areas), enabling multi-component inspection
     by the agent without changing the output mask.

CLI (backward-compatible):
    conda run -n instrMCPdev python top_hbn.py \\
        --footprint-mask <align/footprint_mask.png> \\
        [--footprint-contour <align/footprint_contour.npy>] \\
        --image <full_stack_raw.jpg> \\
        --pixel-size <um/px> \\
        --output-dir <path>
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'nanodevice_flakedetect', 'scripts'))

import cv2
import numpy as np

from core import desaturate

# ---------------------------------------------------------------------------
# Configuration constants (named; not used as naked comparison literals)
# ---------------------------------------------------------------------------

# Corner fraction used to sample substrate LAB
SUBSTRATE_CORNER_FRACTION = 0.10

# Otsu bimodality guard: if the ratio of inter-class variance to total
# variance is below this value, the distribution is treated as unimodal
# (no clean material-vs-substrate separation inside the footprint).
OTSU_BIMODALITY_MIN_RATIO = 0.20

# Adaptive area floor for multi-component enumeration (fraction of footprint)
CONTOUR_AREA_FLOOR_FRACTION = 0.01

# Fixed random state for any sampling operations (determinism for snapshots)
RANDOM_STATE = 0

# Sentinel tolerances used in validation functions (named to satisfy the
# no-naked-numerics-in-comparisons invariant).
_EPS_DIST = 1e-6      # minimum mean distance to avoid division by zero
_EPS_RANGE = 1e-6     # minimum value range to treat distribution as non-flat
_EPS_VAR = 1e-12      # minimum variance to treat distribution as non-flat
_MIN_SAMPLE_FOR_OTSU = 10  # minimum footprint pixels to attempt Otsu

# Low-confidence gate: footprint mean LAB-distance must exceed this fraction
# of the whole-image mean to be considered material-like.  A footprint whose
# mean distance is below this fraction looks more like substrate than material.
# Named constant: not a comparison literal.
EVIDENCE_SCORE_LOW_THRESHOLD = 0.5


# ---------------------------------------------------------------------------
# Evidence validation helpers
# ---------------------------------------------------------------------------


def _sample_substrate_lab(image_lab: np.ndarray) -> np.ndarray:
    """Estimate substrate LAB by averaging image corner patches.

    Uses the 10 % corner margins on all four sides.  This is a lightweight
    approximation 鈥?for top_hbn the footprint is already known from alignment,
    so the only use of substrate_lab is the contrast score, which needs a
    reasonable neutral reference rather than a precise model.

    Returns shape (3,) float32.
    """
    h, w = image_lab.shape[:2]
    ph = max(5, int(h * SUBSTRATE_CORNER_FRACTION))
    pw = max(5, int(w * SUBSTRATE_CORNER_FRACTION))
    corners = np.vstack([
        image_lab[:ph, :pw].reshape(-1, 3),
        image_lab[:ph, w - pw:].reshape(-1, 3),
        image_lab[h - ph:, :pw].reshape(-1, 3),
        image_lab[h - ph:, w - pw:].reshape(-1, 3),
    ]).astype(np.float32)
    return corners.mean(axis=0)


def _lab_distance_map(image_lab: np.ndarray,
                      substrate_lab: np.ndarray) -> np.ndarray:
    """Compute per-pixel Euclidean distance from substrate_lab.

    Returns float32 array of shape (H, W).
    """
    diff = image_lab.astype(np.float32) - substrate_lab.astype(np.float32)
    return np.sqrt((diff ** 2).sum(axis=-1))


def validate_hbn_contrast(image_bgr: np.ndarray,
                          footprint_mask: np.ndarray,
                          substrate_lab: np.ndarray) -> float:
    """Measure how much more material-like the footprint region is vs the image.

    Computes:
      - mean LAB-distance from substrate inside the footprint mask
      - mean LAB-distance from substrate over the whole image

    Score = in_footprint_mean / whole_image_mean, clipped to [0, 1].

    A score near 1 means the footprint region is as material-rich as the
    entire image (expected when the footprint is large and the substrate
    contributes many low-distance pixels to the whole-image mean).
    A score > 0.5 typically indicates the footprint contains genuine material
    contrast above the image average.

    Args:
        image_bgr:      Full stack image in BGR (H, W, 3).
        footprint_mask: Binary mask (uint8), same spatial size as image_bgr.
        substrate_lab:  LAB reference point for the substrate (shape (3,)).

    Returns:
        float in [0, 1].
    """
    image_lab = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2LAB)
    dist_map = _lab_distance_map(image_lab, substrate_lab)

    fp_bool = footprint_mask > 0
    n_fp = fp_bool.sum()
    if n_fp == 0:
        return 0.0

    mean_inside = float(dist_map[fp_bool].mean())
    mean_whole = float(dist_map.mean())

    if mean_whole < _EPS_DIST:
        # Degenerate image 鈥?flat; no evidence either way
        return 0.0

    score = mean_inside / mean_whole
    return float(np.clip(score, 0.0, 1.0))


def _otsu_bimodality(values: np.ndarray) -> tuple[float | None, bool]:
    """Check whether a 1-D distribution is bimodal using Otsu's criterion.

    Normalises `values` to uint8, applies cv2 Otsu, and computes the ratio
    of inter-class variance to total variance.

    Returns:
        (otsu_threshold_in_original_units | None, is_bimodal)

    `is_bimodal` is True when the inter/total variance ratio exceeds
    OTSU_BIMODALITY_MIN_RATIO 鈥?i.e. Otsu finds a clean split.
    """
    if values.size < _MIN_SAMPLE_FOR_OTSU:
        return None, False

    v_min = float(values.min())
    v_max = float(values.max())
    if v_max - v_min < _EPS_RANGE:
        # All identical 鈥?unimodal
        return None, False

    # Map to uint8
    scaled = ((values - v_min) / (v_max - v_min) * 255.0).astype(np.uint8)
    thresh_scaled, _ = cv2.threshold(
        scaled.reshape(1, -1), 0, 255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU,
    )
    thresh_01 = thresh_scaled / 255.0
    # Convert threshold back to original units
    thresh_orig = v_min + thresh_01 * (v_max - v_min)

    # Inter-class variance ratio
    below = values[values <= thresh_orig]
    above = values[values > thresh_orig]
    if below.size == 0 or above.size == 0:
        return float(thresh_orig), False

    w0 = below.size / values.size
    w1 = above.size / values.size
    mu0 = float(below.mean())
    mu1 = float(above.mean())
    mu_total = float(values.mean())
    var_total = float(values.var())

    if var_total < _EPS_VAR:
        return float(thresh_orig), False

    inter_var = w0 * (mu0 - mu_total) ** 2 + w1 * (mu1 - mu_total) ** 2
    ratio = inter_var / var_total

    is_bimodal = ratio >= OTSU_BIMODALITY_MIN_RATIO
    return float(thresh_orig), is_bimodal


def _enumerate_contours(fp_mask: np.ndarray,
                        pixel_size_um: float) -> tuple[list[dict], np.ndarray]:
    """Extract all contours above the adaptive area floor from the footprint mask.

    Returns:
        contour_records: list of dicts with keys {area_px, area_um2, n_points}
                         sorted by area descending.
        largest_contour: The single largest contour as (N, 2) float64 array,
                         used for the output mask (backward-compatible).
    """
    contours, _ = cv2.findContours(fp_mask, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return [], np.empty((0, 2), dtype=np.float64)

    fp_area_px = int((fp_mask > 0).sum())
    area_floor_px = max(1, int(fp_area_px * CONTOUR_AREA_FLOOR_FRACTION))

    # Collect all qualifying contours sorted by area descending
    qualified = sorted(
        [(cv2.contourArea(c), c) for c in contours if cv2.contourArea(c) >= area_floor_px],
        key=lambda x: x[0],
        reverse=True,
    )

    if not qualified:
        # Fall back: keep the largest even if below the floor
        largest_cv = max(contours, key=cv2.contourArea)
        contour_2d = largest_cv.reshape(-1, 2).astype(np.float64)
        record = {
            "area_px": int(cv2.contourArea(largest_cv)),
            "area_um2": round(cv2.contourArea(largest_cv) * pixel_size_um ** 2, 2),
            "n_points": len(contour_2d),
        }
        return [record], contour_2d

    contour_records = []
    for area_px, c in qualified:
        area_um2 = round(float(area_px) * pixel_size_um ** 2, 2)
        contour_records.append({
            "area_px": int(area_px),
            "area_um2": area_um2,
            "n_points": int(c.shape[0]),
        })

    # The primary contour (for the mask) is still the largest one
    largest_contour = qualified[0][1].reshape(-1, 2).astype(np.float64)
    return contour_records, largest_contour


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Top hBN: footprint as geometry + material evidence validation")
    parser.add_argument("--footprint-mask", required=True,
                        help="Footprint mask from align step")
    parser.add_argument("--footprint-contour", default=None,
                        help="Footprint contour from align step (optional)")
    parser.add_argument("--image", required=True,
                        help="Full stack image (for material validation + diagnostic)")
    parser.add_argument("--pixel-size", type=float, required=True,
                        help="Microns per pixel")
    parser.add_argument("--output-dir", required=True,
                        help="Output directory")
    args = parser.parse_args()

    # --- Read inputs --------------------------------------------------------
    fp_mask_path = os.path.abspath(args.footprint_mask)
    fp_mask = cv2.imread(fp_mask_path, cv2.IMREAD_GRAYSCALE)
    if fp_mask is None:
        print(f"ERROR: cannot read footprint mask: {fp_mask_path}",
              file=sys.stderr)
        sys.exit(1)

    image_path = os.path.abspath(os.path.expanduser(args.image))
    image = cv2.imread(image_path)
    if image is None:
        print(f"ERROR: cannot read image: {image_path}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)

    # --- Material evidence validation ---------------------------------------
    image_lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    substrate_lab = _sample_substrate_lab(image_lab)
    dist_map = _lab_distance_map(image_lab, substrate_lab)

    evidence_score = validate_hbn_contrast(image, fp_mask, substrate_lab)

    # Otsu bimodality check on in-footprint LAB-distance distribution
    fp_bool = fp_mask > 0
    material_otsu_threshold: float | None = None
    low_confidence = False

    if fp_bool.sum() > 0:
        fp_distances = dist_map[fp_bool]
        otsu_thresh, is_bimodal = _otsu_bimodality(fp_distances)
        material_otsu_threshold = otsu_thresh

        if is_bimodal and otsu_thresh is not None:
            # Bimodal distribution: check if the majority of footprint pixels
            # fall in the material-side (above threshold) or substrate-side
            # (below threshold).  If mostly below, footprint is substrate-like.
            fraction_above = float((fp_distances > otsu_thresh).mean())
            # fraction_above < EVIDENCE_SCORE_LOW_THRESHOLD means more
            # substrate-like pixels than material-like pixels inside footprint.
            low_confidence = fraction_above < EVIDENCE_SCORE_LOW_THRESHOLD
        else:
            # Unimodal distribution 鈥?check which "mode" the footprint is in.
            # If the footprint mean distance is above the whole-image mean
            # (evidence_score >= 1.0), the footprint is uniformly material-rich
            # 鈫?high confidence.  If below, it looks substrate-like 鈫?low.
            low_confidence = evidence_score < EVIDENCE_SCORE_LOW_THRESHOLD
    else:
        low_confidence = True

    # --- Copy mask (geometry source stays the footprint) -------------------
    cv2.imwrite(os.path.join(args.output_dir, "top_hbn_mask.png"), fp_mask)

    # --- Contour: use supplied or extract from mask -------------------------
    if args.footprint_contour:
        fp_contour_path = os.path.abspath(args.footprint_contour)
        contour_2d = np.load(fp_contour_path).reshape(-1, 2).astype(np.float64)
        # Still enumerate contours from the mask for multi-component info
        contour_areas, _ = _enumerate_contours(fp_mask, args.pixel_size)
    else:
        contour_areas, contour_2d = _enumerate_contours(fp_mask, args.pixel_size)
        if contour_2d.shape[0] == 0:
            print("ERROR: no contour found in footprint mask", file=sys.stderr)
            sys.exit(1)

    np.save(os.path.join(args.output_dir, "top_hbn_contour.npy"), contour_2d)

    # --- Compute areas ------------------------------------------------------
    area_px = int((fp_mask > 0).sum())
    area_um2 = round(area_px * args.pixel_size * args.pixel_size, 2)

    # --- Diagnostic overlay -------------------------------------------------
    diag = desaturate(image, factor=0.4)
    contour_int = contour_2d.reshape(-1, 1, 2).astype(np.int32)
    # Green contour; colour shifts toward yellow when low_confidence
    color = (0, 200, 0) if not low_confidence else (0, 200, 200)
    cv2.drawContours(diag, [contour_int], -1, color, 2)
    cv2.imwrite(os.path.join(args.output_dir, "04_top_hbn_footprint.png"), diag)

    # --- Result sidecar -----------------------------------------------------
    result = {
        "area_px": area_px,
        "area_um2": area_um2,
        "evidence_score": round(evidence_score, 4),
        "material_otsu_threshold": (
            round(float(material_otsu_threshold), 4)
            if material_otsu_threshold is not None else None
        ),
        "low_confidence": low_confidence,
        "contour_areas": contour_areas,
        "substrate_lab": [round(float(v), 2) for v in substrate_lab.tolist()],
    }
    with open(os.path.join(args.output_dir, "top_hbn_result.json"), "w") as f:
        json.dump(result, f, indent=2)

    print(f"OK: top hBN = footprint (evidence-validated)")
    print(f"  Area: {area_px} px ({area_um2} um虏)")
    print(f"  Contour points: {len(contour_2d)}")
    print(f"  Evidence score: {evidence_score:.4f}")
    print(f"  Otsu threshold: {material_otsu_threshold}")
    print(f"  Low confidence: {low_confidence}")
    print(f"  Contour components (>= floor): {len(contour_areas)}")
    print(f"  Outputs: {args.output_dir}")


if __name__ == "__main__":
    main()
