#!/usr/bin/env python
"""Coordinate transforms: detection results 鈫?full_stack coordinate system.

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
    conda run -n instrMCPdev python transform.py \
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
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "nanodevice_flakedetect", "scripts"),
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


class MaskDroppedError(RuntimeError):
    """Raised when a non-empty input mask is reduced to zero pixels by the
    transform pipeline. The orchestrator can recover (re-run detect with a
    different cluster, escalate to vision-review) only if it knows which
    stage zeroed the mask, so the offending stage name is exposed via
    ``dropped_at_stage`` and the per-stage counts via ``stage_counts``.
    """

    def __init__(self, material, dropped_at_stage, stage_counts):
        self.material = material
        self.dropped_at_stage = dropped_at_stage
        self.stage_counts = dict(stage_counts)
        super().__init__(
            f"{material}: input had {stage_counts.get('input_pixels', 0)} px "
            f"but was zeroed at stage {dropped_at_stage!r}; "
            f"stage_counts={self.stage_counts}"
        )


def _pixel_count(mask):
    """Count non-zero pixels in a binary mask (None-safe)."""
    if mask is None:
        return 0
    return int((mask > 0).sum())


def _detect_dropped_stage(stage_counts):
    """Return the first stage where the pixel count fell to 0, or None.

    Stages are checked in pipeline order. ``input_pixels`` itself is not
    a 'drop' stage 鈥?if the input is empty there is no dropping to report.
    """
    if stage_counts.get("input_pixels", 0) <= 0:
        return None
    # Ordered list of (stage_name, key) pairs in pipeline execution order.
    pipeline_keys = [
        ("warp", "post_warp_pixels"),
        ("bitwise_and", "post_bitwise_and_pixels"),
        ("morph", "post_morph_pixels"),
        ("keep_largest", "post_keep_largest_pixels"),
    ]
    for stage_name, key in pipeline_keys:
        if key in stage_counts and stage_counts[key] == 0:
            return stage_name
    return None


def build_masks(detections, detect_dir, warp_bot_inv, warp_top, footprint,
                image_size, min_area=500):
    """Build final material masks in full_stack coordinates.

    Args:
        detections: Parsed detections.json dict.
        detect_dir: Directory containing detection mask/contour files.
        warp_bot_inv: 2x3 affine inverse warp (bottom_part 鈫?full_stack).
        warp_top: 2x3 affine warp (top_part 鈫?full_stack).
        footprint: Binary mask of top_hBN footprint in full_stack coords.
        image_size: Tuple (width, height) of the full_stack image.
        min_area: Minimum area in pixels for keep_largest_n on graphene.
            Defaults to 500 px (鈮?5.6 碌m虏 at 0.106 碌m/px). Raise this to
            reject tiny noise components, lower it when working at a finer
            pixel size or with intentionally small flakes.

    Returns:
        Tuple ``(masks, stage_counts)``:
          - masks: Dict mapping material name -> binary mask (uint8, 0/255).
          - stage_counts: Dict mapping material name -> per-stage pixel
            counts plus an optional ``dropped_at_stage`` entry. Per-stage
            keys vary by material (see issue #33 schema):
              graphene: input/post_warp/post_bitwise_and/post_morph/post_keep_largest
              graphite: input/post_keep_largest
              bottom_hBN, top_hBN: input/post_keep_largest

    Raises:
        MaskDroppedError: If a material had ``input_pixels > 0`` but was
            reduced to zero pixels at any stage. ``dropped_at_stage`` on
            the raised exception identifies the offending stage.
    """
    w, h = image_size
    masks = {}
    stage_counts = {}
    materials = detections.get("materials", {})

    # --- graphite: transform mask from bottom_part coords ---
    if "graphite" in materials:
        info = materials["graphite"]
        contour_path = None
        mask_path = os.path.join(detect_dir, info.get("mask_file", ""))
        if os.path.exists(mask_path) and warp_bot_inv is not None:
            graphite_mask_raw = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
            if graphite_mask_raw is not None:
                counts = {"input_pixels": _pixel_count(graphite_mask_raw)}
                graphite_mask = cv2.warpAffine(
                    graphite_mask_raw, warp_bot_inv, (w, h),
                    flags=cv2.INTER_NEAREST,
                )
                counts["post_warp_pixels"] = _pixel_count(graphite_mask)
                counts["post_keep_largest_pixels"] = _pixel_count(graphite_mask)
                dropped = _detect_dropped_stage(counts)
                if dropped is not None:
                    counts["dropped_at_stage"] = dropped
                    stage_counts["graphite"] = counts
                    raise MaskDroppedError("graphite", dropped, counts)
                stage_counts["graphite"] = counts
                masks["graphite"] = graphite_mask
        if "graphite" not in masks and "contour_file" in info:
            contour_path = os.path.join(detect_dir, info["contour_file"])
            if not (os.path.exists(contour_path) and warp_bot_inv is not None):
                contour_path = None
        if "graphite" not in masks and contour_path:
            contour = np.load(contour_path).reshape(-1, 2).astype(np.float64)
            counts = {"input_pixels": int(len(contour))}
            transformed = transform_contour(contour, warp_bot_inv)
            graphite_mask = np.zeros((h, w), dtype=np.uint8)
            cv2.drawContours(graphite_mask, [transformed], -1, 255, cv2.FILLED)
            counts["post_keep_largest_pixels"] = _pixel_count(graphite_mask)
            dropped = _detect_dropped_stage(counts)
            if dropped is not None:
                counts["dropped_at_stage"] = dropped
                stage_counts["graphite"] = counts
                raise MaskDroppedError("graphite", dropped, counts)
            stage_counts["graphite"] = counts
            masks["graphite"] = graphite_mask

    # --- graphene: warp mask, clip to footprint, clean ---
    if "graphene" in materials:
        info = materials["graphene"]
        mask_path = os.path.join(detect_dir, info["mask_file"])
        if os.path.exists(mask_path) and warp_top is not None:
            graphene_mask_raw = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
            if graphene_mask_raw is not None:
                counts = {"input_pixels": _pixel_count(graphene_mask_raw)}
                graphene_in_stack = cv2.warpAffine(
                    graphene_mask_raw, warp_top, (w, h),
                    flags=cv2.INTER_NEAREST,
                )
                counts["post_warp_pixels"] = _pixel_count(graphene_in_stack)
                if footprint is not None:
                    graphene_clipped = cv2.bitwise_and(graphene_in_stack, footprint)
                else:
                    graphene_clipped = graphene_in_stack
                counts["post_bitwise_and_pixels"] = _pixel_count(graphene_clipped)
                graphene_clean = morph_clean(graphene_clipped, close_k=15, open_k=7)
                counts["post_morph_pixels"] = _pixel_count(graphene_clean)
                graphene_largest = keep_largest_n(
                    graphene_clean, n=1, min_area=min_area
                )
                counts["post_keep_largest_pixels"] = _pixel_count(graphene_largest)
                dropped = _detect_dropped_stage(counts)
                if dropped is not None:
                    counts["dropped_at_stage"] = dropped
                    stage_counts["graphene"] = counts
                    raise MaskDroppedError("graphene", dropped, counts)
                stage_counts["graphene"] = counts
                masks["graphene"] = flood_fill_holes(graphene_largest)

    # --- bottom_hBN: already in full_stack coords ---
    if "bottom_hBN" in materials:
        info = materials["bottom_hBN"]
        mask_path = os.path.join(detect_dir, info["mask_file"])
        if os.path.exists(mask_path):
            bottom_hbn_mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
            if bottom_hbn_mask is not None:
                counts = {"input_pixels": _pixel_count(bottom_hbn_mask)}
                bottom_hbn = keep_largest_n(bottom_hbn_mask, n=1, min_area=5000)
                counts["post_keep_largest_pixels"] = _pixel_count(bottom_hbn)
                dropped = _detect_dropped_stage(counts)
                if dropped is not None:
                    counts["dropped_at_stage"] = dropped
                    stage_counts["bottom_hBN"] = counts
                    raise MaskDroppedError("bottom_hBN", dropped, counts)
                stage_counts["bottom_hBN"] = counts
                masks["bottom_hBN"] = flood_fill_holes(bottom_hbn)

    # --- top_hBN: already in full_stack coords (= footprint) ---
    if "top_hBN" in materials:
        info = materials["top_hBN"]
        mask_path = os.path.join(detect_dir, info["mask_file"])
        if os.path.exists(mask_path):
            top_hbn_mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
            if top_hbn_mask is not None:
                counts = {"input_pixels": _pixel_count(top_hbn_mask)}
                top_hbn = keep_largest_n(top_hbn_mask, n=1, min_area=5000)
                counts["post_keep_largest_pixels"] = _pixel_count(top_hbn)
                dropped = _detect_dropped_stage(counts)
                if dropped is not None:
                    counts["dropped_at_stage"] = dropped
                    stage_counts["top_hBN"] = counts
                    raise MaskDroppedError("top_hBN", dropped, counts)
                stage_counts["top_hBN"] = counts
                masks["top_hBN"] = flood_fill_holes(top_hbn)

    return masks, stage_counts


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
    parser.add_argument(
        "--min-area", type=int, default=500,
        help=(
            "Minimum component area in pixels for keep_largest_n on the "
            "graphene mask. Default 500 px (鈮?5.6 碌m虏 at 0.106 碌m/px). "
            "Lower this when working at finer pixel sizes or with smaller "
            "flakes; raise it to reject more aggressive noise."
        ),
    )
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

    # Refuse footprints whose alignment_report.json marks them unusable.
    # Per #32: combine.transform must not silently consume a footprint that
    # the align stage flagged as degenerate. status="failed" means the
    # candidate aborted; "needs_rotation_selection" means the orchestrator
    # never picked a rotation; in either case the downstream warp would be
    # garbage, so refuse fail-closed.
    align_report_path = os.path.join(align_dir, "alignment_report.json")
    if os.path.exists(align_report_path):
        try:
            with open(align_report_path) as _f:
                _report = json.load(_f)
        except Exception:
            _report = {}
        _status = _report.get("status", "")
        _fp_status = _report.get("footprint", {}).get("status", "")
        bad = {"failed", "needs_rotation_selection"}
        if _status in bad or _fp_status in bad:
            print(
                f"ERROR: refusing to consume align outputs: "
                f"alignment_report.status={_status!r} "
                f"footprint.status={_fp_status!r}",
                file=sys.stderr,
            )
            sys.exit(2)

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
    try:
        masks, stage_counts = build_masks(
            detections, detect_dir, warp_bot_inv, warp_top,
            footprint, image_size, min_area=args.min_area,
        )
    except MaskDroppedError as exc:
        # Persist the partial diagnostics into combine_report.json so the
        # orchestrator can read which stage zeroed the mask without parsing
        # stderr. Then exit non-zero with a structured message.
        os.makedirs(args.output_dir, exist_ok=True)
        report_path = os.path.join(args.output_dir, "combine_report.json")
        if os.path.exists(report_path):
            try:
                with open(report_path) as _rf:
                    _report = json.load(_rf)
            except Exception:
                _report = {}
        else:
            _report = {}
        _report.setdefault("transform_diagnostics", {})[exc.material] = (
            exc.stage_counts
        )
        _report["transform_error"] = {
            "material": exc.material,
            "dropped_at_stage": exc.dropped_at_stage,
            "stage_counts": exc.stage_counts,
        }
        with open(report_path, "w") as _rf:
            json.dump(_report, _rf, indent=2)
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(3)

    # Extract smoothed contours
    contours = extract_contours(masks, min_area_px=args.min_area)

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
        "graphite": "bottom_part 鈫?full_stack via inverted warp_sift_bottom",
        "graphene": "top_part(mirrored) 鈫?full_stack via warp_top (direct)",
        "bottom_hBN": "already in full_stack coords (pass-through)",
        "top_hBN": "already in full_stack coords (= footprint, pass-through)",
    }
    # Per-stage pixel counts for each material (issue #33). Lets the
    # orchestrator distinguish "detect produced nothing" from "transform
    # zeroed a valid input at stage X" without re-running the pipeline.
    report["transform_diagnostics"] = stage_counts
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
