#!/usr/bin/env python
"""ShapeContext + RANSAC contour alignment experiment.

Diagnostic-only fallback: use shape-context descriptors on source/footprint
contour samples to propose point correspondences, then RANSAC a physical
similarity transform. Final scoring is on the complete original masks.
"""

import argparse
import json
import math
import os
import random
import sys

import cv2
import numpy as np

sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "..", "nanodevice_flakedetect", "scripts"),
)
from core import desaturate, warp_contour  # noqa: E402


def as_points(contour):
    return np.asarray(contour, dtype=np.float64).reshape(-1, 2)


def contour_arc_sample(contour, n):
    pts = as_points(contour)
    if len(pts) < 2:
        raise ValueError("contour needs at least 2 points")
    closed = np.vstack([pts, pts[0]])
    seg = np.linalg.norm(np.diff(closed, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = cum[-1]
    targets = np.linspace(0, total, n, endpoint=False)
    out = []
    j = 0
    for t in targets:
        while j + 1 < len(cum) and cum[j + 1] < t:
            j += 1
        denom = max(cum[j + 1] - cum[j], 1e-9)
        a = (t - cum[j]) / denom
        out.append(closed[j] * (1 - a) + closed[j + 1] * a)
    return np.asarray(out, dtype=np.float64)


def shape_context(points, nbins_r=5, nbins_theta=12, r_inner=0.125, r_outer=2.0):
    pts = np.asarray(points, dtype=np.float64)
    n = len(pts)
    diff = pts[None, :, :] - pts[:, None, :]
    r = np.linalg.norm(diff, axis=2)
    nonzero = r[r > 1e-9]
    mean_dist = max(float(nonzero.mean()) if nonzero.size else 1.0, 1e-9)
    r_norm = r / mean_dist
    theta = (np.arctan2(diff[:, :, 1], diff[:, :, 0]) + 2 * np.pi) % (2 * np.pi)
    r_edges = np.logspace(np.log10(r_inner), np.log10(r_outer), nbins_r + 1)
    theta_edges = np.linspace(0, 2 * np.pi, nbins_theta + 1)
    desc = np.zeros((n, nbins_r * nbins_theta), dtype=np.float64)
    for i in range(n):
        valid = np.arange(n) != i
        rb = np.searchsorted(r_edges, r_norm[i, valid], side="right") - 1
        tb = np.searchsorted(theta_edges, theta[i, valid], side="right") - 1
        keep = (rb >= 0) & (rb < nbins_r) & (tb >= 0) & (tb < nbins_theta)
        idx = rb[keep] * nbins_theta + np.clip(tb[keep], 0, nbins_theta - 1)
        for k in idx:
            desc[i, k] += 1.0
    desc /= np.maximum(desc.sum(axis=1, keepdims=True), 1e-9)
    return desc


def chi2_cost(a, b):
    # returns len(a) x len(b)
    aa = a[:, None, :]
    bb = b[None, :, :]
    return 0.5 * np.sum(((aa - bb) ** 2) / np.maximum(aa + bb, 1e-9), axis=2)


def match_descriptors(src_desc, dst_desc, max_matches=120, ratio=1.0):
    cost = chi2_cost(src_desc, dst_desc)
    matches = []
    for i in range(cost.shape[0]):
        order = np.argsort(cost[i])
        if len(order) < 2:
            continue
        j0, j1 = int(order[0]), int(order[1])
        if ratio >= 1.0 or cost[i, j0] <= ratio * cost[i, j1]:
            matches.append((i, j0, float(cost[i, j0])))
    # keep unique destination, best cost first
    matches.sort(key=lambda x: x[2])
    used_dst = set()
    uniq = []
    for m in matches:
        if m[1] in used_dst:
            continue
        used_dst.add(m[1])
        uniq.append(m)
        if len(uniq) >= max_matches:
            break
    return uniq, cost


def estimate_similarity(src, dst):
    src = np.asarray(src, dtype=np.float64)
    dst = np.asarray(dst, dtype=np.float64)
    if len(src) < 2:
        return None
    cs = src.mean(axis=0)
    cd = dst.mean(axis=0)
    xs = src - cs
    xd = dst - cd
    norm = np.sum(xs ** 2)
    if norm < 1e-9:
        return None
    h = xs.T @ xd
    u, _, vt = np.linalg.svd(h)
    r = vt.T @ u.T
    if np.linalg.det(r) < 0:
        vt[-1, :] *= -1
        r = vt.T @ u.T
    scale = float(np.trace((xs @ r.T).T @ xd) / norm)
    if not np.isfinite(scale) or scale <= 0:
        return None
    t = cd - scale * r @ cs
    m = np.array([[scale * r[0, 0], scale * r[0, 1], t[0]],
                  [scale * r[1, 0], scale * r[1, 1], t[1]]], dtype=np.float64)
    return m


def transform_points(m, pts):
    pts = np.asarray(pts, dtype=np.float64)
    return pts @ m[:, :2].T + m[:, 2]


def ransac_similarity(src_pts, dst_pts, matches, iters=3000, thresh=18.0,
                      scale_min=0.75, scale_max=1.25, seed=42):
    rng = random.Random(seed)
    if len(matches) < 2:
        return None, []
    src_m = np.asarray([src_pts[i] for i, _, _ in matches], dtype=np.float64)
    dst_m = np.asarray([dst_pts[j] for _, j, _ in matches], dtype=np.float64)
    best = None
    best_inliers = []
    idxs = list(range(len(matches)))
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
        if len(inliers) < max(4, len(best_inliers)):
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
        mean_err = float(err2[inliers2].mean()) if len(inliers2) else 1e9
        cand = (len(inliers2), -mean_err, m_refit)
        if best is None or cand[:2] > best[:2]:
            best = cand
            best_inliers = inliers2.tolist()
    if best is None:
        return None, []
    return best[2], best_inliers


def evaluate(m, source_mask, footprint_mask, source_contour, footprint_contour, pixel_size):
    h, w = footprint_mask.shape[:2]
    warped_mask = cv2.warpAffine(source_mask, m, (w, h), flags=cv2.INTER_NEAREST)
    inter = cv2.bitwise_and(warped_mask, footprint_mask)
    union = cv2.bitwise_or(warped_mask, footprint_mask)
    outside = cv2.bitwise_and(warped_mask, cv2.bitwise_not(footprint_mask))
    inter_area = int((inter > 0).sum())
    union_area = max(int((union > 0).sum()), 1)
    warped_area = max(int((warped_mask > 0).sum()), 1)
    fp_area = max(int((footprint_mask > 0).sum()), 1)
    try:
        from scipy.spatial import KDTree
        fp_pts = as_points(footprint_contour)
        if len(fp_pts) > 1200:
            fp_pts = fp_pts[np.linspace(0, len(fp_pts) - 1, 1200, dtype=int)]
        warped = as_points(warp_contour(source_contour, m))
        dists, _ = KDTree(fp_pts).query(warped)
        fwd = float(np.mean(dists) * pixel_size)
    except Exception:
        fwd = None
    return {
        "iou": float(inter_area / union_area),
        "top_containment": float(inter_area / warped_area),
        "fp_containment": float(inter_area / fp_area),
        "outside_fraction": float((outside > 0).sum() / warped_area),
        "fwd_chamfer_mean_um": fwd,
        "warped_area_px": warped_area,
        "footprint_area_px": fp_area,
    }, warped_mask


def score_metrics(metrics, m):
    scale = math.sqrt(m[0, 0] ** 2 + m[1, 0] ** 2)
    scale_penalty = abs(math.log(max(scale, 1e-9)))
    fwd = metrics.get("fwd_chamfer_mean_um")
    chamfer = 0.0 if fwd is None else min(float(fwd) / 20.0, 2.0)
    return float(
        4.0 * (1.0 - metrics["iou"]) +
        5.0 * metrics["outside_fraction"] +
        2.0 * (1.0 - metrics["top_containment"]) +
        0.8 * chamfer +
        1.5 * scale_penalty
    )


def draw_sample_points(image, pts, title):
    img = desaturate(image, 0.4) if image.ndim == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    for k, p in enumerate(pts):
        cv2.circle(img, tuple(np.round(p).astype(int)), 3, (0, 255, 255), -1)
        if k % 20 == 0:
            cv2.putText(img, str(k), tuple(np.round(p).astype(int)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
    cv2.putText(img, title, (18, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 0, 0), 4)
    cv2.putText(img, title, (18, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)
    return img


def draw_matches_side_by_side(src_img, dst_img, src_pts, dst_pts, matches, inlier_set=None, title="matches", max_draw=80):
    s = desaturate(src_img, 0.45) if src_img.ndim == 3 else cv2.cvtColor(src_img, cv2.COLOR_GRAY2BGR)
    d = desaturate(dst_img, 0.45) if dst_img.ndim == 3 else cv2.cvtColor(dst_img, cv2.COLOR_GRAY2BGR)
    h = max(s.shape[0], d.shape[0])
    if s.shape[0] < h:
        s = np.vstack([s, np.zeros((h - s.shape[0], s.shape[1], 3), dtype=np.uint8)])
    if d.shape[0] < h:
        d = np.vstack([d, np.zeros((h - d.shape[0], d.shape[1], 3), dtype=np.uint8)])
    canvas = np.hstack([s, d])
    xoff = s.shape[1]
    inlier_set = set(inlier_set or [])
    for idx, (i, j, c) in enumerate(matches[:max_draw]):
        p = tuple(np.round(src_pts[i]).astype(int))
        q = tuple(np.round(dst_pts[j] + np.array([xoff, 0])).astype(int))
        color = (0, 255, 0) if idx in inlier_set else (0, 200, 255)
        cv2.line(canvas, p, q, color, 1)
        cv2.circle(canvas, p, 3, color, -1)
        cv2.circle(canvas, q, 3, color, -1)
    cv2.putText(canvas, title, (18, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 0, 0), 4)
    cv2.putText(canvas, title, (18, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)
    return canvas


def draw_overlay(target, source_contour, footprint_contour, m, metrics, title):
    img = desaturate(target, 0.45)
    cv2.drawContours(img, [as_points(footprint_contour).reshape(-1, 1, 2).astype(np.int32)], -1, (0, 255, 0), 2)
    warped = warp_contour(source_contour, m).astype(np.int32)
    cv2.drawContours(img, [warped], -1, (0, 255, 255), 2)
    scale = math.sqrt(m[0, 0] ** 2 + m[1, 0] ** 2)
    rot = math.degrees(math.atan2(m[1, 0], m[0, 0]))
    txt = f"{title} IoU={metrics['iou']:.3f} outside={metrics['outside_fraction']:.3f} cont={metrics['top_containment']:.3f} s={scale:.3f} rot={rot:.1f}"
    cv2.putText(img, txt, (18, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (0, 0, 0), 4)
    cv2.putText(img, txt, (18, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2)
    return img


def main():
    parser = argparse.ArgumentParser(description="ShapeContext + RANSAC similarity alignment")
    parser.add_argument("--source-contour", required=True)
    parser.add_argument("--source-mask", required=True)
    parser.add_argument("--footprint-contour", required=True)
    parser.add_argument("--footprint-mask", required=True)
    parser.add_argument("--source-image", required=True)
    parser.add_argument("--target-image", required=True)
    parser.add_argument("--pixel-size", type=float, required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--samples", type=int, default=180)
    parser.add_argument("--max-matches", type=int, default=140)
    parser.add_argument("--ransac-iters", type=int, default=5000)
    parser.add_argument("--ransac-thresh", type=float, default=22.0)
    parser.add_argument("--match-ratio", type=float, default=1.0,
                        help="Lowe-style ratio. 1.0 disables ratio filtering and keeps nearest-neighbor matches.")
    parser.add_argument("--scale-min", type=float, default=0.75)
    parser.add_argument("--scale-max", type=float, default=1.25)
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

    src_pts = contour_arc_sample(source_contour, args.samples)
    fp_pts = contour_arc_sample(footprint_contour, args.samples)
    cv2.imwrite(os.path.join(args.output_dir, "01_source_shape_context_samples.png"),
                draw_sample_points(cv2.cvtColor(source_mask, cv2.COLOR_GRAY2BGR), src_pts, "source contour samples"))
    cv2.imwrite(os.path.join(args.output_dir, "01_footprint_shape_context_samples.png"),
                draw_sample_points(target_img, fp_pts, "footprint contour samples"))

    src_desc = shape_context(src_pts)
    fp_desc = shape_context(fp_pts)
    matches, cost = match_descriptors(src_desc, fp_desc, max_matches=args.max_matches, ratio=args.match_ratio)
    source_mask_bgr = cv2.cvtColor(source_mask, cv2.COLOR_GRAY2BGR)
    footprint_mask_bgr = cv2.cvtColor(footprint_mask, cv2.COLOR_GRAY2BGR)

    cv2.imwrite(os.path.join(args.output_dir, "02_shape_context_raw_matches.png"),
                draw_matches_side_by_side(source_mask_bgr, footprint_mask_bgr, src_pts, fp_pts, matches, title=f"raw ShapeContext matches on masks n={len(matches)}"))

    m, inliers = ransac_similarity(
        src_pts, fp_pts, matches,
        iters=args.ransac_iters, thresh=args.ransac_thresh,
        scale_min=args.scale_min, scale_max=args.scale_max,
    )
    if m is None:
        summary = {"status": "failed", "reason": "RANSAC found no similarity", "num_matches": len(matches)}
        with open(os.path.join(args.output_dir, "shape_context_ransac_summary.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        print(json.dumps(summary, indent=2))
        return 2

    inlier_matches = [matches[i] for i in inliers]
    cv2.imwrite(os.path.join(args.output_dir, "03_shape_context_ransac_inliers.png"),
                draw_matches_side_by_side(source_mask_bgr, footprint_mask_bgr, src_pts, fp_pts, inlier_matches, inlier_set=set(range(len(inlier_matches))), title=f"RANSAC inliers on masks n={len(inliers)}/{len(matches)}"))

    metrics, warped_mask = evaluate(m, source_mask, footprint_mask, source_contour, footprint_contour, args.pixel_size)
    score = score_metrics(metrics, m)
    np.save(os.path.join(args.output_dir, "warp_shape_context_ransac.npy"), m)
    cv2.imwrite(os.path.join(args.output_dir, "04_shape_context_ransac_warped_mask.png"), warped_mask)
    cv2.imwrite(os.path.join(args.output_dir, "05_shape_context_ransac_overlay.png"),
                draw_overlay(target_img, source_contour, footprint_contour, m, metrics, "ShapeContext+RANSAC"))
    scale = math.sqrt(m[0, 0] ** 2 + m[1, 0] ** 2)
    rot = math.degrees(math.atan2(m[1, 0], m[0, 0]))
    summary = {
        "status": "complete",
        "samples": args.samples,
        "num_matches": len(matches),
        "num_inliers": len(inliers),
        "inlier_fraction": float(len(inliers) / max(len(matches), 1)),
        "ransac_thresh_px": args.ransac_thresh,
        "transform": {
            "matrix": m.tolist(),
            "scale": float(scale),
            "rotation_deg": float(rot),
        },
        "metrics": metrics,
        "score": float(score),
        "top_raw_matches": [
            {"source_idx": int(i), "footprint_idx": int(j), "cost": float(c)}
            for i, j, c in matches[:30]
        ],
        "inlier_matches": [
            {"source_idx": int(matches[k][0]), "footprint_idx": int(matches[k][1]), "cost": float(matches[k][2])}
            for k in inliers[:80]
        ],
    }
    with open(os.path.join(args.output_dir, "shape_context_ransac_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
