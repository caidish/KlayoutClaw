#!/usr/bin/env python
"""Fine alignment optimization with agent-selected rotation.

Full-resolution DE + L-BFGS-B + multi-restart pipeline. Takes the
agent's rotation hint from the sweep step and optimizes within narrow
bounds to find the precise alignment.

Usage:
    conda run -n base python refine.py \
        --source-contour <.npy> --source-mask <.png> \
        --footprint-contour <.npy> --footprint-mask <.png> \
        --target-image <image> --rot-hint <degrees> \
        [--scale-hint <value>] --pixel-size <um/px> --output-dir <path>
"""

import argparse
import json
import math
import os
import sys
import time

import cv2
import numpy as np
from scipy.optimize import differential_evolution, minimize

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from core import (ChamferAligner, make_warp, warp_contour, desaturate,
                  mask_centroid)


def draw_overlay_raw(target_img, source_contour, footprint_contour,
                     params, aligner, pixel_size):
    """Draw warped contour (yellow) + footprint contour (green) on raw image."""
    rot_deg, scale, dx, dy = params
    M = make_warp(aligner.src_cx, aligner.src_cy,
                  aligner.fp_cx + dx, aligner.fp_cy + dy,
                  math.radians(rot_deg), scale)
    warped = warp_contour(source_contour, M)

    img = target_img.copy()
    cv2.drawContours(img, [footprint_contour.reshape(-1, 1, 2).astype(np.int32)],
                     -1, (0, 255, 0), 2)
    cv2.drawContours(img, [warped.astype(np.int32)], -1, (0, 255, 255), 2)

    metrics = aligner.evaluate(params, pixel_size)
    text = (f"rot={rot_deg:.1f} s={scale:.3f} "
            f"fwd={metrics['fwd_chamfer_mean_um']:.2f}um "
            f"IoU={metrics['iou']:.3f}")
    cv2.putText(img, text, (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                (0, 255, 255), 2)
    return img, metrics


def draw_mask_overlap(target_img, source_mask, footprint_mask,
                      params, aligner, metrics):
    """Draw mask overlap: green=overlap, red=footprint-only, blue=warped-only."""
    rot_deg, scale, dx, dy = params
    M = make_warp(aligner.src_cx, aligner.src_cy,
                  aligner.fp_cx + dx, aligner.fp_cy + dy,
                  math.radians(rot_deg), scale)

    h, w = footprint_mask.shape[:2]
    warped_mask = cv2.warpAffine(source_mask, M, (w, h), flags=cv2.INTER_NEAREST)

    inter = cv2.bitwise_and(warped_mask, footprint_mask)
    fp_only = cv2.bitwise_and(footprint_mask, cv2.bitwise_not(warped_mask))
    warped_only = cv2.bitwise_and(warped_mask, cv2.bitwise_not(footprint_mask))

    bg = desaturate(target_img, 0.4)
    bg[inter > 0] = (bg[inter > 0].astype(float) * 0.5 +
                      np.array([0, 200, 0]) * 0.5).astype(np.uint8)
    bg[fp_only > 0] = (bg[fp_only > 0].astype(float) * 0.5 +
                        np.array([0, 0, 200]) * 0.5).astype(np.uint8)
    bg[warped_only > 0] = (bg[warped_only > 0].astype(float) * 0.5 +
                            np.array([200, 0, 0]) * 0.5).astype(np.uint8)

    text = (f"Green=overlap Red=fp_only Blue=warped_only  "
            f"IoU={metrics['iou']:.3f} outside={metrics['outside_fraction']:.3f}")
    cv2.putText(bg, text, (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                (255, 255, 255), 2)
    return bg


def draw_chamfer_heatmap(target_img, source_contour, footprint_contour,
                         params, aligner, pixel_size):
    """Draw distance-coded warped contour: green=close, red=far."""
    rot_deg, scale, dx, dy = params
    M = make_warp(aligner.src_cx, aligner.src_cy,
                  aligner.fp_cx + dx, aligner.fp_cy + dy,
                  math.radians(rot_deg), scale)
    warped = warp_contour(source_contour, M)
    wc = warped.reshape(-1, 2).astype(np.float64)

    dists, _ = aligner.fp_tree.query(wc)

    bg = desaturate(target_img, 0.4)
    # Draw footprint contour faintly
    cv2.drawContours(bg, [footprint_contour.reshape(-1, 1, 2).astype(np.int32)],
                     -1, (255, 255, 0), 1)

    # Color-code warped contour by distance
    for i in range(len(wc) - 1):
        t = min(dists[i] / 40.0, 1.0)  # normalize: 0=close, 1=far (40px)
        color = (0, int(255 * (1 - t)), int(255 * t))
        pt1 = (int(wc[i, 0]), int(wc[i, 1]))
        pt2 = (int(wc[i + 1, 0]), int(wc[i + 1, 1]))
        cv2.line(bg, pt1, pt2, color, 3)

    mean_um = dists.mean() * pixel_size
    cv2.putText(bg, f"Green=close Red=far  fwd={mean_um:.2f}um",
                (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    return bg


def main():
    parser = argparse.ArgumentParser(description="Fine alignment optimization")
    parser.add_argument("--source-contour", required=True)
    parser.add_argument("--source-mask", required=True)
    parser.add_argument("--footprint-contour", required=True)
    parser.add_argument("--footprint-mask", required=True)
    parser.add_argument("--target-image", required=True)
    parser.add_argument("--rot-hint", type=float, required=True,
                        help="Agent-selected rotation (degrees)")
    parser.add_argument("--scale-hint", type=float, default=None,
                        help="Optional scale hint to narrow search")
    parser.add_argument("--pixel-size", type=float, required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    # Load inputs
    source_contour = np.load(args.source_contour)
    source_mask = cv2.imread(args.source_mask, cv2.IMREAD_GRAYSCALE)
    footprint_contour = np.load(args.footprint_contour)
    footprint_mask = cv2.imread(args.footprint_mask, cv2.IMREAD_GRAYSCALE)
    target_img = cv2.imread(args.target_image)

    if source_mask is None or footprint_mask is None or target_img is None:
        print("ERROR: Cannot read one or more input files.", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)
    h, w = footprint_mask.shape[:2]

    # Build aligner at full resolution
    aligner = ChamferAligner(
        source_contour, source_mask,
        footprint_contour, footprint_mask,
        n_source_pts=600, n_fp_pts=800
    )

    # Bounds: narrow around hint
    rot_lo = args.rot_hint - 15.0
    rot_hi = args.rot_hint + 15.0

    scale_hint = args.scale_hint
    if scale_hint is None:
        # Auto-read from sweep candidates in alignment_report.json
        report_path = os.path.join(args.output_dir, "alignment_report.json")
        if os.path.exists(report_path):
            with open(report_path) as f:
                report = json.load(f)
            candidates = report.get("sweep_candidates", [])
            if candidates:
                # Find candidate closest to --rot-hint
                best = min(candidates,
                           key=lambda c: abs(c["rotation_deg"] - args.rot_hint))
                scale_hint = best["scale"]
                print(f"Auto scale hint: {scale_hint:.3f} "
                      f"(from sweep candidate rot={best['rotation_deg']:.1f}°)")

    if scale_hint is not None:
        s_lo = max(0.3, scale_hint - 0.1)
        s_hi = min(2.0, scale_hint + 0.1)
    else:
        s_lo, s_hi = 0.3, 2.0

    bounds = [
        (rot_lo, rot_hi),
        (s_lo, s_hi),
        (-w / 2, w / 2),
        (-h / 2, h / 2),
    ]

    # Stage 1: Differential Evolution
    print(f"DE: rot=[{rot_lo:.0f},{rot_hi:.0f}] scale=[{s_lo:.2f},{s_hi:.2f}]")
    t0 = time.time()

    de = differential_evolution(
        aligner.cost, bounds=bounds,
        maxiter=500, popsize=50,
        tol=1e-5, seed=42,
        mutation=(0.5, 1.5), recombination=0.9,
        polish=False,
    )

    de_time = time.time() - t0
    print(f"  DE: cost={de.fun:.1f} rot={de.x[0]:.2f} s={de.x[1]:.4f} "
          f"dx={de.x[2]:.1f} dy={de.x[3]:.1f} ({de.nfev} evals, {de_time:.1f}s)")

    best_cost = de.fun
    best_x = de.x.copy()

    # Stage 2: L-BFGS-B local refinement
    nm = minimize(aligner.cost, x0=de.x, method='L-BFGS-B',
                  bounds=bounds, options={'maxiter': 1000})
    if nm.fun < best_cost:
        best_cost = nm.fun
        best_x = nm.x.copy()
        print(f"  L-BFGS-B improved: cost={nm.fun:.1f}")

    # Stage 3: Multi-restart
    print("Multi-restart (150 trials)...")
    rng = np.random.RandomState(42)
    n_improved = 0
    for trial in range(150):
        x0 = best_x + rng.randn(4) * np.array([4.0, 0.03, 12.0, 12.0])
        x0 = np.clip(x0, [b[0] for b in bounds], [b[1] for b in bounds])
        try:
            r = minimize(aligner.cost, x0=x0, method='L-BFGS-B',
                         bounds=bounds, options={'maxiter': 500})
            if r.fun < best_cost:
                best_cost = r.fun
                best_x = r.x.copy()
                n_improved += 1
        except Exception:
            pass

    total_time = time.time() - t0
    print(f"  Multi-restart: {n_improved} improvements, "
          f"final cost={best_cost:.1f} ({total_time:.0f}s total)")

    # Evaluate final result
    final_params = list(best_x)
    metrics = aligner.evaluate(final_params, args.pixel_size)

    print(f"\nFinal alignment:")
    print(f"  rot={metrics['rot_deg']:.2f}°  scale={metrics['scale']:.4f}")
    print(f"  dx={metrics['dx_px']:.1f}px  dy={metrics['dy_px']:.1f}px")
    print(f"  fwd_chamfer: {metrics['fwd_chamfer_mean_um']:.2f}um "
          f"(median={metrics['fwd_chamfer_median_um']:.2f}, "
          f"p90={metrics['fwd_chamfer_p90_um']:.2f})")
    print(f"  IoU={metrics['iou']:.3f}  containment={metrics['top_containment']:.3f}  "
          f"outside={metrics['outside_fraction']:.3f}")

    # Auto-quality grading
    checks = {
        "fwd_chamfer": metrics["fwd_chamfer_mean_um"] < 2.5,
        "IoU": metrics["iou"] > 0.70,
        "containment": metrics["top_containment"] > 0.90,
        "outside": metrics["outside_fraction"] < 0.10,
    }
    borderline = {
        "fwd_chamfer": metrics["fwd_chamfer_mean_um"] < 4.0,
        "IoU": metrics["iou"] > 0.50,
        "containment": metrics["top_containment"] > 0.80,
        "outside": metrics["outside_fraction"] < 0.20,
    }
    n_pass = sum(checks.values())
    n_borderline = sum(borderline.values())
    if n_pass == 4:
        quality = "pass"
    elif n_borderline == 4:
        quality = "borderline"
    else:
        failed = [k for k, v in borderline.items() if not v]
        quality = f"fail ({', '.join(failed)})"
    print(f"\n  Quality: {quality} ({n_pass}/4 pass, {n_borderline}/4 borderline)")

    # Save warp matrix
    warp_matrix = metrics["warp_matrix"]
    warp_path = os.path.join(args.output_dir, "warp_top.npy")
    np.save(warp_path, warp_matrix)

    # Generate diagnostic images
    overlay, _ = draw_overlay_raw(target_img, source_contour, footprint_contour,
                                  final_params, aligner, args.pixel_size)
    cv2.imwrite(os.path.join(args.output_dir, "20_best_overlay_raw.png"), overlay)

    mask_ov = draw_mask_overlap(target_img, source_mask, footprint_mask,
                                final_params, aligner, metrics)
    cv2.imwrite(os.path.join(args.output_dir, "21_mask_overlap.png"), mask_ov)

    chamfer_hm = draw_chamfer_heatmap(target_img, source_contour, footprint_contour,
                                      final_params, aligner, args.pixel_size)
    cv2.imwrite(os.path.join(args.output_dir, "22_chamfer_heatmap.png"), chamfer_hm)

    # Update alignment_report.json
    report_path = os.path.join(args.output_dir, "alignment_report.json")
    if os.path.exists(report_path):
        with open(report_path) as f:
            report = json.load(f)
    else:
        report = {}

    report["status"] = "complete"
    report["quality"] = quality
    if "alignments" not in report:
        report["alignments"] = {}

    report["alignments"]["top"] = {
        "method": "chamfer",
        "warp_file": "warp_top.npy",
        "rotation_deg": metrics["rot_deg"],
        "scale": metrics["scale"],
        "dx_px": metrics["dx_px"],
        "dy_px": metrics["dy_px"],
        "mirror": True,  # inherited from source_contour step
        "fwd_chamfer_um": metrics["fwd_chamfer_mean_um"],
        "fwd_chamfer_median_um": metrics["fwd_chamfer_median_um"],
        "fwd_chamfer_p90_um": metrics["fwd_chamfer_p90_um"],
        "iou": metrics["iou"],
        "top_containment": metrics["top_containment"],
        "outside_fraction": metrics["outside_fraction"],
        "cost": metrics["cost"],
    }

    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\nSaved: warp_top.npy, 20/21/22 diagnostic images")
    print(f"Status: complete")


if __name__ == "__main__":
    main()
