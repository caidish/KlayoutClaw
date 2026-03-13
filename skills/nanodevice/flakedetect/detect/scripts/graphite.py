#!/usr/bin/env python
"""Detect graphite strip from bottom_part image via K-means sub-clustering.

The bottom_part image shows hBN with a graphite strip. Graphite appears
as a distinctly darker region within the hBN flake. Detection first
isolates the hBN flake region, then sub-clusters within it in LAB color
space and selects the darkest sub-cluster as graphite.

Two-pass workflow (same pattern as graphene and align sweep):
  1. First run: isolate hBN flake, sub-cluster, auto-select darkest,
     save candidate images for each sub-cluster so the agent can review.
  2. If agent disagrees: re-run with --cluster-id to pick the correct one.

Usage:
    # First pass: auto-detect + save candidates for review
    conda run -n base python graphite.py \
        --image <bottom_part.jpg> \
        --pixel-size <um/px> \
        --output-dir <path>

    # Second pass: agent picks correct cluster after vision review
    conda run -n base python graphite.py \
        --image <bottom_part.jpg> \
        --pixel-size <um/px> \
        --cluster-id 0 \
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


def detect_graphite(image, n_sub_clusters=4, min_area=5000, cluster_id=None):
    """Detect graphite strip via K-means sub-clustering within hBN flake.

    Args:
        image: BGR image of bottom_part.
        n_sub_clusters: Number of sub-clusters within flake.
        min_area: Minimum pixel area for a graphite candidate.
        cluster_id: If set, use this specific sub-cluster (agent override).

    Returns:
        dict with graphite_mask, graphite_contour, hbn_mask,
              sub_cluster_masks, sub_cluster_stats, selected_id.
    """
    h, w = image.shape[:2]

    # Step 1: Isolate hBN flake region (saturated blue-ish)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    h_chan = hsv[:, :, 0].astype(np.float32)
    s_chan = hsv[:, :, 1].astype(np.float32)

    hbn_mask = np.zeros((h, w), dtype=np.uint8)
    hbn_cond = (s_chan > 60) & (h_chan > 70) & (h_chan < 130)
    hbn_mask[hbn_cond] = 255
    hbn_mask = morph_clean(hbn_mask, close_k=15, open_k=9)
    hbn_mask = keep_largest_n(hbn_mask, n=1, min_area=10000)
    hbn_mask = flood_fill_holes(hbn_mask)

    if hbn_mask.sum() == 0:
        return {"graphite_mask": np.zeros((h, w), dtype=np.uint8),
                "graphite_contour": None, "hbn_mask": hbn_mask,
                "sub_cluster_masks": [], "sub_cluster_stats": [],
                "selected_id": None}

    # Step 2: Sub-cluster WITHIN the hBN flake in LAB space
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    flake_pixels = lab[hbn_mask > 0].reshape(-1, 3).astype(np.float32)
    flake_coords = np.argwhere(hbn_mask > 0)  # (row, col) pairs

    if len(flake_pixels) < n_sub_clusters:
        return {"graphite_mask": np.zeros((h, w), dtype=np.uint8),
                "graphite_contour": None, "hbn_mask": hbn_mask,
                "sub_cluster_masks": [], "sub_cluster_stats": [],
                "selected_id": None}

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
        # Auto: darkest sub-cluster by L-channel center
        selected_id = int(np.argmin(sub_km.cluster_centers_[:, 0]))

    # Build graphite mask from selected sub-cluster
    graphite_mask = sub_cluster_masks[selected_id].copy()
    graphite_mask = morph_clean(graphite_mask, close_k=7, open_k=3)
    graphite_mask = keep_largest_n(graphite_mask, n=1, min_area=min_area)
    graphite_mask = flood_fill_holes(graphite_mask)

    # Extract contour
    contours, _ = cv2.findContours(
        graphite_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    graphite_contour = max(contours, key=cv2.contourArea) if contours else None

    return {
        "graphite_mask": graphite_mask,
        "graphite_contour": graphite_contour,
        "hbn_mask": hbn_mask,
        "sub_cluster_masks": sub_cluster_masks,
        "sub_cluster_stats": sub_cluster_stats,
        "selected_id": selected_id,
    }


def draw_candidates_grid(image, sub_cluster_masks, sub_cluster_stats,
                         selected_id):
    """Draw a grid showing each sub-cluster candidate for agent review."""
    n = len(sub_cluster_masks)
    h, w = image.shape[:2]

    candidate_colors = [
        (0, 200, 255),  # yellow (graphite default)
        (0, 255, 0),    # green
        (0, 0, 255),    # red
        (255, 0, 0),    # blue
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
        cv2.drawContours(panel, contours, -1, color, 2)

        s = sub_cluster_stats[i]
        marker = " [AUTO]" if i == selected_id else ""
        label = f"c{i}: L={s['L']:.0f} area={s['area_px']}px{marker}"
        cv2.putText(panel, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (255, 255, 255), 2, cv2.LINE_AA)
        panels.append(panel)

    # Stack horizontally (or 2-row grid for >3)
    if n <= 3:
        grid = np.hstack(panels)
    else:
        row1 = np.hstack(panels[:(n + 1) // 2])
        row2_panels = panels[(n + 1) // 2:]
        while len(row2_panels) < (n + 1) // 2:
            row2_panels.append(np.zeros_like(panels[0]))
        row2 = np.hstack(row2_panels)
        if row1.shape[1] != row2.shape[1]:
            diff = row1.shape[1] - row2.shape[1]
            if diff > 0:
                row2 = np.hstack(
                    [row2, np.zeros((h, diff, 3), dtype=np.uint8)])
            else:
                row1 = np.hstack(
                    [row1, np.zeros((h, -diff, 3), dtype=np.uint8)])
        grid = np.vstack([row1, row2])

    return grid


def main():
    parser = argparse.ArgumentParser(
        description="Detect graphite strip from bottom_part image "
                    "(K-means sub-clustering within hBN flake)")
    parser.add_argument("--image", required=True,
                        help="Source image (bottom_part)")
    parser.add_argument("--pixel-size", type=float, required=True,
                        help="Microns per pixel")
    parser.add_argument("--output-dir", required=True,
                        help="Output directory")
    parser.add_argument("--n-sub-clusters", type=int, default=4,
                        help="Sub-cluster count within hBN flake (default: 4)")
    parser.add_argument("--min-area", type=int, default=5000,
                        help="Min graphite area in pixels (default: 5000)")
    parser.add_argument("--cluster-id", type=int, default=None,
                        help="Agent override: use this sub-cluster ID "
                             "as graphite (after reviewing candidates)")
    args = parser.parse_args()

    image_path = os.path.abspath(os.path.expanduser(args.image))
    image = cv2.imread(image_path)
    if image is None:
        print(f"ERROR: cannot read image: {image_path}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)

    result = detect_graphite(image, n_sub_clusters=args.n_sub_clusters,
                             min_area=args.min_area,
                             cluster_id=args.cluster_id)

    # Always save candidate grid for agent vision review
    if result["sub_cluster_masks"]:
        grid = draw_candidates_grid(
            image, result["sub_cluster_masks"],
            result["sub_cluster_stats"], result["selected_id"])
        cv2.imwrite(os.path.join(args.output_dir,
                                 "00_graphite_candidates.png"), grid)

    # Print sub-cluster stats for agent
    print("Sub-cluster stats (sorted by L, darkest first):")
    sorted_stats = sorted(result["sub_cluster_stats"],
                          key=lambda s: s["L"])
    for s in sorted_stats:
        marker = " <-- selected" if s["id"] == result["selected_id"] else ""
        print(f"  c{s['id']}: L={s['L']:.0f} a={s['a']:.0f} "
              f"b={s['b']:.0f} area={s['area_px']}px{marker}")

    if result["graphite_contour"] is None:
        print("ERROR: no graphite strip found", file=sys.stderr)
        print("Review 00_graphite_candidates.png and re-run with "
              "--cluster-id <N>", file=sys.stderr)
        sys.exit(1)

    contour = result["graphite_contour"]
    mask = result["graphite_mask"]

    area_px = int((mask > 0).sum())
    area_um2 = round(area_px * args.pixel_size * args.pixel_size, 2)

    # Save mask
    cv2.imwrite(os.path.join(args.output_dir, "graphite_mask.png"), mask)

    # Save contour as (N,2) float64
    contour_2d = contour.reshape(-1, 2).astype(np.float64)
    np.save(os.path.join(args.output_dir, "graphite_contour.npy"), contour_2d)

    # Diagnostic overlay
    diag = desaturate(image, factor=0.4)
    cv2.drawContours(diag, [contour], -1, (0, 200, 255), 2)  # yellow BGR
    cv2.imwrite(os.path.join(args.output_dir, "01_graphite_on_bottom.png"),
                diag)

    # Result sidecar JSON
    with open(os.path.join(args.output_dir, "graphite_result.json"), "w") as f:
        json.dump({"area_px": area_px, "area_um2": area_um2,
                    "cluster_id": result["selected_id"]}, f, indent=2)

    _, _, bw, bh = cv2.boundingRect(contour)
    aspect = max(bw, bh) / max(min(bw, bh), 1)
    print(f"\nOK: graphite strip detected (sub-cluster {result['selected_id']})")
    print(f"  Area: {area_px} px ({area_um2} um²)")
    print(f"  Bounding box: {bw} x {bh} (aspect {aspect:.1f})")
    print(f"  Contour points: {len(contour_2d)}")
    print(f"  Outputs: {args.output_dir}")
    if args.cluster_id is None:
        print(f"\n  To override: re-run with --cluster-id <N> after "
              f"reviewing 00_graphite_candidates.png")


if __name__ == "__main__":
    main()
