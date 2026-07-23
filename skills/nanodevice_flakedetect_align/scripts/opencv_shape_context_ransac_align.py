#!/usr/bin/env python
"""OpenCV-contrib ShapeContext + RANSAC alignment experiment.

OpenCV's ShapeContextDistanceExtractor exposes a whole-shape distance API
(`computeDistance`) but not the internal point correspondences.  Therefore this
diagnostic script builds tentative contour correspondences with the same local
shape-context descriptor matcher used by shape_context_ransac_align.py, samples
similarity transforms with RANSAC, and then uses OpenCV's official
ShapeContextDistanceExtractor to re-rank the candidate transforms.

Final geometric metrics are still computed on the complete original masks.
"""

import argparse
import json
import math
import os
import random
import sys

import cv2
import numpy as np

from shape_context_ransac_align import (
    as_points,
    contour_arc_sample,
    draw_matches_side_by_side,
    draw_overlay,
    draw_sample_points,
    evaluate,
    estimate_similarity,
    match_descriptors,
    score_metrics,
    shape_context,
    transform_points,
)


def cv_contour(points):
    return np.asarray(points, dtype=np.float32).reshape(-1, 1, 2)


def opencv_sc_distance(extractor, source_points, footprint_points, matrix):
    warped = transform_points(matrix, source_points)
    try:
        d = extractor.computeDistance(cv_contour(warped), cv_contour(footprint_points))
        return float(d) if np.isfinite(d) else 1e9
    except Exception:
        return 1e9


def ransac_candidates(src_pts, dst_pts, matches, extractor, iters, thresh,
                      scale_min, scale_max, max_candidates=80, seed=42):
    rng = random.Random(seed)
    src_m = np.asarray([src_pts[i] for i, _, _ in matches], dtype=np.float64)
    dst_m = np.asarray([dst_pts[j] for _, j, _ in matches], dtype=np.float64)
    idxs = list(range(len(matches)))
    candidates = []
    if len(matches) < 2:
        return candidates
    for _ in range(iters):
        sample = rng.sample(idxs, 2)
        m = estimate_similarity(src_m[sample], dst_m[sample])
        if m is None:
            continue
        scale = math.sqrt(m[0, 0] ** 2 + m[1, 0] ** 2)
        if scale < scale_min or scale > scale_max:
            continue
        pred = transform_points(m, src_m)
        err = np.linalg.norm(pred - dst_m, axis=1)
        inliers = np.where(err <= thresh)[0]
        if len(inliers) < 4:
            continue
        m_refit = estimate_similarity(src_m[inliers], dst_m[inliers])
        if m_refit is None:
            continue
        scale2 = math.sqrt(m_refit[0, 0] ** 2 + m_refit[1, 0] ** 2)
        if scale2 < scale_min or scale2 > scale_max:
            continue
        pred2 = transform_points(m_refit, src_m)
        err2 = np.linalg.norm(pred2 - dst_m, axis=1)
        inliers2 = np.where(err2 <= thresh)[0]
        if len(inliers2) < 4:
            continue
        mean_err = float(err2[inliers2].mean())
        sc_dist = opencv_sc_distance(extractor, src_pts, dst_pts, m_refit)
        candidates.append({
            "matrix": m_refit,
            "inliers": inliers2.tolist(),
            "num_inliers": int(len(inliers2)),
            "mean_inlier_error_px": mean_err,
            "opencv_shape_context_distance": sc_dist,
        })
    # Deduplicate approximately by rounded transform.
    seen = set()
    uniq = []
    for c in sorted(candidates, key=lambda x: (-x["num_inliers"], x["mean_inlier_error_px"], x["opencv_shape_context_distance"])):
        key = tuple(np.round(c["matrix"].reshape(-1), 3))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
        if len(uniq) >= max_candidates:
            break
    return uniq


def main():
    parser = argparse.ArgumentParser(description="OpenCV ShapeContext-distance reranked RANSAC alignment")
    parser.add_argument("--source-contour", required=True)
    parser.add_argument("--source-mask", required=True)
    parser.add_argument("--footprint-contour", required=True)
    parser.add_argument("--footprint-mask", required=True)
    parser.add_argument("--source-image", required=True)
    parser.add_argument("--target-image", required=True)
    parser.add_argument("--pixel-size", type=float, required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--samples", type=int, default=180)
    parser.add_argument("--max-matches", type=int, default=160)
    parser.add_argument("--ransac-iters", type=int, default=10000)
    parser.add_argument("--ransac-thresh", type=float, default=28.0)
    parser.add_argument("--match-ratio", type=float, default=1.0)
    parser.add_argument("--scale-min", type=float, default=0.70)
    parser.add_argument("--scale-max", type=float, default=1.35)
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    source_contour = np.load(args.source_contour)
    footprint_contour = np.load(args.footprint_contour)
    source_mask = cv2.imread(args.source_mask, cv2.IMREAD_GRAYSCALE)
    footprint_mask = cv2.imread(args.footprint_mask, cv2.IMREAD_GRAYSCALE)
    source_img = cv2.imread(args.source_image)
    target_img = cv2.imread(args.target_image)
    if source_mask is None or footprint_mask is None or source_img is None or target_img is None:
        print("ERROR: cannot read one or more inputs", file=sys.stderr)
        return 1
    if not hasattr(cv2, "createShapeContextDistanceExtractor"):
        print("ERROR: this OpenCV build has no createShapeContextDistanceExtractor", file=sys.stderr)
        return 2

    extractor = cv2.createShapeContextDistanceExtractor()
    src_pts = contour_arc_sample(source_contour, args.samples)
    fp_pts = contour_arc_sample(footprint_contour, args.samples)
    cv2.imwrite(os.path.join(args.output_dir, "01_source_opencv_sc_samples.png"),
                draw_sample_points(cv2.cvtColor(source_mask, cv2.COLOR_GRAY2BGR), src_pts, "source samples"))
    cv2.imwrite(os.path.join(args.output_dir, "01_footprint_opencv_sc_samples.png"),
                draw_sample_points(target_img, fp_pts, "footprint samples"))

    matches, _ = match_descriptors(
        shape_context(src_pts),
        shape_context(fp_pts),
        max_matches=args.max_matches,
        ratio=args.match_ratio,
    )
    source_mask_bgr = cv2.cvtColor(source_mask, cv2.COLOR_GRAY2BGR)
    footprint_mask_bgr = cv2.cvtColor(footprint_mask, cv2.COLOR_GRAY2BGR)

    cv2.imwrite(os.path.join(args.output_dir, "02_opencv_sc_raw_matches.png"),
                draw_matches_side_by_side(source_mask_bgr, footprint_mask_bgr,
                                          src_pts, fp_pts, matches,
                                          title=f"candidate correspondences on masks n={len(matches)}"))

    candidates = ransac_candidates(
        src_pts, fp_pts, matches, extractor,
        iters=args.ransac_iters,
        thresh=args.ransac_thresh,
        scale_min=args.scale_min,
        scale_max=args.scale_max,
    )
    if not candidates:
        summary = {"status": "failed", "reason": "no RANSAC candidates", "num_matches": len(matches)}
        with open(os.path.join(args.output_dir, "opencv_shape_context_ransac_summary.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        print(json.dumps(summary, indent=2))
        return 3

    ranked = []
    for c in candidates:
        m = c["matrix"]
        metrics, warped_mask = evaluate(m, source_mask, footprint_mask, source_contour, footprint_contour, args.pixel_size)
        full_score = score_metrics(metrics, m)
        # SC distance is only a tie-breaker/diagnostic; final choice still must
        # respect complete-mask overlap and outside fraction.
        combined = full_score + 0.002 * min(c["opencv_shape_context_distance"], 1000.0)
        ranked.append((combined, full_score, metrics, warped_mask, c))
    ranked.sort(key=lambda x: x[0])
    combined, full_score, metrics, warped_mask, best = ranked[0]
    m = best["matrix"]
    inlier_matches = [matches[i] for i in best["inliers"]]

    cv2.imwrite(os.path.join(args.output_dir, "03_opencv_sc_ransac_inliers.png"),
                draw_matches_side_by_side(source_mask_bgr, footprint_mask_bgr,
                                          src_pts, fp_pts, inlier_matches,
                                          inlier_set=set(range(len(inlier_matches))),
                                          title=f"RANSAC inliers on masks n={len(inlier_matches)}/{len(matches)}"))
    np.save(os.path.join(args.output_dir, "warp_opencv_shape_context_ransac.npy"), m)
    cv2.imwrite(os.path.join(args.output_dir, "04_opencv_sc_ransac_warped_mask.png"), warped_mask)
    cv2.imwrite(os.path.join(args.output_dir, "05_opencv_sc_ransac_overlay.png"),
                draw_overlay(target_img, source_contour, footprint_contour, m, metrics, "OpenCV SC + RANSAC"))

    scale = math.sqrt(m[0, 0] ** 2 + m[1, 0] ** 2)
    rot = math.degrees(math.atan2(m[1, 0], m[0, 0]))
    summary = {
        "status": "complete",
        "opencv_version": cv2.__version__,
        "has_createShapeContextDistanceExtractor": True,
        "samples": args.samples,
        "num_matches": len(matches),
        "num_candidates": len(candidates),
        "num_inliers": int(best["num_inliers"]),
        "inlier_fraction": float(best["num_inliers"] / max(len(matches), 1)),
        "ransac_thresh_px": args.ransac_thresh,
        "opencv_shape_context_distance": float(best["opencv_shape_context_distance"]),
        "mean_inlier_error_px": float(best["mean_inlier_error_px"]),
        "transform": {
            "matrix": m.tolist(),
            "scale": float(scale),
            "rotation_deg": float(rot),
        },
        "metrics": metrics,
        "score": float(full_score),
        "combined_score": float(combined),
        "top_candidates": [
            {
                "rank": i + 1,
                "combined_score": float(x[0]),
                "score": float(x[1]),
                "iou": float(x[2]["iou"]),
                "outside_fraction": float(x[2]["outside_fraction"]),
                "top_containment": float(x[2]["top_containment"]),
                "opencv_shape_context_distance": float(x[4]["opencv_shape_context_distance"]),
                "num_inliers": int(x[4]["num_inliers"]),
                "mean_inlier_error_px": float(x[4]["mean_inlier_error_px"]),
                "scale": float(math.sqrt(x[4]["matrix"][0, 0] ** 2 + x[4]["matrix"][1, 0] ** 2)),
                "rotation_deg": float(math.degrees(math.atan2(x[4]["matrix"][1, 0], x[4]["matrix"][0, 0]))),
            }
            for i, x in enumerate(ranked[:12])
        ],
    }
    with open(os.path.join(args.output_dir, "opencv_shape_context_ransac_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
