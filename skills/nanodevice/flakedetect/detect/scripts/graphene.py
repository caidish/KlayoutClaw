#!/usr/bin/env python
"""Detect graphene from top_part image via K-means sub-clustering.

The top_part image shows the graphene flake on PDMS before transfer.
The flake appears as a brighter / more saturated region against dark
PDMS background. Within the flake, graphene is the brightest sub-region.

Two-pass workflow (same pattern as align sweep → refine):
  1. First run: auto-selects brightest sub-cluster, saves candidate
     images for each sub-cluster so the agent can review.
  2. If agent disagrees: re-run with --cluster-id to pick the correct one.

Usage:
    # First pass: auto-detect + save candidates for review
    conda run -n base python graphene.py \
        --image <top_part.jpg> \
        --pixel-size <um/px> \
        [--mirror] \
        --output-dir <path>

    # Second pass: agent picks correct cluster after vision review
    conda run -n base python graphene.py \
        --image <top_part.jpg> \
        --pixel-size <um/px> \
        [--mirror] \
        --cluster-id 1 \
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


def detect_graphene(image, mirror=True, n_sub_clusters=3, cluster_id=None):
    """Detect graphene via K-means sub-clustering of the flake region.

    Args:
        image: BGR image of top_part.
        mirror: Horizontally flip before processing.
        n_sub_clusters: Number of sub-clusters within flake.
        cluster_id: If set, use this sub-cluster (agent override).

    Returns:
        dict with graphene_mask, top_flake_mask, processed_image,
              sub_cluster_masks, sub_cluster_stats, selected_id.
    """
    if mirror:
        img = cv2.flip(image, 1)
    else:
        img = image.copy()

    h, w = img.shape[:2]

    # Step 1: Isolate flake (bright + saturated)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    s_chan = hsv[:, :, 1]

    bright_mask = (gray > 40).astype(np.uint8) * 255
    sat_mask = (s_chan > 15).astype(np.uint8) * 255
    flake_mask = cv2.bitwise_and(bright_mask, sat_mask)
    flake_mask = morph_clean(flake_mask, close_k=15, open_k=11)
    flake_mask = keep_largest_n(flake_mask, n=1, min_area=5000)
    flake_mask = flood_fill_holes(flake_mask)

    # Step 2: Sub-cluster flake region in LAB space
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    flake_pixels = lab[flake_mask == 255].reshape(-1, 3).astype(np.float32)
    flake_coords = np.argwhere(flake_mask == 255)  # (row, col) pairs

    if len(flake_pixels) < n_sub_clusters:
        return {
            "graphene_mask": np.zeros((h, w), dtype=np.uint8),
            "top_flake_mask": flake_mask,
            "processed_image": img,
            "sub_cluster_masks": [],
            "sub_cluster_stats": [],
            "selected_id": None,
        }

    sub_km = KMeans(n_clusters=n_sub_clusters, n_init=10, random_state=42)
    sub_labels = sub_km.fit_predict(flake_pixels)

    # Build per-sub-cluster masks and stats
    sub_cluster_masks = []
    sub_cluster_stats = []
    for i in range(n_sub_clusters):
        mask_i = np.zeros((h, w), dtype=np.uint8)
        for idx, (r, c) in enumerate(flake_coords):
            if sub_labels[idx] == i:
                mask_i[r, c] = 255

        area_px = int((mask_i > 0).sum())
        l_center = float(sub_km.cluster_centers_[i, 0])
        a_center = float(sub_km.cluster_centers_[i, 1])
        b_center = float(sub_km.cluster_centers_[i, 2])

        sub_cluster_masks.append(mask_i)
        sub_cluster_stats.append({
            "id": i, "L": l_center, "a": a_center, "b": b_center,
            "area_px": area_px,
        })

    # Select cluster
    if cluster_id is not None:
        selected_id = cluster_id
    else:
        # Auto: brightest by L-channel center
        selected_id = int(np.argmax(sub_km.cluster_centers_[:, 0]))

    # Build graphene mask from selected sub-cluster
    graphene_mask = sub_cluster_masks[selected_id].copy()
    graphene_mask = morph_clean(graphene_mask, close_k=15, open_k=7)
    graphene_mask = keep_largest_n(graphene_mask, n=1, min_area=2000)
    graphene_mask = flood_fill_holes(graphene_mask)

    return {
        "graphene_mask": graphene_mask,
        "top_flake_mask": flake_mask,
        "processed_image": img,
        "sub_cluster_masks": sub_cluster_masks,
        "sub_cluster_stats": sub_cluster_stats,
        "selected_id": selected_id,
    }


def draw_candidates_grid(image, sub_cluster_masks, sub_cluster_stats,
                         selected_id):
    """Draw a grid showing each sub-cluster candidate for agent review."""
    n = len(sub_cluster_masks)
    h, w = image.shape[:2]

    # Distinct colors for each candidate
    candidate_colors = [
        (0, 0, 255),    # red
        (0, 255, 0),    # green
        (255, 0, 0),    # blue
        (0, 255, 255),  # yellow
        (255, 0, 255),  # magenta
    ]

    panels = []
    for i in range(n):
        panel = desaturate(image, factor=0.3)
        mask_i = sub_cluster_masks[i]
        contours, _ = cv2.findContours(mask_i, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        color = candidate_colors[i % len(candidate_colors)]

        # Fill with semi-transparent color
        overlay = panel.copy()
        cv2.drawContours(overlay, contours, -1, color, -1)
        panel = cv2.addWeighted(overlay, 0.4, panel, 0.6, 0)
        # Draw contour outline
        cv2.drawContours(panel, contours, -1, color, 2)

        s = sub_cluster_stats[i]
        marker = " [AUTO]" if i == selected_id else ""
        label = f"c{i}: L={s['L']:.0f} area={s['area_px']}px{marker}"
        cv2.putText(panel, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (255, 255, 255), 2, cv2.LINE_AA)
        panels.append(panel)

    # Stack horizontally (or in a grid for >3)
    if n <= 3:
        grid = np.hstack(panels)
    else:
        # 2-row grid
        row1 = np.hstack(panels[:((n + 1) // 2)])
        row2_panels = panels[((n + 1) // 2):]
        # Pad if needed
        while len(row2_panels) < (n + 1) // 2:
            row2_panels.append(np.zeros_like(panels[0]))
        row2 = np.hstack(row2_panels)
        if row1.shape[1] != row2.shape[1]:
            # Pad shorter row
            diff = row1.shape[1] - row2.shape[1]
            if diff > 0:
                row2 = np.hstack([row2, np.zeros((h, diff, 3), dtype=np.uint8)])
            else:
                row1 = np.hstack([row1, np.zeros((h, -diff, 3), dtype=np.uint8)])
        grid = np.vstack([row1, row2])

    return grid


def main():
    parser = argparse.ArgumentParser(
        description="Detect graphene from top_part image "
                    "(K-means sub-clustering with vision review)")
    parser.add_argument("--image", required=True,
                        help="Source image (top_part)")
    parser.add_argument("--pixel-size", type=float, required=True,
                        help="Microns per pixel")
    parser.add_argument("--mirror", action="store_true", default=False,
                        help="Horizontally flip image before processing")
    parser.add_argument("--output-dir", required=True,
                        help="Output directory")
    parser.add_argument("--n-sub-clusters", type=int, default=3,
                        help="Sub-cluster count within flake (default: 3)")
    parser.add_argument("--cluster-id", type=int, default=None,
                        help="Agent override: use this sub-cluster ID "
                             "as graphene (after reviewing candidates)")
    args = parser.parse_args()

    image_path = os.path.abspath(os.path.expanduser(args.image))
    image = cv2.imread(image_path)
    if image is None:
        print(f"ERROR: cannot read image: {image_path}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)

    result = detect_graphene(image, mirror=args.mirror,
                             n_sub_clusters=args.n_sub_clusters,
                             cluster_id=args.cluster_id)

    # Always save candidate grid for agent vision review
    if result["sub_cluster_masks"]:
        grid = draw_candidates_grid(
            result["processed_image"], result["sub_cluster_masks"],
            result["sub_cluster_stats"], result["selected_id"])
        cv2.imwrite(os.path.join(args.output_dir,
                                 "00_graphene_candidates.png"), grid)

    # Print sub-cluster stats for agent
    print("Sub-cluster stats (sorted by L, brightest first):")
    sorted_stats = sorted(result["sub_cluster_stats"],
                          key=lambda s: s["L"], reverse=True)
    for s in sorted_stats:
        marker = " <-- selected" if s["id"] == result["selected_id"] else ""
        print(f"  c{s['id']}: L={s['L']:.0f} a={s['a']:.0f} "
              f"b={s['b']:.0f} area={s['area_px']}px{marker}")

    mask = result["graphene_mask"]
    area_px = int((mask > 0).sum())

    if area_px == 0:
        print("ERROR: no graphene region found", file=sys.stderr)
        print("Review 00_graphene_candidates.png and re-run with "
              "--cluster-id <N>", file=sys.stderr)
        sys.exit(1)

    area_um2 = round(area_px * args.pixel_size * args.pixel_size, 2)

    # Save mask
    cv2.imwrite(os.path.join(args.output_dir, "graphene_mask.png"), mask)

    # Save contour
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    largest = max(contours, key=cv2.contourArea)
    contour_2d = largest.reshape(-1, 2).astype(np.float64)
    np.save(os.path.join(args.output_dir, "graphene_contour.npy"), contour_2d)

    # Diagnostic overlay
    diag = desaturate(result["processed_image"], factor=0.4)
    cv2.drawContours(diag, [largest], -1, (0, 0, 255), 2)  # red BGR
    cv2.imwrite(os.path.join(args.output_dir, "02_graphene_on_top.png"), diag)

    # Result sidecar
    with open(os.path.join(args.output_dir, "graphene_result.json"), "w") as f:
        json.dump({"area_px": area_px, "area_um2": area_um2,
                    "cluster_id": result["selected_id"]}, f, indent=2)

    print(f"\nOK: graphene detected (sub-cluster {result['selected_id']})")
    print(f"  Area: {area_px} px ({area_um2} um²)")
    print(f"  Contour points: {len(contour_2d)}")
    print(f"  Mirror: {args.mirror}")
    print(f"  Outputs: {args.output_dir}")
    if args.cluster_id is None:
        print(f"\n  To override: re-run with --cluster-id <N> after "
              f"reviewing 00_graphene_candidates.png")


if __name__ == "__main__":
    main()
