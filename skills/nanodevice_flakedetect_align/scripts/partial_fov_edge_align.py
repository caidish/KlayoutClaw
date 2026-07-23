#!/usr/bin/env python
"""Partial-FOV edge/corner fallback alignment.

Use when source_mask and footprint_mask are different visible crops of the
same flake, so full-mask IoU drives refine toward an implausible scale.
The script aligns two visible non-border edge pairs instead of inventing a
complete flake outline.
"""

import argparse
import itertools
import json
import math
import os
import sys

import cv2
import numpy as np

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "nanodevice_flakedetect",
        "scripts",
    ),
)
from core import desaturate, warp_contour  # noqa: E402


def as_points(contour):
    arr = np.asarray(contour, dtype=np.float64)
    return arr.reshape(-1, 2)


def simplify_contour(contour, epsilon_frac=0.004):
    pts = as_points(contour).reshape(-1, 1, 2).astype(np.float32)
    peri = cv2.arcLength(pts, True)
    epsilon = max(2.0, epsilon_frac * peri)
    return cv2.approxPolyDP(pts, epsilon, True).reshape(-1, 2).astype(np.float64)


def is_border_edge(p0, p1, width, height, margin):
    return (
        abs(p0[0]) <= margin and abs(p1[0]) <= margin
        or abs(p0[0] - (width - 1)) <= margin and abs(p1[0] - (width - 1)) <= margin
        or abs(p0[1]) <= margin and abs(p1[1]) <= margin
        or abs(p0[1] - (height - 1)) <= margin and abs(p1[1] - (height - 1)) <= margin
    )


def segment_support(contour_pts, p0, p1, max_dist_px=5.0):
    """Return how much original contour evidence supports a simplified edge.

    approxPolyDP can create a long chord across a concave bite or missing FOV
    region. Such a chord has endpoints on the contour but very few contour
    samples along the segment. A real flake edge has many boundary samples
    close to the segment.
    """
    vec = p1 - p0
    length = float(np.linalg.norm(vec))
    if length < 1e-9:
        return 0.0, 0.0, 0
    unit = vec / length
    rel = contour_pts - p0
    t = rel @ unit
    in_span = (t >= 0) & (t <= length)
    if not np.any(in_span):
        return 0.0, 0.0, 0
    perp = rel - np.outer(t, unit)
    dist = np.linalg.norm(perp, axis=1)
    supported = in_span & (dist <= max_dist_px)
    supported_t = t[supported]
    count = int(supported_t.size)
    if count == 0:
        return 0.0, 0.0, 0
    coverage = float((supported_t.max() - supported_t.min()) / max(length, 1e-9))
    density = float(count / max(length, 1e-9))
    return coverage, density, count


def extract_edges(contour, width, height, min_len_px, border_margin, min_support_density=0.12):
    contour_pts = as_points(contour)
    poly = simplify_contour(contour)
    edges = []
    for i in range(len(poly)):
        p0 = poly[i]
        p1 = poly[(i + 1) % len(poly)]
        vec = p1 - p0
        length = float(np.linalg.norm(vec))
        if length < min_len_px:
            continue
        if is_border_edge(p0, p1, width, height, border_margin):
            continue
        support_coverage, support_density, support_count = segment_support(contour_pts, p0, p1)
        if support_density < min_support_density:
            continue
        edges.append(
            {
                "idx": int(i),
                "p0": p0,
                "p1": p1,
                "unit": vec / max(length, 1e-9),
                "length": length,
                "support_coverage": support_coverage,
                "support_density": support_density,
                "support_count": support_count,
            }
        )
    return edges


def line_intersection(e1, e2):
    p = e1["p0"]
    r = e1["p1"] - e1["p0"]
    q = e2["p0"]
    s = e2["p1"] - e2["p0"]
    den = r[0] * s[1] - r[1] * s[0]
    if abs(den) < 1e-6:
        return None
    t = ((q - p)[0] * s[1] - (q - p)[1] * s[0]) / den
    return p + t * r


def edge_angle(e1, e2):
    dot = float(np.clip(abs(np.dot(e1["unit"], e2["unit"])), 0.0, 1.0))
    return math.degrees(math.acos(dot))


def ranked_pairs(edges, width, height):
    pairs = []
    pad = max(width, height) * 0.25
    for e1, e2 in itertools.combinations(edges, 2):
        corner = line_intersection(e1, e2)
        if corner is None:
            continue
        if not (-pad <= corner[0] <= width + pad and -pad <= corner[1] <= height + pad):
            continue
        angle = edge_angle(e1, e2)
        if angle < 25 or angle > 155:
            continue
        support = min(e1.get("support_density", 1.0), e2.get("support_density", 1.0))
        score = e1["length"] * e2["length"] * math.sin(math.radians(angle)) * support
        pairs.append(
            {
                "edge1": e1,
                "edge2": e2,
                "corner": corner,
                "angle_deg": float(angle),
                "score": float(score),
            }
        )
    pairs.sort(key=lambda p: p["score"], reverse=True)
    return pairs


def choose_pair(edges, width, height, rank=0):
    pairs = ranked_pairs(edges, width, height)
    if not pairs:
        return None, []
    idx = min(max(int(rank), 0), len(pairs) - 1)
    return pairs[idx], pairs


def pair_match_candidates(source_pairs, footprint_pairs, source_mask, footprint_mask,
                          source_contour, footprint_contour, pixel_size,
                          scale_min, scale_max, top_k=20):
    """Jointly rank source/footprint edge-pair matches.

    Independent "longest pair" ranking is often wrong in partial-FOV cases:
    the longest two source edges and the longest two footprint edges may not be
    the same physical flake edges. This joint ranking prefers edge pairs with
    similar included angles, a near-1 scale, consistent edge-length ratios, and
    reasonable overlap metrics after the induced transform.
    """
    matches = []
    for si, sp in enumerate(source_pairs[:top_k]):
        for fi, fp in enumerate(footprint_pairs[:top_k]):
            angle_diff = abs(sp["angle_deg"] - fp["angle_deg"])
            if angle_diff > 35:
                continue
            m, transform = build_warp(sp, fp, scale_min, scale_max)
            metrics, _ = evaluate(
                m, source_mask, footprint_mask,
                source_contour, footprint_contour, pixel_size
            )
            scale = max(transform["scale"], 1e-6)
            raw_scale = max(transform["raw_edge_length_scale"], 1e-6)
            scale_penalty = abs(math.log(scale))
            raw_scale_penalty = abs(math.log(raw_scale))
            score = (
                2.0 * (1.0 - metrics["iou"])
                + 1.5 * metrics["outside_fraction"]
                + 1.2 * scale_penalty
                + 0.8 * raw_scale_penalty
                + 0.015 * angle_diff
                + 0.5 * transform["vector_alignment_error"]
            )
            matches.append({
                "source_pair_rank": si,
                "footprint_pair_rank": fi,
                "source_pair": sp,
                "footprint_pair": fp,
                "transform": transform,
                "metrics": metrics,
                "score": float(score),
            })
    matches.sort(key=lambda x: x["score"])
    return matches


def mask_centroid_xy(mask):
    ys, xs = np.nonzero(mask > 0)
    if xs.size == 0:
        h, w = mask.shape[:2]
        return np.array([w / 2.0, h / 2.0], dtype=np.float64)
    return np.array([float(xs.mean()), float(ys.mean())], dtype=np.float64)


def signed_line_side(edge, points):
    vec = edge["p1"] - edge["p0"]
    rel = points - edge["p0"]
    return vec[0] * rel[..., 1] - vec[1] * rel[..., 0]


def wedge_mask_for_pair(pair, mask, margin_px=2.0, vicinity_px=120):
    """Fill the interior wedge made by extending two edges.

    The interior side is inferred from the source mask centroid. This avoids
    blindly filling the wrong side of two extended lines.
    """
    h, w = mask.shape[:2]
    centroid = mask_centroid_xy(mask)
    yy, xx = np.mgrid[0:h, 0:w]
    pts = np.stack([xx.astype(np.float64), yy.astype(np.float64)], axis=-1)
    inside = np.ones((h, w), dtype=bool)
    for edge in (pair["edge1"], pair["edge2"]):
        cside = signed_line_side(edge, centroid.reshape(1, 2))[0]
        if abs(cside) < 1e-6:
            cside = 1.0
        side = signed_line_side(edge, pts)
        inside &= (np.sign(cside) * side) >= -margin_px
    wedge = (inside.astype(np.uint8) * 255)
    # Keep the extended-edge fill local to the observed flake/footprint.
    # Without this, two half-planes produce huge image-wide wedges whose IoU is
    # artificially high but physically meaningless.
    k = max(3, int(vicinity_px) | 1)
    vicinity = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    wedge = cv2.bitwise_and(wedge, vicinity)
    wedge = cv2.morphologyEx(wedge, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)))
    wedge = cv2.morphologyEx(wedge, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    return wedge


def wedge_support_metrics(wedge, mask):
    wedge_area = max(int((wedge > 0).sum()), 1)
    mask_area = max(int((mask > 0).sum()), 1)
    inter = int(((wedge > 0) & (mask > 0)).sum())
    return {
        "wedge_area_px": int(wedge_area),
        "mask_overlap_px": int(inter),
        "wedge_inside_mask_fraction": float(inter / wedge_area),
        "mask_covered_fraction": float(inter / mask_area),
    }


def build_wedge_candidates(pairs, mask, max_candidates=40):
    candidates = []
    for rank, pair in enumerate(pairs[:max_candidates]):
        wedge = wedge_mask_for_pair(pair, mask)
        support = wedge_support_metrics(wedge, mask)
        # Reject nearly whole-frame or tiny wedges: they are usually unhelpful
        # half-plane artifacts rather than a local flake corner.
        image_area = mask.shape[0] * mask.shape[1]
        area_frac = support["wedge_area_px"] / max(image_area, 1)
        if area_frac < 0.015 or area_frac > 0.75:
            continue
        if support["wedge_inside_mask_fraction"] < 0.10:
            continue
        cand = {
            "rank": int(rank),
            "pair": pair,
            "wedge": wedge,
            "support": support,
            "area_frac": float(area_frac),
        }
        candidates.append(cand)
    return candidates


def evaluate_wedge_transform(m, source_wedge, footprint_wedge, source_mask,
                             footprint_mask, source_contour, footprint_contour,
                             pixel_size):
    h, w = footprint_wedge.shape[:2]
    warped_wedge = cv2.warpAffine(source_wedge, m, (w, h), flags=cv2.INTER_NEAREST)
    inter = int(((warped_wedge > 0) & (footprint_wedge > 0)).sum())
    union = max(int(((warped_wedge > 0) | (footprint_wedge > 0)).sum()), 1)
    wedge_iou = float(inter / union)
    wedge_outside = float(((warped_wedge > 0) & (footprint_wedge == 0)).sum() / max((warped_wedge > 0).sum(), 1))
    full_metrics, warped_mask = evaluate(
        m, source_mask, footprint_mask, source_contour, footprint_contour, pixel_size
    )
    return {
        "wedge_iou": wedge_iou,
        "wedge_outside_fraction": wedge_outside,
        "full": full_metrics,
    }, warped_wedge, warped_mask


def wedge_match_candidates(source_wedges, footprint_wedges, source_mask,
                           footprint_mask, source_contour, footprint_contour,
                           pixel_size, scale_min, scale_max, top_k=30):
    matches = []
    for sw in source_wedges[:top_k]:
        sp = sw["pair"]
        for fw in footprint_wedges[:top_k]:
            fp = fw["pair"]
            angle_diff = abs(sp["angle_deg"] - fp["angle_deg"])
            if angle_diff > 35:
                continue
            m, transform = build_warp(sp, fp, scale_min, scale_max)
            metrics, warped_wedge, warped_mask = evaluate_wedge_transform(
                m, sw["wedge"], fw["wedge"], source_mask, footprint_mask,
                source_contour, footprint_contour, pixel_size
            )
            scale = max(transform["scale"], 1e-6)
            raw_scale = max(transform["raw_edge_length_scale"], 1e-6)
            scale_penalty = abs(math.log(scale))
            raw_scale_penalty = abs(math.log(raw_scale))
            full = metrics["full"]
            # Wedge/angle geometry is used to propose an affine transform.
            # The selected transform must be judged primarily on the ORIGINAL
            # complete masks, not on the local wedge. Otherwise a local corner
            # can look good while the full source flake is misplaced.
            score = (
                4.0 * (1.0 - full["iou"])
                + 5.0 * full["outside_fraction"]
                + 2.0 * (1.0 - full["top_containment"])
                + 1.0 * scale_penalty
                + 0.5 * raw_scale_penalty
                + 0.25 * (1.0 - metrics["wedge_iou"])
                + 0.10 * metrics["wedge_outside_fraction"]
                + 0.010 * angle_diff
                + 0.3 * transform["vector_alignment_error"]
            )
            matches.append({
                "source_wedge_rank": int(sw["rank"]),
                "footprint_wedge_rank": int(fw["rank"]),
                "source_wedge": sw,
                "footprint_wedge": fw,
                "source_pair": sp,
                "footprint_pair": fp,
                "matrix": m,
                "transform": transform,
                "metrics": metrics,
                "warped_wedge": warped_wedge,
                "warped_mask": warped_mask,
                "score": float(score),
            })
    matches.sort(key=lambda x: x["score"])
    return matches


def outward(edge, corner):
    d0 = edge["p0"] - corner
    d1 = edge["p1"] - corner
    d = d0 if np.linalg.norm(d0) > np.linalg.norm(d1) else d1
    n = np.linalg.norm(d)
    return d / n if n > 1e-9 else edge["unit"]


def fit_rotation(src_vecs, dst_vecs):
    src = np.vstack(src_vecs)
    dst = np.vstack(dst_vecs)
    h = src.T @ dst
    u, _, vt = np.linalg.svd(h)
    r = vt.T @ u.T
    if np.linalg.det(r) < 0:
        vt[-1, :] *= -1
        r = vt.T @ u.T
    return r


def build_warp(src_pair, dst_pair, scale_min, scale_max):
    src_vecs = [
        outward(src_pair["edge1"], src_pair["corner"]),
        outward(src_pair["edge2"], src_pair["corner"]),
    ]
    dst_base = [
        outward(dst_pair["edge1"], dst_pair["corner"]),
        outward(dst_pair["edge2"], dst_pair["corner"]),
    ]
    best = None
    for perm in ((0, 1), (1, 0)):
        dst_vecs = [dst_base[perm[0]], dst_base[perm[1]]]
        r = fit_rotation(src_vecs, dst_vecs)
        aligned = (r @ np.vstack(src_vecs).T).T
        err = float(np.mean([1.0 - abs(np.dot(aligned[i], dst_vecs[i])) for i in range(2)]))
        src_lengths = np.array([src_pair["edge1"]["length"], src_pair["edge2"]["length"]])
        dst_edges = [dst_pair["edge1"], dst_pair["edge2"]]
        dst_lengths = np.array([dst_edges[perm[0]]["length"], dst_edges[perm[1]]["length"]])
        raw_scale = float(np.median(dst_lengths / np.maximum(src_lengths, 1e-9)))
        scale = float(np.clip(raw_scale, scale_min, scale_max))
        if best is None or err < best["err"]:
            best = {"r": r, "err": err, "scale": scale, "raw_scale": raw_scale, "perm": perm}

    a = best["scale"] * best["r"]
    t = dst_pair["corner"] - a @ src_pair["corner"]
    m = np.array([[a[0, 0], a[0, 1], t[0]], [a[1, 0], a[1, 1], t[1]]], dtype=np.float64)
    return m, {
        "rotation_deg": float(math.degrees(math.atan2(a[1, 0], a[0, 0]))),
        "scale": float(best["scale"]),
        "raw_edge_length_scale": float(best["raw_scale"]),
        "vector_alignment_error": float(best["err"]),
        "edge_permutation": list(best["perm"]),
    }


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
        if len(fp_pts) > 1000:
            fp_pts = fp_pts[np.linspace(0, len(fp_pts) - 1, 1000, dtype=int)]
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


def full_mask_score(metrics, transform):
    scale = max(transform["scale"], 1e-6)
    raw_scale = max(transform["raw_edge_length_scale"], 1e-6)
    scale_penalty = abs(math.log(scale))
    raw_scale_penalty = abs(math.log(raw_scale))
    fwd = metrics.get("fwd_chamfer_mean_um")
    chamfer_penalty = 0.0 if fwd is None else min(float(fwd) / 20.0, 2.0)
    return float(
        4.0 * (1.0 - metrics["iou"])
        + 5.0 * metrics["outside_fraction"]
        + 2.0 * (1.0 - metrics["top_containment"])
        + 0.8 * chamfer_penalty
        + 1.0 * scale_penalty
        + 0.5 * raw_scale_penalty
    )


def rotation_from_matrix(m):
    a = np.asarray(m, dtype=np.float64)[:2, :2]
    scale = max(float(np.sqrt(a[0, 0] * a[0, 0] + a[1, 0] * a[1, 0])), 1e-9)
    return a / scale


def refine_similarity_grid(m, transform, src_anchor, dst_anchor, source_mask,
                           footprint_mask, source_contour, footprint_contour,
                           pixel_size, scale_min=0.85, scale_max=1.15,
                           scale_step=0.01, coarse_radius=0,
                           coarse_step=1, fine_radius=0, fine_step=1):
    """Search vertex-anchored homothety/similarity transforms.

    Keep the rotation inferred from the edge/wedge geometry and keep the matched
    vertex fixed: src_anchor maps exactly to dst_anchor. Then try only uniform
    scale values. This matches the physical intuition that once the corner and
    edge directions are known, the remaining degree of freedom is the distance
    from the vertex to points along the edges.
    """
    base = np.asarray(m, dtype=np.float64)
    rot = rotation_from_matrix(base)
    src_anchor = np.asarray(src_anchor, dtype=np.float64).reshape(2)
    dst_anchor = np.asarray(dst_anchor, dtype=np.float64).reshape(2)
    all_results = []

    def eval_candidate(scale, stage):
        mm = np.zeros((2, 3), dtype=np.float64)
        mm[:2, :2] = float(scale) * rot
        mm[:, 2] = dst_anchor - mm[:2, :2] @ src_anchor
        cand_transform = dict(transform)
        cand_transform["scale"] = float(scale)
        cand_transform["homothety_refine_dx_px"] = 0.0
        cand_transform["homothety_refine_dy_px"] = 0.0
        cand_transform["homothety_anchor_mode"] = "fixed_vertex"
        metrics, warped_mask = evaluate(
            mm, source_mask, footprint_mask, source_contour, footprint_contour, pixel_size
        )
        score = full_mask_score(metrics, cand_transform)
        rec = {
            "stage": stage,
            "scale": float(scale),
            "dx": 0.0,
            "dy": 0.0,
            "matrix": mm,
            "transform": cand_transform,
            "metrics": metrics,
            "warped_mask": warped_mask,
            "score": score,
        }
        all_results.append(rec)
        return rec

    best = None
    n_steps = int(round((scale_max - scale_min) / max(scale_step, 1e-9)))
    scales = [scale_min + i * scale_step for i in range(n_steps + 1)]
    if scales[-1] < scale_max - 1e-9:
        scales.append(scale_max)
    for scale in scales:
        rec = eval_candidate(scale, "coarse")
        if best is None or rec["score"] < best["score"]:
            best = rec

    fine_scales = [
        max(scale_min, min(scale_max, best["scale"] + ds))
        for ds in np.arange(-scale_step, scale_step + 1e-9, max(scale_step / 5.0, 0.005))
    ]
    for scale in fine_scales:
        rec = eval_candidate(scale, "fine")
        if rec["score"] < best["score"]:
            best = rec

    all_results.sort(key=lambda r: r["score"])
    return best, all_results


def draw_overlay(target, source_contour, footprint_contour, m, src_pair, dst_pair, metrics):
    img = desaturate(target, 0.45)
    warped = warp_contour(source_contour, m).astype(np.int32)
    cv2.drawContours(img, [as_points(footprint_contour).reshape(-1, 1, 2).astype(np.int32)], -1, (0, 255, 0), 2)
    cv2.drawContours(img, [warped], -1, (0, 255, 255), 2)
    cv2.circle(img, tuple(np.round(dst_pair["corner"]).astype(int)), 8, (0, 0, 255), -1)
    txt = f"partial-FOV IoU={metrics['iou']:.3f} outside={metrics['outside_fraction']:.3f}"
    cv2.putText(img, txt, (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)
    return img


def draw_pair_wedge(base, cand, title):
    img = desaturate(base, 0.35) if base.ndim == 3 else cv2.cvtColor(base, cv2.COLOR_GRAY2BGR)
    wedge = cand["wedge"]
    overlay = img.copy()
    overlay[wedge > 0] = (0, 180, 255)
    img[wedge > 0] = cv2.addWeighted(img, 0.55, overlay, 0.45, 0)[wedge > 0]
    pair = cand["pair"]
    for edge, color in ((pair["edge1"], (0, 255, 255)), (pair["edge2"], (255, 0, 255))):
        p0 = tuple(np.round(edge["p0"]).astype(int))
        p1 = tuple(np.round(edge["p1"]).astype(int))
        cv2.line(img, p0, p1, color, 4)
    cv2.circle(img, tuple(np.round(pair["corner"]).astype(int)), 8, (0, 0, 255), -1)
    cv2.putText(
        img,
        f"{title} rank={cand['rank']} angle={pair['angle_deg']:.1f} "
        f"inside={cand['support']['wedge_inside_mask_fraction']:.2f}",
        (16, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.68, (0, 0, 0), 4,
    )
    cv2.putText(
        img,
        f"{title} rank={cand['rank']} angle={pair['angle_deg']:.1f} "
        f"inside={cand['support']['wedge_inside_mask_fraction']:.2f}",
        (16, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.68, (255, 255, 255), 2,
    )
    return img


def draw_all_edges(base, edges, title):
    img = desaturate(base, 0.35) if base.ndim == 3 else cv2.cvtColor(base, cv2.COLOR_GRAY2BGR)
    palette = [
        (0, 255, 255), (255, 0, 255), (0, 180, 255), (80, 255, 80),
        (255, 180, 0), (180, 120, 255), (255, 255, 0), (0, 120, 255),
    ]
    for i, edge in enumerate(edges):
        color = palette[i % len(palette)]
        p0 = tuple(np.round(edge["p0"]).astype(int))
        p1 = tuple(np.round(edge["p1"]).astype(int))
        cv2.line(img, p0, p1, color, 3)
        mid = tuple(np.round((edge["p0"] + edge["p1"]) / 2).astype(int))
        cv2.putText(img, str(i), mid, cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3)
        cv2.putText(img, str(i), mid, cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1)
    cv2.putText(img, f"{title}: {len(edges)} reliable edges", (16, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 0, 0), 4)
    cv2.putText(img, f"{title}: {len(edges)} reliable edges", (16, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)
    return img


def write_wedge_candidate_montage(path, base, candidates, title, max_n=16):
    panels = []
    h, w = base.shape[:2]
    scale = min(1.0, 520.0 / max(h, w))
    for cand in candidates[:max_n]:
        panel = draw_pair_wedge(base, cand, title)
        panels.append(cv2.resize(panel, None, fx=scale, fy=scale))
    if not panels:
        return
    cols = 4
    rows = []
    for i in range(0, len(panels), cols):
        row = panels[i:i + cols]
        max_h = max(p.shape[0] for p in row)
        padded = []
        for p in row:
            if p.shape[0] < max_h:
                p = np.vstack([p, np.full((max_h - p.shape[0], p.shape[1], 3), 245, dtype=np.uint8)])
            padded.append(p)
        rows.append(np.hstack(padded))
    max_w = max(r.shape[1] for r in rows)
    out_rows = []
    for r in rows:
        if r.shape[1] < max_w:
            r = np.hstack([r, np.full((r.shape[0], max_w - r.shape[1], 3), 245, dtype=np.uint8)])
        out_rows.append(r)
    cv2.imwrite(path, np.vstack(out_rows))


def draw_wedge_match_overlay(target, source_contour, footprint_contour, match):
    img = desaturate(target, 0.45)
    fw = match["footprint_wedge"]["wedge"]
    ww = match["warped_wedge"]
    green = img.copy()
    green[fw > 0] = (0, 220, 0)
    img[fw > 0] = cv2.addWeighted(img, 0.62, green, 0.38, 0)[fw > 0]
    yellow = img.copy()
    yellow[ww > 0] = (0, 220, 255)
    img[ww > 0] = cv2.addWeighted(img, 0.62, yellow, 0.38, 0)[ww > 0]
    cv2.drawContours(img, [as_points(footprint_contour).reshape(-1, 1, 2).astype(np.int32)], -1, (0, 255, 0), 2)
    warped = warp_contour(source_contour, match["matrix"]).astype(np.int32)
    cv2.drawContours(img, [warped], -1, (0, 255, 255), 2)
    fp = match["footprint_pair"]
    cv2.circle(img, tuple(np.round(fp["corner"]).astype(int)), 8, (0, 0, 255), -1)
    txt = (
        f"wedge match score={match['score']:.3f} "
        f"wIoU={match['metrics']['wedge_iou']:.3f} "
        f"fullIoU={match['metrics']['full']['iou']:.3f} "
        f"outside={match['metrics']['full']['outside_fraction']:.3f}"
    )
    cv2.putText(img, txt, (20, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.68, (0, 0, 0), 4)
    cv2.putText(img, txt, (20, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.68, (255, 255, 255), 2)
    return img


def write_wedge_match_montage(path, target, source_contour, footprint_contour, matches, max_n=16):
    panels = []
    h, w = target.shape[:2]
    scale = min(1.0, 520.0 / max(h, w))
    for match in matches[:max_n]:
        panel = draw_wedge_match_overlay(target, source_contour, footprint_contour, match)
        panels.append(cv2.resize(panel, None, fx=scale, fy=scale))
    if not panels:
        return
    cols = 4
    rows = []
    for i in range(0, len(panels), cols):
        row = panels[i:i + cols]
        max_h = max(p.shape[0] for p in row)
        padded = []
        for p in row:
            if p.shape[0] < max_h:
                p = np.vstack([p, np.full((max_h - p.shape[0], p.shape[1], 3), 245, dtype=np.uint8)])
            padded.append(p)
        rows.append(np.hstack(padded))
    cv2.imwrite(path, np.vstack(rows))


def draw_translation_refine_overlay(target, source_contour, footprint_contour, rec):
    img = desaturate(target, 0.45)
    cv2.drawContours(img, [as_points(footprint_contour).reshape(-1, 1, 2).astype(np.int32)], -1, (0, 255, 0), 2)
    warped = warp_contour(source_contour, rec["matrix"]).astype(np.int32)
    cv2.drawContours(img, [warped], -1, (0, 255, 255), 2)
    m = rec["metrics"]
    txt = (
        f"{rec['stage']} s={rec.get('scale', 0):.3f} dx={rec['dx']:.0f} dy={rec['dy']:.0f} "
        f"score={rec['score']:.3f} IoU={m['iou']:.3f} "
        f"outside={m['outside_fraction']:.3f} cont={m['top_containment']:.3f}"
    )
    cv2.putText(img, txt, (18, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (0, 0, 0), 4)
    cv2.putText(img, txt, (18, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2)
    return img


def write_translation_refine_montage(path, target, source_contour, footprint_contour, results, max_n=16):
    panels = []
    h, w = target.shape[:2]
    scale = min(1.0, 520.0 / max(h, w))
    for rec in results[:max_n]:
        panel = draw_translation_refine_overlay(target, source_contour, footprint_contour, rec)
        panels.append(cv2.resize(panel, None, fx=scale, fy=scale))
    if not panels:
        return
    cols = 4
    rows = []
    for i in range(0, len(panels), cols):
        row = panels[i:i + cols]
        max_h = max(p.shape[0] for p in row)
        padded = []
        for p in row:
            if p.shape[0] < max_h:
                p = np.vstack([p, np.full((max_h - p.shape[0], p.shape[1], 3), 245, dtype=np.uint8)])
            padded.append(p)
        rows.append(np.hstack(padded))
    cv2.imwrite(path, np.vstack(rows))


def pair_summary(pair):
    return {
        "corner": [float(pair["corner"][0]), float(pair["corner"][1])],
        "angle_deg": float(pair["angle_deg"]),
        "edge_lengths_px": [float(pair["edge1"]["length"]), float(pair["edge2"]["length"])],
        "edge_indices": [int(pair["edge1"]["idx"]), int(pair["edge2"]["idx"])],
    }


def main():
    parser = argparse.ArgumentParser(description="Partial-FOV edge/corner fallback alignment")
    parser.add_argument("--source-contour", required=True)
    parser.add_argument("--source-mask", required=True)
    parser.add_argument("--footprint-contour", required=True)
    parser.add_argument("--footprint-mask", required=True)
    parser.add_argument("--target-image", required=True)
    parser.add_argument("--pixel-size", type=float, required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--min-edge-len", type=float, default=80.0)
    parser.add_argument("--border-margin", type=float, default=8.0)
    parser.add_argument("--min-edge-support-density", type=float, default=0.12,
                        help="Minimum original-contour samples per pixel along a simplified edge; "
                             "rejects long chords across concavities or cropped gaps")
    parser.add_argument("--scale-min", type=float, default=0.85)
    parser.add_argument("--scale-max", type=float, default=1.15)
    parser.add_argument("--source-pair-rank", type=int, default=0)
    parser.add_argument("--footprint-pair-rank", type=int, default=0)
    parser.add_argument("--joint-match-rank", type=int, default=0,
                        help="Use jointly ranked source/footprint pair match; set <0 to use explicit pair ranks")
    parser.add_argument("--joint-top-k", type=int, default=20,
                        help="Number of source and footprint edge-pair candidates to jointly evaluate")
    parser.add_argument("--wedge-mode", action=argparse.BooleanOptionalAction, default=True,
                        help="Generate extended-edge filled wedge candidates and rank alignments by wedge overlap")
    parser.add_argument("--wedge-top-k", type=int, default=30,
                        help="Number of wedge candidates from each side to compare")
    parser.add_argument("--homothety-refine", action=argparse.BooleanOptionalAction, default=True,
                        help="After edge/wedge angle estimate, search uniform scale + dx/dy using complete original masks")
    parser.add_argument("--homothety-scale-min", type=float, default=0.85)
    parser.add_argument("--homothety-scale-max", type=float, default=1.15)
    parser.add_argument("--homothety-scale-step", type=float, default=0.01)
    parser.add_argument("--homothety-coarse-radius", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--homothety-coarse-step", type=int, default=1, help=argparse.SUPPRESS)
    parser.add_argument("--homothety-fine-radius", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--homothety-fine-step", type=int, default=1, help=argparse.SUPPRESS)
    parser.add_argument("--write-warp-top", action="store_true")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    source_contour = np.load(args.source_contour)
    footprint_contour = np.load(args.footprint_contour)
    source_mask = cv2.imread(args.source_mask, cv2.IMREAD_GRAYSCALE)
    footprint_mask = cv2.imread(args.footprint_mask, cv2.IMREAD_GRAYSCALE)
    target = cv2.imread(args.target_image)
    if source_mask is None or footprint_mask is None or target is None:
        print("ERROR: Cannot read one or more input images", file=sys.stderr)
        return 1

    sh, sw = source_mask.shape[:2]
    fh, fw = footprint_mask.shape[:2]
    src_edges = extract_edges(
        source_contour, sw, sh, args.min_edge_len, args.border_margin,
        args.min_edge_support_density
    )
    fp_edges = extract_edges(
        footprint_contour, fw, fh, args.min_edge_len, args.border_margin,
        args.min_edge_support_density
    )
    if len(src_edges) < 2 or len(fp_edges) < 2:
        print(f"ERROR: Not enough reliable edges: source={len(src_edges)} footprint={len(fp_edges)}", file=sys.stderr)
        return 2

    _, src_pairs = choose_pair(src_edges, sw, sh, args.source_pair_rank)
    _, fp_pairs = choose_pair(fp_edges, fw, fh, args.footprint_pair_rank)
    if not src_pairs or not fp_pairs:
        print("ERROR: Could not find intersecting long-edge pairs", file=sys.stderr)
        return 3

    source_wedges = build_wedge_candidates(src_pairs, source_mask, max_candidates=max(args.wedge_top_k, args.joint_top_k))
    footprint_wedges = build_wedge_candidates(fp_pairs, footprint_mask, max_candidates=max(args.wedge_top_k, args.joint_top_k))

    source_base = cv2.cvtColor(source_mask, cv2.COLOR_GRAY2BGR)
    footprint_base = target.copy()
    cv2.imwrite(
        os.path.join(args.output_dir, "01_source_all_reliable_long_edges.png"),
        draw_all_edges(source_base, src_edges, "source")
    )
    cv2.imwrite(
        os.path.join(args.output_dir, "01_footprint_all_reliable_long_edges.png"),
        draw_all_edges(footprint_base, fp_edges, "footprint")
    )
    write_wedge_candidate_montage(
        os.path.join(args.output_dir, "02_source_extended_edge_wedge_candidates.png"),
        source_base, source_wedges, "source wedge", max_n=16
    )
    write_wedge_candidate_montage(
        os.path.join(args.output_dir, "02_footprint_extended_edge_wedge_candidates.png"),
        footprint_base, footprint_wedges, "footprint wedge", max_n=16
    )

    wedge_matches = []
    if args.wedge_mode and source_wedges and footprint_wedges:
        wedge_matches = wedge_match_candidates(
            source_wedges, footprint_wedges,
            source_mask, footprint_mask, source_contour, footprint_contour,
            args.pixel_size, args.scale_min, args.scale_max, top_k=args.wedge_top_k
        )
        write_wedge_match_montage(
            os.path.join(args.output_dir, "03_extended_wedge_match_candidates.png"),
            target, source_contour, footprint_contour, wedge_matches, max_n=16
        )

    joint_matches = pair_match_candidates(
        src_pairs, fp_pairs, source_mask, footprint_mask,
        source_contour, footprint_contour, args.pixel_size,
        args.scale_min, args.scale_max, top_k=args.joint_top_k
    )
    selected_joint = None
    selected_wedge = None
    if args.wedge_mode and wedge_matches:
        selected_wedge = wedge_matches[min(args.joint_match_rank, len(wedge_matches) - 1)]
        src_pair = selected_wedge["source_pair"]
        fp_pair = selected_wedge["footprint_pair"]
        m = selected_wedge["matrix"]
        transform = selected_wedge["transform"]
        metrics = selected_wedge["metrics"]["full"]
        warped_mask = selected_wedge["warped_mask"]
    elif args.joint_match_rank >= 0 and joint_matches:
        jidx = min(args.joint_match_rank, len(joint_matches) - 1)
        selected_joint = joint_matches[jidx]
        src_pair = selected_joint["source_pair"]
        fp_pair = selected_joint["footprint_pair"]
        m, transform = build_warp(src_pair, fp_pair, args.scale_min, args.scale_max)
        metrics, warped_mask = evaluate(m, source_mask, footprint_mask, source_contour, footprint_contour, args.pixel_size)
    else:
        src_pair = src_pairs[min(max(args.source_pair_rank, 0), len(src_pairs) - 1)]
        fp_pair = fp_pairs[min(max(args.footprint_pair_rank, 0), len(fp_pairs) - 1)]
        m, transform = build_warp(src_pair, fp_pair, args.scale_min, args.scale_max)
        metrics, warped_mask = evaluate(m, source_mask, footprint_mask, source_contour, footprint_contour, args.pixel_size)

    homothety_best = None
    homothety_results = []
    pre_homothety_metrics = metrics
    pre_homothety_matrix = m.copy()
    if args.homothety_refine:
        homothety_best, homothety_results = refine_similarity_grid(
            m, transform, src_pair["corner"], fp_pair["corner"],
            source_mask, footprint_mask, source_contour, footprint_contour,
            args.pixel_size,
            scale_min=args.homothety_scale_min,
            scale_max=args.homothety_scale_max,
            scale_step=args.homothety_scale_step,
            coarse_radius=args.homothety_coarse_radius,
            coarse_step=args.homothety_coarse_step,
            fine_radius=args.homothety_fine_radius,
            fine_step=args.homothety_fine_step,
        )
        if homothety_best is not None:
            m = homothety_best["matrix"]
            metrics = homothety_best["metrics"]
            warped_mask = homothety_best["warped_mask"]
            transform = dict(homothety_best["transform"])
            transform["pre_homothety_metrics"] = pre_homothety_metrics

    np.save(os.path.join(args.output_dir, "warp_top_partial_fov.npy"), m)
    if args.write_warp_top:
        np.save(os.path.join(args.output_dir, "warp_top.npy"), m)
    cv2.imwrite(os.path.join(args.output_dir, "partial_fov_warped_source_mask.png"), warped_mask)
    cv2.imwrite(
        os.path.join(args.output_dir, "partial_fov_edge_overlay.png"),
        draw_overlay(target, source_contour, footprint_contour, m, src_pair, fp_pair, metrics),
    )
    if selected_wedge:
        cv2.imwrite(
            os.path.join(args.output_dir, "04_selected_source_extended_wedge.png"),
            draw_pair_wedge(source_base, selected_wedge["source_wedge"], "selected source wedge"),
        )
        cv2.imwrite(
            os.path.join(args.output_dir, "04_selected_footprint_extended_wedge.png"),
            draw_pair_wedge(footprint_base, selected_wedge["footprint_wedge"], "selected footprint wedge"),
        )
        cv2.imwrite(
            os.path.join(args.output_dir, "05_extended_wedge_alignment_overlay.png"),
            draw_wedge_match_overlay(target, source_contour, footprint_contour, selected_wedge),
        )
    if homothety_results:
        write_translation_refine_montage(
            os.path.join(args.output_dir, "06_homothety_refine_candidates_full_mask.png"),
            target, source_contour, footprint_contour, homothety_results, max_n=16
        )
        cv2.imwrite(
            os.path.join(args.output_dir, "07_homothety_refined_final_overlay.png"),
            draw_translation_refine_overlay(target, source_contour, footprint_contour, homothety_results[0]),
        )

    report_path = os.path.join(args.output_dir, "alignment_report.json")
    report = {}
    if os.path.exists(report_path):
        with open(report_path, "r", encoding="utf-8") as f:
            report = json.load(f)
    report["partial_fov_edge_corner_fallback"] = {
        "status": "complete",
        "warp_file": "warp_top_partial_fov.npy",
        "overwrote_warp_top": bool(args.write_warp_top),
        "transform": transform,
        "metrics": metrics,
        "source_edge_pair": pair_summary(src_pair),
        "footprint_edge_pair": pair_summary(fp_pair),
        "source_pair_rank": int(args.source_pair_rank),
        "footprint_pair_rank": int(args.footprint_pair_rank),
        "joint_match_rank": int(args.joint_match_rank),
        "selected_joint_match": {
            "source_pair_rank": int(selected_joint["source_pair_rank"]),
            "footprint_pair_rank": int(selected_joint["footprint_pair_rank"]),
            "score": float(selected_joint["score"]),
        } if selected_joint else None,
        "wedge_mode": bool(args.wedge_mode),
        "selected_wedge_match": {
            "source_wedge_rank": int(selected_wedge["source_wedge_rank"]),
            "footprint_wedge_rank": int(selected_wedge["footprint_wedge_rank"]),
            "score": float(selected_wedge["score"]),
            "wedge_iou": float(selected_wedge["metrics"]["wedge_iou"]),
            "wedge_outside_fraction": float(selected_wedge["metrics"]["wedge_outside_fraction"]),
        } if selected_wedge else None,
        "homothety_refine": {
            "enabled": bool(args.homothety_refine),
            "pre_homothety_metrics": pre_homothety_metrics,
            "pre_homothety_matrix": pre_homothety_matrix.tolist(),
            "best": {
                "scale": float(homothety_best["scale"]),
                "dx": float(homothety_best["dx"]),
                "dy": float(homothety_best["dy"]),
                "score": float(homothety_best["score"]),
                "metrics": homothety_best["metrics"],
                "matrix": homothety_best["matrix"].tolist(),
            } if homothety_best else None,
            "top_candidates": [
                {
                    "stage": r["stage"],
                    "scale": float(r["scale"]),
                    "dx": float(r["dx"]),
                    "dy": float(r["dy"]),
                    "score": float(r["score"]),
                    "iou": float(r["metrics"]["iou"]),
                    "outside_fraction": float(r["metrics"]["outside_fraction"]),
                    "top_containment": float(r["metrics"]["top_containment"]),
                    "fwd_chamfer_mean_um": r["metrics"].get("fwd_chamfer_mean_um"),
                }
                for r in homothety_results[:16]
            ],
        },
        "top_wedge_matches": [
            {
                "source_wedge_rank": int(j["source_wedge_rank"]),
                "footprint_wedge_rank": int(j["footprint_wedge_rank"]),
                "score": float(j["score"]),
                "rotation_deg": float(j["transform"]["rotation_deg"]),
                "scale": float(j["transform"]["scale"]),
                "wedge_iou": float(j["metrics"]["wedge_iou"]),
                "wedge_outside_fraction": float(j["metrics"]["wedge_outside_fraction"]),
                "full_iou": float(j["metrics"]["full"]["iou"]),
                "full_outside_fraction": float(j["metrics"]["full"]["outside_fraction"]),
            }
            for j in wedge_matches[:12]
        ],
        "num_source_wedges": int(len(source_wedges)),
        "num_footprint_wedges": int(len(footprint_wedges)),
        "top_joint_matches": [
            {
                "source_pair_rank": int(j["source_pair_rank"]),
                "footprint_pair_rank": int(j["footprint_pair_rank"]),
                "score": float(j["score"]),
                "rotation_deg": float(j["transform"]["rotation_deg"]),
                "scale": float(j["transform"]["scale"]),
                "iou": float(j["metrics"]["iou"]),
                "outside_fraction": float(j["metrics"]["outside_fraction"]),
            }
            for j in joint_matches[:12]
        ],
        "top_source_pairs": [pair_summary(p) for p in src_pairs[:8]],
        "top_footprint_pairs": [pair_summary(p) for p in fp_pairs[:8]],
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print("Partial-FOV edge/corner fallback complete")
    if selected_wedge:
        print(f"  wedge_match_rank={args.joint_match_rank} -> source_wedge_rank={selected_wedge['source_wedge_rank']} footprint_wedge_rank={selected_wedge['footprint_wedge_rank']} score={selected_wedge['score']:.3f}")
        print(f"  wedge IoU={selected_wedge['metrics']['wedge_iou']:.3f} wedge outside={selected_wedge['metrics']['wedge_outside_fraction']:.3f}")
    elif selected_joint:
        print(f"  joint_match_rank={args.joint_match_rank} -> source_pair_rank={selected_joint['source_pair_rank']} footprint_pair_rank={selected_joint['footprint_pair_rank']} score={selected_joint['score']:.3f}")
    else:
        print(f"  source_pair_rank={args.source_pair_rank} footprint_pair_rank={args.footprint_pair_rank}")
    print(f"  rot={transform['rotation_deg']:.2f} scale={transform['scale']:.3f} raw_edge_scale={transform['raw_edge_length_scale']:.3f}")
    if homothety_best:
        print(f"  homothety refine: scale={homothety_best['scale']:.3f} dx={homothety_best['dx']:.0f} dy={homothety_best['dy']:.0f} score={homothety_best['score']:.3f}")
        print(f"  pre-refine IoU={pre_homothety_metrics['iou']:.3f} outside={pre_homothety_metrics['outside_fraction']:.3f} containment={pre_homothety_metrics['top_containment']:.3f}")
    print(f"  IoU={metrics['iou']:.3f} outside={metrics['outside_fraction']:.3f} containment={metrics['top_containment']:.3f}")
    print("  wrote warp_top_partial_fov.npy and partial_fov_edge_overlay.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
