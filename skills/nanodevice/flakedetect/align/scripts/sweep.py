#!/usr/bin/env python
"""Coarse rotation sweep using Chamfer+DE alignment.

Sweeps rotation from -180 to +168 in 12-degree steps. For each step,
runs differential evolution over (scale, dx, dy) with the
Chamfer+containment cost function. Masks/contours are downsampled to
50% resolution for speed. Produces candidate overlay images for the
agent to select the correct rotation.

Usage:
    conda run -n base python sweep.py \
        --source-contour <.npy> --source-mask <.png> \
        --footprint-contour <.npy> --footprint-mask <.png> \
        --target-image <image> --pixel-size <um/px> --output-dir <path>
"""

import argparse
import json
import os
import sys
import time

import cv2
import numpy as np
from scipy.optimize import differential_evolution

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from core import ChamferAligner, make_warp, warp_contour, desaturate, mask_centroid


def downsample_mask(mask, scale=0.5):
    """Downsample a binary mask by the given scale factor."""
    h, w = mask.shape[:2]
    new_w, new_h = int(w * scale), int(h * scale)
    resized = cv2.resize(mask, (new_w, new_h), interpolation=cv2.INTER_NEAREST)
    return resized


def downsample_contour(contour, scale=0.5):
    """Scale contour coordinates by the given factor."""
    pts = np.asarray(contour, dtype=np.float64).reshape(-1, 2)
    return pts * scale


def upsample_params(params, scale=0.5):
    """Convert optimized params from downsampled to full resolution.

    rot and scale stay the same; dx and dy need to be scaled back.
    """
    rot, s, dx, dy = params
    return [rot, s, dx / scale, dy / scale]


def draw_candidate(target_img, source_contour, footprint_contour, params,
                   src_cx, src_cy, fp_cx, fp_cy):
    """Draw a candidate overlay: warped contour on desaturated target."""
    rot_deg, scale, dx, dy = params
    import math
    M = make_warp(src_cx, src_cy, fp_cx + dx, fp_cy + dy,
                  math.radians(rot_deg), scale)
    warped = warp_contour(source_contour, M)

    bg = desaturate(target_img, 0.4)
    cv2.drawContours(bg, [footprint_contour.reshape(-1, 1, 2).astype(np.int32)],
                     -1, (0, 255, 0), 1)
    cv2.drawContours(bg, [warped.astype(np.int32)], -1, (0, 255, 255), 2)
    cv2.putText(bg, f"rot={rot_deg:.1f} s={scale:.3f}",
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
    return bg


def main():
    parser = argparse.ArgumentParser(description="Coarse rotation sweep (Chamfer+DE)")
    parser.add_argument("--source-contour", required=True, help="source_contour.npy")
    parser.add_argument("--source-mask", required=True, help="source_mask.png")
    parser.add_argument("--footprint-contour", required=True, help="footprint_contour.npy")
    parser.add_argument("--footprint-mask", required=True, help="footprint_mask.png")
    parser.add_argument("--target-image", required=True, help="full_stack image for overlays")
    parser.add_argument("--pixel-size", type=float, required=True, help="um/px")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    args = parser.parse_args()

    # Load inputs at full resolution
    source_contour = np.load(args.source_contour)
    source_mask = cv2.imread(args.source_mask, cv2.IMREAD_GRAYSCALE)
    footprint_contour = np.load(args.footprint_contour)
    footprint_mask = cv2.imread(args.footprint_mask, cv2.IMREAD_GRAYSCALE)
    target_img = cv2.imread(args.target_image)

    if source_mask is None or footprint_mask is None or target_img is None:
        print("ERROR: Cannot read one or more input files.", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)

    # Downsample for speed
    ds = 0.5
    ds_source_mask = downsample_mask(source_mask, ds)
    ds_footprint_mask = downsample_mask(footprint_mask, ds)
    ds_source_contour = downsample_contour(source_contour, ds)
    ds_footprint_contour = downsample_contour(footprint_contour, ds)

    ds_h, ds_w = ds_footprint_mask.shape[:2]

    # Build ChamferAligner on downsampled data
    aligner = ChamferAligner(
        ds_source_contour, ds_source_mask,
        ds_footprint_contour, ds_footprint_mask,
        n_source_pts=300, n_fp_pts=400
    )

    # Coarse rotation sweep: 12-degree steps over [-180, 168]
    rot_steps = np.arange(-180, 180, 12)
    scale_bounds = (0.3, 2.0)
    dx_bounds = (-ds_w / 2, ds_w / 2)
    dy_bounds = (-ds_h / 2, ds_h / 2)

    print(f"Sweep: {len(rot_steps)} rotation steps, downsampled to {ds_w}x{ds_h}")
    results = []
    t0 = time.time()

    for i, rot in enumerate(rot_steps):
        # Fix rotation, optimize scale + dx + dy
        bounds = [
            (rot, rot),          # rotation fixed
            scale_bounds,
            dx_bounds,
            dy_bounds,
        ]

        de = differential_evolution(
            aligner.cost, bounds=bounds,
            maxiter=80, popsize=15,
            seed=42, tol=1e-4,
            polish=False,
        )

        results.append({
            "rot": float(de.x[0]),
            "scale": float(de.x[1]),
            "dx": float(de.x[2]),
            "dy": float(de.x[3]),
            "cost": float(de.fun),
        })

        if (i + 1) % 10 == 0:
            elapsed = time.time() - t0
            print(f"  {i+1}/{len(rot_steps)} steps ({elapsed:.0f}s)")

    total_time = time.time() - t0
    print(f"Sweep complete: {total_time:.0f}s")

    # Degenerate sweep detection: if all top-8 scales < 0.75,
    # the cost function's small-scale minimum dominated. Re-sweep
    # with a raised scale floor to find the true solution.
    results.sort(key=lambda r: r["cost"])
    top8 = results[:min(8, len(results))]
    max_top_scale = max(r["scale"] for r in top8)

    if max_top_scale < 0.75:
        print(f"\nWARNING: All top candidates have scale < 0.75 "
              f"(max={max_top_scale:.3f}). Re-sweeping with scale >= 0.75...")
        fine_scale_bounds = (0.75, 2.0)
        fine_results = []
        t1 = time.time()

        for i, rot in enumerate(rot_steps):
            bounds = [
                (rot, rot),
                fine_scale_bounds,
                dx_bounds,
                dy_bounds,
            ]
            de = differential_evolution(
                aligner.cost, bounds=bounds,
                maxiter=80, popsize=15,
                seed=42, tol=1e-4,
                polish=False,
            )
            fine_results.append({
                "rot": float(de.x[0]),
                "scale": float(de.x[1]),
                "dx": float(de.x[2]),
                "dy": float(de.x[3]),
                "cost": float(de.fun),
            })
            if (i + 1) % 10 == 0:
                elapsed = time.time() - t1
                print(f"  {i+1}/{len(rot_steps)} fine steps ({elapsed:.0f}s)")

        fine_time = time.time() - t1
        total_time += fine_time
        print(f"Fine sweep complete: {fine_time:.0f}s")
        results = fine_results

    # Sort by cost, take top candidates
    results.sort(key=lambda r: r["cost"])
    n_candidates = min(8, len(results))
    top = results[:n_candidates]

    # Scale params back to full resolution for visualization and reporting
    full_h, full_w = footprint_mask.shape[:2]
    src_centroid = mask_centroid(source_mask)
    fp_centroid = mask_centroid(footprint_mask)

    print(f"\nTop {n_candidates} candidates:")
    candidate_images = []
    for rank, r in enumerate(top):
        # Convert dx/dy from downsampled to full resolution
        full_params = upsample_params(
            [r["rot"], r["scale"], r["dx"], r["dy"]], ds
        )
        r["full_rot"] = full_params[0]
        r["full_scale"] = full_params[1]
        r["full_dx"] = full_params[2]
        r["full_dy"] = full_params[3]

        print(f"  #{rank+1}: rot={r['rot']:.1f}°  s={r['scale']:.3f}  cost={r['cost']:.1f}")

        # Draw candidate overlay at full resolution
        img = draw_candidate(
            target_img, source_contour, footprint_contour,
            full_params, src_centroid[0], src_centroid[1],
            fp_centroid[0], fp_centroid[1]
        )
        fname = f"candidate_{rank+1:02d}.png"
        cv2.imwrite(os.path.join(args.output_dir, fname), img)
        r["image"] = fname
        candidate_images.append(img)

    # Create grid of all candidates
    if candidate_images:
        # Resize to fit in a grid
        grid_w = min(640, full_w)
        scale_grid = grid_w / full_w
        grid_h = int(full_h * scale_grid)
        resized = [cv2.resize(im, (grid_w, grid_h)) for im in candidate_images]

        # 2 rows x 4 cols (or less)
        cols = 4
        rows = (len(resized) + cols - 1) // cols
        while len(resized) < rows * cols:
            resized.append(np.zeros_like(resized[0]))

        grid_rows = []
        for r_idx in range(rows):
            row = np.hstack(resized[r_idx * cols:(r_idx + 1) * cols])
            grid_rows.append(row)
        grid = np.vstack(grid_rows)
        cv2.imwrite(os.path.join(args.output_dir, "05_sweep_grid.png"), grid)

    # Write alignment_report.json
    report_path = os.path.join(args.output_dir, "alignment_report.json")
    if os.path.exists(report_path):
        with open(report_path) as f:
            report = json.load(f)
    else:
        report = {}

    report["status"] = "needs_rotation_selection"
    report["sweep_candidates"] = [
        {
            "rank": i + 1,
            "rotation_deg": r["rot"],
            "scale": r["scale"],
            "cost": r["cost"],
            "image": r["image"],
        }
        for i, r in enumerate(top)
    ]
    report["sweep_time_sec"] = round(total_time, 1)

    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\nSaved {n_candidates} candidate images + 05_sweep_grid.png")
    print(f"Status: needs_rotation_selection")
    print(f"Agent: view candidates and pick the correct rotation for --rot-hint")


if __name__ == "__main__":
    main()
