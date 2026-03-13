#!/usr/bin/env python
"""Coordinate transforms: detection results → full_stack coordinate system.

Reads per-material detections from detections.json, applies the appropriate
warp matrix to bring each material into the common full_stack frame, then
builds the unified traces.json.

Transform rules:
  - graphite (bottom_part): invert warp_sift_bottom, apply to contour
  - graphene (top_part):    apply warp_top to mask (INTER_NEAREST),
                            clip to footprint, morph clean, re-extract contour
  - bottom_hBN (full_stack): pass through
  - top_hBN (full_stack):    pass through (= footprint)

Usage:
    conda run -n base python transform.py \
        --detections detect/detections.json \
        --align-dir align/ \
        --image full_stack_raw.jpg \
        --pixel-size 0.087 \
        --output-dir /tmp/combine
"""

import argparse
import json
import os
import sys

import cv2
import numpy as np

# Import shared utilities
sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"),
)

from core import (
    morph_clean,
    flood_fill_holes,
    keep_largest_n,
    smooth_material,
    LAYER_MAP,
    STACK_ORDER,
)


def transform_contour(contour, warp_matrix):
    """Transform contour points by a 2x3 affine warp matrix.

    Args:
        contour: Contour array, shape (N,1,2) or (N,2).
        warp_matrix: 2x3 numpy array.

    Returns:
        Transformed contour as int32 array with shape (N,1,2).
    """
    pts = contour.reshape(-1, 2).astype(np.float64)
    A = warp_matrix[:, :2]
    b = warp_matrix[:, 2]
    transformed = pts @ A.T + b
    return np.round(transformed).astype(np.int32).reshape(-1, 1, 2)


def build_masks(detections, detect_dir, warp_bot_inv, warp_top, footprint, image_size):
    """Build final material masks in full_stack coordinates.

    Args:
        detections: Parsed detections.json dict.
        detect_dir: Directory containing detection mask/contour files.
        warp_bot_inv: 2x3 affine inverse warp (bottom_part → full_stack).
        warp_top: 2x3 affine warp (top_part → full_stack).
        footprint: Binary mask of top_hBN footprint in full_stack coords.
        image_size: Tuple (width, height) of the full_stack image.

    Returns:
        Dict mapping material name -> binary mask (uint8, 0/255).
    """
    w, h = image_size
    masks = {}
    materials = detections.get("materials", {})

    # --- graphite: transform contour from bottom_part coords ---
    if "graphite" in materials:
        info = materials["graphite"]
        contour_path = os.path.join(detect_dir, info["contour_file"])
        if os.path.exists(contour_path) and warp_bot_inv is not None:
            contour = np.load(contour_path).reshape(-1, 2).astype(np.float64)
            transformed = transform_contour(contour, warp_bot_inv)
            graphite_mask = np.zeros((h, w), dtype=np.uint8)
            cv2.drawContours(graphite_mask, [transformed], -1, 255, cv2.FILLED)
            masks["graphite"] = graphite_mask

    # --- graphene: warp mask, clip to footprint, clean ---
    if "graphene" in materials:
        info = materials["graphene"]
        mask_path = os.path.join(detect_dir, info["mask_file"])
        if os.path.exists(mask_path) and warp_top is not None:
            graphene_mask_raw = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
            if graphene_mask_raw is not None:
                graphene_in_stack = cv2.warpAffine(
                    graphene_mask_raw, warp_top, (w, h),
                    flags=cv2.INTER_NEAREST,
                )
                if footprint is not None:
                    graphene_clipped = cv2.bitwise_and(graphene_in_stack, footprint)
                else:
                    graphene_clipped = graphene_in_stack
                graphene_clean = morph_clean(graphene_clipped, close_k=15, open_k=7)
                graphene_largest = keep_largest_n(graphene_clean, n=1, min_area=500)
                masks["graphene"] = flood_fill_holes(graphene_largest)

    # --- bottom_hBN: already in full_stack coords ---
    if "bottom_hBN" in materials:
        info = materials["bottom_hBN"]
        mask_path = os.path.join(detect_dir, info["mask_file"])
        if os.path.exists(mask_path):
            bottom_hbn_mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
            if bottom_hbn_mask is not None:
                bottom_hbn = keep_largest_n(bottom_hbn_mask, n=1, min_area=5000)
                masks["bottom_hBN"] = flood_fill_holes(bottom_hbn)

    # --- top_hBN: already in full_stack coords (= footprint) ---
    if "top_hBN" in materials:
        info = materials["top_hBN"]
        mask_path = os.path.join(detect_dir, info["mask_file"])
        if os.path.exists(mask_path):
            top_hbn_mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
            if top_hbn_mask is not None:
                top_hbn = keep_largest_n(top_hbn_mask, n=1, min_area=5000)
                masks["top_hBN"] = flood_fill_holes(top_hbn)

    return masks


def extract_contours(masks, min_area_px=500):
    """Extract and smooth contours from final material masks.

    Args:
        masks: Dict mapping material name -> binary mask (uint8, 0/255).
        min_area_px: Minimum contour area in pixels to keep.

    Returns:
        Dict mapping material name -> list of smoothed contours (N,1,2) int32.
    """
    result = {}
    for material, mask in masks.items():
        contours_raw, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE
        )
        smoothed_list = []
        for cnt in contours_raw:
            if cv2.contourArea(cnt) < min_area_px:
                continue
            smoothed = smooth_material(cnt, material)
            if len(smoothed) >= 3:
                smoothed_list.append(smoothed)
        result[material] = smoothed_list
    return result


def build_traces_json(contours, pixel_size_um, image_size_px, image_path):
    """Build the unified traces JSON dict.

    Args:
        contours: Dict mapping material name -> list of contours (N,1,2).
        pixel_size_um: Microns per pixel.
        image_size_px: Tuple (width, height).
        image_path: Absolute path to full_stack reference image.

    Returns:
        Dict with the traces data structure.
    """
    w_px, h_px = image_size_px
    w_um = round(w_px * pixel_size_um, 3)
    h_um = round(h_px * pixel_size_um, 3)

    materials = {}
    global_id = 0

    for material in STACK_ORDER:
        material_contours = contours.get(material, [])
        entries = []
        for cnt in material_contours:
            global_id += 1
            pts = cnt.reshape(-1, 2)
            contour_px = pts.tolist()
            contour_um = [
                [round(p[0] * pixel_size_um, 3),
                 round(p[1] * pixel_size_um, 3)]
                for p in contour_px
            ]
            area_um2 = round(
                cv2.contourArea(cnt) * pixel_size_um * pixel_size_um, 3
            )
            entries.append({
                "id": global_id,
                "contour_px": contour_px,
                "contour_um": contour_um,
                "area_um2": area_um2,
                "num_points": len(contour_px),
            })
        materials[material] = entries

    return {
        "image": os.path.abspath(image_path),
        "pixel_size_um": pixel_size_um,
        "image_size_px": [w_px, h_px],
        "image_size_um": [w_um, h_um],
        "stack": list(STACK_ORDER),
        "layer_map": dict(LAYER_MAP),
        "materials": materials,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Transform detection results into full_stack coordinates"
    )
    parser.add_argument("--detections", required=True,
                        help="Path to detections.json")
    parser.add_argument("--align-dir", required=True,
                        help="Directory containing warp matrices and footprint")
    parser.add_argument("--image", required=True,
                        help="Full stack raw image (for size reference)")
    parser.add_argument("--pixel-size", type=float, required=True,
                        help="Microns per pixel")
    parser.add_argument("--output-dir", required=True,
                        help="Output directory")
    args = parser.parse_args()

    # Load reference image for size
    ref_img = cv2.imread(os.path.abspath(args.image))
    if ref_img is None:
        print(f"ERROR: Cannot read reference image: {args.image}", file=sys.stderr)
        sys.exit(1)
    h, w = ref_img.shape[:2]
    image_size = (w, h)

    # Load detections.json
    det_path = os.path.abspath(args.detections)
    with open(det_path) as f:
        detections = json.load(f)
    detect_dir = os.path.dirname(det_path)

    # Load warp matrices from align dir
    align_dir = os.path.abspath(args.align_dir)

    warp_bot_inv = None
    warp_bot_path = os.path.join(align_dir, "warp_sift_bottom.npy")
    if os.path.exists(warp_bot_path):
        warp_bot = np.load(warp_bot_path)
        warp_bot_inv = cv2.invertAffineTransform(warp_bot)

    warp_top = None
    warp_top_path = os.path.join(align_dir, "warp_top.npy")
    if os.path.exists(warp_top_path):
        warp_top = np.load(warp_top_path)

    # Load footprint mask
    footprint = None
    fp_path = os.path.join(align_dir, "footprint_mask.png")
    if os.path.exists(fp_path):
        footprint = cv2.imread(fp_path, cv2.IMREAD_GRAYSCALE)

    os.makedirs(args.output_dir, exist_ok=True)

    # Build masks in full_stack coordinates
    masks = build_masks(detections, detect_dir, warp_bot_inv, warp_top,
                        footprint, image_size)

    # Extract smoothed contours
    contours = extract_contours(masks)

    # Save per-material transformed masks
    mask_names = {
        "graphite": "graphite_full.png",
        "graphene": "graphene_full.png",
        "bottom_hBN": "bottom_hbn_full.png",
        "top_hBN": "top_hbn_full.png",
    }
    for material, mask in masks.items():
        fname = mask_names.get(material)
        if fname:
            cv2.imwrite(os.path.join(args.output_dir, fname), mask)

    # Build and save traces.json
    traces = build_traces_json(contours, args.pixel_size, image_size,
                               args.image)
    traces_path = os.path.join(args.output_dir, "traces.json")
    with open(traces_path, "w") as f:
        json.dump(traces, f, indent=2)

    # Append transform_summary to combine_report.json
    report_path = os.path.join(args.output_dir, "combine_report.json")
    if os.path.exists(report_path):
        with open(report_path) as f:
            report = json.load(f)
    else:
        report = {}

    report["transform_summary"] = {
        "graphite": "bottom_part → full_stack via inverted warp_sift_bottom",
        "graphene": "top_part(mirrored) → full_stack via warp_top (direct)",
        "bottom_hBN": "already in full_stack coords (pass-through)",
        "top_hBN": "already in full_stack coords (= footprint, pass-through)",
    }
    report["traces_file"] = "traces.json"

    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    # Summary
    print(f"OK: traces written to {traces_path}")
    print(f"  Image: {w}x{h} px "
          f"({traces['image_size_um'][0]}x{traces['image_size_um'][1]} um)")
    for material in STACK_ORDER:
        entries = traces["materials"].get(material, [])
        if entries:
            total_area = sum(e["area_um2"] for e in entries)
            print(f"  {material}: {len(entries)} region(s), "
                  f"total area {total_area:.1f} um^2 -> layer {LAYER_MAP.get(material, '?')}")
        else:
            print(f"  {material}: no regions detected")


if __name__ == "__main__":
    main()
