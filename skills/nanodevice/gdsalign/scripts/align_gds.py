#!/usr/bin/env python
"""Match detected image markers to GDS markers and compute similarity transform.

Exhaustive enumeration over 2-point correspondences finds the best matching,
then least-squares refinement over all inliers produces the final transform.

Usage:
    python align_gds.py --gds-markers gds_markers.json \
        --image-markers image_markers.json --output-dir output/gdsalign/
"""
import argparse
import itertools
import json
import math
import os
import sys

import numpy as np
import scipy.optimize


# ---------------------------------------------------------------------------
# Similarity transform helpers
# ---------------------------------------------------------------------------

def similarity_from_2_points(src, dst):
    """Compute similarity transform from 2 source->dest point pairs.

    src: shape (2,2), dst: shape (2,2)
    Returns: (2,3) affine matrix for rotation+scale (no reflection)
    """
    x1, y1 = src[0]
    x2, y2 = src[1]
    X1, Y1 = dst[0]
    X2, Y2 = dst[1]
    A = np.array([
        [x1, -y1, 1, 0],
        [y1,  x1, 0, 1],
        [x2, -y2, 1, 0],
        [y2,  x2, 0, 1],
    ])
    B = np.array([X1, Y1, X2, Y2])
    try:
        params = np.linalg.solve(A, B)
    except np.linalg.LinAlgError:
        return None
    a, b, tx, ty = params
    return np.array([[a, -b, tx], [b, a, ty]])


def similarity_reflect_from_2_points(src, dst):
    """Compute similarity + Y-reflection transform from 2 point pairs.

    For mapping image-um (Y-down) to GDS-um (Y-up), the transform is:
        x' =  a*x + b*y + tx
        y' =  b*x - a*y + ty
    Matrix form: [[a, b, tx], [b, -a, ty]]

    src: shape (2,2), dst: shape (2,2)
    Returns: (2,3) affine matrix encoding scale + rotation + Y-reflection
    """
    x1, y1 = src[0]
    x2, y2 = src[1]
    X1, Y1 = dst[0]
    X2, Y2 = dst[1]
    A = np.array([
        [x1,   y1, 1, 0],
        [-y1,  x1, 0, 1],
        [x2,   y2, 1, 0],
        [-y2,  x2, 0, 1],
    ])
    B = np.array([X1, Y1, X2, Y2])
    try:
        params = np.linalg.solve(A, B)
    except np.linalg.LinAlgError:
        return None
    a, b, tx, ty = params
    return np.array([[a, b, tx], [b, -a, ty]])


def apply_transform(M, pts):
    """Apply 2x3 similarity matrix to Nx2 points. Returns Nx2."""
    return (M[:2, :2] @ pts.T).T + M[:2, 2]


def refine_similarity(src_pts, dst_pts, M_init, reflected=False):
    """Refine via least-squares over [theta, s, tx, ty].

    Parameters
    ----------
    reflected : bool
        If True, use the reflected form [[a, b, tx], [b, -a, ty]].
    """
    a, b = M_init[0, 0], M_init[1, 0]
    theta0 = math.atan2(b, a)
    scale0 = math.sqrt(a**2 + b**2)
    tx0, ty0 = M_init[0, 2], M_init[1, 2]

    def residuals(params):
        th, s, tx, ty = params
        c, sn = math.cos(th), math.sin(th)
        if reflected:
            M = np.array([[s * c, s * sn, tx], [s * sn, -s * c, ty]])
        else:
            M = np.array([[s * c, -s * sn, tx], [s * sn, s * c, ty]])
        transformed = apply_transform(M, src_pts)
        return (transformed - dst_pts).ravel()

    result = scipy.optimize.least_squares(
        residuals, [theta0, scale0, tx0, ty0], method='lm'
    )
    th, s, tx, ty = result.x
    c, sn = math.cos(th), math.sin(th)
    if reflected:
        return np.array([[s * c, s * sn, tx], [s * sn, -s * c, ty]])
    else:
        return np.array([[s * c, -s * sn, tx], [s * sn, s * c, ty]])


# ---------------------------------------------------------------------------
# Exhaustive enumeration
# ---------------------------------------------------------------------------

def _score_transform(M, img_pts, gds_pts, inlier_thresh):
    """Score a candidate transform by inlier count and mean residual.

    Returns (inlier_count, mean_residual, correspondences).
    """
    transformed = apply_transform(M, img_pts)
    n_img = len(img_pts)

    assignments = []
    for ii in range(n_img):
        dists = np.sqrt(((gds_pts - transformed[ii]) ** 2).sum(axis=1))
        min_idx = int(np.argmin(dists))
        min_dist = dists[min_idx]
        assignments.append((min_dist, ii, min_idx))

    assignments.sort(key=lambda x: x[0])
    used_gds = set()
    inliers = []
    total_res = 0.0
    for dist, ii, gi in assignments:
        if dist < inlier_thresh and gi not in used_gds:
            inliers.append((ii, gi, dist))
            used_gds.add(gi)
            total_res += dist

    n_inliers = len(inliers)
    avg_res = total_res / n_inliers if n_inliers > 0 else float('inf')
    return n_inliers, avg_res, inliers


def find_best_correspondence(img_pts, gds_pts, inlier_thresh=1.0):
    """Exhaustive search over 2-point correspondences.

    For each pair of image points x each pair of GDS points x both orderings,
    compute reflected similarity transforms (image Y-down → GDS Y-up).

    Returns (best_M, best_correspondences, reflected) or (None, None, True).
    """
    n_img = len(img_pts)
    n_gds = len(gds_pts)

    best_inliers = -1
    best_residual = float('inf')
    best_M = None
    best_corr = None

    for i1, i2 in itertools.combinations(range(n_img), 2):
        src_pair = img_pts[[i1, i2]]
        for g1, g2 in itertools.combinations(range(n_gds), 2):
            for dst_pair in [gds_pts[[g1, g2]], gds_pts[[g2, g1]]]:
                # Image→GDS requires Y-reflection (Y-down → Y-up)
                M = similarity_reflect_from_2_points(src_pair, dst_pair)
                if M is None:
                    continue
                n_inl, avg_res, inliers = _score_transform(
                    M, img_pts, gds_pts, inlier_thresh)

                if (n_inl > best_inliers or
                        (n_inl == best_inliers and avg_res < best_residual)):
                    best_inliers = n_inl
                    best_residual = avg_res
                    best_M = M
                    best_corr = inliers

    return best_M, best_corr, True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Match markers and compute image->GDS similarity transform")
    parser.add_argument("--gds-markers", required=True,
                        help="Path to gds_markers.json")
    parser.add_argument("--image-markers", required=True,
                        help="Path to image_markers.json")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    args = parser.parse_args()

    # --- Load inputs ---
    for path, label in [(args.gds_markers, "gds_markers"),
                        (args.image_markers, "image_markers")]:
        if not os.path.exists(path):
            print(f"ERROR: {label} not found: {path}", file=sys.stderr)
            sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)

    with open(args.gds_markers) as f:
        gds_data = json.load(f)
    with open(args.image_markers) as f:
        img_data = json.load(f)

    # Extract GDS reference points (4 pair centroids, in um)
    gds_pts = np.array([p["center_um"] for p in gds_data["pairs"]])
    if len(gds_pts) < 2:
        print("ERROR: Need at least 2 GDS marker pairs", file=sys.stderr)
        sys.exit(1)

    # Extract detected image marker centroids (in um)
    detections = img_data["detections"]
    if len(detections) < 2:
        print("ERROR: Need at least 2 detected markers", file=sys.stderr)
        sys.exit(1)

    # If too many detections, limit to top-15 by score for tractability
    if len(detections) > 15:
        detections = sorted(detections, key=lambda d: d["score"], reverse=True)[:15]
        print(f"Limiting to top-15 detections by score")

    img_pts = np.array([d["center_um"] for d in detections])
    n_img = len(img_pts)
    n_gds = len(gds_pts)

    print(f"GDS markers: {n_gds}, Image detections: {n_img}")
    n_trials = (
        n_img * (n_img - 1) // 2  # C(n_img, 2)
        * n_gds * (n_gds - 1) // 2  # C(n_gds, 2)
        * 2  # both orderings
    )
    print(f"Exhaustive search: {n_trials} trials")

    # --- Exhaustive enumeration ---
    # Try progressively wider inlier thresholds until we get >= 3 inliers
    M_init = None
    correspondences = None
    is_reflected = False
    for inlier_thresh in [1.0, 2.0, 3.0, 5.0]:
        M_cand, corr_cand, refl_cand = find_best_correspondence(
            img_pts, gds_pts, inlier_thresh=inlier_thresh)
        if M_cand is not None and corr_cand is not None:
            M_init = M_cand
            correspondences = corr_cand
            is_reflected = refl_cand
            n_inliers = len(correspondences)
            mode_str = "reflected" if is_reflected else "rotation"
            print(f"Threshold {inlier_thresh:.1f} um: {n_inliers} inliers "
                  f"({mode_str})")
            if n_inliers >= 3:
                break
        else:
            print(f"Threshold {inlier_thresh:.1f} um: no valid correspondence")

    if M_init is None or correspondences is None:
        print("ERROR: No valid correspondence found", file=sys.stderr)
        sys.exit(2)

    n_inliers = len(correspondences)
    print(f"Best candidate: {n_inliers} inliers")

    if n_inliers < 2:
        print(f"ERROR: Only {n_inliers} inliers (need >= 2)", file=sys.stderr)
        sys.exit(2)

    # --- Refine with least-squares ---
    src_inlier = np.array([img_pts[ii] for ii, gi, _ in correspondences])
    dst_inlier = np.array([gds_pts[gi] for ii, gi, _ in correspondences])

    M_refined = refine_similarity(src_inlier, dst_inlier, M_init,
                                  reflected=is_reflected)

    # --- Compute final residuals ---
    transformed = apply_transform(M_refined, src_inlier)
    residuals = np.sqrt(((transformed - dst_inlier) ** 2).sum(axis=1))
    mean_res = float(residuals.mean())
    max_res = float(residuals.max())

    # Extract transform parameters
    a, b = M_refined[0, 0], M_refined[1, 0]
    theta = math.atan2(b, a)
    scale = math.sqrt(a**2 + b**2)
    tx, ty = float(M_refined[0, 2]), float(M_refined[1, 2])

    print(f"Refined: rot={math.degrees(theta):.1f} deg, scale={scale:.4f}, "
          f"tx={tx:.1f}, ty={ty:.1f}")
    print(f"Residuals: mean={mean_res:.3f} um, max={max_res:.3f} um")

    # --- Disambiguate 180° ambiguity ---
    # For marker patterns with 180° rotational symmetry (e.g., square grid),
    # both θ and θ+180° reflected similarities yield identical residuals with
    # permuted marker assignments.  Prefer M[0,0] > 0 (no horizontal flip),
    # matching standard upright microscopy.
    if is_reflected and M_refined[0, 0] < 0 and "grid_center_um" in gds_data:
        gds_center = np.array(gds_data["grid_center_um"])
        M_comp = M_refined.copy()
        M_comp[:2, :2] = -M_refined[:2, :2]
        M_comp[0, 2] = 2 * gds_center[0] - M_refined[0, 2]
        M_comp[1, 2] = 2 * gds_center[1] - M_refined[1, 2]

        n_comp, avg_comp, corr_comp = _score_transform(
            M_comp, img_pts, gds_pts, inlier_thresh=max_res * 3)

        if n_comp >= n_inliers:
            src_comp = np.array([img_pts[ii] for ii, gi, _ in corr_comp])
            dst_comp = np.array([gds_pts[gi] for ii, gi, _ in corr_comp])
            M_comp = refine_similarity(
                src_comp, dst_comp, M_comp, reflected=is_reflected)
            trans_comp = apply_transform(M_comp, src_comp)
            res_comp = np.sqrt(((trans_comp - dst_comp) ** 2).sum(axis=1))
            mean_comp = float(res_comp.mean())
            max_comp = float(res_comp.max())

            if mean_comp <= mean_res * 2.0:
                print(f"Resolved 180deg ambiguity: using non-flipped companion "
                      f"(residual {mean_comp:.3f} vs {mean_res:.3f} um)")
                M_refined = M_comp
                correspondences = corr_comp
                src_inlier = src_comp
                dst_inlier = dst_comp
                residuals = res_comp
                mean_res = mean_comp
                max_res = max_comp
                # Re-extract transform parameters
                a, b = M_refined[0, 0], M_refined[1, 0]
                theta = math.atan2(b, a)
                scale = math.sqrt(a**2 + b**2)
                tx, ty = float(M_refined[0, 2]), float(M_refined[1, 2])
                print(f"Companion: rot={math.degrees(theta):.1f} deg, "
                      f"scale={scale:.4f}, tx={tx:.1f}, ty={ty:.1f}")
                print(f"Residuals: mean={mean_res:.3f} um, max={max_res:.3f} um")

    # --- Check quality ---
    if mean_res > 5.0:
        print(f"ERROR: Mean residual {mean_res:.3f} um > 5.0 um threshold",
              file=sys.stderr)
        sys.exit(2)

    # --- Save outputs ---
    warp_path = os.path.join(args.output_dir, "gds_warp.npy")
    np.save(warp_path, M_refined)
    print(f"Saved {warp_path}")

    report = {
        "status": "complete",
        "transform": {
            "rotation_deg": float(math.degrees(theta)),
            "scale": float(scale),
            "translation_um": [tx, ty],
            "reflected": is_reflected,
        },
        "quality": {
            "inliers": n_inliers,
            "total_detected": len(img_data["detections"]),
            "mean_residual_um": mean_res,
            "max_residual_um": max_res,
        },
        "warp_file": "gds_warp.npy",
    }

    report_path = os.path.join(args.output_dir, "gds_alignment_report.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"Saved {report_path}")

    sys.exit(0)


if __name__ == "__main__":
    main()
