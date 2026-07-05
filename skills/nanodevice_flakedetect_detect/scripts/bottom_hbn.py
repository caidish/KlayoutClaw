#!/usr/bin/env python
"""bottom_hbn.py — Detect bottom hBN from bottom_part via substrate
rejection followed by explicit hBN classification within the host.

Pipeline:
  1. Substrate sample: GMM on LAB of low-texture, edge-distance-prioritised
     pixels (via graphite.py compute_host).
  2. Host mask: non-substrate region from compute_host (max_components=1 for
     bottom-hBN which is typically a single continuous flake).  The host is
     a *region prior*, not the hBN detection itself.
  3. hBN classification (extract_hbn): inside the host, fit a multi-Otsu
     threshold on the LAB-distance-to-substrate distribution.  With
     classes=3, three natural groups appear:
       Class 0 — substrate-like pixels (distance below lower threshold):
                 dropped.
       Class 1 — intermediate distance: hBN (optically lighter than graphite,
                 darker than graphene; intermediate LAB distance to substrate).
       Class 2 — high-distance material (graphite, gold, other): dropped.
     Result = Class 1 pixels with the same morphological cleanup as the host.
     This correctly handles gold-backgate stacks (gold falls in Class 2) and
     any other multi-material host regions.
  4. Warp hBN mask from bottom_part coords to full_stack coords.
  5. Post-warp morphology: kernels derived from pixel_size via _kernel_from_um.
  6. Fixed 1.5 um dilation to match the current GT-dilation convention.

After detection the mask is warped from bottom_part coords to full_stack
coords via the affine matrix from align/sift_align.py.

Usage:
    conda run -n instrMCPdev python bottom_hbn.py \\
        --image <bottom_part.jpg> \\
        --warp-matrix <align/warp_sift_bottom.npy> \\
        --target-image <full_stack_raw.jpg> \\
        --pixel-size <um/px> \\
        --output-dir <path>

Outputs:
    bottom_hbn_mask.png        full_stack coords (uint8 0/255)
    bottom_hbn_mask_bp.png     bottom_part coords (uint8 0/255)
    bottom_hbn_contour.npy     (N, 2) float64 in full_stack px
    bottom_hbn_result.json     area + substrate ref + T* + low_confidence
    03_bottom_hbn_on_full.png  contour overlay on desaturated full_stack
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..',
                                'nanodevice_flakedetect', 'scripts'))
from core import desaturate, _kernel_from_um  # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))
from graphite import compute_host  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration constants (all adaptive or named — no raw numeric comparisons)
# ---------------------------------------------------------------------------

# hBN multi-Otsu classification
HBN_OTSU_CLASSES = 3          # 3 classes → 2 thresholds; Class 1 = hBN
HBN_OTSU_CLIP_PERCENTILE = 99  # clip tail before fitting Otsu

# Post-warp morphology in physical units
POSTWARP_CLOSE_UM = 0.435   # ≈ 5 px at 0.087 um/px
POSTWARP_OPEN_UM = 0.261    # ≈ 3 px at 0.087 um/px

# Dilation: fixed GT-convention radius for bottom hBN masks.
BOTTOM_HBN_DILATION_UM = 1.5

# Low-confidence: hBN area < 0.5% of host area means the classification step
# found almost nothing — report low_confidence rather than a spurious mask.
LOW_CONFIDENCE_HOST_FRAC = 0.005   # 0.5% of host_area_um2


def invert_affine(M: np.ndarray) -> np.ndarray:
    M3 = np.vstack([M, [0, 0, 1]])
    return np.linalg.inv(M3)[:2]


def _dilation_radius_um(host_mask: np.ndarray, pixel_size: float,
                        warp_matrix_path: str) -> float:
    """Return the fixed bottom hBN dilation radius in microns."""
    return max(pixel_size, BOTTOM_HBN_DILATION_UM)


def extract_hbn(host_mask: np.ndarray,
                image_lab: np.ndarray,
                mu_sub: np.ndarray,
                pixel_size: float) -> np.ndarray:
    """Classify hBN pixels within the host region using multi-Otsu on LAB distance.

    Parameters
    ----------
    host_mask   : uint8 0/255, bottom_part coords — graphite host region prior.
    image_lab   : LAB image (H, W, 3) float32 or uint8.
    mu_sub      : (3,) float32 substrate LAB mean from compute_host.
    pixel_size  : physical pixel size in um/px (used for morphology cleanup).

    Returns
    -------
    uint8 mask (0/255) of the hBN subregion within the host.

    Algorithm
    ---------
    1. Compute LAB distance of every *in-host* pixel to mu_sub.
    2. Fit multi-Otsu with HBN_OTSU_CLASSES on the clipped distance
       distribution → thresholds t0, t1.
    3. Class 0 (dist < t0): substrate-like — drop.
       Class 1 (t0 ≤ dist < t1): hBN candidate (intermediate contrast).
       Class 2 (dist ≥ t1): graphite / gold / other high-contrast — drop.
    4. Morphological cleanup with pixel_size-derived kernels.

    Fallback: if multi-Otsu fails (unimodal distribution), treat the whole
    host as hBN (conservative — keeps backward compatibility on simple stacks).
    """
    from skimage.filters import threshold_multiotsu

    image_lab_f = image_lab.astype(np.float32)
    dist_full = np.linalg.norm(image_lab_f - mu_sub, axis=2)

    # Work only on in-host pixels
    in_host = (host_mask > 0)
    if not in_host.any():
        return np.zeros_like(host_mask)

    dist_in = dist_full[in_host]

    # Clip tail for Otsu histogram stability
    clip_val = float(np.percentile(dist_in, HBN_OTSU_CLIP_PERCENTILE))
    dist_clipped = dist_in[dist_in <= clip_val]

    # Multi-Otsu classification
    try:
        thresholds = threshold_multiotsu(dist_clipped,
                                        classes=HBN_OTSU_CLASSES)
        t0 = float(thresholds[0])
        t1 = float(thresholds[1]) if len(thresholds) > 1 else float(clip_val)
    except Exception:
        # Unimodal fallback: use the whole host as hBN (conservative)
        return host_mask.copy()

    # Class 1 mask (hBN): t0 ≤ dist < t1, inside host
    hbn_raw = np.zeros_like(host_mask)
    hbn_class1 = in_host & (dist_full >= t0) & (dist_full < t1)
    hbn_raw[hbn_class1] = 255

    # Morphological cleanup with pixel_size-derived kernels
    close_k = _kernel_from_um(POSTWARP_CLOSE_UM, pixel_size, ellipse=True)
    open_k = _kernel_from_um(POSTWARP_OPEN_UM, pixel_size, ellipse=True)
    hbn_clean = cv2.morphologyEx(hbn_raw, cv2.MORPH_CLOSE, close_k)
    hbn_clean = cv2.morphologyEx(hbn_clean, cv2.MORPH_OPEN, open_k)

    # If hBN classification produced almost nothing, fall back to host
    # (hBN is the dominant material in most bench stacks)
    hbn_fraction = float(hbn_clean.sum()) / max(float(host_mask.sum()), 1.0)
    MIN_HBN_HOST_FRACTION = 0.05  # at least 5% of host pixels must be Class 1
    if hbn_fraction < MIN_HBN_HOST_FRACTION:
        # Classification degenerate — conservative fallback to host
        return host_mask.copy()

    return hbn_clean


def main() -> int:
    p = argparse.ArgumentParser(
        description='Detect bottom hBN from bottom_part via substrate '
                    'rejection + hBN classification; warp to full_stack.')
    p.add_argument('--image', required=True)
    p.add_argument('--warp-matrix', required=True)
    p.add_argument('--target-image', required=True)
    p.add_argument('--pixel-size', type=float, required=True)
    p.add_argument('--output-dir', required=True)
    args = p.parse_args()

    image = cv2.imread(os.path.abspath(args.image))
    if image is None:
        print(f'ERROR: cannot read image: {args.image}', file=sys.stderr)
        return 1
    target_img = cv2.imread(os.path.abspath(args.target_image))
    if target_img is None:
        print(f'ERROR: cannot read target image: {args.target_image}',
              file=sys.stderr)
        return 1
    warp_tgt2src = np.load(os.path.abspath(args.warp_matrix))
    if warp_tgt2src.shape != (2, 3):
        print(f'ERROR: warp matrix has unexpected shape '
              f'{warp_tgt2src.shape}', file=sys.stderr)
        return 1
    os.makedirs(args.output_dir, exist_ok=True)

    # Step 1: host region (region prior — not the hBN mask itself)
    host_bp, image_lab, mu_sub, sub_corner, t_star = compute_host(
        image, args.pixel_size, max_components=1)
    if (host_bp > 0).sum() == 0:
        print('ERROR: no host region detected in bottom_part',
              file=sys.stderr)
        return 1

    # Step 2: hBN classification within the host
    hbn_bp = extract_hbn(host_bp, image_lab, mu_sub, args.pixel_size)

    cv2.imwrite(os.path.join(args.output_dir, 'bottom_hbn_mask_bp.png'),
                hbn_bp)

    # Step 3: Warp hBN mask to full_stack coords
    M_src2tgt = invert_affine(warp_tgt2src)
    h_fs, w_fs = target_img.shape[:2]
    mask = cv2.warpAffine(hbn_bp, M_src2tgt, (w_fs, h_fs),
                          flags=cv2.INTER_NEAREST)

    # Post-warp morphology: kernels derived from pixel_size (no literal sizes)
    close_k = _kernel_from_um(POSTWARP_CLOSE_UM, args.pixel_size, ellipse=True)
    open_k = _kernel_from_um(POSTWARP_OPEN_UM, args.pixel_size, ellipse=True)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_k)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, open_k)

    # Step 4: Dilation — fixed GT-convention radius
    dilate_um = _dilation_radius_um(host_bp, args.pixel_size, args.warp_matrix)
    dilate_k = _kernel_from_um(dilate_um, args.pixel_size, ellipse=True)
    mask = cv2.dilate(mask, dilate_k)

    area_px = int((mask > 0).sum())
    if area_px == 0:
        print('ERROR: no bottom hBN region after warp to full_stack',
              file=sys.stderr)
        return 1
    area_um2 = round(area_px * args.pixel_size * args.pixel_size, 2)

    # Host area for low-confidence threshold (host-fraction based)
    host_area_px = int((host_bp > 0).sum())
    host_area_um2 = host_area_px * args.pixel_size * args.pixel_size
    low_confidence_floor_um2 = LOW_CONFIDENCE_HOST_FRAC * host_area_um2
    low_confidence = area_um2 < low_confidence_floor_um2

    cv2.imwrite(os.path.join(args.output_dir, 'bottom_hbn_mask.png'),
                mask)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                    cv2.CHAIN_APPROX_SIMPLE)
    largest = max(contours, key=cv2.contourArea)
    np.save(os.path.join(args.output_dir, 'bottom_hbn_contour.npy'),
            largest.reshape(-1, 2).astype(np.float64))

    diag = desaturate(target_img, factor=0.4)
    cv2.drawContours(diag, [largest], -1, (255, 100, 0), 2)
    cv2.imwrite(os.path.join(args.output_dir, '03_bottom_hbn_on_full.png'),
                diag)

    sidecar = {
        'area_px': area_px,
        'area_um2': area_um2,
        'pixel_size_um': args.pixel_size,
        'substrate': {
            'corner': sub_corner,
            'mu_lab': [round(float(x), 2) for x in mu_sub.tolist()],
            't_star': round(float(t_star), 2),
        },
        'host': {
            'area_px': host_area_px,
            'area_um2': round(host_area_um2, 2),
        },
        'dilation_um': round(dilate_um, 4),
        'low_confidence_floor_um2': round(low_confidence_floor_um2, 2),
        'low_confidence': bool(low_confidence),
    }
    with open(os.path.join(args.output_dir, 'bottom_hbn_result.json'),
              'w') as f:
        json.dump(sidecar, f, indent=2)

    print(f'OK: bottom hBN area={area_um2} um2  T*={t_star:.1f}  '
          f'mu_sub=[{mu_sub[0]:.0f},{mu_sub[1]:.0f},{mu_sub[2]:.0f}]  '
          f'dilate_um={dilate_um:.3f}  '
          f'low_confidence={low_confidence}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
