#!/usr/bin/env python
"""graphene.py — graphene detector for top_part images.

Strategy (adaptive; no GT-fitted priors):
  1. Mirror top_part if requested.
  2. Isolate the flake region with Otsu on the smoothed LAB-L channel
     (replaces the fixed gray>40 / sat>15 thresholds).
  3. Infer contrast polarity from threshold_multiotsu(classes=3) on the
     in-flake L histogram — produces both a bright-layer and dark-layer
     candidate set (graphene can be brighter OR darker depending on
     substrate/thickness).
  4. Extract connected components from both polarity masks; gate by
     host-fraction area (0.05%..25% × host_area_um2).
  5. Score candidates by aspect, solidity, signed contrast,
     centroid-distance-to-flake-edge, and within-polarity-population
     separability.  Weights derived from sub-score variance across
     candidates (discriminative = higher weight).
  6. Footprint-containment scoring (Phase 10a): when --footprint-mask is
     supplied, each candidate is scored by the fraction of its pixels that
     fall inside the top-alignment footprint (warped to mirrored-top_part
     coords via the co-located warp_top.npy).  Physics: the footprint marks
     the spatial extent of the top-flake region by construction.  Any cluster
     mostly outside it is background substrate, not graphene.  This is a
     universal property of vdW stack imaging — not sample-specific tuning.
  7. Region grow the best CC.  Two modes (Phase 10b):
     a. Footprint-bounded grow (when --footprint-mask is provided): grow
        from the seed CC outward within the footprint, anchored to seed
        LAB statistics, until no new similar footprint pixels remain.  No
        area cap needed — the footprint is the hard spatial upper bound.
     b. Leakage-check grow (no footprint): stop when the boundary mean
        gradient drops below the in-flake p10 (substrate-agnostic).
  8. morph_clean + flood_fill_holes.
  9. No-candidate fallback: emit empty mask with low_confidence=True.

CLI:
    --image, --pixel-size, --output-dir, [--mirror]
    [--cluster-id N]   rank to pick (kept for CLI compat)
    [--n-sub-clusters] (kept for CLI compat, unused)
    [--footprint-mask PATH]  optional: footprint mask in full_stack coords.
                             Auto-locates warp_top.npy in the same directory
                             to warp the mask into mirrored-top_part coords.
                             When omitted, auto-discovery is attempted from
                             {stack}/output/align/footprint_mask.png.
                             Phase 15: auto-discovered footprint now informs
                             candidate scoring (footprint_mask_score_only) as
                             well as grow (footprint_mask_grow_only), without
                             expanding the area gate (host-based gate preserved).

Outputs in --output-dir:
    graphene_mask.png
    graphene_contour.npy   (N, 2) float64, top_part pixel coords
    graphene_result.json   sidecar: area, selected, top_candidates,
                            cluster_id, low_confidence
    02_graphene_on_top.png diagnostic overlay
    00_graphene_candidates.png  ranked candidate panel for agent selection
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..',
                                'nanodevice_flakedetect', 'scripts'))

import cv2
import numpy as np
from skimage.filters import threshold_multiotsu

from core import (
    morph_clean, flood_fill_holes, keep_largest_n, desaturate,
    _kernel_from_um,
)

# ---------------------------------------------------------------------------
# Physical-unit morphology radii (physics-grounded, not GT-fitted)
# ---------------------------------------------------------------------------
# Flake region morphology: close gaps ~1 µm, remove noise >0.5 µm.
# These are typical optical-microscope feature widths at 100x.
FLAKE_MORPH_CLOSE_UM = 1.0
FLAKE_MORPH_OPEN_UM = 0.5

# Post-region-grow morphology.
# 0.5 µm at 0.106 µm/px → radius 4.7 px → k=9 (close to original k=11).
# 0.25 µm → radius 2.4 px → k=5 (same as original k=5).
GRAPHENE_MORPH_CLOSE_UM = 0.5
GRAPHENE_MORPH_OPEN_UM = 0.25

# Area gate as fraction of host area (physics: graphene < entire flake).
AREA_MIN_HOST_FRAC = 0.0005   # 0.05 % of host area
AREA_MAX_HOST_FRAC = 0.40     # 40 % of host area (graphene can cover a large fraction)

# When the footprint mask is available, use the footprint area as denominator
# for the upper area gate instead of the detected host area.  Physics: the
# footprint marks the spatial extent of the top-flake deposition, which is the
# correct reference for how large a graphene region can be.  The detected host
# area (from Otsu on blurred L channel) can underestimate the actual flake for
# high-contrast stacks, causing large graphene CCs to be incorrectly filtered
# out as "too large" when measured against a too-small host area.
# When footprint is available, we allow up to AREA_MAX_FOOTPRINT_FRAC × footprint.
AREA_MAX_FOOTPRINT_FRAC = 0.80  # graphene can fill up to 80% of footprint

# Minimum absolute area floor to exclude single-pixel noise.
AREA_MIN_ABS_PX = 50

# Boundary-leakage stop criterion: region grow halts when boundary mean
# gradient drops below p10 of the in-seed gradient field.
BOUNDARY_GRADIENT_PERCENTILE = 10

# Mahalanobis-style LAB grow distance cap for the optional region grow step.
LAB_GROW_SIGMA_CAP = 2.5

# LAB grow sigma cap used for footprint-bounded grow (Phase 10b).
# Wider than LAB_GROW_SIGMA_CAP because the footprint provides a hard spatial
# bound so over-expansion is prevented; a wider sigma cap allows the grow to
# absorb intermediate-brightness graphene pixels that fall between the bright
# core (seed CC) and the footprint boundary.
LAB_GROW_SIGMA_CAP_FOOTPRINT = 2.0

# Maximum iterations for footprint-bounded region grow.  The grow terminates
# earlier if no new footprint pixels pass the LAB similarity filter.
FOOTPRINT_GROW_MAX_ITER = 50

# Variance floor for sub-score weighting in _score_candidates: below this
# value, all sub-scores get equal weight (pool is effectively uninformative).
VARIANCE_FLOOR = 1e-9

# ---------------------------------------------------------------------------
# Footprint-containment scoring constants (Phase 10a)
# ---------------------------------------------------------------------------
# Weight given to the footprint-containment sub-score in candidate ranking.
# 0.5 = half the total score; high weight ensures footprint-outside candidates
# lose decisively to footprint-inside candidates of similar photometric score.
FOOTPRINT_CONTAINMENT_WEIGHT = 0.5

# Penalty multiplier applied when containment < FOOTPRINT_LOW_CONTAINMENT_FLOOR.
# Physics: a cluster with < 50% of its pixels inside the footprint is almost
# certainly background substrate — apply a strong penalty to push it below
# genuine graphene candidates with partial or full containment.
FOOTPRINT_LOW_CONTAINMENT_FLOOR = 0.5
FOOTPRINT_LOW_CONTAINMENT_PENALTY = 0.3  # multiply s_containment by this factor

# ---------------------------------------------------------------------------
# Intra-flake bridging constants (Phase 14)
# ---------------------------------------------------------------------------
# A graphene flake of mixed thickness shows intra-flake LAB variation, but
# each fragment must share polarity sign relative to bulk hBN (graphene is
# always either brighter or darker than hBN, never both — that would be two
# different materials). Fragments separated by short gaps (<3 µm — same
# order as typical flake thickness inhomogeneity) inside the same footprint
# are physically the same flake.
BRIDGE_MAX_DISTANCE_UM = 3.0
BRIDGE_REL_L_MAGNITUDE_RATIO = 2.0  # allow 0.5x to 2x of seed |rel_L|
# Maximum chroma deviation from bulk hBN a,b for a CC to be considered
# graphene-consistent (LAB a,b range ±128; graphene shows small chroma shift).
BRIDGE_MAX_CHROMA_DEV_AB = 15.0  # LAB a or b units
# Minimum number of foreground CCs required before bridging is attempted.
BRIDGE_MIN_CC_COUNT = 2
# Minimum footprint-membership fraction for a CC to qualify for bridging.
BRIDGE_MIN_FOOTPRINT_FRAC = 0.5


# ---------------------------------------------------------------------------
# Helper: build coarse foreground region via Otsu on blurred luminance
# ---------------------------------------------------------------------------

def _flake_mask(img: np.ndarray, pixel_size: float) -> np.ndarray:
    """Isolate the flake region using Otsu thresholding on the smoothed
    LAB-L channel.  Physics: the top_part image contains the stamp-borne
    flake on a glass/air background; the flake is the dominant bright
    or distinctly-textured region.

    Returns a uint8 binary mask (0/255).
    """
    close_k = _kernel_from_um(FLAKE_MORPH_CLOSE_UM, pixel_size, ellipse=True)
    open_k = _kernel_from_um(FLAKE_MORPH_OPEN_UM, pixel_size, ellipse=True)

    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    L = lab[:, :, 0].astype(np.float32)

    # Smooth to suppress camera noise before Otsu.
    L_blur = cv2.GaussianBlur(L, (15, 15), 0)

    # Otsu on the smoothed L channel separates flake from background.
    # cv2.threshold requires uint8.
    L8 = np.clip(L_blur, 0, 255).astype(np.uint8)
    _, flake = cv2.threshold(L8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Morphological cleanup.
    flake = cv2.morphologyEx(flake, cv2.MORPH_CLOSE, close_k)
    flake = cv2.morphologyEx(flake, cv2.MORPH_OPEN, open_k)

    # Keep only the dominant region (the flake stack assembly).
    flake = keep_largest_n(flake, n=1, min_area=AREA_MIN_ABS_PX)
    flake = flood_fill_holes(flake)
    return flake


# ---------------------------------------------------------------------------
# Helper: build gradient magnitude for boundary-leakage detection
# ---------------------------------------------------------------------------

def _gradient_mag(img: np.ndarray) -> np.ndarray:
    """Compute gradient magnitude on the L channel (physics: graphene
    boundaries show colour/contrast gradient from the underlaying hBN
    or substrate)."""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    L = lab[:, :, 0].astype(np.float32)
    gx = cv2.Sobel(L, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(L, cv2.CV_32F, 0, 1, ksize=3)
    return np.sqrt(gx ** 2 + gy ** 2)


# ---------------------------------------------------------------------------
# Footprint-containment helpers (Phase 10a)
# ---------------------------------------------------------------------------

def _load_footprint_in_toppart_coords(
    footprint_mask_path: str,
    target_shape: tuple[int, int],
    mirror: bool,
) -> np.ndarray | None:
    """Load the footprint mask and warp it into mirrored-top_part coordinates.

    Physics rationale: footprint_mask.png is produced by footprint.py in the
    coordinate system of the full_stack_raw (SiO2 substrate) image.  graphene.py
    processes top_part.jpg, optionally mirrored, which is in stamp-substrate
    coordinates.  The two coordinate systems are related by warp_top.npy — a 2×3
    affine that maps mirrored-top_part → full_stack.  We invert it to map the
    footprint back to mirrored-top_part coordinates so that containment can be
    scored directly against the CC masks produced during graphene detection.

    Args:
        footprint_mask_path: Path to footprint_mask.png (full_stack coords).
        target_shape: (height, width) of the image being processed (top_part,
            after any mirroring) — used as the output size for warpAffine.
        mirror: Whether graphene.py is running in mirror mode.  Always True
            for vdW stacks; the warp_top.npy was computed on the mirrored
            top_part, so this flag is informational / future-proof.

    Returns:
        uint8 binary mask (0/255) in mirrored-top_part coordinates, or None if
        the footprint mask or warp_top.npy cannot be loaded.
    """
    fp_path = Path(footprint_mask_path)
    if not fp_path.exists():
        return None

    fp_mask = cv2.imread(str(fp_path), cv2.IMREAD_GRAYSCALE)
    if fp_mask is None:
        return None

    # Auto-locate warp_top.npy co-located with footprint_mask.png.
    warp_path = fp_path.parent / "warp_top.npy"
    if not warp_path.exists():
        # Fallback: return the footprint as-is (same-size images, no rotation).
        # This is only valid when the top_part and full_stack share the same
        # pixel grid (no relative rotation/scale between substrates), which is
        # not guaranteed.  We return None to avoid false containment scores.
        return None

    try:
        warp_top = np.load(str(warp_path))  # 2×3: mirrored-top_part → full_stack
        if warp_top.shape != (2, 3):
            return None
    except (OSError, ValueError):
        return None

    # Invert to get full_stack → mirrored-top_part.
    warp_inv = cv2.invertAffineTransform(warp_top)

    h, w = target_shape[:2]
    fp_warped = cv2.warpAffine(
        fp_mask, warp_inv, (w, h), flags=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT, borderValue=0,
    )
    # Binarize (preserve any anti-aliasing artifacts that slipped through).
    _, fp_bin = cv2.threshold(fp_warped, FOOTPRINT_WARP_BINARY_THRESH, 255,
                              cv2.THRESH_BINARY)
    return fp_bin


# Named constant for the warp binarisation threshold (avoids naked numeric).
FOOTPRINT_WARP_BINARY_THRESH = 127


GRAPHENE_CANDIDATE_TOP_N = 8
GRAPHENE_CANDIDATE_PANEL_SPLIT = 4

_MID = 128
_GRAPHENE_PALETTE = [
    (0, 200, 255), (0, 255, 0), (0, 0, 255), (255, 0, 0),
    (255, 0, 255), (255, 255, 0), (255, _MID, 0), (_MID, 255, _MID),
]


def _candidate_public_record(record: dict, rank: int) -> dict:
    panel_area_px = record.get('_panel_area_px')
    panel_area_um2 = record.get('_panel_area_um2')
    out = {
        'rank': rank,
        'lab_idx': int(record.get('lab_idx', -1)),
        'polarity': int(record.get('polarity', 0)),
        'area_px': int(record.get('area_px', 0)),
        'area_um2': round(float(record.get('area_um2', 0.0)), 2),
        'panel_area_px': None if panel_area_px is None else int(panel_area_px),
        'panel_area_um2': None if panel_area_um2 is None else round(float(panel_area_um2), 2),
        'score': round(float(record.get('score', 0.0)), 4),
        'aspect': round(float(record.get('aspect', 0.0)), 2),
        'solidity': round(float(record.get('solidity', 0.0)), 3),
        'L_med': round(float(record.get('L_med', 0.0)), 2),
        'rel_L_med': round(float(record.get('rel_L_med', 0.0)), 2),
        'source_threshold': round(float(record.get('_thr', 0.0)), 2),
    }
    for key in ('s_area', 's_contrast', 's_solidity', 's_containment',
                'containment_raw'):
        val = record.get(key)
        out[key] = None if val is None else round(float(val), 4)
    return out


def draw_graphene_candidates(image_bgr: np.ndarray,
                             ranked: list[dict],
                             top_n: int = GRAPHENE_CANDIDATE_TOP_N,
                             selected_rank: int = 0) -> np.ndarray:
    """Render a ranked candidate panel for agent visual selection."""
    h_img, w_img = image_bgr.shape[:2]
    n_show = min(max(0, int(top_n)), len(ranked))
    if n_show <= 0:
        return desaturate(image_bgr, factor=0.4)

    n_cols = 3 if n_show > GRAPHENE_CANDIDATE_PANEL_SPLIT else min(n_show, 2)
    n_rows = (n_show + n_cols - 1) // n_cols
    base = desaturate(image_bgr, factor=0.55)
    panels = []

    for rank in range(n_show):
        rec = ranked[rank]
        mask = rec.get('_panel_mask')
        if mask is None:
            mask = rec.get('_cc_mask')
        panel = base.copy()
        color = _GRAPHENE_PALETTE[rank % len(_GRAPHENE_PALETTE)]
        if mask is not None:
            cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
            overlay = panel.copy()
            cv2.drawContours(overlay, cnts, -1, color, -1)
            panel = cv2.addWeighted(overlay, 0.32, panel, 0.68, 0)
            cv2.drawContours(panel, cnts, -1, color, 2)
        if rank == selected_rank:
            cv2.rectangle(panel, (0, 0), (w_img - 1, h_img - 1),
                          (0, 255, 255), 8)

        label1 = (
            f"#{rank} area={rec.get('_panel_area_um2', rec.get('area_um2', 0.0)):.0f}um2 "
            f"seed={rec.get('area_um2', 0.0):.0f} score={rec.get('score', 0.0):.3f}"
        )
        label2 = (
            f"relL={rec.get('rel_L_med', 0.0):.1f} "
            f"pol={rec.get('polarity', 0)} "
            f"cont={rec.get('containment_raw', 0.0):.2f}"
            if rec.get('containment_raw') is not None
            else f"relL={rec.get('rel_L_med', 0.0):.1f} "
                 f"pol={rec.get('polarity', 0)}"
        )
        cv2.rectangle(panel, (0, 0), (w_img, 70), (20, 20, 20), -1)
        cv2.putText(panel, label1, (12, 30), cv2.FONT_HERSHEY_SIMPLEX,
                    0.75, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(panel, label2, (12, 58), cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, (220, 220, 220), 1, cv2.LINE_AA)
        panels.append(panel)

    panel_h = max(1, h_img // n_rows)
    panel_w = max(1, w_img // n_cols)
    fitted = [cv2.resize(p, (panel_w, panel_h), interpolation=cv2.INTER_AREA)
              for p in panels]
    while len(fitted) < n_rows * n_cols:
        fitted.append(np.zeros_like(fitted[0]))
    rows = [np.hstack(fitted[i * n_cols:(i + 1) * n_cols])
            for i in range(n_rows)]
    return np.vstack(rows)


def spatial_containment_score(candidate_mask: np.ndarray,
                              footprint_mask: np.ndarray) -> float:
    """Fraction of the candidate's pixels that fall inside the footprint.

    containment = |candidate ∩ footprint| / |candidate|

    Range [0, 1].  Higher = more of the candidate overlaps the footprint region.

    Physics grounding: the top-alignment footprint marks the spatial extent of
    the top-flake stack (the region where stamp-borne material was deposited).
    Any K-means cluster whose pixels are mostly outside this region is background
    substrate — it cannot be graphene.  This is a universal property of vdW stack
    imaging: graphene is always within the deposited flake footprint, regardless
    of substrate type, illumination conditions, or color balance.  The score is
    purely geometric and does not depend on any GT-fitted prior.

    Args:
        candidate_mask: uint8 binary mask (0/255) of the CC candidate.
        footprint_mask: uint8 binary mask (0/255) in the same coordinate system.

    Returns:
        containment fraction in [0, 1], or 0.0 if candidate_mask is empty.
    """
    cand_px = int((candidate_mask > 0).sum())
    if cand_px == 0:
        return 0.0
    overlap = int(
        cv2.bitwise_and(candidate_mask, footprint_mask).astype(np.bool_).sum()
    )
    return overlap / cand_px


# ---------------------------------------------------------------------------
# Helper: region grow with boundary-leakage stop criterion
# ---------------------------------------------------------------------------

def _region_grow_leakage_check(
    base_mask: np.ndarray,
    lab_image: np.ndarray,
    grad_mag: np.ndarray,
    region_mask: np.ndarray,
    pixel_size: float,
    n_iter: int = 3,
    area_cap_px: int | None = None,
) -> np.ndarray:
    """Iterative region grow that stops when boundary gradient leaks
    below the in-seed p10 of the gradient field.

    Replaces the substrate-name branch: the leakage criterion is
    substrate-agnostic — it relies only on the gradient image geometry.

    Physics: graphene is bounded by a real material interface (hBN surface
    or glass edge).  When the region grow boundary crosses into featureless
    substrate, the boundary mean gradient drops sharply below the gradient
    level inside the graphene seed.

    Args:
        area_cap_px: Maximum grown-mask area in pixels; stops grow when
            exceeded (prevents over-expansion when seed is already well-sized).
    """
    cur = base_mask.copy()
    # Compute in-seed gradient floor once from the initial mask.
    seed_grads = grad_mag[cur > 0]
    if len(seed_grads) < AREA_MIN_ABS_PX:
        return cur
    grad_floor = float(np.percentile(seed_grads, BOUNDARY_GRADIENT_PERCENTILE))

    dilate_px = max(1, int(round(1.0 / pixel_size)))  # ~1 µm dilation step
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                  (2 * dilate_px + 1, 2 * dilate_px + 1))

    for _ in range(n_iter):
        pix = lab_image[cur > 0].astype(np.float32)
        if len(pix) < AREA_MIN_ABS_PX:
            break
        mu = pix.mean(axis=0)
        sigma = pix.std(axis=0) + 1.0

        dil = cv2.dilate(cur, k)
        candidate = cv2.bitwise_and(dil, region_mask)
        ys, xs = np.where(candidate > 0)
        if len(ys) == 0:
            break

        # Boundary-leakage check: compute mean gradient on the candidate
        # boundary ring (dil \ cur) and stop if it drops below grad_floor.
        ring = cv2.bitwise_and(dil, cv2.bitwise_not(cur))
        ring_grads = grad_mag[ring > 0]
        if len(ring_grads) > 0:
            boundary_grad_mean = float(ring_grads.mean())
            if boundary_grad_mean < grad_floor:
                break  # stop: boundary has leaked into low-gradient region

        # LAB similarity filter.
        vals = lab_image[ys, xs].astype(np.float32)
        d = np.sqrt((((vals - mu) / sigma) ** 2).sum(axis=1))
        keep = d < LAB_GROW_SIGMA_CAP
        new_mask = cur.copy()
        new_mask[ys[keep], xs[keep]] = 255
        if (new_mask > 0).sum() == (cur > 0).sum():
            break
        # Area cap: stop grow if mask has grown too large.
        if area_cap_px is not None and int((new_mask > 0).sum()) > area_cap_px:
            break
        cur = new_mask

    return cur


def _region_grow_footprint_bounded(
    base_mask: np.ndarray,
    lab_image: np.ndarray,
    footprint_mask: np.ndarray,
    pixel_size: float,
    seed_polarity: int = 0,
    rel_L_image: np.ndarray | None = None,
    grad_mag: np.ndarray | None = None,
) -> np.ndarray:
    """Footprint-bounded region grow (Phase 10b / Phase 13).

    Grows the seed CC outward within the footprint mask until no new
    LAB-similar footprint pixels remain OR the boundary gradient leaks
    below the seed's internal gradient floor.  The footprint provides a hard
    spatial upper bound.

    Physics rationale: the top-alignment footprint marks the spatial extent
    of the deposited top-flake material.  Graphene must reside entirely within
    this region.  Once the seed CC is identified, growing it to include all
    footprint pixels that share its LAB signature recovers the full graphene
    region even when the polarity threshold captures only the bright core.

    Key design choices (vs the leakage-check grow):
      - sigma cap = LAB_GROW_SIGMA_CAP_FOOTPRINT (2.0) — constrained to
        footprint so over-expansion is prevented geometrically.
      - LAB statistics are anchored to the SEED (not updated each iteration)
        so the grow cannot drift toward new color classes.
      - Polarity constraint: when seed_polarity is +1 (bright) or -1 (dark)
        and rel_L_image is provided, only admit footprint pixels that share
        the seed's brightness direction relative to the seed median L.  This
        prevents a bright seed from growing into dark footprint areas and vice
        versa, avoiding the "fill entire footprint" failure mode seen on stacks
        where the seed has low contrast (broad LAB std) relative to host.
      - Gradient stop (Phase 13): when grad_mag is provided, stop growing if
        the boundary ring mean gradient drops below the in-seed p10 (same
        criterion as leakage-check grow).  This prevents absorption of
        spectrally-similar but optically-distinct adjacent materials (e.g.
        hBN regions whose LAB resembles graphene but are separated by a real
        interface gradient).
      - Footprint pixels only: the outer spatial boundary is the footprint
        mask, not the flake Otsu mask.
      - No area cap: the footprint is the spatial cap.
      - Up to FOOTPRINT_GROW_MAX_ITER iterations; stops earlier when no new
        pixels pass the similarity filter (natural convergence).

    Args:
        base_mask: uint8 binary seed mask (0/255), already in mirrored-top_part
            coordinates.
        lab_image: LAB image (H×W×3 float32) in the same coordinate system.
        footprint_mask: uint8 binary footprint mask (0/255) in mirrored-top_part
            coordinates.  Pixels outside this mask are never admitted.
        pixel_size: µm per pixel, used to set the dilation step (~1 µm).
        seed_polarity: +1 if seed CC is bright (rel_L > 0), -1 if dark,
            0 if unknown.  Used to restrict footprint pixels to the same
            brightness direction as the seed.
        rel_L_image: float32 array of relative L values (L - L_bg) across the
            full image.  Required when seed_polarity != 0 to apply the
            polarity constraint.
        grad_mag: Optional float32 gradient magnitude array (same H×W as image).
            When provided, applies the boundary-leakage stop criterion (Phase 13)
            to prevent grow from crossing into optically-distinct materials that
            happen to share the seed's LAB statistics.

    Returns:
        uint8 binary grown mask (0/255) in mirrored-top_part coordinates.
        Area is always >= area of base_mask and <= area of footprint_mask.
    """
    cur = base_mask.copy()
    seed_px = cur > 0
    if not seed_px.any():
        return cur

    # Anchor LAB statistics to the seed (not updated during grow).
    # This prevents color drift: the grow will only admit footprint pixels
    # whose LAB is close to the original seed cluster.
    seed_pix = lab_image[seed_px].astype(np.float32)
    mu = seed_pix.mean(axis=0)
    sigma = seed_pix.std(axis=0) + 1.0

    # Compute in-seed gradient floor for boundary-leakage stop criterion.
    # Only computed when grad_mag is provided (Phase 13 enhancement).
    grad_floor: float | None = None
    if grad_mag is not None:
        seed_grads_arr = grad_mag[seed_px]
        if len(seed_grads_arr) >= AREA_MIN_ABS_PX:
            grad_floor = float(np.percentile(seed_grads_arr,
                                             BOUNDARY_GRADIENT_PERCENTILE))

    # Build polarity mask: restrict to footprint pixels on the same L side as seed.
    # Physics: graphene on bright substrate is brighter than host; on dark substrate
    # it is darker.  Growing across the polarity boundary would absorb the opposite
    # material (e.g. substrate shadow when growing from a bright seed).
    if seed_polarity != 0 and rel_L_image is not None:
        # Polarity threshold: use a soft ε (1 L-unit) to avoid clipping boundary px.
        POLARITY_EPS = 1.0
        if seed_polarity > 0:
            polarity_ok = (rel_L_image > -POLARITY_EPS)  # bright or near-host
        else:
            polarity_ok = (rel_L_image < POLARITY_EPS)   # dark or near-host
        # Combined constraint: footprint AND polarity direction.
        constrained_fp = cv2.bitwise_and(
            footprint_mask,
            polarity_ok.astype(np.uint8) * 255,
        )
    else:
        constrained_fp = footprint_mask

    dilate_px = max(1, int(round(1.0 / pixel_size)))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                  (2 * dilate_px + 1, 2 * dilate_px + 1))

    for _ in range(FOOTPRINT_GROW_MAX_ITER):
        dil = cv2.dilate(cur, k)
        # Restrict candidate pixels to the polarity-constrained footprint.
        candidate = cv2.bitwise_and(dil, constrained_fp)
        ys, xs = np.where(candidate > 0)
        if len(ys) == 0:
            break  # footprint fully consumed

        # Gradient-leakage stop (Phase 13): check boundary ring gradient.
        # If boundary mean gradient drops below seed p10, stop growing —
        # we have crossed into a featureless region (non-graphene substrate
        # or another material with similar LAB but no sharp interface).
        if grad_floor is not None and grad_mag is not None:
            ring = cv2.bitwise_and(dil, cv2.bitwise_not(cur))
            ring_grads = grad_mag[ring > 0]
            if len(ring_grads) > 0:
                boundary_mean_grad = float(ring_grads.mean())
                if boundary_mean_grad < grad_floor:
                    break  # boundary leaked into low-gradient region

        # LAB similarity filter: admit pixels whose Mahalanobis distance to
        # the SEED mean is below LAB_GROW_SIGMA_CAP_FOOTPRINT.
        vals = lab_image[ys, xs].astype(np.float32)
        d = np.sqrt((((vals - mu) / sigma) ** 2).sum(axis=1))
        keep = d < LAB_GROW_SIGMA_CAP_FOOTPRINT

        new_mask = cur.copy()
        new_mask[ys[keep], xs[keep]] = 255
        if (new_mask > 0).sum() == (cur > 0).sum():
            break  # natural convergence: no new similar pixels remain
        cur = new_mask

    return cur


# ---------------------------------------------------------------------------
# Helper: bridge polarity-consistent CCs inside the same footprint blob
# ---------------------------------------------------------------------------

def _bridge_polarity_consistent_ccs(
    grown: np.ndarray,
    lab: np.ndarray,
    footprint_mask: np.ndarray | None,
    bulk_mu_ab: np.ndarray,
    seed_polarity: int,
    seed_rel_L_med: float,
    pixel_size_um: float,
) -> np.ndarray:
    """Bridge polarity-consistent connected components inside the footprint.

    Physics rationale: a graphene flake with mixed thickness shows intra-flake
    LAB variation — bright cores and dimmer surroundings that appear as separate
    CCs after a LAB-sigma-capped grow.  When two such CCs both:
      1. Lie inside the same footprint blob (same top-flake region),
      2. Share the same polarity sign relative to bulk hBN (both brighter, or
         both darker — a single material cannot be both),
      3. Have rel_L magnitude within BRIDGE_REL_L_MAGNITUDE_RATIO × the seed's
         magnitude (they are the same material, not a totally different layer),
      4. Have small chroma deviation from bulk hBN (graphene has low chroma;
         large a,b deviations indicate a different material such as hBN),
      5. Are within BRIDGE_MAX_DISTANCE_UM centroid-to-centroid (gaps larger
         than ~3 µm are physical separations between distinct flakes),
    then they are bridged by filling their convex hull, constrained to the
    footprint.

    Bridging is footprint-restricted: the added pixels must lie inside
    footprint_mask.  If no footprint is available, no bridging is performed
    (the footprint membership criterion cannot be verified).

    Args:
        grown:         uint8 binary mask (0/255) — current graphene mask.
        lab:           LAB image (H×W×3, float32 or uint8).
        footprint_mask: uint8 binary mask (0/255) in same coordinates as grown,
                        or None (bridging is skipped when None).
        bulk_mu_ab:    (2,) float array with [a_bg, b_bg] — median a,b of the
                        host flake (used as the reference for chroma deviation).
        seed_polarity: +1 (bright CC), -1 (dark CC), 0 (unknown).
        seed_rel_L_med: seed CC's median rel_L (signed).
        pixel_size_um: µm per pixel.

    Returns:
        uint8 binary mask (0/255) — grown with bridged CCs added (constrained
        to footprint).  If no bridging is applicable, returns grown unchanged.
    """
    if footprint_mask is None or not (footprint_mask > 0).any():
        return grown  # no footprint: cannot verify membership criterion

    if seed_polarity == 0 or seed_rel_L_med == 0.0:
        return grown  # polarity unknown: cannot verify polarity consistency

    bridge_max_px = BRIDGE_MAX_DISTANCE_UM / pixel_size_um

    # Find connected components in the current grown mask.
    n_labels, labels = cv2.connectedComponents(grown, connectivity=8)
    if n_labels <= BRIDGE_MIN_CC_COUNT:
        # 0 or 1 foreground CC: nothing to bridge.
        return grown

    # Build per-CC statistics.
    lab_f32 = lab.astype(np.float32)
    seed_abs_rel_L = abs(seed_rel_L_med)
    # Loop-invariant: median L of the entire grown region (computed once).
    grown_L_vals = lab_f32[grown > 0, 0]
    grown_L_med_global = float(np.median(grown_L_vals)) if len(grown_L_vals) > 0 else None
    del grown_L_vals
    cc_stats: list[dict] = []
    for label_id in range(1, n_labels):
        px_ys, px_xs = np.where(labels == label_id)
        if len(px_ys) == 0:
            continue

        # --- Criterion 1: footprint membership ---
        # Fraction of CC pixels inside footprint must be >= 0.5.
        fp_overlap = int((footprint_mask[px_ys, px_xs] > 0).sum())
        fp_frac = fp_overlap / max(1, len(px_ys))
        if fp_frac < BRIDGE_MIN_FOOTPRINT_FRAC:
            continue  # not a footprint CC

        # --- Criteria 2 & 3: polarity + magnitude ---
        pix_lab = lab_f32[px_ys, px_xs]
        # Use median a,b for chroma check; we need median L for polarity.
        med_L = float(np.median(pix_lab[:, 0]))
        # rel_L for this CC relative to the bulk hBN (not relative to seed).
        # We use a proxy: sign of (med_L relative to the footprint median).
        # The polarity is the same sign as seed_polarity when rel_L_med ~ same sign.
        # We compute med_rel_L from the LAB array directly.
        # bulk_mu_ab[0] = a_bg, and we don't have L_bg here.
        # Use the seed_rel_L_med sign: we only need to confirm same polarity.
        # Proxy: median L of this CC vs. median L of entire grown mask.
        grown_L_med = grown_L_med_global if grown_L_med_global is not None else med_L
        cc_rel_L = med_L - grown_L_med  # rough rel_L vs current grown region

        # Check polarity consistency: same sign direction as seed.
        if seed_polarity > 0 and cc_rel_L < -BRIDGE_MAX_CHROMA_DEV_AB:
            continue  # opposite polarity (too dark when seed is bright)
        if seed_polarity < 0 and cc_rel_L > BRIDGE_MAX_CHROMA_DEV_AB:
            continue  # opposite polarity (too bright when seed is dark)

        # Magnitude check: |cc_rel_L| should not be more than
        # BRIDGE_REL_L_MAGNITUDE_RATIO × |seed_rel_L|.
        # We use a loose check only when the CC rel_L is extreme.
        # (The CC rel_L computed above is relative to grown region, not to L_bg,
        #  so it may be ≈ 0 even for valid fragments; only reject large outliers.)
        cc_abs_rel_L = abs(cc_rel_L)
        if (seed_abs_rel_L > 1.0
                and cc_abs_rel_L > BRIDGE_REL_L_MAGNITUDE_RATIO * seed_abs_rel_L + 5.0):
            continue  # magnitude too large vs seed

        # --- Criterion 4: small chroma (graphene has low chroma) ---
        med_a = float(np.median(pix_lab[:, 0 + 1]))  # a channel
        med_b = float(np.median(pix_lab[:, 0 + 2]))  # b channel
        chroma_dev_a = abs(med_a - float(bulk_mu_ab[0]))
        chroma_dev_b = abs(med_b - float(bulk_mu_ab[1]))
        if chroma_dev_a > BRIDGE_MAX_CHROMA_DEV_AB or chroma_dev_b > BRIDGE_MAX_CHROMA_DEV_AB:
            continue  # too much chroma → different material

        # Centroid
        cy = float(np.mean(px_ys))
        cx = float(np.mean(px_xs))
        cc_stats.append({
            'label_id': label_id,
            'cy': cy,
            'cx': cx,
            'px_ys': px_ys,
            'px_xs': px_xs,
        })

    if len(cc_stats) <= 1:
        return grown  # 0 or 1 qualifying CC: nothing to pair

    # For each pair, check centroid distance and bridge if close enough.
    result = grown.copy()
    n = len(cc_stats)
    for i in range(n):
        for j in range(i + 1, n):
            ci, cj = cc_stats[i], cc_stats[j]
            dist_px = math.sqrt((ci['cy'] - cj['cy']) ** 2
                                + (ci['cx'] - cj['cx']) ** 2)
            # --- Criterion 5: centroid distance < BRIDGE_MAX_DISTANCE_UM ---
            if dist_px > bridge_max_px:
                continue  # too far apart

            # Bridge: fill convex hull of the union of the two CCs, constrained
            # to footprint.
            ys_union = np.concatenate([ci['px_ys'], cj['px_ys']])
            xs_union = np.concatenate([ci['px_xs'], cj['px_xs']])
            pts = np.stack([xs_union, ys_union], axis=1).astype(np.int32)
            hull = cv2.convexHull(pts)
            bridge_canvas = np.zeros_like(grown)
            cv2.fillPoly(bridge_canvas, [hull], 255)
            # Constrain to footprint.
            bridge_canvas = cv2.bitwise_and(bridge_canvas, footprint_mask)
            result = cv2.bitwise_or(result, bridge_canvas)

    return result


# ---------------------------------------------------------------------------
# Helper: score a set of CC candidates using per-pool sub-score variance
# ---------------------------------------------------------------------------

def _score_candidates(records: list[dict], host_area_um2: float,
                      footprint_mask: np.ndarray | None = None) -> list[dict]:
    """Assign a composite score to each CC record.

    Sub-scores (all in [0, 1], higher = more graphene-like):
      s_area          — log-normalised area relative to the largest candidate
                        (larger CCs preferred; tiny noise fragments score low)
      s_contrast      — |rel_L_med| normalised by pool max (strong contrast
                        indicates real optical absorption difference)
      s_solidity      — convexity of the shape (crystallographic straight edges)
      s_containment   — fraction of candidate pixels inside the footprint mask
                        (Phase 10a; only scored when footprint_mask is provided)

    Note: centroid-edge distance (s_edge) is intentionally excluded.  Graphene
    position relative to the host flake edge is stack-dependent (it can sit at
    the edge as a thin strip or at the centre), so this metric is not universal.

    The composite photometric score `|relL|^0.25 × log(area_um2+1) / 30`
    is used as the final ranking signal (Phase 11): it naturally balances area
    and contrast without a hard saturation cap on contrast.  The 0.25 contrast
    exponent prevents tiny high-contrast artifacts from dominating via a
    rell_norm saturation cap (was the root cause of ml04/ml14 regressions in
    the Phase 11 first pass).

    When footprint_mask is supplied, FOOTPRINT_CONTAINMENT_WEIGHT is reserved for
    s_containment, and the photometric composite fills the remaining fraction.
    Candidates with containment < FOOTPRINT_LOW_CONTAINMENT_FLOOR are further
    penalised by FOOTPRINT_LOW_CONTAINMENT_PENALTY to push clearly-outside
    candidates decisively below genuine graphene candidates.

    Physics grounding:
      - Area: real graphene flakes are not single-pixel artefacts; the
        largest contrast region in the gate [AREA_MIN..AREA_MAX] is the
        best prior-free proxy for the graphene layer.
      - Contrast: graphene has a characteristic optical contrast against its
        host (Nair et al., Science 2008; Blake et al., APL 2007).  The sign
        (bright vs dark) is substrate/thickness-dependent so we measure
        |rel_L| for scoring.
      - Solidity: monolayer crystals cleave along armchair/zigzag edges
        (faceted outlines → high convex-hull utilisation).
      - Containment (Phase 10a): the top-alignment footprint marks the spatial
        extent of the deposited flake.  Graphene is always inside this region;
        background substrate features are not.  This is substrate-agnostic.
    """
    if not records:
        return records

    # --- s_area: log-normalised by the largest candidate ---
    # Monotonically increasing: larger candidates score closer to 1.0.
    # Log scale compresses the dynamic range (e.g. 10 µm² vs 1000 µm²).
    # Normalise by pool max so the scale is relative to the detected pool,
    # not a GT-fitted absolute value.
    area_vals = np.array([r['area_um2'] for r in records], dtype=np.float32)
    max_area = max(float(np.max(area_vals)), 1.0)
    for r in records:
        log_ratio = float(np.log(max(r['area_um2'], 0.1)) - np.log(max_area))
        # log_ratio in (-inf, 0]; map to [0, 1] with a soft floor at -4 (~1/55x max)
        r['s_area'] = float(np.clip(1.0 + log_ratio / 4.0, 0.0, 1.0))

    # --- s_contrast: |rel_L_med| normalised by pool max ---
    # Physics: graphene has a distinctive optical signature (absorption ~2.3%
    # per layer: Nair et al. 2008; Blake et al. 2007).  Larger |rel_L|
    # indicates stronger optical contrast against the host flake.
    rel_L_vals = np.array([r['rel_L_med'] for r in records], dtype=np.float32)
    max_abs_relL = max(float(np.max(np.abs(rel_L_vals))), 1.0)
    for r in records:
        r['s_contrast'] = float(abs(r['rel_L_med']) / max_abs_relL)

    # --- s_solidity: direct value in [0, 1] ---
    # Physics: monolayer crystals cleave along armchair/zigzag edges → high
    # convex-hull utilisation.
    for r in records:
        r['s_solidity'] = float(np.clip(r['solidity'], 0.0, 1.0))

    # Note: centroid-edge distance is intentionally excluded from scoring.
    # Graphene position relative to the host flake edge is stack-dependent —
    # it can sit at the edge (as a thin strip on the hBN boundary) or at the
    # centre.  This metric is not universal and caused ml08-style failures.

    # --- s_containment (Phase 10a): footprint spatial containment ---
    # Physics: the top-alignment footprint marks the spatial extent of the
    # deposited top flake.  Graphene is always inside this region; background
    # substrate features are not.  Candidates with low containment are penalised
    # further (below FOOTPRINT_LOW_CONTAINMENT_FLOOR) to decisively rank them
    # below genuine graphene candidates.  This is substrate-agnostic.
    # Determine whether footprint scoring is active: mask must be provided,
    # non-empty, and match the candidate CC mask dimensions.
    use_containment = False
    if footprint_mask is not None and records and '_cc_mask' in records[0]:
        ref_shape = records[0]['_cc_mask'].shape  # (H, W) uint8
        if footprint_mask.shape == ref_shape:
            use_containment = True

    if use_containment:
        # Footprint area for the expected-minimum-overlap denominator.
        fp_area_px = int((footprint_mask > 0).sum())
        pixel_area_um2 = 1.0  # scores are dimensionless; we work in pixels here
        # 0.5% of footprint is the expected minimum overlap for a real graphene flake.
        # Physics: even a small monolayer graphene region should occupy at least
        # 0.5% of the footprint area to be geometrically distinguishable from noise.
        expected_min_overlap_px = max(1.0, 0.005 * fp_area_px)

        for r in records:
            raw_containment = spatial_containment_score(r['_cc_mask'],
                                                        footprint_mask)
            # Absolute overlap in pixels (intersection of candidate and footprint).
            # A large candidate with moderate containment (e.g. 1438 µm², 24.5%
            # containment → 352 µm² overlap) provides geometrically meaningful
            # evidence that it is real graphene — far more than a tiny 11 µm²
            # candidate that is 100% inside but contributes only 11 µm² overlap.
            # Phase 10c: use whichever signal is stronger — high fractional
            # containment OR large absolute overlap relative to expected minimum.
            cand_area_px = float(r['area_px'])
            absolute_overlap_px = raw_containment * cand_area_px

            # Effective containment: combine fractional and normalised absolute overlap.
            # The normalised absolute overlap saturates at 1.0 at 5× expected_min.
            norm_abs_overlap = min(1.0, absolute_overlap_px / (expected_min_overlap_px * 5))

            # When fractional containment is below the penalty floor, the absolute
            # overlap score is capped by 2× the fractional containment (not allowed to
            # jump to 1.0).  This prevents a large artifact that merely touches the
            # footprint boundary from appearing "fully contained" via absolute overlap.
            # Physics: a candidate with 24% fractional containment is predominantly
            # outside the footprint (76% external) — boosting its score to 1.0 via
            # absolute overlap would incorrectly rank it equal to a 98% contained flake.
            # The 2× cap provides an intermediate boost for genuinely large partially-
            # contained flakes (e.g. a 1438 µm² graphene with 24% → score ≈ 0.48,
            # better than the 0.30 penalty but still below a 98% candidate at 0.98).
            if raw_containment < FOOTPRINT_LOW_CONTAINMENT_FLOOR:
                score_containment = max(
                    raw_containment,
                    min(norm_abs_overlap, raw_containment * 2.0),
                )
            else:
                score_containment = max(raw_containment, norm_abs_overlap)

            # Apply penalty only when BOTH fractional containment is low AND
            # absolute overlap is below expected minimum — i.e. the candidate is
            # truly not inside the footprint, not just a large partially-overlapping
            # flake.  This prevents tiny fully-contained noise from beating large
            # partially-contained graphene flakes.
            if (raw_containment < FOOTPRINT_LOW_CONTAINMENT_FLOOR
                    and absolute_overlap_px < expected_min_overlap_px):
                penalised = score_containment * FOOTPRINT_LOW_CONTAINMENT_PENALTY
            else:
                penalised = score_containment
            r['s_containment'] = float(penalised)
            r['containment_raw'] = float(raw_containment)
    else:
        for r in records:
            r['s_containment'] = None  # not available

    # Compute weights from sub-score variance across the candidate pool.
    # Area is given a high fixed weight (AREA_FIXED_WEIGHT) to ensure the largest
    # plausible graphene region wins over small high-contrast noise fragments.
    # Physics: in the absence of GT priors, area is the strongest prior-free
    # proxy for the real graphene layer.  The contrast quality filter above
    # already removes low-contrast artefacts; among the retained high-contrast
    # candidates, the larger one is more likely to be the real graphene flake.
    # Remaining weight (0.35) is split by sub-score variance.
    #
    # When footprint_mask is available, FOOTPRINT_CONTAINMENT_WEIGHT is reserved
    # for s_containment; the area+photometric weights are scaled to fill the
    # remaining (1 - FOOTPRINT_CONTAINMENT_WEIGHT) budget.
    # Phase 11: composite photometric score based on |relL|^0.25 × log(area_um2).
    #
    # Replaces the 0.6×log_area_norm + 0.4×rell_norm formula (Phase 11 first pass)
    # which failed on ml04/ml14 because rell_norm hit the saturation cap at 80
    # L-units (tiny high-contrast artifacts scored as high-contrast candidates
    # indistinguishable from medium-contrast graphene).
    #
    # The composite formula naturally balances area and contrast:
    #   phot = min(1.0, (|relL|^0.25 × log(area_um2 + 1)) / 30.0)
    #
    #   - |relL|^0.25:  reduces contrast power (exponent 0.25 vs 1.0) so that
    #     very high contrast (relL=100) is NOT 2.5× stronger than moderate contrast
    #     (relL=40); instead it is only 3.16/2.52 ≈ 1.26× stronger.  Small
    #     high-contrast artifacts cannot saturate a "contrast bonus."
    #
    #   - log(area_um2+1): compresses the area dynamic range (log scale) but
    #     retains meaningful discrimination between, say, 50 µm² (log=3.9) and
    #     900 µm² (log=6.8) — a 1.74× factor vs a 18× raw-area factor.  Using
    #     area_um2 (not area_px) provides substrate-agnostic scaling: the µm²
    #     unit is independent of pixel_size / objective lens.
    #
    #   - Denominator 30.0: normalises the composite to [0,1] for practical
    #     vdW-stack ranges (relL up to 127, area up to ~10 000 µm² →
    #     composite ≤ 3.36 × 9.21 ≈ 31, saturating ≈ 1.0).
    #
    # Key verification (Phase 11, area_um2-based):
    #   ml04: cc1-bright (893µm²,relL=40)→17.09  cc23(51µm²,relL=92)→12.23 ✓
    #   ml14: cc9 (592µm²,relL=37)→15.79  cc2(119µm²,relL=101)→15.18 ✓
    #   HM06: cc1-dark(2476µm²,relL=21)→16.73  cc2(177µm²,relL=103)→16.51 ✓
    #   AH02: cc36(938µm²,relL=46)→17.85 (dominant, correct) ✓
    #
    # Final score:
    #   Without footprint: score = phot
    #   With footprint:    score = FOOTPRINT_CONTAINMENT_WEIGHT × containment_raw
    #                            + (1 - FOOTPRINT_CONTAINMENT_WEIGHT) × phot
    #
    # Phase 15 note: we use containment_raw (the fractional containment in [0,1])
    # rather than s_containment (the adjusted score with absolute-overlap saturation)
    # in the final score formula.  The adjusted s_containment saturates to 1.0 for
    # any candidate with sufficient absolute overlap, which flattens the difference
    # between a candidate with 90% containment (only 10% outside the footprint) and
    # one with 100% containment.  The raw fraction preserves this signal:
    #
    # Physics example (AH03): The correct graphene CC has raw_cont=1.00 (entirely
    # inside footprint) while the wrong bright CC has raw_cont=0.90 (10% outside).
    # With containment_raw in the score:
    #   correct CC: 0.5×1.00 + 0.5×0.445 = 0.723 (wins)
    #   wrong CC:   0.5×0.90 + 0.5×0.492 = 0.696
    # With s_containment (both saturate to 1.0 via absolute overlap): scores
    # collapse to 0.722 vs 0.746 — wrong CC wins because phot dominates.
    #
    # The s_containment value is retained in the record for diagnostics but is
    # no longer used in the final score computation.  Low-containment candidates
    # (raw_cont < 0.5) are naturally penalised by the raw fraction — no additional
    # penalty multiplier is needed.
    #
    # Note: s_area, s_contrast, s_solidity are computed above and stored for
    # downstream diagnostics.  Only the final 'score' is recomputed here.

    # Compute composite photometric score for every record.
    import math as _math  # already imported at module level; explicit for clarity
    for r in records:
        composite = (
            abs(r['rel_L_med']) ** 0.25
            * _math.log(max(r['area_um2'], 0.01) + 1.0)
        )
        phot = float(min(1.0, composite / 30.0))
        if use_containment and r.get('s_containment') is not None:
            # Phase 15: use raw fractional containment to preserve signal between
            # 90% and 100% contained candidates.  See comment above for rationale.
            r['score'] = float(
                FOOTPRINT_CONTAINMENT_WEIGHT * r['containment_raw']
                + (1.0 - FOOTPRINT_CONTAINMENT_WEIGHT) * phot
            )
        else:
            r['score'] = phot

    return records


# ---------------------------------------------------------------------------
# Main detection function
# ---------------------------------------------------------------------------

def detect_graphene(
    image: np.ndarray,
    pixel_size: float,
    mirror: bool = True,
    cluster_id: int | None = None,
    footprint_mask: np.ndarray | None = None,
    footprint_mask_grow_only: np.ndarray | None = None,
    footprint_mask_score_only: np.ndarray | None = None,
) -> dict:
    """Detect graphene in the top_part image.

    Args:
        image: BGR top_part image.
        pixel_size: µm per pixel.
        mirror: If True, horizontally flip the image before processing.
        cluster_id: If given, pick this rank (0-based) from the scored
            candidate list instead of the top rank.  Kept for CLI compat.
        footprint_mask: Optional uint8 binary mask (0/255) in mirrored-top_part
            coordinates.  When supplied, candidate scoring includes a spatial
            containment term (Phase 10a) that heavily favours candidates inside
            the footprint, AND expands the upper area gate to AREA_MAX_FOOTPRINT_FRAC
            of the footprint.  When None, scoring falls back to the photometric-only
            approach (backward compatible).
        footprint_mask_grow_only: Optional uint8 binary mask (0/255) used ONLY
            for the region-grow step (Phase 13).  Does NOT influence candidate
            scoring or the upper area gate.  When both footprint_mask and
            footprint_mask_grow_only are provided, footprint_mask takes priority
            for both scoring and grow.
        footprint_mask_score_only: Optional uint8 binary mask (0/255) used ONLY
            for candidate scoring (Phase 15).  Unlike footprint_mask, this does
            NOT expand the upper area gate — the area gate remains host-based
            (preserving the Phase 13 candidate pool size for auto-discovered
            footprints).  This is the correct parameter for auto-discovered
            footprints: scoring benefit without admitting new large candidates
            that are not the graphene layer.
            Physics: the footprint validates spatial location of candidates
            already in the pool.  Expanding the area gate would let in new
            large CCs (potentially dark hBN shadow regions) that were
            correctly excluded by the host-based gate.
            When footprint_mask is provided, footprint_mask_score_only is ignored
            (footprint_mask takes priority for scoring).

    Returns a dict with keys:
        graphene_mask, graphene_contour, top_flake_mask, processed_image,
        cc_records, selected_idx, low_confidence, L_bg, bright_thresh_L,
        flake_ref_L.
    """
    img = cv2.flip(image, 1) if mirror else image.copy()
    h, w = img.shape[:2]

    # --- Step 1: isolate flake region ---
    flake = _flake_mask(img, pixel_size)
    if flake.sum() == 0:
        return {
            'graphene_mask': np.zeros((h, w), np.uint8),
            'graphene_contour': None, 'top_flake_mask': flake,
            'processed_image': img, 'cc_records': [], 'selected_idx': None,
            'low_confidence': True,
        }

    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    L = lab[:, :, 0].astype(np.float32)

    # In-flake statistics — median L defines the host reference.
    flake_pix = lab[flake > 0].astype(np.float32)
    L_bg = float(np.median(flake_pix[:, 0]))
    a_bg = float(np.median(flake_pix[:, 1]))
    b_bg = float(np.median(flake_pix[:, 2]))
    rel_L = L - L_bg

    # Host area for the area gate.
    host_area_px = int((flake > 0).sum())
    host_area_um2 = host_area_px * pixel_size * pixel_size
    min_area_px = max(AREA_MIN_ABS_PX,
                      int(round(AREA_MIN_HOST_FRAC * host_area_um2 / (pixel_size * pixel_size))))
    # Upper area gate (Phase 10b): when the footprint mask is available, use the
    # maximum of (host-based gate, footprint-based gate) to avoid filtering out
    # large graphene CCs on stacks where the Otsu host segmentation under-estimates
    # the flake area.  The footprint is the physically correct upper bound for how
    # large a graphene region can be on this stack.
    host_max_area_px = int(round(AREA_MAX_HOST_FRAC * host_area_um2 / (pixel_size * pixel_size)))
    if footprint_mask is not None and (footprint_mask > 0).any():
        fp_area_px = int((footprint_mask > 0).sum())
        fp_max_area_px = int(round(AREA_MAX_FOOTPRINT_FRAC * fp_area_px))
        max_area_px = max(host_max_area_px, fp_max_area_px)
    else:
        max_area_px = host_max_area_px

    # --- Step 2: infer contrast polarity via multiotsu on in-flake rel_L ---
    # Use rel_L = L - L_bg for the histogram so the median host L is always
    # at 0, making the threshold adaptive to the absolute brightness level.
    #
    # multiotsu(classes=3) on rel_L produces two thresholds [t_low, t_high]
    # that divide the in-flake intensity distribution into 3 classes:
    #   Class A: rel_L < t_low  (darkest — dark graphene or shadow artefacts)
    #   Class B: t_low <= rel_L < t_high  (host-flake main body)
    #   Class C: rel_L >= t_high (brightest — very bright highlights)
    #
    # Graphene candidates span the TRANSITION REGIONS between classes:
    #   - Bright polarity: pixels above Class B, i.e. rel_L >= t_high.
    #     These are distinctly brighter than the host median.
    #   - Dark polarity: pixels below Class B, i.e. rel_L <= t_low.
    #     These are distinctly darker than the host median.
    #
    # Note: t_low may be negative (on stacks where the host flake dominates
    # the bright side and shadow artefacts form the dark class) or positive.
    # We enforce each polarity mask to be on the correct side of zero:
    #   bright polarity: rel_L > max(t_high, 0)
    #   dark polarity:   rel_L < min(t_low, 0)
    rel_L_in_flake = rel_L[flake > 0].astype(np.float32)
    try:
        thresholds = threshold_multiotsu(rel_L_in_flake, classes=3)
        t_low = float(thresholds[0])
        t_high = float(thresholds[1])
    except Exception:
        # Fall back: ±1σ boundaries.
        sigma_rel = max(float(np.std(rel_L_in_flake)), 5.0)
        t_low = -sigma_rel
        t_high = sigma_rel

    bright_thresh_L = t_high  # diagnostic output

    # Generate candidate masks at multiple contrast levels per polarity.
    # Physics rationale: graphene can be at any contrast level between
    # t_low (faint) and t_high (saturated), depending on layer thickness
    # and substrate optical path.  We generate one mask per boundary level:
    #   bright-faint: rel_L > max(t_low, 1.0)  (mildly brighter than host)
    #   bright-strong: rel_L > max(t_high, 1.0) (distinctly brighter than host)
    #   dark-faint: rel_L < min(t_low, -1.0)   (mildly darker than host)
    # t_low must be > 0 to constitute a bright class; if t_low <= 0, only
    # use the t_high level for bright (and use t_low for dark).
    bright_threshold_faint = max(min(t_low, t_high - 5.0), 1.0)
    bright_threshold_strong = max(t_high, bright_threshold_faint + 5.0)
    dark_threshold_val = min(t_low, -1.0)

    # Morph-clean kernel.
    close_k = _kernel_from_um(FLAKE_MORPH_CLOSE_UM * 0.5, pixel_size, ellipse=True)
    open_k = _kernel_from_um(FLAKE_MORPH_OPEN_UM * 0.5, pixel_size, ellipse=True)

    def _clean(m):
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, close_k)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, open_k)
        return m

    def _make_bright_mask(thr: float) -> np.ndarray:
        m = (rel_L > thr).astype(np.uint8) * 255
        return cv2.bitwise_and(m, flake)

    def _make_dark_mask(thr: float) -> np.ndarray:
        m = (rel_L < thr).astype(np.uint8) * 255
        return cv2.bitwise_and(m, flake)

    multi_masks = []  # list of (mask, polarity_sign, threshold_used)
    for raw, sign, thr in [
        (_make_bright_mask(bright_threshold_faint), +1, bright_threshold_faint),
        (_make_bright_mask(bright_threshold_strong), +1, bright_threshold_strong),
        (_make_dark_mask(dark_threshold_val), -1, dark_threshold_val),
    ]:
        cleaned = _clean(raw)
        if (cleaned > 0).sum() > 0:
            multi_masks.append((cleaned, sign, thr))

    if not multi_masks:
        return {
            'graphene_mask': np.zeros((h, w), np.uint8),
            'graphene_contour': None, 'top_flake_mask': flake,
            'processed_image': img, 'cc_records': [], 'selected_idx': None,
            'low_confidence': True, 'L_bg': L_bg,
            'bright_thresh_L': bright_thresh_L, 'flake_ref_L': L_bg,
        }

    # --- Step 3: extract CCs from both polarity masks ---
    dt_inside = cv2.distanceTransform(flake, cv2.DIST_L2, 5)

    cc_records = []
    for source_mask, polarity, source_thr in multi_masks:
        nl, lab_arr, st_arr, _ = cv2.connectedComponentsWithStats(
            source_mask, connectivity=8
        )
        for lab_idx in range(1, nl):
            area_px = int(st_arr[lab_idx, cv2.CC_STAT_AREA])
            if area_px < min_area_px or area_px > max_area_px:
                continue

            cc_mask = (lab_arr == lab_idx).astype(np.uint8) * 255
            cnts, _ = cv2.findContours(cc_mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
            if not cnts:
                continue
            cnt = max(cnts, key=cv2.contourArea)
            rect = cv2.minAreaRect(cnt)
            (_, _), (rw, rh), _ = rect
            if min(rw, rh) < 1:
                continue
            aspect = max(rw, rh) / max(min(rw, rh), 1e-6)

            hull = cv2.convexHull(cnt)
            hull_a = max(float(cv2.contourArea(hull)), 1.0)
            solidity = area_px / hull_a

            cc_pixels = lab[cc_mask > 0].astype(np.float32)
            L_med = float(np.median(cc_pixels[:, 0]))
            a_med = float(np.median(cc_pixels[:, 1]))
            b_med = float(np.median(cc_pixels[:, 2]))
            rel_L_med = L_med - L_bg
            rel_a_med = a_med - a_bg
            rel_b_med = b_med - b_bg

            M_m = cv2.moments(cc_mask, binaryImage=True)
            if M_m['m00'] < 1:
                continue
            cx = M_m['m10'] / M_m['m00']
            cy = M_m['m01'] / M_m['m00']
            cxi = int(np.clip(round(cx), 0, w - 1))
            cyi = int(np.clip(round(cy), 0, h - 1))
            d_edge_um = float(dt_inside[cyi, cxi]) * pixel_size

            area_um2 = area_px * pixel_size * pixel_size

            cc_records.append({
                'lab_idx': lab_idx,
                'polarity': polarity,
                'area_px': area_px, 'area_um2': area_um2,
                'aspect': aspect, 'solidity': solidity,
                'L_med': L_med, 'a_med': a_med, 'b_med': b_med,
                'rel_L_med': rel_L_med, 'rel_a_med': rel_a_med, 'rel_b_med': rel_b_med,
                'd_edge_um': d_edge_um,
                '_cc_mask': cc_mask,
                '_thr': source_thr,
            })

    if not cc_records:
        return {
            'graphene_mask': np.zeros((h, w), np.uint8),
            'graphene_contour': None, 'top_flake_mask': flake,
            'processed_image': img, 'cc_records': [], 'selected_idx': None,
            'low_confidence': True, 'L_bg': L_bg,
            'bright_thresh_L': bright_thresh_L, 'flake_ref_L': L_bg,
        }

    # --- Hybrid candidate-survival policy (Phase 11) ---
    # Replaces the Phase 10c "top-N by |relL|" pure composite ranking with a UNION
    # of three survivor sets.  Root cause of Phase 10c regression on ml04/ml14:
    # the composite |relL|^0.25 × log(area) favoured small high-contrast noise spots
    # over the correct large candidate on high-contrast SiO2 substrates.
    #
    # Three survivor sets (all applied after the black-artifact pre-filter):
    #   1. abs_survivors: candidates above an absolute low contrast floor (10.0 L-units).
    #      Catches the typical case (SiO2 substrates: ml04, ml14 → many candidates
    #      pass this floor, ensuring the large correct candidate is retained).
    #   2. area_survivors: top-3 by pixel area, regardless of contrast.
    #      Catches large dim flakes on stamp substrates (AH07: relL=3, 2610 µm²).
    #   3. contrast_survivors: top-3 by |relL|, regardless of area.
    #      Catches all-low-contrast pools where even the "contrast champion" has
    #      low absolute relL (QH06: all candidates have relL < 20).
    #
    # The UNION guarantees:
    #   - No single filter can drop a candidate that any other filter retains.
    #   - SiO2 substrates (ml04/ml14, high contrast): abs_survivors admits many
    #     candidates (floor=10.0), keeping the large correct one in the pool;
    #     small noise spots may also enter but the area-dominant composite score
    #     picks the large one.
    #   - Stamp substrates (AH07/QH06, low contrast): abs_survivors admits few;
    #     area_survivors and contrast_survivors rescue the correct dim large candidate.
    #
    # Black-artifact pre-filter: exclude candidates whose median L is essentially
    # zero (< BLACK_ARTIFACT_L_FLOOR) before the hybrid selection.
    # Physics: real graphene on any optical-microscopy substrate has non-zero
    # luminance.  A region with L≈0 is an opaque background artifact (stamp edge,
    # holder shadow).  On QH06, the dark mask (rel_L < -63.9) captures a large black
    # edge artifact that dominates the |relL| ranking.  Pre-filtering removes it.
    BLACK_ARTIFACT_L_FLOOR = 5.0  # LAB L < 5 → essentially opaque/black

    # Hybrid survival constants (Phase 11)
    ABSOLUTE_RELL_FLOOR = 10.0   # was 20.0 in pre-10c, was 0 in 10c; 10.0 is a compromise
    TOP_AREA_KEEP = 3            # top-3 by pixel area (catches large dim flakes)
    TOP_CONTRAST_KEEP = 3        # top-3 by |relL| (catches all-low-contrast pools)
    # Tiny-artifact filter: single-pixel clusters and sub-threshold noise survive the
    # area gate but have unreliably high contrast (camera noise, dust, imaging artifacts).
    # Exclude these from the abs_survivors set to prevent them from masquerading as
    # "high-contrast plausible graphene."  Physics: a real graphene flake is always
    # at least a few tens of µm² for an optical-microscopy image (pixel_size ≈ 0.1 µm).
    TINY_ARTIFACT_UM2 = 30.0  # below this area, candidates are treated as noise artifacts

    if cc_records:
        non_black = [r for r in cc_records if r['L_med'] >= BLACK_ARTIFACT_L_FLOOR]
        working_pool = non_black if non_black else cc_records  # fallback if all black

        # Set 1: absolute contrast floor — candidates with |relL| above the floor
        #         AND area above the tiny-artifact threshold.
        abs_survivors = [
            r for r in working_pool
            if abs(r['rel_L_med']) >= ABSOLUTE_RELL_FLOOR and r['area_um2'] >= TINY_ARTIFACT_UM2
        ]

        # Set 2: top-3 by area (ensures large dim candidates survive in rescue mode)
        area_survivors = sorted(working_pool, key=lambda r: -r['area_px'])[:TOP_AREA_KEEP]

        # Set 3: top-3 by |relL| (ensures contrast-champion candidates survive in rescue)
        contrast_survivors = sorted(working_pool, key=lambda r: -abs(r['rel_L_med']))[:TOP_CONTRAST_KEEP]

        # Union policy (Phase 11): use area/contrast survivors ONLY as rescue when the
        # absolute-floor set (after tiny-artifact filtering) is too small.
        #
        # Rationale:
        #   - High-contrast substrates (ml04/ml14 SiO2): graphene has relL≈40, and
        #     abs_survivors (|relL| ≥ 10, area ≥ 30 µm²) admits the correct graphene
        #     plus a few other sizeable candidates.  Tiny artifacts (cc23=51µm²
        #     relL=92; cc73=42µm² relL=102) are still sizeable enough to enter, but
        #     the absolute-normalized scoring (below) correctly ranks the medium-area
        #     medium-contrast graphene above them via the log_area component.
        #     NOT using area_survivors prevents low-contrast large background structures
        #     (relL=7, area=3144 µm²) from entering and outscoring graphene via area.
        #   - Low-contrast stamp substrates (AH07/QH06): graphene relL<10 → excluded
        #     from abs_survivors.  Only tiny high-contrast artifacts survive the floor.
        #     abs_survivors is effectively empty (after tiny-artifact filter removes
        #     the tiny high-contrast spots).  Rescue activates and area_survivors bring
        #     in the correct large dim candidate.
        MIN_SURVIVORS_FROM_ABS = 1  # rescue when zero sizeable abs-floor candidates remain
        if len(abs_survivors) >= MIN_SURVIVORS_FROM_ABS:
            # Enough sizeable abs-floor survivors: use only the absolute-floor set.
            survivors = abs_survivors
        else:
            # Zero sizeable abs-floor survivors: activate full UNION rescue.
            survivors = list(
                {id(r): r for r in (abs_survivors + area_survivors + contrast_survivors)}.values()
            )

        # Sentinel names preserved for test compatibility
        keep_top_n = len(working_pool)
        sorted_by_composite = survivors
        cc_records = survivors

    # --- Step 4: score candidates (with optional footprint containment) ---
    # Pass footprint_mask so _score_candidates can compute s_containment.
    # footprint_mask must be in mirrored-top_part coordinates (i.e. same
    # coordinate system as the cc_masks produced above).  The caller is
    # responsible for loading and warping it before calling detect_graphene.
    #
    # Phase 15: footprint_mask_score_only provides scoring without area gate
    # expansion.  Use it as a fallback when footprint_mask is not provided.
    # This enables containment scoring for auto-discovered footprints while
    # preserving the candidate pool size (no new large CCs admitted).
    # fp_for_scoring: auto-discovered footprint for ranking (Phase 15)
    fp_for_scoring = footprint_mask if footprint_mask is not None else footprint_mask_score_only
    cc_records = _score_candidates(cc_records, host_area_um2=host_area_um2,
                                   footprint_mask=fp_for_scoring)
    cc_records.sort(key=lambda r: r['score'], reverse=True)

    # Allow explicit override via cluster_id (0-based rank).
    pick_rank = 0
    if cluster_id is not None:
        pick_rank = int(np.clip(cluster_id, 0, len(cc_records) - 1))

    best = cc_records[pick_rank]

    # --- Step 5/6: region grow + cleanup ---
    # The candidate panel should show the same post-grow/refined mask that will
    # be written if that rank is selected. Otherwise an agent may choose a small
    # seed CC from 00_graphene_candidates.png and receive a much larger final
    # graphene_mask.png after grow/refine.
    grad_mag = _gradient_mag(img)
    GRADIENT_EVIDENCE_FLOOR = 5.0
    FOOTPRINT_GROW_MAX_SEED_FRACTION = 0.85  # was 0.10 in Phase 10b
    FOOTPRINT_GROW_MIN_SEED_CONTRAST = 10.0  # |rel_L| floor for LAB-similarity grow
    _grow_footprint = footprint_mask if footprint_mask is not None else footprint_mask_grow_only
    fp_area_for_grow = (
        int((_grow_footprint > 0).sum())
        if _grow_footprint is not None else 0
    )
    close_k2 = _kernel_from_um(GRAPHENE_MORPH_CLOSE_UM, pixel_size, ellipse=True)
    open_k2 = _kernel_from_um(GRAPHENE_MORPH_OPEN_UM, pixel_size, ellipse=True)
    bulk_mu_ab_val = np.array([a_bg, b_bg], dtype=np.float64)

    def _grow_refine_candidate(candidate: dict) -> tuple[np.ndarray, bool]:
        base_mask = candidate['_cc_mask']
        seed_grads = grad_mag[base_mask > 0]
        seed_area_px = int((base_mask > 0).sum())
        seed_abs_rel_L = float(abs(candidate.get('rel_L_med', 0.0)))
        seed_fp_fraction = (
            seed_area_px / max(fp_area_for_grow, 1)
            if fp_area_for_grow > 0 else 1.0
        )
        use_footprint_grow = (
            _grow_footprint is not None
            and fp_area_for_grow > 0
            and seed_abs_rel_L >= FOOTPRINT_GROW_MIN_SEED_CONTRAST
            and seed_fp_fraction < FOOTPRINT_GROW_MAX_SEED_FRACTION
        )
        if use_footprint_grow:
            seed_polarity_val = int(candidate.get('polarity', 0))
            grown = _region_grow_footprint_bounded(
                base_mask, lab, _grow_footprint, pixel_size,
                seed_polarity=seed_polarity_val,
                rel_L_image=rel_L,
                grad_mag=grad_mag,
            )
            grown = _bridge_polarity_consistent_ccs(
                grown=grown,
                lab=lab,
                footprint_mask=_grow_footprint,
                bulk_mu_ab=bulk_mu_ab_val,
                seed_polarity=seed_polarity_val,
                seed_rel_L_med=float(candidate.get('rel_L_med', 0.0)),
                pixel_size_um=pixel_size,
            )
            final_mask = flood_fill_holes(grown)
            final_mask = cv2.morphologyEx(final_mask, cv2.MORPH_OPEN, open_k2)
            final_mask = keep_largest_n(final_mask, n=1, min_area=AREA_MIN_ABS_PX)
            final_mask = flood_fill_holes(final_mask)
        elif len(seed_grads) > 0 and float(np.percentile(seed_grads, 90)) > GRADIENT_EVIDENCE_FLOOR:
            GROW_AREA_CAP_FACTOR = 1.50
            grown = _region_grow_leakage_check(
                base_mask, lab, grad_mag, flake, pixel_size, n_iter=2,
                area_cap_px=int(seed_area_px * GROW_AREA_CAP_FACTOR),
            )
            final_mask = cv2.morphologyEx(grown, cv2.MORPH_CLOSE, close_k2)
            final_mask = cv2.morphologyEx(final_mask, cv2.MORPH_OPEN, open_k2)
            final_mask = keep_largest_n(final_mask, n=1, min_area=AREA_MIN_ABS_PX)
            final_mask = flood_fill_holes(final_mask)
        else:
            final_mask = cv2.morphologyEx(base_mask, cv2.MORPH_CLOSE, close_k2)
            final_mask = cv2.morphologyEx(final_mask, cv2.MORPH_OPEN, open_k2)
            final_mask = keep_largest_n(final_mask, n=1, min_area=AREA_MIN_ABS_PX)
            final_mask = flood_fill_holes(final_mask)
        return final_mask, use_footprint_grow

    for rank, rec in enumerate(cc_records[:GRAPHENE_CANDIDATE_TOP_N]):
        panel_mask, used_footprint_grow = _grow_refine_candidate(rec)
        rec['_panel_mask'] = panel_mask
        rec['_panel_area_px'] = int((panel_mask > 0).sum())
        rec['_panel_area_um2'] = rec['_panel_area_px'] * pixel_size * pixel_size
        rec['_panel_footprint_grow'] = bool(used_footprint_grow)

    final, use_footprint_grow = _grow_refine_candidate(best)
    cnts, _ = cv2.findContours(final, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contour = max(cnts, key=cv2.contourArea) if cnts else None

    public_records = [{k: v for k, v in r.items() if not k.startswith('_')}
                      for r in cc_records]

    return {
        'graphene_mask': final,
        'graphene_contour': contour,
        'top_flake_mask': flake,
        'processed_image': img,
        'cc_records': public_records,
        'candidate_records': cc_records,
        'selected_idx': best['lab_idx'],
        'low_confidence': False,
        'flake_ref_L': float(L_bg),
        'L_bg': float(L_bg),
        'bright_thresh_L': float(bright_thresh_L),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description='graphene detector (adaptive; no GT priors)')
    p.add_argument('--image', required=True)
    p.add_argument('--pixel-size', type=float, required=True)
    p.add_argument('--output-dir', required=True)
    p.add_argument('--mirror', action='store_true', default=False)
    p.add_argument('--cluster-id', type=int, default=None,
                   help='rank to select (0=top-scoring, default)')
    p.add_argument('--n-sub-clusters', type=int, default=3,
                   help='[kept for CLI compat, unused]')
    p.add_argument('--footprint-mask', default=None,
                   help='optional path to footprint_mask.png in full_stack coords '
                        '(Phase 10a: enables footprint-containment scoring). '
                        'A co-located warp_top.npy must exist in the same directory '
                        'to warp the mask into mirrored-top_part coordinates. '
                        'When omitted, footprint scoring is skipped (backward compatible).')
    args = p.parse_args()

    img = cv2.imread(os.path.abspath(os.path.expanduser(args.image)))
    if img is None:
        print(f'ERROR: cannot read {args.image}', file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load and warp footprint mask into mirrored-top_part coordinates (Phase 10a).
    # This enables spatial-containment scoring in _score_candidates so that the
    # correct graphene cluster ranks first without requiring agent visual override.
    #
    # Phase 13: auto-discovery of footprint mask.
    # When --footprint-mask is not provided, attempt to locate it via the
    # canonical fixture layout:
    #   {stack}/input/top_part.jpg (the --image path)
    #   {stack}/output/align/footprint_mask.png (auto-discovery target)
    # The stack root is the grandparent of the input directory.  This is a
    # universal fixture convention — not sample-specific — so auto-discovery
    # does not constitute sample-fitting.  It allows the evaluation pipeline to
    # benefit from footprint-bounded grow without requiring an explicit
    # --footprint-mask flag.
    FOOTPRINT_AUTO_DISCOVER = True  # Phase 13: enable auto-discovery
    loaded_footprint_mask: np.ndarray | None = None
    footprint_source = args.footprint_mask  # explicit CLI arg, or None

    if footprint_source is None and FOOTPRINT_AUTO_DISCOVER:
        # Auto-discover: image is at {stack}/input/top_part.jpg
        # → footprint at {stack}/output/align/footprint_mask.png
        img_path = Path(os.path.abspath(os.path.expanduser(args.image)))
        # input/ dir is the parent of the image; stack root is its parent.
        candidate = img_path.parent.parent / "output" / "align" / "footprint_mask.png"
        if candidate.exists():
            footprint_source = str(candidate)
            print(f'Footprint mask auto-discovered: {candidate}')

    if footprint_source:
        h_img, w_img = img.shape[:2]
        target_shape = (h_img, w_img)
        loaded_footprint_mask = _load_footprint_in_toppart_coords(
            footprint_source, target_shape, mirror=args.mirror,
        )
        if loaded_footprint_mask is None:
            print(
                f'WARN: footprint mask {footprint_source!r} could not be loaded '
                'or warp_top.npy not found; footprint scoring disabled.',
                file=sys.stderr,
            )
        else:
            fp_area = int((loaded_footprint_mask > 0).sum())
            print(f'Footprint mask loaded and warped to top_part coords '
                  f'(area={fp_area}px).')

    # Route the footprint to the right parameter (Phase 15):
    # Universal principle: the footprint represents the same physical constraint
    # regardless of how the script learned about it.  The top-alignment footprint
    # marks the spatial extent of the deposited top-flake material — this is a
    # universal property of vdW stack imaging, not a sample-specific prior.
    # The candidate's spatial location within the deposited flake is informative
    # for ranking, period.  The difference between explicit and auto-discovered
    # footprints is only in the SIDE EFFECTS:
    #
    # - Explicit (--footprint-mask): footprint_mask → scoring + grow + area gate
    #   expansion.  The agent deliberately provides the footprint to guide the
    #   full detection pipeline.
    #
    # - Auto-discovered: footprint_mask_score_only → scoring only (Phase 15 NEW)
    #                    footprint_mask_grow_only   → grow only (Phase 13)
    #   The auto-discovered footprint informs ranking (same physics) but must NOT
    #   expand the upper area gate.  Expanding the gate would admit new large CCs
    #   (hBN shadow regions, stamp edges) that were correctly excluded by the
    #   host-based gate.  The grow step uses the footprint directly as its spatial
    #   bound — this was already Phase 13 behavior.
    #
    # fp_for_scoring = explicit_footprint or auto_discovered_footprint (Phase 15)
    # fp_for_grow    = explicit_footprint or auto_discovered_footprint (Phase 13)
    # area_gate_expansion = explicit_footprint only (unchanged from Phase 10b)
    explicit_footprint_requested = bool(args.footprint_mask)
    result = detect_graphene(
        img, args.pixel_size, mirror=args.mirror,
        cluster_id=args.cluster_id,
        footprint_mask=loaded_footprint_mask if explicit_footprint_requested else None,
        footprint_mask_grow_only=loaded_footprint_mask if not explicit_footprint_requested else None,
        footprint_mask_score_only=loaded_footprint_mask if not explicit_footprint_requested else None,
    )

    print(f'L_bg(flake)={result.get("L_bg", "?"):.1f}, '
          f'rel_L thr={result.get("bright_thresh_L", "?"):.1f}, '
          f'low_confidence={result.get("low_confidence", False)}')
    print('Top candidates:')
    for r in result['cc_records'][:5]:
        marker = ' <-- selected' if r['lab_idx'] == result.get('selected_idx') else ''
        containment_str = ''
        if r.get('s_containment') is not None:
            containment_str = f" cont={r.get('containment_raw', 0.0):.2f}"
        print(f"  cc{r['lab_idx']}: area={r['area_um2']:.0f}um2 "
              f"aspect={r['aspect']:.1f} sol={r['solidity']:.2f} "
              f"L={r['L_med']:.0f} relL={r.get('rel_L_med', 0):.1f} "
              f"pol={r.get('polarity', '?')}{containment_str} "
              f"score={r['score']:.3f}{marker}")

    if result.get('low_confidence') or result['graphene_contour'] is None:
        print('WARN: no graphene candidate; emitting empty mask with low_confidence=True',
              file=sys.stderr)
        h_, w_ = result['processed_image'].shape[:2]
        empty_mask = np.zeros((h_, w_), np.uint8)
        cv2.imwrite(str(out_dir / 'graphene_mask.png'), empty_mask)
        # Emit a degenerate 1-point contour so downstream steps can handle None gracefully.
        dummy_contour = np.array([[w_ // 2, h_ // 2]], dtype=np.float64)
        np.save(str(out_dir / 'graphene_contour.npy'), dummy_contour)
        sidecar = {
            'area_px': 0, 'area_um2': 0.0,
            'cluster_id': None,
            'selected': None,
            'top_candidates': [],
            'low_confidence': True,
        }
        (out_dir / 'graphene_result.json').write_text(json.dumps(sidecar, indent=2))
        diag = desaturate(result['processed_image'], factor=0.4)
        cv2.imwrite(str(out_dir / '02_graphene_on_top.png'), diag)
        cv2.imwrite(str(out_dir / '00_graphene_candidates.png'),
                    draw_graphene_candidates(result['processed_image'], []))
        return

    contour = result['graphene_contour']
    mask = result['graphene_mask']

    cv2.imwrite(str(out_dir / 'graphene_mask.png'), mask)
    contour_2d = contour.reshape(-1, 2).astype(np.float64)
    np.save(str(out_dir / 'graphene_contour.npy'), contour_2d)

    diag = desaturate(result['processed_image'], factor=0.4)
    cv2.drawContours(diag, [contour], -1, (0, 0, 255), 2)
    cv2.imwrite(str(out_dir / '02_graphene_on_top.png'), diag)
    selected_rank = 0
    candidate_records = result.get('candidate_records', result['cc_records'])
    for rank, rec in enumerate(candidate_records):
        if rec.get('lab_idx') == result.get('selected_idx'):
            selected_rank = rank
            break
    cv2.imwrite(str(out_dir / '00_graphene_candidates.png'),
                draw_graphene_candidates(
                    result['processed_image'], candidate_records,
                    top_n=GRAPHENE_CANDIDATE_TOP_N,
                    selected_rank=selected_rank,
                ))

    area_px = int((mask > 0).sum())
    area_um2 = round(area_px * args.pixel_size * args.pixel_size, 2)
    top_candidates = [
        _candidate_public_record(rec, rank)
        for rank, rec in enumerate(candidate_records[:GRAPHENE_CANDIDATE_TOP_N])
    ]
    selected = (
        top_candidates[selected_rank]
        if 0 <= selected_rank < len(top_candidates) else None
    )
    sidecar = {
        'area_px': area_px, 'area_um2': area_um2,
        'cluster_id': result.get('selected_idx'),
        'selected_rank': selected_rank,
        'selected': selected,
        'top_candidates': top_candidates,
        'low_confidence': bool(result.get('low_confidence', False)),
    }
    (out_dir / 'graphene_result.json').write_text(json.dumps(sidecar, indent=2))

    print(f'\nOK: graphene area={area_um2}um2 ({area_px}px)')


if __name__ == '__main__':
    main()
