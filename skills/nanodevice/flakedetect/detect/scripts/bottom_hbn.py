#!/usr/bin/env python
"""Detect bottom hBN from full_stack image via K-means clustering.

Runs independent K-means clustering in LAB color space, then selects
clusters with hBN-like color signature (high saturation, blue hue)
while excluding the top hBN region using the footprint mask from align.

Usage:
    conda run -n base python bottom_hbn.py \
        --image <full_stack_raw.jpg> \
        --footprint-mask <align/footprint_mask.png> \
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
from sklearn.cluster import KMeans

from core import morph_clean, flood_fill_holes, keep_largest_n, desaturate


def detect_bottom_hbn(image, footprint_mask, n_clusters=8):
    """Detect bottom hBN via K-means with footprint exclusion.

    Args:
        image: BGR image of full_stack_raw.
        footprint_mask: Binary mask of top hBN footprint (uint8, 0/255).
        n_clusters: Number of K-means clusters.

    Returns:
        dict with bottom_hbn_mask, cluster_stats, bottom_hbn_ids.
    """
    h, w = image.shape[:2]

    # K-means in LAB space
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    pixels = lab.reshape(-1, 3).astype(np.float32)
    km = KMeans(n_clusters=n_clusters, n_init=10, random_state=42)
    labels = km.fit_predict(pixels)
    label_map = labels.reshape(h, w)

    # Compute per-cluster HSV stats
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)

    # Green-gain for cluster classification
    b = image[:, :, 0].astype(np.float32)
    g = image[:, :, 1].astype(np.float32)
    r = image[:, :, 2].astype(np.float32)
    gg_map = g - (r + b) / 2.0

    cluster_stats = []
    for i in range(n_clusters):
        pix_mask = (label_map == i)
        if pix_mask.sum() == 0:
            cluster_stats.append({
                "label": i, "H": 0, "S": 0, "V": 0, "g_gain": 0,
                "area_frac": 0, "fp_overlap_frac": 0,
            })
            continue

        h_mean = hsv[:, :, 0][pix_mask].astype(np.float64).mean()
        s_mean = hsv[:, :, 1][pix_mask].astype(np.float64).mean()
        v_mean = hsv[:, :, 2][pix_mask].astype(np.float64).mean()
        gg = float(gg_map[pix_mask].mean())
        area_frac = float(pix_mask.sum() / (h * w))

        # How much of this cluster overlaps the footprint?
        fp_overlap = (pix_mask & (footprint_mask > 0)).sum()
        fp_overlap_frac = float(fp_overlap / max(pix_mask.sum(), 1))

        cluster_stats.append({
            "label": i, "H": float(h_mean), "S": float(s_mean),
            "V": float(v_mean), "g_gain": gg, "area_frac": area_frac,
            "fp_overlap_frac": fp_overlap_frac,
        })

    # Select bottom hBN clusters:
    # - High saturation (S > 80), blue hue (H in 80-130)
    # - NOT predominantly inside the footprint (fp_overlap_frac < 0.7)
    bottom_hbn_ids = []
    for s in cluster_stats:
        if s["S"] > 80 and 80 <= s["H"] <= 130:
            if s["fp_overlap_frac"] < 0.7:
                bottom_hbn_ids.append(s["label"])

    # Build mask
    bhbn_mask = np.zeros((h, w), dtype=np.uint8)
    for cid in bottom_hbn_ids:
        bhbn_mask[label_map == cid] = 255

    # Union with footprint to bridge gaps, then keep component overlapping footprint
    combined = cv2.bitwise_or(bhbn_mask, footprint_mask)
    combined = morph_clean(combined, close_k=21, open_k=11)
    combined = flood_fill_holes(combined)

    # Select component overlapping footprint
    num_labels, comp_labels, stats, _ = cv2.connectedComponentsWithStats(
        combined, connectivity=8)
    result_mask = np.zeros((h, w), dtype=np.uint8)
    for i in range(1, num_labels):
        comp = (comp_labels == i).astype(np.uint8) * 255
        overlap = cv2.bitwise_and(comp, footprint_mask)
        if overlap.sum() > 0:
            result_mask = cv2.bitwise_or(result_mask, comp)
    result_mask = flood_fill_holes(result_mask)

    return {
        "bottom_hbn_mask": result_mask,
        "cluster_stats": cluster_stats,
        "bottom_hbn_ids": bottom_hbn_ids,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Detect bottom hBN from full_stack image "
                    "(K-means + footprint exclusion)")
    parser.add_argument("--image", required=True,
                        help="Source image (full_stack_raw)")
    parser.add_argument("--footprint-mask", required=True,
                        help="Footprint mask from align step")
    parser.add_argument("--pixel-size", type=float, required=True,
                        help="Microns per pixel")
    parser.add_argument("--output-dir", required=True,
                        help="Output directory")
    parser.add_argument("--n-clusters", type=int, default=8,
                        help="K-means cluster count (default: 8)")
    args = parser.parse_args()

    image_path = os.path.abspath(os.path.expanduser(args.image))
    image = cv2.imread(image_path)
    if image is None:
        print(f"ERROR: cannot read image: {image_path}", file=sys.stderr)
        sys.exit(1)

    fp_mask = cv2.imread(os.path.abspath(args.footprint_mask),
                         cv2.IMREAD_GRAYSCALE)
    if fp_mask is None:
        print(f"ERROR: cannot read footprint mask: {args.footprint_mask}",
              file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)

    result = detect_bottom_hbn(image, fp_mask, n_clusters=args.n_clusters)

    mask = result["bottom_hbn_mask"]
    area_px = int((mask > 0).sum())

    if area_px == 0:
        print("ERROR: no bottom hBN region found", file=sys.stderr)
        sys.exit(1)

    area_um2 = round(area_px * args.pixel_size * args.pixel_size, 2)

    # Save mask
    cv2.imwrite(os.path.join(args.output_dir, "bottom_hbn_mask.png"), mask)

    # Save contour
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    largest = max(contours, key=cv2.contourArea)
    contour_2d = largest.reshape(-1, 2).astype(np.float64)
    np.save(os.path.join(args.output_dir, "bottom_hbn_contour.npy"),
            contour_2d)

    # Diagnostic overlay
    diag = desaturate(image, factor=0.4)
    cv2.drawContours(diag, [largest], -1, (255, 100, 0), 2)  # blue-ish BGR
    cv2.imwrite(os.path.join(args.output_dir, "03_bottom_hbn_on_full.png"),
                diag)

    # Result sidecar
    with open(os.path.join(args.output_dir, "bottom_hbn_result.json"),
              "w") as f:
        json.dump({"area_px": area_px, "area_um2": area_um2}, f, indent=2)

    print(f"OK: bottom hBN detected")
    print(f"  Area: {area_px} px ({area_um2} um²)")
    print(f"  Contour points: {len(contour_2d)}")
    print(f"  Selected clusters: {result['bottom_hbn_ids']}")
    print(f"  Outputs: {args.output_dir}")


if __name__ == "__main__":
    main()
