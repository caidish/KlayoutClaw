#!/usr/bin/env python
"""Top hBN detection — copies footprint from align step.

Top hBN IS the footprint from the align step. This script copies the
footprint mask and contour, draws a diagnostic overlay, and produces
the result sidecar JSON.

Usage:
    conda run -n base python top_hbn.py \
        --footprint-mask <align/footprint_mask.png> \
        [--footprint-contour <align/footprint_contour.npy>] \
        --image <full_stack_raw.jpg> \
        --pixel-size <um/px> \
        --output-dir <path>
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))

import cv2
import numpy as np

from core import desaturate


def main():
    parser = argparse.ArgumentParser(
        description="Top hBN = footprint from align (copy + diagnostic overlay)")
    parser.add_argument("--footprint-mask", required=True,
                        help="Footprint mask from align step")
    parser.add_argument("--footprint-contour", default=None,
                        help="Footprint contour from align step (optional)")
    parser.add_argument("--image", required=True,
                        help="Full stack image (for diagnostic overlay)")
    parser.add_argument("--pixel-size", type=float, required=True,
                        help="Microns per pixel")
    parser.add_argument("--output-dir", required=True,
                        help="Output directory")
    args = parser.parse_args()

    # Read inputs
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

    # Copy mask
    cv2.imwrite(os.path.join(args.output_dir, "top_hbn_mask.png"), fp_mask)

    # Get or copy contour
    if args.footprint_contour:
        fp_contour_path = os.path.abspath(args.footprint_contour)
        contour_2d = np.load(fp_contour_path).reshape(-1, 2).astype(np.float64)
    else:
        # Extract contour from mask
        contours, _ = cv2.findContours(fp_mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            print("ERROR: no contour found in footprint mask",
                  file=sys.stderr)
            sys.exit(1)
        largest = max(contours, key=cv2.contourArea)
        contour_2d = largest.reshape(-1, 2).astype(np.float64)

    np.save(os.path.join(args.output_dir, "top_hbn_contour.npy"), contour_2d)

    # Compute areas
    area_px = int((fp_mask > 0).sum())
    area_um2 = round(area_px * args.pixel_size * args.pixel_size, 2)

    # Diagnostic overlay
    diag = desaturate(image, factor=0.4)
    contour_int = contour_2d.reshape(-1, 1, 2).astype(np.int32)
    cv2.drawContours(diag, [contour_int], -1, (0, 200, 0), 2)  # green BGR
    cv2.imwrite(os.path.join(args.output_dir, "04_top_hbn_footprint.png"),
                diag)

    # Result sidecar
    with open(os.path.join(args.output_dir, "top_hbn_result.json"), "w") as f:
        json.dump({"area_px": area_px, "area_um2": area_um2}, f, indent=2)

    print(f"OK: top hBN = footprint")
    print(f"  Area: {area_px} px ({area_um2} um²)")
    print(f"  Contour points: {len(contour_2d)}")
    print(f"  Outputs: {args.output_dir}")


if __name__ == "__main__":
    main()
