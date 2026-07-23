#!/usr/bin/env python
"""Fixed/floating-rotation scale/translation alignment experiment.

Use a rotation supplied by ShapeContext/edge matching, then keep that angle
fixed by default and optimize only uniform scale plus translation using the
full-mask geometric score from shape_context_ransac_align.py.  Optionally allow
a narrow rotation refinement window around the supplied angle.
"""

import argparse
import json
import math
import os
import sys

import cv2
import numpy as np
from scipy.optimize import differential_evolution, minimize

sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "..", "nanodevice_flakedetect", "scripts"),
)
from core import desaturate, mask_centroid, warp_contour  # noqa: E402

from shape_context_ransac_align import as_points, evaluate, score_metrics  # noqa: E402


def make_similarity_from_centroids(src_center, dst_center, rot_deg, scale, dx, dy):
    theta = math.radians(rot_deg)
    c, s = math.cos(theta), math.sin(theta)
    a = np.array([[scale * c, -scale * s], [scale * s, scale * c]], dtype=np.float64)
    target_center = np.asarray(dst_center, dtype=np.float64) + np.array([dx, dy], dtype=np.float64)
    t = target_center - a @ np.asarray(src_center, dtype=np.float64)
    return np.array([[a[0, 0], a[0, 1], t[0]], [a[1, 0], a[1, 1], t[1]]], dtype=np.float64)


def draw_overlay(target, source_contour, footprint_contour, matrix, metrics, title):
    img = desaturate(target, 0.45)
    cv2.drawContours(img, [as_points(footprint_contour).reshape(-1, 1, 2).astype(np.int32)], -1, (0, 255, 0), 2)
    warped = warp_contour(source_contour, matrix).astype(np.int32)
    cv2.drawContours(img, [warped], -1, (0, 255, 255), 2)
    scale = math.sqrt(matrix[0, 0] ** 2 + matrix[1, 0] ** 2)
    rot = math.degrees(math.atan2(matrix[1, 0], matrix[0, 0]))
    text = (
        f"{title} IoU={metrics['iou']:.3f} outside={metrics['outside_fraction']:.3f} "
        f"cont={metrics['top_containment']:.3f} s={scale:.3f} rot={rot:.1f}"
    )
    cv2.putText(img, text, (18, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (0, 0, 0), 4)
    cv2.putText(img, text, (18, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2)
    return img


def draw_mask_overlap(target, source_mask, footprint_mask, matrix, metrics):
    h, w = footprint_mask.shape[:2]
    warped_mask = cv2.warpAffine(source_mask, matrix, (w, h), flags=cv2.INTER_NEAREST)
    inter = cv2.bitwise_and(warped_mask, footprint_mask)
    fp_only = cv2.bitwise_and(footprint_mask, cv2.bitwise_not(warped_mask))
    warped_only = cv2.bitwise_and(warped_mask, cv2.bitwise_not(footprint_mask))
    bg = desaturate(target, 0.4)
    bg[inter > 0] = (bg[inter > 0].astype(float) * 0.45 + np.array([0, 220, 0]) * 0.55).astype(np.uint8)
    bg[fp_only > 0] = (bg[fp_only > 0].astype(float) * 0.45 + np.array([0, 0, 220]) * 0.55).astype(np.uint8)
    bg[warped_only > 0] = (bg[warped_only > 0].astype(float) * 0.45 + np.array([220, 0, 0]) * 0.55).astype(np.uint8)
    text = f"green=overlap red=footprint-only blue=source-only IoU={metrics['iou']:.3f} outside={metrics['outside_fraction']:.3f}"
    cv2.putText(bg, text, (18, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (0, 0, 0), 4)
    cv2.putText(bg, text, (18, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (255, 255, 255), 2)
    return bg


def draw_scale_sweep(target, source_contour, footprint_contour, rows, out_path):
    panels = []
    for r in rows[:12]:
        img = desaturate(target, 0.35)
        cv2.drawContours(img, [as_points(footprint_contour).reshape(-1, 1, 2).astype(np.int32)], -1, (0, 255, 0), 2)
        warped = warp_contour(source_contour, np.asarray(r["matrix"], dtype=np.float64)).astype(np.int32)
        cv2.drawContours(img, [warped], -1, (0, 255, 255), 2)
        txt = f"rank {r['rank']} score={r['score']:.2f} IoU={r['iou']:.3f} out={r['outside_fraction']:.3f} s={r['scale']:.3f}"
        cv2.putText(img, txt, (16, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 0, 0), 4)
        cv2.putText(img, txt, (16, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 2)
        panels.append(cv2.resize(img, (520, int(img.shape[0] * 520 / img.shape[1]))))
    if not panels:
        return
    h = max(p.shape[0] for p in panels)
    padded = []
    for p in panels:
        if p.shape[0] < h:
            p = np.vstack([p, np.zeros((h - p.shape[0], p.shape[1], 3), dtype=np.uint8)])
        padded.append(p)
    grid_rows = []
    for i in range(0, len(padded), 3):
        row = padded[i:i + 3]
        while len(row) < 3:
            row.append(np.zeros_like(padded[0]))
        grid_rows.append(np.hstack(row))
    cv2.imwrite(out_path, np.vstack(grid_rows))


def read_rotation_from_summary(path):
    if not path:
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return float(data["transform"]["rotation_deg"])


def main():
    parser = argparse.ArgumentParser(description="Fixed/floating-rotation scale/translation full-mask align")
    parser.add_argument("--source-contour", required=True)
    parser.add_argument("--source-mask", required=True)
    parser.add_argument("--footprint-contour", required=True)
    parser.add_argument("--footprint-mask", required=True)
    parser.add_argument("--target-image", required=True)
    parser.add_argument("--pixel-size", type=float, required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--rot-deg", type=float)
    parser.add_argument("--rot-summary-json")
    parser.add_argument("--rot-window", type=float, default=0.0,
                        help="Allow +/- this many degrees around rot-deg. Default 0 keeps rotation fixed.")
    parser.add_argument("--scale-min", type=float, default=0.86)
    parser.add_argument("--scale-max", type=float, default=1.12)
    parser.add_argument("--dx-range", type=float, default=520.0)
    parser.add_argument("--dy-range", type=float, default=520.0)
    parser.add_argument("--maxiter", type=int, default=90)
    parser.add_argument("--popsize", type=int, default=12)
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    rot_deg = args.rot_deg
    if rot_deg is None:
        rot_deg = read_rotation_from_summary(args.rot_summary_json)
    if rot_deg is None:
        raise ValueError("provide --rot-deg or --rot-summary-json")

    source_contour = np.load(args.source_contour)
    footprint_contour = np.load(args.footprint_contour)
    source_mask = cv2.imread(args.source_mask, cv2.IMREAD_GRAYSCALE)
    footprint_mask = cv2.imread(args.footprint_mask, cv2.IMREAD_GRAYSCALE)
    target = cv2.imread(args.target_image)
    if source_mask is None or footprint_mask is None or target is None:
        print("ERROR: cannot read inputs", file=sys.stderr)
        return 1

    src_center = mask_centroid(source_mask)
    dst_center = mask_centroid(footprint_mask)
    if src_center is None or dst_center is None:
        raise ValueError("empty source or footprint mask")

    cache = {}
    rows = []

    def unpack_params(params):
        if args.rot_window > 0:
            rot, scale, dx, dy = map(float, params)
        else:
            scale, dx, dy = map(float, params)
            rot = float(rot_deg)
        return rot, scale, dx, dy

    def eval_params(params):
        rot, scale, dx, dy = unpack_params(params)
        key = (round(rot, 4), round(scale, 5), round(dx, 2), round(dy, 2))
        if key in cache:
            return cache[key][0]
        m = make_similarity_from_centroids(src_center, dst_center, rot, scale, dx, dy)
        metrics, _ = evaluate(m, source_mask, footprint_mask, source_contour, footprint_contour, args.pixel_size)
        score = score_metrics(metrics, m)
        cache[key] = (score, metrics, m)
        return score

    bounds = []
    if args.rot_window > 0:
        bounds.append((rot_deg - args.rot_window, rot_deg + args.rot_window))
    bounds.extend([
        (args.scale_min, args.scale_max),
        (-args.dx_range, args.dx_range),
        (-args.dy_range, args.dy_range),
    ])
    de = differential_evolution(
        eval_params,
        bounds,
        maxiter=args.maxiter,
        popsize=args.popsize,
        seed=42,
        polish=False,
        updating="immediate",
        workers=1,
        tol=1e-4,
    )
    local = minimize(
        eval_params,
        de.x,
        method="L-BFGS-B",
        bounds=bounds,
        options={"maxiter": 240, "ftol": 1e-7},
    )
    best_params = local.x if local.fun <= de.fun else de.x
    best_score = eval_params(best_params)
    best_rot, best_scale, best_dx, best_dy = unpack_params(best_params)
    best_key = (round(best_rot, 4), round(best_scale, 5), round(best_dx, 2), round(best_dy, 2))
    best_metrics = cache[best_key][1]
    best_m = cache[best_key][2]

    # Diagnostic rank list: preserve best cached candidates for visual review.
    for score, metrics, m in cache.values():
        rows.append({
            "score": float(score),
            "iou": float(metrics["iou"]),
            "outside_fraction": float(metrics["outside_fraction"]),
            "top_containment": float(metrics["top_containment"]),
            "scale": float(math.sqrt(m[0, 0] ** 2 + m[1, 0] ** 2)),
            "rotation_deg": float(math.degrees(math.atan2(m[1, 0], m[0, 0]))),
            "matrix": m.tolist(),
        })
    rows.sort(key=lambda r: r["score"])
    for i, r in enumerate(rows):
        r["rank"] = i + 1

    tag = "rotrefine" if args.rot_window > 0 else "fixedrot"
    np.save(os.path.join(args.output_dir, f"warp_{tag}_scale_translate.npy"), best_m)
    cv2.imwrite(os.path.join(args.output_dir, f"06_{tag}_scale_translate_overlay.png"),
                draw_overlay(target, source_contour, footprint_contour, best_m, best_metrics,
                             f"{tag} scale+translation"))
    cv2.imwrite(os.path.join(args.output_dir, f"07_{tag}_scale_translate_mask_overlap.png"),
                draw_mask_overlap(target, source_mask, footprint_mask, best_m, best_metrics))
    draw_scale_sweep(
        target,
        source_contour,
        footprint_contour,
        rows,
        os.path.join(args.output_dir, f"08_{tag}_scale_translate_top_candidates.png"),
    )

    summary = {
        "status": "complete",
        "rotation_hint_deg": float(rot_deg),
        "rotation_window_deg": float(args.rot_window),
        "optimized_params": {
            "rot_deg": float(best_rot),
            "scale": float(best_scale),
            "dx_px_from_centroid": float(best_dx),
            "dy_px_from_centroid": float(best_dy),
        },
        "transform": {
            "matrix": best_m.tolist(),
            "scale": float(math.sqrt(best_m[0, 0] ** 2 + best_m[1, 0] ** 2)),
            "rotation_deg": float(math.degrees(math.atan2(best_m[1, 0], best_m[0, 0]))),
        },
        "metrics": best_metrics,
        "score": float(best_score),
        "top_candidates": rows[:20],
        "optimizer": {
            "de_fun": float(de.fun),
            "local_fun": float(local.fun),
            "num_evaluations": int(len(cache)),
            "scale_bounds": [float(args.scale_min), float(args.scale_max)],
            "dx_bounds": [-float(args.dx_range), float(args.dx_range)],
            "dy_bounds": [-float(args.dy_range), float(args.dy_range)],
        },
    }
    with open(os.path.join(args.output_dir, f"{tag}_scale_translate_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
