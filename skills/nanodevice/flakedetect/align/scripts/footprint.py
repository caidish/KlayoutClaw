#!/usr/bin/env python
"""Build target footprint via shape-guided K-means + GrabCut.

Uses the source flake's shape (Hu moments, convexity, solidity) to
automatically select which K-means clusters in the target image form
the footprint. Refines edges with GrabCut.

Usage:
    conda run -n base python footprint.py \
        --source <source_image> --target <target_image> \
        [--mirror] --pixel-size <um/px> --output-dir <path>
"""

import argparse
import json
import os
import sys
from itertools import combinations

import cv2
import numpy as np
from sklearn.cluster import KMeans

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from core import morph_clean, flood_fill_holes, keep_largest_n, mask_centroid


def compute_shape_descriptors(contour):
    """Compute shape descriptors for a contour.

    Returns dict with: hu_moments, convexity, solidity, aspect_ratio, area.
    """
    area = cv2.contourArea(contour)
    if area < 10:
        return None

    hull = cv2.convexHull(contour)
    hull_area = cv2.contourArea(hull)
    perimeter = cv2.arcLength(contour, closed=True)
    hull_perimeter = cv2.arcLength(hull, closed=True)

    # Hu moments (log-transformed, scale/rotation invariant)
    moments = cv2.moments(contour)
    hu = cv2.HuMoments(moments).flatten()

    # Bounding rect for aspect ratio
    _, _, bw, bh = cv2.boundingRect(contour)
    aspect = max(bw, bh) / max(min(bw, bh), 1)

    return {
        "hu_moments": hu,
        "convexity": hull_perimeter / max(perimeter, 1),
        "solidity": area / max(hull_area, 1),
        "aspect_ratio": aspect,
        "area": area,
    }


def segment_source_flake(image):
    """Segment the source flake for shape reference (same as source_contour.py)."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]

    _, mask_gray = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    _, mask_sat = cv2.threshold(sat, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = cv2.bitwise_and(mask_gray, mask_sat)

    mask = morph_clean(mask, close_k=15, open_k=11)
    mask = keep_largest_n(mask, n=1, min_area=5000)
    mask = flood_fill_holes(mask)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None, None
    contour = max(contours, key=cv2.contourArea)
    return contour, mask


def cluster_target(image, n_clusters=8):
    """K-means clustering on target image in LAB color space.

    Returns:
        label_map: (H, W) int array with cluster labels 0..n_clusters-1
        km: fitted KMeans object
    """
    h, w = image.shape[:2]
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    pixels = lab.reshape(-1, 3).astype(np.float32)

    km = KMeans(n_clusters=n_clusters, n_init=10, random_state=42)
    labels = km.fit_predict(pixels)
    label_map = labels.reshape(h, w)

    return label_map, km


def filter_clusters(label_map, image, n_clusters):
    """Filter out substrate and tiny clusters.

    Returns list of cluster IDs that are candidates for the footprint.
    """
    h, w = label_map.shape
    total_px = h * w
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]

    candidates = []
    for cid in range(n_clusters):
        cluster_mask = (label_map == cid)
        area_frac = cluster_mask.sum() / total_px
        mean_sat = sat[cluster_mask].mean() if cluster_mask.any() else 0

        # Skip substrate (low saturation) and tiny clusters
        if mean_sat < 20:
            continue
        if area_frac < 0.02:
            continue
        # Skip dominant background (>60% of image is likely substrate)
        if area_frac > 0.60:
            continue

        candidates.append(cid)

    return candidates


def shape_distance(desc_a, desc_b, contour_a, contour_b, area_weight=0.3):
    """Compute shape distance between two contours using multiple metrics.

    Includes an area ratio penalty: footprints much smaller or larger than
    the source are penalized. This prevents tiny high-shape-match candidates
    from outranking correctly-sized ones.

    area_weight controls the penalty strength. At 0.3, a 2:1 area mismatch
    adds ~0.21 to the distance (ln(2) * 0.3).
    """
    # cv2.matchShapes: Hu-moment-based (invariant to rotation/scale/translation)
    hu_dist = cv2.matchShapes(contour_a, contour_b, cv2.CONTOURS_MATCH_I1, 0)

    # Convexity and solidity differences
    conv_diff = abs(desc_a["convexity"] - desc_b["convexity"])
    sol_diff = abs(desc_a["solidity"] - desc_b["solidity"])

    # Area ratio penalty: penalize candidates far from source area
    area_a = max(desc_a["area"], 1)
    area_b = max(desc_b["area"], 1)
    area_ratio = max(area_a, area_b) / min(area_a, area_b)  # always >= 1
    area_penalty = np.log(area_ratio) * area_weight  # 0 when equal, grows with mismatch

    return hu_dist + conv_diff + sol_diff + area_penalty


def enumerate_footprint_candidates(label_map, candidate_ids, source_desc,
                                   source_contour, source_area, scale_range=(0.3, 2.0)):
    """Enumerate subsets of 2-5 clusters and rank by shape similarity to source.

    Returns list of (distance, cluster_ids, contour, mask) sorted by distance.
    """
    h, w = label_map.shape
    results = []

    # Area bounds: source area adjusted for plausible scale range
    min_area = source_area * scale_range[0] ** 2
    max_area = source_area * scale_range[1] ** 2

    for size in range(2, min(6, len(candidate_ids) + 1)):
        for subset in combinations(candidate_ids, size):
            # Merge cluster masks
            merged = np.zeros((h, w), dtype=np.uint8)
            for cid in subset:
                merged[label_map == cid] = 255

            # Morph clean + largest component
            merged = morph_clean(merged, close_k=35, open_k=11)
            merged = keep_largest_n(merged, n=1, min_area=5000)
            merged = flood_fill_holes(merged)

            area = (merged > 0).sum()
            if area < min_area or area > max_area:
                continue

            # Extract contour
            contours, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL,
                                           cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                continue
            contour = max(contours, key=cv2.contourArea)

            # Shape descriptors
            desc = compute_shape_descriptors(contour)
            if desc is None:
                continue

            dist = shape_distance(source_desc, desc, source_contour, contour)
            results.append((dist, list(subset), contour, merged))

    results.sort(key=lambda x: x[0])
    return results


def grabcut_refine(image, kmeans_mask):
    """Refine a K-means footprint mask using GrabCut.

    The K-means mask provides object identity; GrabCut refines edges.
    Eroded region = definite foreground, as-is = probable foreground,
    dilated = probable background, rest = definite background.
    """
    h, w = image.shape[:2]
    erode_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25))
    dilate_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (35, 35))

    gc_mask = np.full((h, w), cv2.GC_BGD, dtype=np.uint8)
    gc_mask[cv2.dilate(kmeans_mask, dilate_k) > 0] = cv2.GC_PR_BGD
    gc_mask[kmeans_mask > 0] = cv2.GC_PR_FGD
    gc_mask[cv2.erode(kmeans_mask, erode_k) > 0] = cv2.GC_FGD

    bgd = np.zeros((1, 65), np.float64)
    fgd = np.zeros((1, 65), np.float64)

    try:
        cv2.grabCut(image, gc_mask, None, bgd, fgd, 8, cv2.GC_INIT_WITH_MASK)
    except cv2.error as e:
        print(f"WARNING: GrabCut failed ({e}), using K-means mask directly",
              file=sys.stderr)
        return kmeans_mask

    result = np.where(
        (gc_mask == cv2.GC_FGD) | (gc_mask == cv2.GC_PR_FGD), 255, 0
    ).astype(np.uint8)

    result = morph_clean(result, close_k=15, open_k=7)
    result = keep_largest_n(result, n=1, min_area=5000)
    result = flood_fill_holes(result)

    return result


def draw_cluster_map(label_map, n_clusters):
    """Draw a colored visualization of K-means clusters."""
    np.random.seed(42)
    colors = np.random.randint(50, 255, size=(n_clusters, 3), dtype=np.uint8)
    h, w = label_map.shape
    vis = np.zeros((h, w, 3), dtype=np.uint8)
    for cid in range(n_clusters):
        vis[label_map == cid] = colors[cid]
    return vis


def draw_candidates(image, candidates, source_contour, top_n=3):
    """Draw top N footprint candidates side by side."""
    h, w = image.shape[:2]
    n = min(top_n, len(candidates))
    if n == 0:
        return np.zeros((h, w, 3), dtype=np.uint8)

    panels = []
    for i in range(n):
        dist, cluster_ids, contour, mask = candidates[i]
        panel = image.copy()
        cv2.drawContours(panel, [contour], -1, (0, 255, 0), 2)
        cv2.putText(panel, f"#{i+1} dist={dist:.3f} cl={cluster_ids}",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        panels.append(panel)

    # Stack horizontally, scaled down
    scale = min(1.0, 1200.0 / (w * n))
    resized = [cv2.resize(p, None, fx=scale, fy=scale) for p in panels]
    return np.hstack(resized)


def main():
    parser = argparse.ArgumentParser(
        description="Build target footprint via shape-guided K-means + GrabCut."
    )
    parser.add_argument("--source", required=True,
                        help="Source image (for shape reference)")
    parser.add_argument("--target", required=True,
                        help="Target image (full_stack, for K-means)")
    parser.add_argument("--source-contour", default=None,
                        help="Pre-computed source contour .npy (from source_contour.py). "
                             "If provided, skips internal segmentation.")
    parser.add_argument("--source-mask", default=None,
                        help="Pre-computed source mask .png (from source_contour.py)")
    parser.add_argument("--mirror", action="store_true",
                        help="Mirror source before shape extraction")
    parser.add_argument("--pixel-size", type=float, required=True,
                        help="Microns per pixel")
    parser.add_argument("--n-clusters", type=int, default=16,
                        help="Number of K-means clusters (default: 16, try 20-24 on retry)")
    parser.add_argument("--candidate-rank", type=int, default=1,
                        help="Which ranked candidate to use (1=best, 2=second-best, etc.)")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    args = parser.parse_args()

    # Load images
    source_img = cv2.imread(args.source)
    target_img = cv2.imread(args.target)
    if source_img is None:
        print(f"ERROR: Cannot read source: {args.source}", file=sys.stderr)
        sys.exit(1)
    if target_img is None:
        print(f"ERROR: Cannot read target: {args.target}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)

    # Step 1: Get source flake shape reference
    if args.source_contour and args.source_mask:
        # Use pre-computed contour/mask from source_contour.py
        source_contour = np.load(args.source_contour).astype(np.int32)
        source_mask = cv2.imread(args.source_mask, cv2.IMREAD_GRAYSCALE)
        if source_contour is None or source_mask is None:
            print("ERROR: Cannot read pre-computed source contour/mask.", file=sys.stderr)
            sys.exit(1)
        # Reshape for OpenCV compatibility
        source_contour = source_contour.reshape(-1, 1, 2)
    else:
        # Fall back to internal segmentation
        if args.mirror:
            source_img = cv2.flip(source_img, 1)
        source_contour, source_mask = segment_source_flake(source_img)
        if source_contour is None:
            print("ERROR: Cannot segment source flake.", file=sys.stderr)
            sys.exit(1)

    source_desc = compute_shape_descriptors(source_contour)
    if source_desc is None:
        print("ERROR: Source flake too small for shape descriptors.", file=sys.stderr)
        sys.exit(1)

    source_area = source_desc["area"]
    print(f"Source flake: area={source_area:.0f}px, "
          f"convexity={source_desc['convexity']:.3f}, "
          f"solidity={source_desc['solidity']:.3f}")

    # Step 2: K-means clustering on target
    print(f"K-means clustering (n={args.n_clusters})...")
    label_map, km = cluster_target(target_img, n_clusters=args.n_clusters)

    cluster_vis = draw_cluster_map(label_map, args.n_clusters)
    cv2.imwrite(os.path.join(args.output_dir, "02_cluster_map.png"), cluster_vis)

    # Step 3: Filter and enumerate candidates
    candidate_ids = filter_clusters(label_map, target_img, args.n_clusters)
    print(f"Candidate clusters: {candidate_ids} ({len(candidate_ids)} of {args.n_clusters})")

    if len(candidate_ids) < 2:
        print("ERROR: Too few candidate clusters for footprint construction.",
              file=sys.stderr)
        sys.exit(1)

    print("Enumerating cluster subsets...")
    candidates = enumerate_footprint_candidates(
        label_map, candidate_ids, source_desc,
        source_contour, source_area
    )

    if not candidates:
        print("ERROR: No viable footprint candidates found.", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(candidates)} candidates. Best shape_distance={candidates[0][0]:.4f}")

    # Save candidates diagnostic
    cand_vis = draw_candidates(target_img, candidates, source_contour, top_n=3)
    cv2.imwrite(os.path.join(args.output_dir, "03_footprint_candidates.png"), cand_vis)

    # Step 4: GrabCut refinement on selected candidate
    rank = args.candidate_rank - 1  # 1-indexed to 0-indexed
    if rank >= len(candidates):
        print(f"ERROR: Requested candidate rank {args.candidate_rank} but only "
              f"{len(candidates)} candidates available.", file=sys.stderr)
        sys.exit(1)
    best_dist, best_ids, best_contour, best_mask = candidates[rank]
    print(f"Selected candidate #{args.candidate_rank}: clusters={best_ids}, "
          f"shape_distance={best_dist:.4f}")
    print("Running GrabCut refinement...")

    fp_mask = grabcut_refine(target_img, best_mask)
    fp_area = int((fp_mask > 0).sum())

    # Extract footprint contour
    fp_contours, _ = cv2.findContours(fp_mask, cv2.RETR_EXTERNAL,
                                      cv2.CHAIN_APPROX_SIMPLE)
    if not fp_contours:
        print("ERROR: GrabCut produced empty mask.", file=sys.stderr)
        sys.exit(1)

    fp_contour = max(fp_contours, key=cv2.contourArea)
    fp_pts = fp_contour.reshape(-1, 2).astype(np.float64)

    # Save outputs
    mask_path = os.path.join(args.output_dir, "footprint_mask.png")
    cv2.imwrite(mask_path, fp_mask)

    contour_path = os.path.join(args.output_dir, "footprint_contour.npy")
    np.save(contour_path, fp_pts)

    # GrabCut diagnostic
    diag = target_img.copy()
    cv2.drawContours(diag, [fp_contour], -1, (0, 255, 0), 2)
    cv2.drawContours(diag, [best_contour], -1, (0, 255, 255), 1)  # K-means contour in yellow
    cv2.putText(diag, f"GrabCut area={fp_area}px  clusters={best_ids}  dist={best_dist:.3f}",
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    cv2.imwrite(os.path.join(args.output_dir, "04_footprint_grabcut.png"), diag)

    # Update alignment_report.json
    report_path = os.path.join(args.output_dir, "alignment_report.json")
    if os.path.exists(report_path):
        with open(report_path) as f:
            report = json.load(f)
    else:
        report = {}

    report["footprint"] = {
        "cluster_ids": best_ids,
        "shape_distance": round(best_dist, 4),
        "grabcut_area_px": fp_area,
        "kmeans_area_px": int((best_mask > 0).sum()),
        "mask_file": "footprint_mask.png",
        "contour_file": "footprint_contour.npy",
        "n_points": len(fp_pts),
    }
    report["target_image"] = os.path.abspath(args.target)
    report["pixel_size_um"] = args.pixel_size

    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    # Summary
    km_area = int((best_mask > 0).sum())
    pct_change = (fp_area - km_area) / km_area * 100 if km_area > 0 else 0
    print(f"Footprint: {len(fp_pts)} pts, {fp_area} px "
          f"(GrabCut {pct_change:+.1f}% vs K-means {km_area} px)")
    print(f"shape_distance={best_dist:.4f}, clusters={best_ids}")


if __name__ == "__main__":
    main()
