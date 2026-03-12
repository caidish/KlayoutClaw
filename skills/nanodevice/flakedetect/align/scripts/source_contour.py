#!/usr/bin/env python
"""Extract the largest flake contour from a source image.

Segments the source image (e.g., top_part on PDMS) using Otsu
auto-thresholding on both grayscale and saturation channels.
Produces a contour and binary mask for use by sweep.py and refine.py.

Usage:
    conda run -n base python source_contour.py \
        --image <source_image> [--mirror] --output-dir <path>
"""

import argparse
import json
import os
import sys

import cv2
import numpy as np

# Import shared utilities from nanodevice/flakedetect/scripts/core.py
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from core import morph_clean, flood_fill_holes, keep_largest_n, mask_centroid


def segment_flake(image, gray_only=False):
    """Segment the largest flake using Otsu auto-thresholding.

    Applies Otsu on grayscale (bright regions) AND on HSV saturation
    (colored regions). The intersection isolates bright + saturated
    pixels, which typically correspond to flakes on PDMS substrate.

    When gray_only=True, uses grayscale Otsu only (skips saturation).
    This captures very bright/overexposed areas that have low saturation.

    Args:
        image: BGR image (uint8).
        gray_only: If True, use grayscale Otsu only.

    Returns:
        Binary mask (uint8, 0/255) of the largest flake region.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Otsu on grayscale — separates bright flake from dark background
    _, mask_gray = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    if gray_only:
        mask = mask_gray
    else:
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        sat = hsv[:, :, 1]

        # Otsu on saturation — separates colored flake from unsaturated substrate
        _, mask_sat = cv2.threshold(sat, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # Intersection: must be both bright AND saturated
        mask = cv2.bitwise_and(mask_gray, mask_sat)

    # Morphological cleanup
    mask = morph_clean(mask, close_k=15, open_k=11)
    mask = keep_largest_n(mask, n=1, min_area=5000)
    mask = flood_fill_holes(mask)

    return mask


def main():
    parser = argparse.ArgumentParser(
        description="Extract source flake contour from a microscope image."
    )
    parser.add_argument("--image", required=True, help="Source image path")
    parser.add_argument("--mirror", action="store_true",
                        help="Horizontal flip before processing (PDMS transfers)")
    parser.add_argument("--gray-only", action="store_true",
                        help="Use grayscale Otsu only, skip saturation "
                             "(captures very bright/overexposed areas)")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    args = parser.parse_args()

    # Load image
    image = cv2.imread(args.image)
    if image is None:
        print(f"ERROR: Cannot read image: {args.image}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)

    # Mirror if requested
    if args.mirror:
        image = cv2.flip(image, 1)

    # Segment flake
    mask = segment_flake(image, gray_only=args.gray_only)

    # Check we found something
    area_px = int((mask > 0).sum())
    if area_px == 0:
        print("ERROR: No flake detected in source image.", file=sys.stderr)
        sys.exit(1)

    # Extract contour
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        print("ERROR: No contours found.", file=sys.stderr)
        sys.exit(1)

    contour = max(contours, key=cv2.contourArea)
    contour_pts = contour.reshape(-1, 2).astype(np.float64)

    # Save contour
    contour_path = os.path.join(args.output_dir, "source_contour.npy")
    np.save(contour_path, contour_pts)

    # Save mask
    mask_path = os.path.join(args.output_dir, "source_mask.png")
    cv2.imwrite(mask_path, mask)

    # Draw diagnostic overlay
    diag = image.copy()
    cv2.drawContours(diag, [contour], -1, (0, 255, 0), 2)
    centroid = mask_centroid(mask)
    if centroid:
        cx, cy = int(centroid[0]), int(centroid[1])
        cv2.circle(diag, (cx, cy), 8, (0, 0, 255), -1)
        cv2.putText(diag, f"area={area_px}px  pts={len(contour_pts)}",
                    (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
    diag_path = os.path.join(args.output_dir, "01_source_contour.png")
    cv2.imwrite(diag_path, diag)

    # Write/update alignment_report.json
    report_path = os.path.join(args.output_dir, "alignment_report.json")
    if os.path.exists(report_path):
        with open(report_path) as f:
            report = json.load(f)
    else:
        report = {}

    report["source"] = {
        "contour_file": "source_contour.npy",
        "mask_file": "source_mask.png",
        "mirrored": args.mirror,
        "area_px": area_px,
        "n_points": len(contour_pts),
    }
    report["source_image"] = os.path.abspath(args.image)

    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    # Summary
    print(f"Source contour: {len(contour_pts)} points, {area_px} px area")
    print(f"Mirrored: {args.mirror}")
    print(f"Saved: {contour_path}, {mask_path}, {diag_path}")


if __name__ == "__main__":
    main()
