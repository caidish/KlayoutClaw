#!/usr/bin/env python
"""ECC translation alignment between raw and LUT full-stack images.

Computes the spatial shift (dx, dy) between the raw microscope image and the
LUT (color-enhanced) version using Enhanced Correlation Coefficient alignment
with a translation-only motion model.

Creates or initialises combine_report.json with the raw2lut section.

Usage:
    conda run -n base python ecc_register.py \
        --raw full_stack_raw.jpg \
        --lut full_stack_w_LUT.jpg \
        --output-dir /tmp/combine
"""

import argparse
import json
import os
import sys

import cv2
import numpy as np


def ecc_translation(ref_image, mov_image):
    """Compute ECC translation alignment between two images.

    Args:
        ref_image: Reference image (BGR or grayscale uint8).
        mov_image: Moving image (BGR or grayscale uint8).

    Returns:
        Tuple of (dx, dy, correlation).
        dx/dy are the translation offsets (ref -> mov).
        correlation is the ECC value at convergence.
    """
    # Convert to grayscale
    if ref_image.ndim == 3:
        gray_ref = cv2.cvtColor(ref_image, cv2.COLOR_BGR2GRAY)
    else:
        gray_ref = ref_image.copy()

    if mov_image.ndim == 3:
        gray_mov = cv2.cvtColor(mov_image, cv2.COLOR_BGR2GRAY)
    else:
        gray_mov = mov_image.copy()

    # Equalize histograms for robustness
    eq_ref = cv2.equalizeHist(gray_ref)
    eq_mov = cv2.equalizeHist(gray_mov)

    # Initialize identity warp
    warp_matrix = np.eye(2, 3, dtype=np.float32)

    criteria = (
        cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
        5000,
        1e-8,
    )

    try:
        cc, warp_matrix = cv2.findTransformECC(
            eq_ref, eq_mov, warp_matrix, cv2.MOTION_TRANSLATION, criteria
        )
    except cv2.error:
        print("WARNING: ECC failed to converge, using identity", file=sys.stderr)
        cc = 0.0
        warp_matrix = np.eye(2, 3, dtype=np.float32)

    dx = float(warp_matrix[0, 2])
    dy = float(warp_matrix[1, 2])
    return dx, dy, float(cc)


def main():
    parser = argparse.ArgumentParser(
        description="ECC translation alignment between raw and LUT images"
    )
    parser.add_argument("--raw", required=True,
                        help="Path to full_stack_raw image")
    parser.add_argument("--lut", required=True,
                        help="Path to full_stack_w_LUT image")
    parser.add_argument("--output-dir", required=True,
                        help="Output directory for combine_report.json")
    args = parser.parse_args()

    # Load images
    raw_img = cv2.imread(os.path.abspath(args.raw))
    if raw_img is None:
        print(f"ERROR: Cannot read raw image: {args.raw}", file=sys.stderr)
        sys.exit(1)

    lut_img = cv2.imread(os.path.abspath(args.lut))
    if lut_img is None:
        print(f"ERROR: Cannot read LUT image: {args.lut}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)

    # Compute ECC translation
    dx, dy, cc = ecc_translation(raw_img, lut_img)

    # Write combine_report.json
    report = {
        "raw2lut": {
            "dx": round(dx, 1),
            "dy": round(dy, 1),
            "rotation_deg": 0.0,
            "scale": 1.0,
            "ecc_correlation": round(cc, 4),
            "method": "ecc_translation",
        }
    }

    report_path = os.path.join(args.output_dir, "combine_report.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    # Summary
    print(f"OK: ECC translation alignment complete")
    print(f"  dx={dx:.1f} px, dy={dy:.1f} px")
    print(f"  ECC correlation: {cc:.4f}")
    print(f"  Report: {report_path}")


if __name__ == "__main__":
    main()
