#!/usr/bin/env python
"""Emit detailed diagnostic images for footprint generation.

This script is intentionally diagnostic-only. It mirrors footprint.py's B1
pipeline but saves every major intermediate state so a human can inspect why a
footprint candidate was selected.
"""

import argparse
import json
import os
import sys

import cv2
import numpy as np

SCRIPT_DIR = os.path.dirname(__file__)
sys.path.insert(0, SCRIPT_DIR)

from footprint import (  # noqa: E402
    compute_diff_image,
    compute_shape_descriptors,
    draw_cluster_map,
    enumerate_footprint_candidates,
    filter_clusters_diff,
    flood_fill_holes,
    grabcut_with_quality_retry,
    load_warp_matrix,
    morph_clean,
    resolve_warp_path,
    segment_source_flake,
    split_clusters,
)


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def put_label(img, text, org=(16, 36), scale=0.75):
    out = img.copy()
    cv2.putText(out, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(out, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 2, cv2.LINE_AA)
    return out


def color_mask_on_image(image, mask, color=(0, 255, 255), alpha=0.38):
    out = image.copy()
    overlay = out.copy()
    overlay[mask > 0] = color
    out[mask > 0] = cv2.addWeighted(out, 1 - alpha, overlay, alpha, 0)[mask > 0]
    return out


def draw_mask_contour(image, mask, label, contour_color=(0, 255, 0)):
    out = color_mask_on_image(image, mask)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        cv2.drawContours(out, contours, -1, contour_color, 2)
    return put_label(out, label)


def save_diff_heatmap(path, diff_gray):
    heat = cv2.applyColorMap(diff_gray, cv2.COLORMAP_INFERNO)
    cv2.imwrite(path, put_label(heat, "LAB diff heatmap: full_stack - warped_bottom"))


def save_diff_hist(path, diff_gray):
    hist = cv2.calcHist([diff_gray], [0], None, [256], [0, 256]).flatten()
    h, w = 420, 900
    img = np.full((h, w, 3), 255, dtype=np.uint8)
    hist = hist / max(hist.max(), 1)
    for x in range(256):
        x0 = int(x / 256 * w)
        x1 = int((x + 1) / 256 * w)
        y = int(h - 40 - hist[x] * (h - 80))
        cv2.rectangle(img, (x0, y), (max(x1, x0 + 1), h - 40), (80, 80, 80), -1)
    for thresh in (15,):
        x = int(thresh / 256 * w)
        cv2.line(img, (x, 20), (x, h - 40), (0, 0, 255), 2)
        cv2.putText(img, f"filter mean>{thresh}", (x + 8, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 255), 2)
    cv2.putText(img, "diff intensity histogram", (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
    cv2.imwrite(path, img)


def save_cluster_panels(out_dir, target_img, label_map, candidate_ids, prefix):
    ensure_dir(out_dir)
    h, w = target_img.shape[:2]
    panels = []
    for cid in candidate_ids:
        mask = ((label_map == cid).astype(np.uint8) * 255)
        area = int((mask > 0).sum())
        panel = draw_mask_contour(target_img, mask, f"{prefix} cluster {cid} area={area}")
        scale = min(1.0, 480 / max(w, h))
        panel_small = cv2.resize(panel, None, fx=scale, fy=scale)
        cv2.imwrite(os.path.join(out_dir, f"{prefix}_cluster_{cid}.png"), panel)
        panels.append(panel_small)
    if panels:
        rows = []
        cols = 3
        for i in range(0, len(panels), cols):
            row = panels[i:i + cols]
            max_h = max(p.shape[0] for p in row)
            padded = []
            for p in row:
                if p.shape[0] < max_h:
                    pad = np.full((max_h - p.shape[0], p.shape[1], 3), 245, dtype=np.uint8)
                    p = np.vstack([p, pad])
                padded.append(p)
            rows.append(np.hstack(padded))
        max_w = max(r.shape[1] for r in rows)
        rows2 = []
        for r in rows:
            if r.shape[1] < max_w:
                pad = np.full((r.shape[0], max_w - r.shape[1], 3), 245, dtype=np.uint8)
                r = np.hstack([r, pad])
            rows2.append(r)
        cv2.imwrite(os.path.join(out_dir, f"{prefix}_clusters_montage.png"), np.vstack(rows2))


def save_candidate_montage(path, target_img, candidates, selected_rank, max_n=12):
    h, w = target_img.shape[:2]
    panels = []
    for idx, (dist, ids, contour, mask) in enumerate(candidates[:max_n], start=1):
        panel = color_mask_on_image(target_img, mask, color=(0, 255, 255), alpha=0.32)
        cv2.drawContours(panel, [contour], -1, (0, 255, 0), 2)
        color = (0, 0, 255) if idx == selected_rank else (255, 255, 255)
        txt = f"#{idx} dist={dist:.3f} ids={ids} area={(mask > 0).sum()}"
        cv2.putText(panel, txt, (12, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (0, 0, 0), 4)
        cv2.putText(panel, txt, (12, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.58, color, 2)
        scale = min(1.0, 520 / max(w, h))
        panels.append(cv2.resize(panel, None, fx=scale, fy=scale))
    cols = 3
    rows = []
    for i in range(0, len(panels), cols):
        row = panels[i:i + cols]
        max_h = max(p.shape[0] for p in row)
        padded = []
        for p in row:
            if p.shape[0] < max_h:
                pad = np.full((max_h - p.shape[0], p.shape[1], 3), 245, dtype=np.uint8)
                p = np.vstack([p, pad])
            padded.append(p)
        rows.append(np.hstack(padded))
    cv2.imwrite(path, np.vstack(rows))


def main():
    parser = argparse.ArgumentParser(description="Detailed footprint process debug output")
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--bottom", required=True)
    parser.add_argument("--source-contour")
    parser.add_argument("--source-mask")
    parser.add_argument("--warp")
    parser.add_argument("--pixel-size", type=float, required=True)
    parser.add_argument("--n-clusters", type=int, default=12)
    parser.add_argument("--candidate-rank", type=int, default=1)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    ensure_dir(args.output_dir)
    target_img = cv2.imread(args.target)
    bottom_img = cv2.imread(args.bottom)
    source_img = cv2.imread(args.source)
    if target_img is None or bottom_img is None or source_img is None:
        print("ERROR: cannot read one or more input images", file=sys.stderr)
        return 1

    if args.source_contour and args.source_mask:
        source_contour = np.load(args.source_contour).astype(np.int32).reshape(-1, 1, 2)
        source_mask = cv2.imread(args.source_mask, cv2.IMREAD_GRAYSCALE)
    else:
        source_contour, source_mask = segment_source_flake(source_img)
    if source_contour is None or source_mask is None:
        print("ERROR: source contour/mask unavailable", file=sys.stderr)
        return 2
    source_desc = compute_shape_descriptors(source_contour)
    source_area = source_desc["area"]

    cv2.imwrite(os.path.join(args.output_dir, "00_target_full_stack_raw.png"), put_label(target_img, "target full_stack_raw"))
    cv2.imwrite(os.path.join(args.output_dir, "00_bottom_part_raw.png"), put_label(bottom_img, "bottom_part raw"))
    cv2.imwrite(os.path.join(args.output_dir, "00_source_top_part_raw.png"), put_label(source_img, "source top_part raw"))
    cv2.imwrite(os.path.join(args.output_dir, "01_source_mask_for_shape.png"),
                draw_mask_contour(source_img, source_mask, f"source mask/contour area={source_area:.0f}px"))

    warp_path, warp_src = resolve_warp_path(args.warp, args.output_dir, args.source)
    if warp_path is None:
        print("ERROR: no warp_sift_bottom.npy found; pass --warp explicitly", file=sys.stderr)
        return 3
    warp = load_warp_matrix(warp_path)
    bottom_to_target = cv2.invertAffineTransform(warp)
    h, w = target_img.shape[:2]
    warped_bottom = cv2.warpAffine(bottom_img, bottom_to_target, (w, h))
    valid_src = np.full(bottom_img.shape[:2], 255, dtype=np.uint8)
    overlap = cv2.warpAffine(valid_src, bottom_to_target, (w, h), flags=cv2.INTER_NEAREST,
                             borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    cv2.imwrite(os.path.join(args.output_dir, "02_bottom_warped_to_target.png"),
                put_label(warped_bottom, f"bottom warped to target using {warp_src}: {os.path.basename(warp_path)}"))
    cv2.imwrite(os.path.join(args.output_dir, "02_bottom_target_overlap_mask.png"),
                put_label(cv2.cvtColor(overlap, cv2.COLOR_GRAY2BGR), "valid overlap after bottom warp"))

    diff_gray = compute_diff_image(target_img, bottom_img, bottom_to_target,
                                   source_bgr=source_img, source_mask=source_mask)
    cv2.imwrite(os.path.join(args.output_dir, "03_diff_gray.png"), put_label(cv2.cvtColor(diff_gray, cv2.COLOR_GRAY2BGR), "LAB diff gray"))
    save_diff_heatmap(os.path.join(args.output_dir, "03_diff_heatmap.png"), diff_gray)
    save_diff_hist(os.path.join(args.output_dir, "03_diff_histogram.png"), diff_gray)

    label_map, km = __import__("footprint").cluster_target_diff(diff_gray, n_clusters=args.n_clusters)
    raw_candidate_ids = filter_clusters_diff(label_map, diff_gray, args.n_clusters)
    cv2.imwrite(os.path.join(args.output_dir, "04_kmeans_cluster_map.png"), put_label(draw_cluster_map(label_map), f"KMeans cluster map n={args.n_clusters}"))
    save_cluster_panels(os.path.join(args.output_dir, "04_raw_candidate_clusters"), target_img, label_map, raw_candidate_ids, "raw")

    split_label_map, split_candidate_ids = split_clusters(label_map, raw_candidate_ids, source_area)
    cv2.imwrite(os.path.join(args.output_dir, "05_split_cluster_map.png"), put_label(draw_cluster_map(split_label_map), "split disconnected candidate clusters"))
    save_cluster_panels(os.path.join(args.output_dir, "05_split_candidate_clusters"), target_img, split_label_map, split_candidate_ids, "split")

    candidates = enumerate_footprint_candidates(
        split_label_map, split_candidate_ids, source_desc, source_contour, source_area
    )
    if not candidates:
        print("ERROR: no footprint candidates", file=sys.stderr)
        return 4
    selected_rank = min(max(args.candidate_rank, 1), len(candidates))
    save_candidate_montage(os.path.join(args.output_dir, "06_candidate_rank_montage_top12.png"),
                           target_img, candidates, selected_rank, max_n=12)

    best_dist, best_ids, best_contour, best_mask = candidates[selected_rank - 1]
    cv2.imwrite(os.path.join(args.output_dir, "07_selected_kmeans_seed_mask.png"),
                draw_mask_contour(target_img, best_mask, f"selected seed rank={selected_rank} dist={best_dist:.4f} ids={best_ids}"))
    cv2.imwrite(os.path.join(args.output_dir, "07_selected_seed_binary.png"), best_mask)

    fp_mask, attempt, fp_info, border_px = grabcut_with_quality_retry(
        target_img, best_mask, h, w, border_min_px=50, max_attempts=3
    )
    cv2.imwrite(os.path.join(args.output_dir, "08_grabcut_final_mask.png"), fp_mask)
    cv2.imwrite(os.path.join(args.output_dir, "08_grabcut_final_overlay.png"),
                draw_mask_contour(target_img, fp_mask, f"GrabCut final attempt={attempt} border={border_px} area={fp_info['area_px']} gate={fp_info['gate_pass']}"))

    contours, _ = cv2.findContours(fp_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    final = target_img.copy()
    if contours:
        cv2.drawContours(final, [max(contours, key=cv2.contourArea)], -1, (0, 255, 0), 3)
    cv2.drawContours(final, [best_contour], -1, (0, 255, 255), 1)
    cv2.imwrite(os.path.join(args.output_dir, "09_final_footprint_contour_vs_seed.png"),
                put_label(final, "green=final footprint contour, yellow=selected KMeans seed contour"))

    summary = {
        "source_area_px": float(source_area),
        "warp_path": os.path.abspath(warp_path),
        "warp_source": warp_src,
        "n_clusters": args.n_clusters,
        "raw_candidate_ids": [int(x) for x in raw_candidate_ids],
        "split_candidate_ids": [int(x) for x in split_candidate_ids],
        "num_candidates": len(candidates),
        "selected_rank": selected_rank,
        "selected_cluster_ids": [int(x) for x in best_ids],
        "selected_shape_distance": float(best_dist),
        "selected_kmeans_area_px": int((best_mask > 0).sum()),
        "grabcut": fp_info,
        "border_px": int(border_px),
        "top_candidates": [
            {
                "rank": i + 1,
                "shape_distance": float(c[0]),
                "cluster_ids": [int(x) for x in c[1]],
                "area_px": int((c[3] > 0).sum()),
            }
            for i, c in enumerate(candidates[:12])
        ],
    }
    with open(os.path.join(args.output_dir, "footprint_process_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    with open(os.path.join(args.output_dir, "README_process.md"), "w", encoding="utf-8") as f:
        f.write("# HM08 footprint generation process\n\n")
        for name in [
            "00_target_full_stack_raw.png",
            "00_bottom_part_raw.png",
            "00_source_top_part_raw.png",
            "01_source_mask_for_shape.png",
            "02_bottom_warped_to_target.png",
            "02_bottom_target_overlap_mask.png",
            "03_diff_gray.png",
            "03_diff_heatmap.png",
            "03_diff_histogram.png",
            "04_kmeans_cluster_map.png",
            "04_raw_candidate_clusters/raw_clusters_montage.png",
            "05_split_cluster_map.png",
            "05_split_candidate_clusters/split_clusters_montage.png",
            "06_candidate_rank_montage_top12.png",
            "07_selected_kmeans_seed_mask.png",
            "08_grabcut_final_overlay.png",
            "09_final_footprint_contour_vs_seed.png",
        ]:
            f.write(f"- `{name}`\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
