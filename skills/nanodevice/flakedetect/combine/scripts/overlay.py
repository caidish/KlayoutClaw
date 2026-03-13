#!/usr/bin/env python
"""Draw material contour overlays on raw and LUT images.

Reads traces.json (all contours in full_stack pixel coordinates) and draws
coloured contours on desaturated background images. For LUT overlays, reads
the raw→LUT spatial shift from combine_report.json.

Usage:
    conda run -n base python overlay.py \
        --traces combine/traces.json \
        --raw full_stack_raw.jpg \
        --lut full_stack_w_LUT.jpg \
        --combine-report combine/combine_report.json \
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

from core import desaturate, MATERIAL_COLORS


def draw_overlay(image, traces, thickness=2, label=True):
    """Draw material contours on a desaturated image.

    Args:
        image: BGR image (uint8).
        traces: Traces dict with materials → contour_px.
        thickness: Contour line thickness.
        label: If True, draw material name labels near centroids.

    Returns:
        Annotated BGR image (uint8).
    """
    canvas = desaturate(image, factor=0.4)

    for material, entries in traces.get("materials", {}).items():
        color = MATERIAL_COLORS.get(material, (200, 200, 200))
        for entry in entries:
            pts_px = np.array(entry["contour_px"], dtype=np.int32).reshape(-1, 1, 2)
            cv2.drawContours(canvas, [pts_px], -1, color, thickness)

            if label and len(pts_px) > 0:
                cx = int(np.mean(pts_px[:, 0, 0]))
                cy = int(np.mean(pts_px[:, 0, 1]))
                cv2.putText(
                    canvas, material,
                    (cx - 30, cy - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1,
                    cv2.LINE_AA,
                )

    return canvas


def draw_overlay_on_lut(lut_image, traces, dx, dy, thickness=2):
    """Draw contours on the LUT image, shifting by (dx, dy).

    Args:
        lut_image: BGR LUT image (uint8).
        traces: Traces dict with contours in raw coordinates.
        dx: Horizontal shift raw → LUT (pixels).
        dy: Vertical shift raw → LUT (pixels).
        thickness: Contour line thickness.

    Returns:
        Annotated BGR image (uint8).
    """
    canvas = desaturate(lut_image, factor=0.4)

    for material, entries in traces.get("materials", {}).items():
        color = MATERIAL_COLORS.get(material, (200, 200, 200))
        for entry in entries:
            pts_raw = np.array(entry["contour_px"], dtype=np.float64)
            pts_lut = pts_raw + np.array([dx, dy])
            pts_lut = np.round(pts_lut).astype(np.int32).reshape(-1, 1, 2)
            cv2.drawContours(canvas, [pts_lut], -1, color, thickness)

    return canvas


def build_mask_composite(traces, image_size):
    """Create a colour-coded mask composite of all materials.

    Each material region is filled with its BGR colour at 50% alpha.

    Args:
        traces: Traces dict.
        image_size: Tuple (width, height).

    Returns:
        BGR image (uint8).
    """
    w, h = image_size
    canvas = np.zeros((h, w, 3), dtype=np.uint8)

    for material, entries in traces.get("materials", {}).items():
        color = MATERIAL_COLORS.get(material, (200, 200, 200))
        material_mask = np.zeros((h, w), dtype=np.uint8)
        for entry in entries:
            pts_px = np.array(entry["contour_px"], dtype=np.int32).reshape(-1, 1, 2)
            cv2.drawContours(material_mask, [pts_px], -1, 255, cv2.FILLED)

        # Apply at 50% alpha
        overlay = np.zeros((h, w, 3), dtype=np.uint8)
        overlay[material_mask > 0] = color
        canvas = cv2.addWeighted(canvas, 1.0, overlay, 0.5, 0)

    return canvas


def main():
    parser = argparse.ArgumentParser(
        description="Draw material contour overlays on raw and LUT images"
    )
    parser.add_argument("--traces", required=True,
                        help="Path to traces.json")
    parser.add_argument("--raw", required=True,
                        help="Full stack raw image")
    parser.add_argument("--lut", default=None,
                        help="Full stack LUT image (optional)")
    parser.add_argument("--combine-report", default=None,
                        help="Path to combine_report.json (for raw→LUT dx/dy)")
    parser.add_argument("--output-dir", required=True,
                        help="Output directory")
    args = parser.parse_args()

    # Load traces
    with open(os.path.abspath(args.traces)) as f:
        traces = json.load(f)

    os.makedirs(args.output_dir, exist_ok=True)

    # Raw overlay
    raw_img = cv2.imread(os.path.abspath(args.raw))
    if raw_img is None:
        print(f"ERROR: Cannot read raw image: {args.raw}", file=sys.stderr)
        sys.exit(1)

    h, w = raw_img.shape[:2]
    image_size = (w, h)

    overlay_raw = draw_overlay(raw_img, traces)
    raw_out = os.path.join(args.output_dir, "overlay_raw.png")
    cv2.imwrite(raw_out, overlay_raw)
    print(f"  Raw overlay: {raw_out}")

    # LUT overlay (optional)
    if args.lut:
        lut_img = cv2.imread(os.path.abspath(args.lut))
        if lut_img is not None:
            dx, dy = 0.0, 0.0
            if args.combine_report and os.path.exists(args.combine_report):
                with open(args.combine_report) as f:
                    report = json.load(f)
                raw2lut = report.get("raw2lut", {})
                dx = raw2lut.get("dx", 0.0)
                dy = raw2lut.get("dy", 0.0)

            overlay_lut = draw_overlay_on_lut(lut_img, traces, dx, dy)
            lut_out = os.path.join(args.output_dir, "overlay_lut.png")
            cv2.imwrite(lut_out, overlay_lut)
            print(f"  LUT overlay: {lut_out}")
        else:
            print(f"WARNING: Cannot read LUT image: {args.lut}", file=sys.stderr)

    # Mask composite
    composite = build_mask_composite(traces, image_size)
    composite_out = os.path.join(args.output_dir, "mask_composite.png")
    cv2.imwrite(composite_out, composite)
    print(f"  Mask composite: {composite_out}")

    # Update combine_report.json with overlay_files section
    report_path = os.path.join(args.output_dir, "combine_report.json")
    if os.path.exists(report_path):
        with open(report_path) as f:
            report = json.load(f)
    else:
        report = {}

    report["overlay_files"] = {
        "raw": "overlay_raw.png",
        "lut": "overlay_lut.png" if args.lut else None,
        "composite": "mask_composite.png",
    }

    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"OK: overlays complete")


if __name__ == "__main__":
    main()
