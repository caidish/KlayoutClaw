#!/usr/bin/env python
"""Detect lithographic marker pairs in a microscope image.

Uses multi-method template matching (grayscale, edge-based, CLAHE, inverted)
with geometric consistency filtering to robustly locate marker pairs even
when some are occluded by flakes or have inverted contrast.

Usage:
    python detect_markers.py --image full_stack_raw.jpg \
        --pixel-size 0.087 --gds-markers gds_markers.json \
        --output-dir output/gdsalign/
"""
import argparse
import itertools
import json
import os
import sys

import cv2
import numpy as np


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------

def render_pair_template(pair, pixel_size, padding_px=10):
    """Render a binary template for a marker pair (white squares on black).

    Parameters
    ----------
    pair : dict
        A pair entry from gds_markers.json with two marker bboxes.
    pixel_size : float
        Microscope pixel size in um/px.
    padding_px : int
        Padding around the pair in pixels.

    Returns
    -------
    template : ndarray (uint8)
        Binary image with white squares on black background.
    """
    markers = pair["markers"]

    # Compute overall bbox of the pair in um
    all_coords = []
    for m in markers:
        bb = m["bbox"]
        all_coords.append(bb[0])
        all_coords.append(bb[1])
    all_coords = np.array(all_coords)
    pair_min = all_coords.min(axis=0)  # [x_min, y_min] in um
    pair_max = all_coords.max(axis=0)  # [x_max, y_max] in um

    extent_um = pair_max - pair_min  # [width, height] in um
    extent_px = np.ceil(extent_um / pixel_size).astype(int)

    w = extent_px[0] + 2 * padding_px
    h = extent_px[1] + 2 * padding_px
    template = np.zeros((h, w), dtype=np.uint8)

    for m in markers:
        bb = m["bbox"]
        # Convert to pixel coords relative to pair_min, with padding offset
        x0 = int(round((bb[0][0] - pair_min[0]) / pixel_size)) + padding_px
        y0 = int(round((bb[0][1] - pair_min[1]) / pixel_size)) + padding_px
        x1 = int(round((bb[1][0] - pair_min[0]) / pixel_size)) + padding_px
        y1 = int(round((bb[1][1] - pair_min[1]) / pixel_size)) + padding_px

        # In image coords, y increases downward but GDS y increases upward.
        # We flip y: image_y = (h - 1) - gds_y_px
        iy0 = (h - 1) - y1
        iy1 = (h - 1) - y0
        # Ensure correct order
        px_left = min(x0, x1)
        px_right = max(x0, x1)
        px_top = min(iy0, iy1)
        px_bot = max(iy0, iy1)
        cv2.rectangle(template, (px_left, px_top), (px_right, px_bot), 255, -1)

    return template


# ---------------------------------------------------------------------------
# Multi-scale, multi-rotation template matching
# ---------------------------------------------------------------------------

def rotate_template(template, angle_deg):
    """Rotate template by angle_deg around its center, padding to fit."""
    h, w = template.shape[:2]
    cx, cy = w / 2, h / 2
    M = cv2.getRotationMatrix2D((cx, cy), angle_deg, 1.0)
    # Compute new bounding size
    cos_a = abs(M[0, 0])
    sin_a = abs(M[0, 1])
    nw = int(w * cos_a + h * sin_a) + 2
    nh = int(h * cos_a + w * sin_a) + 2
    M[0, 2] += (nw - w) / 2
    M[1, 2] += (nh - h) / 2
    rotated = cv2.warpAffine(template, M, (nw, nh),
                             flags=cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_CONSTANT,
                             borderValue=0)
    return rotated


def scale_template(template, scale):
    """Resize template by scale factor."""
    h, w = template.shape[:2]
    nw = max(int(round(w * scale)), 1)
    nh = max(int(round(h * scale)), 1)
    return cv2.resize(template, (nw, nh), interpolation=cv2.INTER_LINEAR)


def find_peaks(corr_map, threshold, min_dist):
    """Find local maxima in correlation map above threshold."""
    mask = (corr_map >= threshold).astype(np.uint8)
    if mask.sum() == 0:
        return []

    kernel_size = max(3, int(min_dist) | 1)  # must be odd
    dilated = cv2.dilate(corr_map, np.ones((kernel_size, kernel_size)),
                         iterations=1)
    local_max = (corr_map == dilated) & (corr_map >= threshold)

    ys, xs = np.where(local_max)
    scores = corr_map[ys, xs]
    return list(zip(xs.tolist(), ys.tolist(), scores.tolist()))


def sweep_match(image, base_template, rotations, scales, threshold,
                pair_diam_px, method_name="gray"):
    """Run multi-rotation, multi-scale template matching.

    Returns list of detection dicts.
    """
    detections = []
    min_dist = max(5, pair_diam_px // 3)

    for rot in rotations:
        rot_tmpl = rotate_template(base_template, rot)
        for sc in scales:
            tmpl = scale_template(rot_tmpl, sc)
            th, tw = tmpl.shape[:2]
            if th >= image.shape[0] or tw >= image.shape[1]:
                continue
            tmpl_u8 = tmpl.astype(np.uint8)
            corr = cv2.matchTemplate(image, tmpl_u8, cv2.TM_CCOEFF_NORMED)
            peaks = find_peaks(corr, threshold, min_dist)
            for px, py, score in peaks:
                cx = px + tw / 2
                cy = py + th / 2
                detections.append({
                    "x": cx,
                    "y": cy,
                    "score": float(score),
                    "rotation_deg": float(rot),
                    "scale": float(sc),
                    "method": method_name,
                })
    return detections


# ---------------------------------------------------------------------------
# Multi-method preprocessing
# ---------------------------------------------------------------------------

def make_edge_image(gray, low=30, high=100, dilate_k=3):
    """Canny edge detection with optional dilation for template matching."""
    edges = cv2.Canny(gray, low, high)
    if dilate_k > 0:
        edges = cv2.dilate(edges, np.ones((dilate_k, dilate_k), np.uint8),
                           iterations=1)
    return edges


def make_edge_template(template, low=30, high=100, dilate_k=3):
    """Convert binary template to edge template."""
    edges = cv2.Canny(template, low, high)
    if dilate_k > 0:
        edges = cv2.dilate(edges, np.ones((dilate_k, dilate_k), np.uint8),
                           iterations=1)
    return edges


# ---------------------------------------------------------------------------
# Non-maximum suppression
# ---------------------------------------------------------------------------

def nms(detections, min_dist):
    """Greedy non-maximum suppression by score."""
    if not detections:
        return []
    dets = sorted(detections, key=lambda d: d["score"], reverse=True)
    kept = []
    for d in dets:
        too_close = False
        for k in kept:
            dx = d["x"] - k["x"]
            dy = d["y"] - k["y"]
            if (dx * dx + dy * dy) < min_dist * min_dist:
                too_close = True
                break
        if not too_close:
            kept.append(d)
    return kept


# ---------------------------------------------------------------------------
# Geometric consistency filtering
# ---------------------------------------------------------------------------

def compute_gds_spacing(pairs):
    """Compute pairwise distances between GDS marker pair centers.

    Returns sorted array of 6 pairwise distances (for 4 markers).
    """
    centers = np.array([p["center_um"] for p in pairs])
    n = len(centers)
    dists = []
    for i, j in itertools.combinations(range(n), 2):
        d = np.sqrt(((centers[i] - centers[j]) ** 2).sum())
        dists.append(d)
    return np.sort(dists)


def score_square(pts):
    """Score how well 4 points form a square.

    Returns (score, side_length) where lower score = better square.
    For a perfect square: 4 equal sides, 2 equal diagonals, ratio = sqrt(2).
    """
    dists = []
    for i, j in itertools.combinations(range(4), 2):
        d = np.sqrt(((pts[i] - pts[j]) ** 2).sum())
        dists.append(d)
    dists = sorted(dists)

    sides = dists[:4]
    diags = dists[4:]

    if min(sides) < 1:
        return float('inf'), 0

    side_cv = np.std(sides) / np.mean(sides)
    diag_cv = np.std(diags) / np.mean(diags) if np.mean(diags) > 0 else 1.0
    ratio = np.mean(diags) / np.mean(sides)
    ratio_err = abs(ratio - np.sqrt(2))

    return side_cv + diag_cv + ratio_err, np.mean(sides)


def score_right_triangle(pts):
    """Score how well 3 points form a right isosceles triangle.

    For 3 corners of a square: two equal legs, hypotenuse = sqrt(2) * leg.
    Returns (score, leg_length).
    """
    dists = []
    for i, j in itertools.combinations(range(3), 2):
        d = np.sqrt(((pts[i] - pts[j]) ** 2).sum())
        dists.append(d)
    dists = sorted(dists)

    legs = dists[:2]
    hyp = dists[2]

    if min(legs) < 1:
        return float('inf'), 0

    leg_cv = np.std(legs) / np.mean(legs)
    ratio = hyp / np.mean(legs)
    ratio_err = abs(ratio - np.sqrt(2))

    return leg_cv + ratio_err, np.mean(legs)


def find_geometric_subset(detections, gds_pairs, pixel_size, n_target=4):
    """Find detections that form a pattern consistent with GDS markers.

    The GDS markers form a square. After similarity transform (rotation +
    scale + reflection), image markers should also form a square. We find
    the subset of detections that best matches this geometric constraint.

    Parameters
    ----------
    detections : list of dicts with 'x', 'y' keys (pixel coords)
    gds_pairs : list of GDS pair dicts with 'center_um'
    pixel_size : float
    n_target : int
        Target number of markers (4).

    Returns
    -------
    list of detection dicts forming the best geometric match.
    """
    if len(detections) <= n_target:
        return detections

    # Cap candidates to top 25 by score to keep C(n,4) tractable
    MAX_CANDIDATES = 25
    if len(detections) > MAX_CANDIDATES:
        detections = sorted(detections, key=lambda d: d["score"],
                            reverse=True)[:MAX_CANDIDATES]

    positions = np.array([[d["x"], d["y"]] for d in detections])
    n = len(detections)

    # Compute expected spacing from GDS (in pixels, approximate)
    gds_dists = compute_gds_spacing(gds_pairs)
    gds_side = gds_dists[0]  # shortest distance = side of square

    # Expected side in pixels (range accounts for unknown scale)
    # Allow wide range since we don't know the exact pixel_size
    min_side_px = gds_side / pixel_size * 0.5
    max_side_px = gds_side / pixel_size * 2.0

    # Try 4-point square subsets
    best_score = float('inf')
    best_combo = None

    for combo in itertools.combinations(range(n), min(n_target, 4)):
        pts = positions[list(combo)]
        sq_score, side = score_square(pts)
        # Check that side length is plausible
        if side < min_side_px or side > max_side_px:
            continue
        if sq_score < best_score:
            best_score = sq_score
            best_combo = combo

    if best_combo is not None and best_score < 0.15:
        result = [detections[i] for i in best_combo]
        print(f"Geometric filter (4-square): score={best_score:.4f}, "
              f"selected {len(result)} from {n} candidates")
        return result

    # Fallback: try 3-point right triangle
    best_score_3 = float('inf')
    best_combo_3 = None

    for combo in itertools.combinations(range(n), 3):
        pts = positions[list(combo)]
        tri_score, leg = score_right_triangle(pts)
        if leg < min_side_px or leg > max_side_px:
            continue
        if tri_score < best_score_3:
            best_score_3 = tri_score
            best_combo_3 = combo

    if best_combo_3 is not None and best_score_3 < 0.15:
        result = [detections[i] for i in best_combo_3]
        print(f"Geometric filter (3-triangle): score={best_score_3:.4f}, "
              f"selected {len(result)} from {n} candidates")
        return result

    # Final fallback: return top detections by score
    top = sorted(detections, key=lambda d: d["score"], reverse=True)
    result = top[:n_target]
    print(f"Geometric filter: no consistent pattern found, "
          f"returning top {len(result)} by score")
    return result


# ---------------------------------------------------------------------------
# Diagnostic outputs
# ---------------------------------------------------------------------------

def save_template_diagnostic(base_template, edge_template, rotations,
                             output_dir):
    """Save 01_template.png showing both binary and edge templates."""
    n = len(rotations)
    templates_bin = []
    templates_edge = []
    max_h, max_w = 0, 0
    for rot in rotations:
        t_bin = rotate_template(base_template, rot)
        t_edge = rotate_template(edge_template, rot)
        templates_bin.append(t_bin)
        templates_edge.append(t_edge)
        max_h = max(max_h, t_bin.shape[0])
        max_w = max(max_w, t_bin.shape[1])

    pad = 10
    canvas_w = n * (max_w + pad) + pad
    canvas_h = 2 * max_h + 3 * pad + 30
    canvas = np.zeros((canvas_h, canvas_w), dtype=np.uint8)

    for i, (t_bin, t_edge, rot) in enumerate(
            zip(templates_bin, templates_edge, rotations)):
        th, tw = t_bin.shape[:2]
        x_off = pad + i * (max_w + pad) + (max_w - tw) // 2
        # Binary template row
        y_off = pad + (max_h - th) // 2
        canvas[y_off:y_off + th, x_off:x_off + tw] = t_bin
        # Edge template row
        y_off2 = 2 * pad + max_h + (max_h - th) // 2
        canvas[y_off2:y_off2 + th, x_off:x_off + tw] = t_edge

    canvas_c = cv2.cvtColor(canvas, cv2.COLOR_GRAY2BGR)
    for i, rot in enumerate(rotations):
        x_off = pad + i * (max_w + pad)
        cv2.putText(canvas_c, f"{int(rot)}deg", (x_off, canvas_h - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)

    path = os.path.join(output_dir, "01_template.png")
    cv2.imwrite(path, canvas_c)
    print(f"Saved {path}")


def save_detections_diagnostic(image, all_candidates, final_detections,
                               pair_diam_px, output_dir):
    """Save 03_detections.png with final markers and candidate overlay."""
    vis = image.copy()
    if len(vis.shape) == 2:
        vis = cv2.cvtColor(vis, cv2.COLOR_GRAY2BGR)
    radius = int(pair_diam_px / 2)

    # Draw all candidates as small gray circles
    for det in all_candidates:
        cx = int(round(det["x"]))
        cy = int(round(det["y"]))
        cv2.circle(vis, (cx, cy), radius // 2, (128, 128, 128), 1)

    # Draw final detections as colored circles
    colors = [
        (0, 255, 0),    # green
        (0, 255, 255),  # yellow
        (255, 0, 0),    # blue
        (255, 0, 255),  # magenta
    ]
    for i, det in enumerate(final_detections):
        cx = int(round(det["x"]))
        cy = int(round(det["y"]))
        color = colors[i % len(colors)]
        cv2.circle(vis, (cx, cy), radius, color, 2)
        method = det.get("method", "?")
        label = f"#{i+1} s={det['score']:.2f} [{method}]"
        cv2.putText(vis, label, (cx + radius + 5, cy),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

    path = os.path.join(output_dir, "03_detections.png")
    cv2.imwrite(path, vis)
    print(f"Saved {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Detect marker pairs in microscope image")
    parser.add_argument("--image", required=True, help="Microscope image path")
    parser.add_argument("--pixel-size", required=True, type=float,
                        help="Pixel size in um/px")
    parser.add_argument("--gds-markers", required=True,
                        help="Path to gds_markers.json")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    args = parser.parse_args()

    # Validate inputs
    if not os.path.exists(args.image):
        print(f"ERROR: Image not found: {args.image}", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(args.gds_markers):
        print(f"ERROR: gds_markers.json not found: {args.gds_markers}",
              file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)
    pixel_size = args.pixel_size

    # Load GDS markers
    with open(args.gds_markers) as f:
        gds_data = json.load(f)

    pairs = gds_data["pairs"]
    if not pairs:
        print("ERROR: No marker pairs in gds_markers.json", file=sys.stderr)
        sys.exit(1)

    # Render templates
    base_template = render_pair_template(pairs[0], pixel_size, padding_px=10)
    edge_template = make_edge_template(base_template)
    pair_diam_px = 6.0 / pixel_size  # ~69 px for 0.087 um/px

    # Load image
    image = cv2.imread(args.image)
    if image is None:
        print(f"ERROR: Could not load image: {args.image}", file=sys.stderr)
        sys.exit(1)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Prepare preprocessed images
    inverted = cv2.bitwise_not(gray)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    gray_clahe = clahe.apply(gray)
    edges_img = make_edge_image(gray)

    # Sweep parameters
    rotations = [0, 90, 180, 270]
    scales = [0.8, 0.9, 1.0, 1.1, 1.2]

    # Save template diagnostic
    save_template_diagnostic(base_template, edge_template, rotations,
                             args.output_dir)

    # --- Multi-method matching ---
    all_raw = []
    threshold = 0.35

    # Method 1: Standard grayscale
    raw_gray = sweep_match(gray, base_template, rotations, scales, threshold,
                           pair_diam_px, method_name="gray")
    all_raw.extend(raw_gray)
    print(f"  gray: {len(raw_gray)} raw detections")

    # Method 2: Inverted grayscale (catches dark-on-light markers)
    raw_inv = sweep_match(inverted, base_template, rotations, scales, threshold,
                          pair_diam_px, method_name="inverted")
    all_raw.extend(raw_inv)
    print(f"  inverted: {len(raw_inv)} raw detections")

    # Method 3: CLAHE enhanced
    raw_clahe = sweep_match(gray_clahe, base_template, rotations, scales,
                            threshold, pair_diam_px, method_name="clahe")
    all_raw.extend(raw_clahe)
    print(f"  clahe: {len(raw_clahe)} raw detections")

    # Method 4: Edge-based (polarity invariant)
    raw_edge = sweep_match(edges_img, edge_template, rotations, scales,
                           threshold, pair_diam_px, method_name="edge")
    all_raw.extend(raw_edge)
    print(f"  edge: {len(raw_edge)} raw detections")

    print(f"Total raw: {len(all_raw)}")

    # NMS across all methods
    candidates = nms(all_raw, pair_diam_px)
    print(f"After NMS: {len(candidates)} candidates")

    # If very few candidates, try lower threshold on edges only
    if len(candidates) < 4:
        print("Too few candidates, retrying edges at threshold 0.2...")
        raw_edge_low = sweep_match(edges_img, edge_template, rotations, scales,
                                   0.2, pair_diam_px, method_name="edge_low")
        all_raw.extend(raw_edge_low)
        candidates = nms(all_raw, pair_diam_px)
        print(f"After NMS (with low-threshold edges): {len(candidates)}")

    # --- Geometric consistency filtering ---
    if len(candidates) >= 4:
        final_detections = find_geometric_subset(
            candidates, pairs, pixel_size, n_target=4)
    else:
        final_detections = candidates
        print(f"Only {len(candidates)} candidates, skipping geometric filter")

    # Save detections diagnostic
    save_detections_diagnostic(image, candidates, final_detections,
                               pair_diam_px, args.output_dir)

    # Build output
    output_detections = []
    for det in final_detections:
        output_detections.append({
            "center_px": [det["x"], det["y"]],
            "center_um": [det["x"] * pixel_size, det["y"] * pixel_size],
            "score": det["score"],
            "rotation_deg": det["rotation_deg"],
            "scale": det.get("scale", 1.0),
            "method": det.get("method", "unknown"),
        })

    report = {
        "status": "complete",
        "source_image": os.path.basename(args.image),
        "pixel_size_um": pixel_size,
        "n_candidates": len(candidates),
        "detections": output_detections,
    }

    out_path = os.path.join(args.output_dir, "image_markers.json")
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"Wrote {out_path} with {len(output_detections)} detections")

    if not final_detections:
        print("ERROR: No markers detected", file=sys.stderr)
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
