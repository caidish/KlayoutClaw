#!/usr/bin/env python
"""Fine alignment optimization v2 — optimized for speed.

Changes from refine.py:
  1. Downsampled masks (25% resolution) in cost() — warpAffine on 386x520
     instead of 1544x2080. Full-res masks still used in evaluate().
  2. Reduced iteration budget: DE pop=20/maxiter=200, multi-restart 30 trials
     with early stopping (stop after 10 consecutive non-improving trials).

Usage: same CLI as refine.py
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
from scipy.spatial import KDTree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "nanodevice_flakedetect", "scripts"))
from core import make_warp, warp_contour, desaturate, mask_centroid


def largest_contour_from_mask(mask):
    contours, _ = cv2.findContours(
        mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        return None
    return max(contours, key=cv2.contourArea).reshape(-1, 2).astype(np.float64)


class FastChamferAligner:
    """ChamferAligner with downsampled masks for fast cost evaluation.

    Identical to ChamferAligner except:
    - cost() uses 25% resolution masks for the warpAffine containment check
    - evaluate() still uses full-resolution masks for accurate final metrics
    """

    def __init__(self, source_contour, source_mask,
                 footprint_contour, footprint_mask,
                 n_source_pts=600, n_fp_pts=800, ds_factor=4):
        # Store full-res masks (for evaluate)
        self.source_mask = source_mask
        self.footprint_mask = footprint_mask
        self.h, self.w = footprint_mask.shape[:2]

        # Downsampled masks (for cost)
        self.ds = ds_factor
        self.small_w = self.w // ds_factor
        self.small_h = self.h // ds_factor
        self.small_source_mask = cv2.resize(
            source_mask, (self.small_w, self.small_h),
            interpolation=cv2.INTER_NEAREST)
        self.small_footprint_mask = cv2.resize(
            footprint_mask, (self.small_w, self.small_h),
            interpolation=cv2.INTER_NEAREST)

        # Centroids (full-res coordinates)
        src_centroid = mask_centroid(source_mask)
        if src_centroid is None:
            raise ValueError("Source mask is empty")
        self.src_cx, self.src_cy = src_centroid

        fp_centroid = mask_centroid(footprint_mask)
        if fp_centroid is None:
            raise ValueError("Footprint mask is empty")
        self.fp_cx, self.fp_cy = fp_centroid

        # Subsample source contour
        src_pts = np.asarray(source_contour, dtype=np.float64).reshape(-1, 2)
        if len(src_pts) > n_source_pts:
            idx = np.linspace(0, len(src_pts) - 1, n_source_pts, dtype=int)
            src_pts = src_pts[idx]
        self.source_pts = src_pts

        # Subsample footprint contour + KDTree
        fp_pts = np.asarray(footprint_contour, dtype=np.float64).reshape(-1, 2)
        if len(fp_pts) > n_fp_pts:
            idx = np.linspace(0, len(fp_pts) - 1, n_fp_pts, dtype=int)
            fp_pts = fp_pts[idx]
        self.fp_pts = fp_pts
        self.fp_tree = KDTree(fp_pts)

    def cost(self, params):
        """Fast cost using downsampled masks for containment."""
        rot_deg, scale, dx, dy = params
        M = make_warp(self.src_cx, self.src_cy,
                      self.fp_cx + dx, self.fp_cy + dy,
                      math.radians(rot_deg), scale)

        # Warp source contour points (full-res coordinates)
        ones = np.ones((len(self.source_pts), 1))
        warped = (M @ np.hstack([self.source_pts, ones]).T).T

        # Out-of-bounds check
        oob = ((warped[:, 0] < 0) | (warped[:, 0] >= self.w) |
               (warped[:, 1] < 0) | (warped[:, 1] >= self.h))
        oob_frac = oob.sum() / len(warped)
        if oob_frac > 0.3:
            return 1e6

        # Forward Chamfer (KDTree, unchanged)
        dists_fwd, _ = self.fp_tree.query(warped)
        fwd = (dists_fwd ** 2).mean()

        # Containment on DOWNSAMPLED masks
        # Both src and dst downsampled by ds, so M_small[:,:2] = M[:,:2]
        # and M_small[:,2] = M[:,2] / ds
        M_small = M.copy()
        M_small[0, 2] /= self.ds
        M_small[1, 2] /= self.ds

        warped_mask = cv2.warpAffine(self.small_source_mask, M_small,
                                     (self.small_w, self.small_h),
                                     flags=cv2.INTER_NEAREST)
        warped_area = (warped_mask > 0).sum()
        if warped_area < (100 // (self.ds * self.ds)):
            return 1e6
        outside = cv2.bitwise_and(warped_mask,
                                  cv2.bitwise_not(self.small_footprint_mask))
        outside_frac = (outside > 0).sum() / warped_area

        return fwd + 3000.0 * outside_frac + 500.0 * oob_frac

    def evaluate(self, params, pixel_size_um=1.0):
        """Full-resolution evaluation (identical to original ChamferAligner)."""
        rot_deg, scale, dx, dy = params
        M = make_warp(self.src_cx, self.src_cy,
                      self.fp_cx + dx, self.fp_cy + dy,
                      math.radians(rot_deg), scale)

        ones = np.ones((len(self.source_pts), 1))
        warped = (M @ np.hstack([self.source_pts, ones]).T).T

        dists_fwd, _ = self.fp_tree.query(warped)

        warped_mask = cv2.warpAffine(self.source_mask, M, (self.w, self.h),
                                     flags=cv2.INTER_NEAREST)

        inter = cv2.bitwise_and(warped_mask, self.footprint_mask)
        union = cv2.bitwise_or(warped_mask, self.footprint_mask)

        inter_area = (inter > 0).sum()
        union_area = max((union > 0).sum(), 1)
        warped_area = max((warped_mask > 0).sum(), 1)
        fp_area = max((self.footprint_mask > 0).sum(), 1)

        outside = cv2.bitwise_and(warped_mask,
                                  cv2.bitwise_not(self.footprint_mask))

        return {
            "rot_deg": float(rot_deg),
            "scale": float(scale),
            "dx_px": float(dx),
            "dy_px": float(dy),
            "cost": float(self.cost(params)),
            "fwd_chamfer_mean_um": float(dists_fwd.mean() * pixel_size_um),
            "fwd_chamfer_median_um": float(np.median(dists_fwd) * pixel_size_um),
            "fwd_chamfer_p90_um": float(np.percentile(dists_fwd, 90) * pixel_size_um),
            "iou": float(inter_area / union_area),
            "top_containment": float(inter_area / warped_area),
            "fp_containment": float(inter_area / fp_area),
            "outside_fraction": float((outside > 0).sum() / warped_area),
            "warp_matrix": M,
        }


# ---- Visualization (unchanged from refine.py) ----

def draw_overlay_raw(target_img, source_contour, footprint_contour,
                     params, aligner, pixel_size):
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
    rot_deg, scale, dx, dy = params
    M = make_warp(aligner.src_cx, aligner.src_cy,
                  aligner.fp_cx + dx, aligner.fp_cy + dy,
                  math.radians(rot_deg), scale)
    warped = warp_contour(source_contour, M)
    wc = warped.reshape(-1, 2).astype(np.float64)

    dists, _ = aligner.fp_tree.query(wc)

    bg = desaturate(target_img, 0.4)
    cv2.drawContours(bg, [footprint_contour.reshape(-1, 1, 2).astype(np.int32)],
                     -1, (255, 255, 0), 1)

    for i in range(len(wc) - 1):
        t = min(dists[i] / 40.0, 1.0)
        color = (0, int(255 * (1 - t)), int(255 * t))
        pt1 = (int(wc[i, 0]), int(wc[i, 1]))
        pt2 = (int(wc[i + 1, 0]), int(wc[i + 1, 1]))
        cv2.line(bg, pt1, pt2, color, 3)

    mean_um = dists.mean() * pixel_size
    cv2.putText(bg, f"Green=close Red=far  fwd={mean_um:.2f}um",
                (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    return bg


# ---- Main ----

def main():
    parser = argparse.ArgumentParser(description="Fine alignment optimization v2")
    parser.add_argument("--source-contour", required=True)
    parser.add_argument("--source-mask", required=True)
    parser.add_argument("--footprint-contour", required=True)
    parser.add_argument("--footprint-mask", required=True)
    parser.add_argument("--target-image", required=True)
    parser.add_argument("--align-target-mask", default=None,
                        help="Optional target mask for choosing rotation/scale (e.g. 02_diff_target_mask.png).")
    parser.add_argument("--align-target-image", default=None,
                        help="Optional image used for final alignment diagnostics.")
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

    align_target_mask = footprint_mask
    align_target_contour = footprint_contour
    align_target_name = "footprint"
    diag_img = target_img
    if args.align_target_mask:
        align_target_mask = cv2.imread(args.align_target_mask, cv2.IMREAD_GRAYSCALE)
        if align_target_mask is None:
            print(f"ERROR: Cannot read align target mask: {args.align_target_mask}",
                  file=sys.stderr)
            sys.exit(1)
        align_target_contour = largest_contour_from_mask(align_target_mask)
        if align_target_contour is None:
            print(f"ERROR: Empty align target mask: {args.align_target_mask}",
                  file=sys.stderr)
            sys.exit(1)
        align_target_name = os.path.basename(args.align_target_mask)
        if args.align_target_image:
            img = cv2.imread(args.align_target_image)
            if img is not None:
                diag_img = img

    os.makedirs(args.output_dir, exist_ok=True)
    h, w = align_target_mask.shape[:2]

    # Build fast aligner (downsampled masks)
    aligner = FastChamferAligner(
        source_contour, source_mask,
        align_target_contour, align_target_mask,
        n_source_pts=600, n_fp_pts=800, ds_factor=4
    )

    # Bounds: narrow around hint
    rot_lo = args.rot_hint - 15.0
    rot_hi = args.rot_hint + 15.0

    scale_hint = args.scale_hint
    if scale_hint is None:
        report_path = os.path.join(args.output_dir, "alignment_report.json")
        if os.path.exists(report_path):
            with open(report_path) as f:
                report = json.load(f)
            candidates = report.get("sweep_candidates", [])
            if candidates:
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

    # Stage 1: Differential Evolution (reduced: pop=20, maxiter=200)
    print(f"DE: rot=[{rot_lo:.0f},{rot_hi:.0f}] scale=[{s_lo:.2f},{s_hi:.2f}]")
    t0 = time.time()

    de = differential_evolution(
        aligner.cost, bounds=bounds,
        maxiter=200, popsize=20,
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

    # Stage 3: Multi-restart (reduced: 30 trials, early stop after 10 non-improving)
    print("Multi-restart (30 trials, early stop=10)...")
    rng = np.random.RandomState(42)
    n_improved = 0
    no_improve_streak = 0
    for trial in range(30):
        x0 = best_x + rng.randn(4) * np.array([4.0, 0.03, 12.0, 12.0])
        x0 = np.clip(x0, [b[0] for b in bounds], [b[1] for b in bounds])
        try:
            r = minimize(aligner.cost, x0=x0, method='L-BFGS-B',
                         bounds=bounds, options={'maxiter': 500})
            if r.fun < best_cost:
                best_cost = r.fun
                best_x = r.x.copy()
                n_improved += 1
                no_improve_streak = 0
            else:
                no_improve_streak += 1
        except Exception:
            no_improve_streak += 1

        if no_improve_streak >= 10:
            print(f"  Early stop at trial {trial + 1} (10 consecutive non-improving)")
            break

    total_time = time.time() - t0
    print(f"  Multi-restart: {n_improved} improvements, "
          f"final cost={best_cost:.1f} ({total_time:.0f}s total)")

    # Evaluate final result (full resolution)
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
    overlay, _ = draw_overlay_raw(diag_img, source_contour, align_target_contour,
                                  final_params, aligner, args.pixel_size)
    cv2.imwrite(os.path.join(args.output_dir, "20_best_overlay_raw.png"), overlay)

    mask_ov = draw_mask_overlap(diag_img, source_mask, align_target_mask,
                                final_params, aligner, metrics)
    cv2.imwrite(os.path.join(args.output_dir, "21_mask_overlap.png"), mask_ov)

    chamfer_hm = draw_chamfer_heatmap(diag_img, source_contour, align_target_contour,
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
        "alignment_target": align_target_name,
        "warp_file": "warp_top.npy",
        "rotation_deg": metrics["rot_deg"],
        "scale": metrics["scale"],
        "dx_px": metrics["dx_px"],
        "dy_px": metrics["dy_px"],
        "mirror": True,
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
    print(f"Status: complete ({total_time:.0f}s)")


if __name__ == "__main__":
    main()
