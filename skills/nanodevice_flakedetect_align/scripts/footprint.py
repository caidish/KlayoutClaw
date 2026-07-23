#!/usr/bin/env python
"""Build target footprint via shape-guided K-means + GrabCut.

Candidate B1: GrabCut with black-border padding + post-GrabCut quality gate
+ aggressive seed-shrink retry loop.

Why black-border padding: the GrabCut algorithm has no anchor telling it
"image edge = definitely background"; a flake whose substrate-side gradient
is similar to the image margin will let GrabCut grow off the edge and engulf
the entire frame as foreground (the canonical #32 whole-image quadrilateral).
Padding the input with a thick black border (>= 50 px or max(H,W)//30) gives
GrabCut unambiguous BGD pixels around the perimeter so the foreground cannot
escape past the original ROI.

Quality gate (after GrabCut, before commit):
- n_points >= 8 on the contour
- area / image_area < 0.85
- if either fails, retry with a 2x more aggressive erosion of the K-means seed
- max 3 attempts; on final failure write status:"failed" and exit non-zero.

Usage:
    conda run -n instrMCPdev python footprint.py \
        --source <source_image> --target <target_image> \
        --bottom <bottom_part_image> \
        [--mirror] --pixel-size <um/px> --output-dir <path> \
        [--warp <warp_sift_bottom.npy>]

Warp resolution (issue #31):
    If --warp PATH is given, the precomputed bottom->target warp is loaded
    via np.load(PATH) and the internal SIFT pass is skipped. If --warp is
    omitted, footprint.py looks for a precomputed warp at
    <output-dir>/warp_sift_bottom.npy and at <source-dir>/../align/
    warp_sift_bottom.npy before falling back to running SIFT internally.
"""

import argparse
import json
import os
import sys
from itertools import combinations

import cv2
import numpy as np
from sklearn.cluster import KMeans

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "nanodevice_flakedetect", "scripts"))
from core import morph_clean, flood_fill_holes, keep_largest_n, mask_centroid


# ---------------------------------------------------------------------------
# Black-border padding
# ---------------------------------------------------------------------------

def black_border_pad(image, mask=None, min_border_px=50):
    """Pad image (and optional mask) with a thick black border.

    Border width = max(min_border_px, max(H, W) // 30).
    Returns (padded_image, padded_mask, border_px).
    """
    h, w = image.shape[:2]
    border_px = max(min_border_px, max(h, w) // 30)
    if image.ndim == 3:
        padded = cv2.copyMakeBorder(image, border_px, border_px, border_px, border_px,
                                    cv2.BORDER_CONSTANT, value=(0, 0, 0))
    else:
        padded = cv2.copyMakeBorder(image, border_px, border_px, border_px, border_px,
                                    cv2.BORDER_CONSTANT, value=0)
    padded_mask = None
    if mask is not None:
        padded_mask = cv2.copyMakeBorder(mask, border_px, border_px, border_px, border_px,
                                         cv2.BORDER_CONSTANT, value=0)
    return padded, padded_mask, border_px


def crop_border(padded, border_px):
    return padded[border_px:-border_px, border_px:-border_px]


# ---------------------------------------------------------------------------
# SIFT alignment
# ---------------------------------------------------------------------------

def sift_align(bottom_img, target_img, n_features=5000, ratio_thresh=0.7):
    gray_bot = cv2.cvtColor(bottom_img, cv2.COLOR_BGR2GRAY) if bottom_img.ndim == 3 else bottom_img
    gray_tgt = cv2.cvtColor(target_img, cv2.COLOR_BGR2GRAY) if target_img.ndim == 3 else target_img

    sift = cv2.SIFT_create(nfeatures=n_features)
    kp1, des1 = sift.detectAndCompute(gray_bot, None)
    kp2, des2 = sift.detectAndCompute(gray_tgt, None)

    if des1 is None or des2 is None or len(kp1) < 4 or len(kp2) < 4:
        return None, 0

    bf = cv2.BFMatcher(cv2.NORM_L2)
    raw_matches = bf.knnMatch(des1, des2, k=2)
    good = [m for m, n in raw_matches if len([m, n]) == 2 and m.distance < ratio_thresh * n.distance]

    if len(good) < 10:
        return None, len(good)

    src_pts = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst_pts = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    M, mask = cv2.estimateAffinePartial2D(src_pts, dst_pts, method=cv2.RANSAC,
                                           ransacReprojThreshold=5.0)
    n_inliers = int(mask.sum()) if mask is not None else 0
    return M, n_inliers


def resolve_warp_path(explicit_path, output_dir, source_path):
    """Resolve the path of a precomputed bottom->target SIFT warp.

    Priority (issue #31):
      1. --warp PATH (if given and exists)
      2. <output_dir>/warp_sift_bottom.npy
      3. <parent_of_source_dir>/align/warp_sift_bottom.npy
    Returns (path, source_tag) or (None, None) if no candidate exists.
    """
    if explicit_path:
        if os.path.exists(explicit_path):
            return explicit_path, "--warp"
        return None, None
    cand1 = os.path.join(output_dir, "warp_sift_bottom.npy")
    if os.path.exists(cand1):
        return cand1, "output_dir"
    src_parent = os.path.dirname(os.path.abspath(source_path))
    cand2 = os.path.join(os.path.dirname(src_parent), "align",
                         "warp_sift_bottom.npy")
    if os.path.exists(cand2):
        return cand2, "source_parent/align"
    return None, None


def load_warp_matrix(path):
    """Load and validate a 2x3 affine warp matrix from .npy."""
    M = np.load(path)
    if M.shape != (2, 3):
        raise ValueError(
            f"warp matrix at {path} has shape {M.shape}, expected (2, 3)"
        )
    return M.astype(np.float64)


def target_corner_gray_mean_bgr(target_bgr, corner_frac=0.08):
    """Mean BGR color from low-saturation gray pixels in full_stack corners."""
    h, w = target_bgr.shape[:2]
    ph = max(8, int(round(h * corner_frac)))
    pw = max(8, int(round(w * corner_frac)))
    patches = [
        target_bgr[:ph, :pw],
        target_bgr[:ph, w - pw:],
        target_bgr[h - ph:, :pw],
        target_bgr[h - ph:, w - pw:],
    ]
    corner_pixels = np.concatenate(
        [p.reshape(-1, target_bgr.shape[2]) for p in patches], axis=0
    )
    hsv = cv2.cvtColor(corner_pixels.reshape(-1, 1, 3), cv2.COLOR_BGR2HSV)
    sat = hsv[:, 0, 1]
    val = hsv[:, 0, 2]
    gray_pixels = corner_pixels[(sat <= 35) & (val >= 20)]
    if gray_pixels.size == 0:
        gray_pixels = corner_pixels
    return gray_pixels.reshape(-1, target_bgr.shape[2]).mean(axis=0)


def compute_diff_image(target_bgr, bottom_bgr, warp_matrix,
                       source_bgr=None, source_mask=None):
    h, w = target_bgr.shape[:2]
    warped = cv2.warpAffine(bottom_bgr, warp_matrix, (w, h))
    valid_src = np.full(bottom_bgr.shape[:2], 255, dtype=np.uint8)
    overlap = cv2.warpAffine(
        valid_src, warp_matrix, (w, h), flags=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT, borderValue=0,
    )
    fill = target_corner_gray_mean_bgr(target_bgr)
    warped = warped.copy()
    warped[overlap == 0] = np.clip(fill, 0, 255).astype(np.uint8)

    target_lab = cv2.cvtColor(target_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    warped_lab = cv2.cvtColor(warped, cv2.COLOR_BGR2LAB).astype(np.float32)

    diff = target_lab - warped_lab
    mag = np.sqrt((diff ** 2).sum(axis=2))
    return np.clip(mag, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# Shape descriptors
# ---------------------------------------------------------------------------

def compute_shape_descriptors(contour):
    area = cv2.contourArea(contour)
    if area < 10:
        return None
    hull = cv2.convexHull(contour)
    hull_area = cv2.contourArea(hull)
    perimeter = cv2.arcLength(contour, closed=True)
    hull_perimeter = cv2.arcLength(hull, closed=True)
    moments = cv2.moments(contour)
    hu = cv2.HuMoments(moments).flatten()
    _, _, bw, bh = cv2.boundingRect(contour)
    aspect = max(bw, bh) / max(min(bw, bh), 1)
    return {
        "hu_moments": hu,
        "convexity": hull_perimeter / max(perimeter, 1),
        "solidity": area / max(hull_area, 1),
        "aspect_ratio": aspect,
        "area": area,
    }


# ---------------------------------------------------------------------------
# Source segmentation (used only when --source-contour not provided)
# ---------------------------------------------------------------------------

def grabcut_refine_basic(image, kmeans_mask):
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
    except cv2.error:
        return kmeans_mask
    result = np.where(
        (gc_mask == cv2.GC_FGD) | (gc_mask == cv2.GC_PR_FGD), 255, 0
    ).astype(np.uint8)
    result = morph_clean(result, close_k=15, open_k=7)
    result = keep_largest_n(result, n=1, min_area=5000)
    result = flood_fill_holes(result)
    return result


def segment_source_flake(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]
    _, mask_gray = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    _, mask_sat = cv2.threshold(sat, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = cv2.bitwise_or(mask_gray, mask_sat)
    mask = morph_clean(mask, close_k=5, open_k=3)
    mask = keep_largest_n(mask, n=1, min_area=5000)
    mask = flood_fill_holes(mask)
    mask = grabcut_refine_basic(image, mask)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None, None
    contour = max(contours, key=cv2.contourArea)
    return contour, mask


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------

def cluster_target_diff(diff_gray, n_clusters=12):
    h, w = diff_gray.shape[:2]
    pixels = diff_gray.reshape(-1, 1).astype(np.float32)
    km = KMeans(n_clusters=n_clusters, n_init=30, random_state=42)
    labels = km.fit_predict(pixels)
    return labels.reshape(h, w), km


def filter_clusters_diff(label_map, diff_gray, n_clusters):
    h, w = label_map.shape
    total_px = h * w
    candidates = []
    for cid in range(n_clusters):
        cluster_mask = (label_map == cid)
        area_frac = cluster_mask.sum() / total_px
        mean_intensity = diff_gray[cluster_mask].mean() if cluster_mask.any() else 0
        if mean_intensity < 15:
            continue
        if area_frac < 0.003:
            continue
        if area_frac > 0.60:
            continue
        candidates.append(cid)
    return candidates


def split_clusters(label_map, candidate_ids, source_area):
    min_split_area = source_area / 16
    new_label_map = label_map.copy()
    new_candidates = []
    next_id = int(label_map.max()) + 1
    for cid in candidate_ids:
        cluster_mask = (label_map == cid).astype(np.uint8) * 255
        num_labels, comp_labels, stats, centroids = cv2.connectedComponentsWithStats(
            cluster_mask, connectivity=8
        )
        large_comps = []
        small_comps = []
        for i in range(1, num_labels):
            area = stats[i, cv2.CC_STAT_AREA]
            if area >= min_split_area:
                large_comps.append(i)
            else:
                small_comps.append(i)
        if len(large_comps) <= 1:
            new_candidates.append(cid)
            continue
        comp_to_subcid = {}
        for comp_i in large_comps:
            sub_id = next_id
            next_id += 1
            comp_to_subcid[comp_i] = sub_id
            new_label_map[comp_labels == comp_i] = sub_id
            new_candidates.append(sub_id)
        for comp_i in small_comps:
            cy, cx = centroids[comp_i][1], centroids[comp_i][0]
            best_dist = float('inf')
            best_sub = large_comps[0]
            for lc in large_comps:
                lcy, lcx = centroids[lc][1], centroids[lc][0]
                d = (cy - lcy)**2 + (cx - lcx)**2
                if d < best_dist:
                    best_dist = d
                    best_sub = lc
            new_label_map[comp_labels == comp_i] = comp_to_subcid[best_sub]
    return new_label_map, new_candidates


def shape_distance(desc_a, desc_b, contour_a, contour_b, area_weight=0.45):
    hu_dist = cv2.matchShapes(contour_a, contour_b, cv2.CONTOURS_MATCH_I1, 0)
    conv_diff = abs(desc_a["convexity"] - desc_b["convexity"])
    sol_diff = abs(desc_a["solidity"] - desc_b["solidity"])
    area_a = max(desc_a["area"], 1)
    area_b = max(desc_b["area"], 1)
    area_ratio = max(area_a, area_b) / min(area_a, area_b)
    area_penalty = np.log(area_ratio) * area_weight
    return hu_dist + conv_diff + sol_diff + area_penalty


def enumerate_footprint_candidates(label_map, candidate_ids, source_desc,
                                   source_contour, source_area, scale_range=(0.3, 2.0)):
    h, w = label_map.shape
    results = []
    min_area = source_area * scale_range[0] ** 2
    max_area = source_area * scale_range[1] ** 2
    for size in range(1, min(6, len(candidate_ids) + 1)):
        for subset in combinations(candidate_ids, size):
            merged = np.zeros((h, w), dtype=np.uint8)
            for cid in subset:
                merged[label_map == cid] = 255
            merged = morph_clean(merged, close_k=10, open_k=5)
            merged = keep_largest_n(
                merged, n=64, min_area=max(5000, int(source_area / 16))
            )
            merged = flood_fill_holes(merged)
            area = (merged > 0).sum()
            if area < min_area or area > max_area:
                continue
            contours, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL,
                                           cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                continue
            contour = max(contours, key=cv2.contourArea)
            desc = compute_shape_descriptors(contour)
            if desc is None:
                continue
            dist = shape_distance(source_desc, desc, source_contour, contour)
            results.append((dist, list(subset), contour, merged.copy()))
            # Bound memory: keep only the top-K candidates by shape distance.
            # Each stored candidate retains a full-frame uint8 mask (~3 MB),
            # so thousands of passing subsets would OOM a 4 GB cgroup.
            if len(results) > 60:
                results.sort(key=lambda x: x[0])
                del results[30:]
    results.sort(key=lambda x: x[0])
    return results


# ---------------------------------------------------------------------------
# Padded GrabCut + retry
# ---------------------------------------------------------------------------

def grabcut_padded(image, kmeans_mask, erode_size=25, dilate_size=35,
                   border_min_px=50, n_iter=8):
    h, w = image.shape[:2]
    padded_img, padded_seed, border_px = black_border_pad(image, kmeans_mask, border_min_px)
    erode_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (erode_size, erode_size))
    dilate_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate_size, dilate_size))
    ph, pw = padded_img.shape[:2]
    gc_mask = np.full((ph, pw), cv2.GC_BGD, dtype=np.uint8)
    gc_mask[cv2.dilate(padded_seed, dilate_k) > 0] = cv2.GC_PR_BGD
    gc_mask[padded_seed > 0] = cv2.GC_PR_FGD
    gc_mask[cv2.erode(padded_seed, erode_k) > 0] = cv2.GC_FGD
    bgd = np.zeros((1, 65), np.float64)
    fgd = np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(padded_img, gc_mask, None, bgd, fgd, n_iter, cv2.GC_INIT_WITH_MASK)
    except cv2.error as e:
        print(f"WARNING: GrabCut failed ({e}), using K-means seed directly",
              file=sys.stderr)
        return kmeans_mask, border_px
    padded_result = np.where(
        (gc_mask == cv2.GC_FGD) | (gc_mask == cv2.GC_PR_FGD), 255, 0
    ).astype(np.uint8)
    result = crop_border(padded_result, border_px)
    result = morph_clean(result, close_k=15, open_k=7)
    result = keep_largest_n(result, n=1, min_area=5000)
    result = flood_fill_holes(result)
    return result, border_px


def evaluate_footprint(mask, image_h, image_w):
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return 0, 0, 0.0
    contour = max(contours, key=cv2.contourArea)
    n_points = len(contour)
    area_px = int((mask > 0).sum())
    area_frac = area_px / max(1, image_h * image_w)
    return n_points, area_px, area_frac


def grabcut_with_quality_retry(image, kmeans_seed, image_h, image_w,
                               border_min_px=50, max_attempts=3):
    erode_size = 25
    dilate_size = 35
    last_mask = None
    last_info = None
    last_border = 0
    for attempt in range(1, max_attempts + 1):
        mask, border_px = grabcut_padded(image, kmeans_seed,
                                         erode_size=erode_size,
                                         dilate_size=dilate_size,
                                         border_min_px=border_min_px)
        n_points, area_px, area_frac = evaluate_footprint(mask, image_h, image_w)
        gate_pass = (n_points >= 8) and (area_frac < 0.85) and (area_px > 0)
        info = {
            "attempt": attempt,
            "erode_size": erode_size,
            "dilate_size": dilate_size,
            "n_points": int(n_points),
            "area_px": int(area_px),
            "area_frac": float(area_frac),
            "gate_pass": bool(gate_pass),
        }
        last_mask = mask
        last_info = info
        last_border = border_px
        if gate_pass:
            return mask, attempt, info, border_px
        erode_size = min(95, erode_size * 2)
    return last_mask, max_attempts, last_info, last_border


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------

def _read_report(report_path):
    if os.path.exists(report_path):
        with open(report_path) as f:
            return json.load(f)
    return {}


def emit_failure(report_path, reason, image_h, image_w, fp_info=None,
                 extra=None):
    report = _read_report(report_path)
    fp = {
        "mode": "diff",
        "shape_distance": -1.0,
        "n_points": int((fp_info or {}).get("n_points", 0)),
        "area_px": int((fp_info or {}).get("area_px", 0)),
        "image_h": int(image_h),
        "image_w": int(image_w),
        "status": "failed",
        "reason": reason,
        "candidate_id": "B1",
    }
    if extra:
        fp.update(extra)
    report["footprint"] = fp
    report["status"] = "failed"
    report["image_h"] = int(image_h)
    report["image_w"] = int(image_w)
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)


def emit_success(report_path, fp_info, image_h, image_w, best_ids,
                 km_area, fp_area, best_dist, n_points, mask_file,
                 contour_file, target_image, pixel_size, attempt, border_px):
    report = _read_report(report_path)
    report["footprint"] = {
        "mode": "diff",
        "cluster_ids": list(best_ids),
        "shape_distance": round(float(best_dist), 4),
        "grabcut_area_px": int(fp_area),
        "kmeans_area_px": int(km_area),
        "area_px": int(fp_area),
        "mask_file": mask_file,
        "contour_file": contour_file,
        "n_points": int(n_points),
        "image_h": int(image_h),
        "image_w": int(image_w),
        "status": "ok",
        "candidate_id": "B1",
        "border_px": int(border_px),
        "attempt": int(attempt),
    }
    report["target_image"] = os.path.abspath(target_image)
    report["pixel_size_um"] = pixel_size
    report["image_h"] = int(image_h)
    report["image_w"] = int(image_w)
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)


def draw_cluster_map(label_map):
    np.random.seed(42)
    n_max = int(label_map.max()) + 1
    colors = np.random.randint(50, 255, size=(n_max, 3), dtype=np.uint8)
    h, w = label_map.shape
    vis = np.zeros((h, w, 3), dtype=np.uint8)
    for cid in range(n_max):
        vis[label_map == cid] = colors[cid]
    return vis


def draw_candidates(image, candidates, source_contour, top_n=3):
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
    scale = min(1.0, 1200.0 / (w * n))
    resized = [cv2.resize(p, None, fx=scale, fy=scale) for p in panels]
    return np.hstack(resized)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Build target footprint via shape-guided K-means + GrabCut "
                    "(B1: black-border padding + quality-gate retry).")
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--bottom", required=True)
    parser.add_argument("--source-contour", default=None)
    parser.add_argument("--source-mask", default=None)
    parser.add_argument("--mirror", action="store_true")
    parser.add_argument("--pixel-size", type=float, required=True)
    parser.add_argument("--n-clusters", type=int, default=12)
    parser.add_argument("--candidate-rank", type=int, default=1)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--warp", default=None,
        help="Path to precomputed bottom->target SIFT warp (.npy). "
             "If omitted, footprint.py auto-resolves "
             "<output-dir>/warp_sift_bottom.npy or "
             "<source-parent>/../align/warp_sift_bottom.npy. "
             "Only runs SIFT internally when neither path resolves (#31).")
    args = parser.parse_args()

    source_img = cv2.imread(args.source)
    target_img = cv2.imread(args.target)
    bottom_img = cv2.imread(args.bottom)
    if source_img is None or target_img is None or bottom_img is None:
        print("ERROR: Cannot read input images", file=sys.stderr)
        sys.exit(1)
    os.makedirs(args.output_dir, exist_ok=True)
    report_path = os.path.join(args.output_dir, "alignment_report.json")
    target_h, target_w = target_img.shape[:2]

    # Source contour
    if args.source_contour and args.source_mask:
        source_contour = np.load(args.source_contour).astype(np.int32)
        source_mask = cv2.imread(args.source_mask, cv2.IMREAD_GRAYSCALE)
        if source_contour is None or source_mask is None:
            emit_failure(report_path, "cannot read source contour/mask",
                         target_h, target_w)
            print("ERROR: Cannot read pre-computed source contour/mask.",
                  file=sys.stderr)
            sys.exit(1)
        if args.mirror:
            source_img = cv2.flip(source_img, 1)
        source_contour = source_contour.reshape(-1, 1, 2)
    else:
        if args.mirror:
            source_img = cv2.flip(source_img, 1)
        source_contour, source_mask = segment_source_flake(source_img)
        if source_contour is None:
            emit_failure(report_path, "cannot segment source flake",
                         target_h, target_w)
            print("ERROR: Cannot segment source flake.", file=sys.stderr)
            sys.exit(1)

    source_desc = compute_shape_descriptors(source_contour)
    if source_desc is None:
        emit_failure(report_path, "source flake too small",
                     target_h, target_w)
        print("ERROR: Source flake too small for shape descriptors.",
              file=sys.stderr)
        sys.exit(1)
    source_area = source_desc["area"]
    print(f"[B1] Source flake area={source_area:.0f}px")

    # SIFT (or precomputed warp via --warp / auto-lookup; #31)
    warp_path, warp_src = resolve_warp_path(
        args.warp, args.output_dir, args.source
    )
    if args.warp and warp_path is None:
        emit_failure(report_path,
                     f"--warp path not found: {args.warp}",
                     target_h, target_w)
        print(f"ERROR: --warp path not found: {args.warp}", file=sys.stderr)
        sys.exit(1)
    if warp_path is not None:
        try:
            warp_matrix = load_warp_matrix(warp_path)
        except (ValueError, OSError) as e:
            emit_failure(report_path, f"cannot load warp {warp_path}: {e}",
                         target_h, target_w)
            print(f"ERROR: Cannot load warp {warp_path}: {e}", file=sys.stderr)
            sys.exit(1)
        # warp_sift_bottom.npy is saved as target鈫抯ource (full_stack鈫抌ottom_part);
        # invert to bottom鈫抰arget so cv2.warpAffine treats it as a forward map.
        warp_matrix = cv2.invertAffineTransform(warp_matrix)
        print(f"[B1] Reusing precomputed SIFT warp from {warp_src}: {warp_path}")
    else:
        print("[B1] SIFT-aligning bottom to target...")
        warp_matrix, n_inliers = sift_align(bottom_img, target_img)
        if warp_matrix is None:
            emit_failure(report_path,
                         f"sift alignment failed ({n_inliers} matches)",
                         target_h, target_w)
            print(f"ERROR: SIFT alignment failed ({n_inliers}).",
                  file=sys.stderr)
            sys.exit(1)
        print(f"[B1] SIFT: {n_inliers} inliers")

    diff_gray = compute_diff_image(
        target_img, bottom_img, warp_matrix,
        source_bgr=source_img, source_mask=source_mask,
    )
    cv2.imwrite(os.path.join(args.output_dir, "02_diff_image.png"), diff_gray)

    n_clusters = args.n_clusters
    print(f"[B1] K-means clustering on diff (n={n_clusters})...")
    label_map, km = cluster_target_diff(diff_gray, n_clusters=n_clusters)
    candidate_ids = filter_clusters_diff(label_map, diff_gray, n_clusters)
    cv2.imwrite(os.path.join(args.output_dir, "02_cluster_map.png"),
                draw_cluster_map(label_map))
    print(f"[B1] Candidate clusters: {candidate_ids}")
    if len(candidate_ids) < 1:
        emit_failure(report_path, "too few candidate clusters",
                     target_h, target_w)
        print("ERROR: Too few candidate clusters.", file=sys.stderr)
        sys.exit(1)

    label_map, candidate_ids = split_clusters(label_map, candidate_ids,
                                               source_area)
    print(f"[B1] After split: {len(candidate_ids)} sub-clusters")

    candidates = enumerate_footprint_candidates(
        label_map, candidate_ids, source_desc, source_contour, source_area
    )
    if not candidates:
        emit_failure(report_path, "no viable footprint candidates",
                     target_h, target_w)
        print("ERROR: No viable footprint candidates found.", file=sys.stderr)
        sys.exit(1)

    print(f"[B1] {len(candidates)} candidates. "
          f"best_dist={candidates[0][0]:.4f}")
    cv2.imwrite(os.path.join(args.output_dir, "03_footprint_candidates.png"),
                draw_candidates(target_img, candidates, source_contour))

    rank = args.candidate_rank - 1
    if rank >= len(candidates):
        emit_failure(report_path,
                     f"requested rank {args.candidate_rank}, only "
                     f"{len(candidates)} available",
                     target_h, target_w)
        print(f"ERROR: rank {args.candidate_rank} unavailable.", file=sys.stderr)
        sys.exit(1)
    best_dist, best_ids, best_contour, best_mask = candidates[rank]
    km_area = int((best_mask > 0).sum())
    print(f"[B1] Selected #{args.candidate_rank}: clusters={best_ids} dist={best_dist:.4f}")

    fp_mask, attempt, fp_info, border_px = grabcut_with_quality_retry(
        target_img, best_mask, target_h, target_w,
        border_min_px=50, max_attempts=3
    )
    print(f"[B1] GrabCut attempts={attempt} info={fp_info} border={border_px}")

    if not fp_info["gate_pass"]:
        cv2.imwrite(os.path.join(args.output_dir, "footprint_mask.png"), fp_mask)
        diag = target_img.copy()
        contours_dbg, _ = cv2.findContours(fp_mask, cv2.RETR_EXTERNAL,
                                            cv2.CHAIN_APPROX_SIMPLE)
        if contours_dbg:
            cv2.drawContours(diag, [max(contours_dbg, key=cv2.contourArea)],
                             -1, (0, 0, 255), 2)
        cv2.putText(diag, f"FAILED B1 attempt={attempt} {fp_info}",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
        cv2.imwrite(os.path.join(args.output_dir, "04_footprint_grabcut.png"),
                    diag)
        emit_failure(report_path,
                     f"degenerate after {attempt} attempts: "
                     f"n_points={fp_info['n_points']} "
                     f"area_frac={fp_info['area_frac']:.3f}",
                     target_h, target_w, fp_info,
                     extra={"cluster_ids": list(best_ids),
                            "kmeans_area_px": km_area,
                            "border_px": int(border_px)})
        print(f"ERROR: B1 degenerate {attempt} attempts {fp_info}",
              file=sys.stderr)
        sys.exit(2)

    fp_contours, _ = cv2.findContours(fp_mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
    fp_contour = max(fp_contours, key=cv2.contourArea)
    fp_pts = fp_contour.reshape(-1, 2).astype(np.float64)
    fp_area = int((fp_mask > 0).sum())

    cv2.imwrite(os.path.join(args.output_dir, "footprint_mask.png"), fp_mask)
    np.save(os.path.join(args.output_dir, "footprint_contour.npy"), fp_pts)

    diag = target_img.copy()
    cv2.drawContours(diag, [fp_contour], -1, (0, 255, 0), 2)
    cv2.drawContours(diag, [best_contour], -1, (0, 255, 255), 1)
    cv2.putText(diag, f"B1 area={fp_area}px attempt={attempt} border={border_px}",
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    cv2.imwrite(os.path.join(args.output_dir, "04_footprint_grabcut.png"), diag)

    emit_success(report_path, fp_info, target_h, target_w, list(best_ids),
                 km_area, fp_area, best_dist, len(fp_pts),
                 "footprint_mask.png", "footprint_contour.npy",
                 args.target, args.pixel_size, attempt, border_px)
    print(f"[B1] OK: {len(fp_pts)} pts, {fp_area} px, attempt {attempt}")


if __name__ == "__main__":
    main()
