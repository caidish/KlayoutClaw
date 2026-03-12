#!/usr/bin/env python
"""Shared utilities for the nanodevice:flakedetect workflow.

Morphological helpers, contour smoothing, image manipulation, affine warp
utilities, and the Chamfer+containment cost function used across all
sub-skills (align, detect, combine).
"""

import math

import cv2
import numpy as np


# ---------------------------------------------------------------------------
# Morphological helpers
# ---------------------------------------------------------------------------

def morph_clean(mask, close_k=9, open_k=9):
    """Morphological close then open.

    Close fills small gaps and bridges narrow breaks, open removes small
    noise blobs. Combined, this produces a cleaner binary mask while
    preserving the overall shape.

    Args:
        mask: Binary mask (uint8, 0 or 255).
        close_k: Kernel size for morphological closing.
        open_k: Kernel size for morphological opening.

    Returns:
        Cleaned binary mask (uint8, 0 or 255).
    """
    if close_k > 0:
        k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_k, close_k))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k_close)
    if open_k > 0:
        k_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (open_k, open_k))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k_open)
    return mask


def flood_fill_holes(mask):
    """Fill interior holes in a binary mask.

    Performs flood fill from the top-left corner on the inverted mask,
    then unions with the original to fill all enclosed holes.

    Args:
        mask: Binary mask (uint8, 0 or 255).

    Returns:
        Mask with interior holes filled (uint8, 0 or 255).
    """
    h, w = mask.shape[:2]
    flood = mask.copy()
    fill_mask = np.zeros((h + 2, w + 2), dtype=np.uint8)
    cv2.floodFill(flood, fill_mask, (0, 0), 255)
    # Invert: holes that were NOT reached by flood from corner
    holes = cv2.bitwise_not(flood)
    return cv2.bitwise_or(mask, holes)


def keep_largest_n(mask, n=1, min_area=0):
    """Keep the N largest connected components by area.

    Args:
        mask: Binary mask (uint8, 0 or 255).
        n: Number of largest components to keep.
        min_area: Minimum pixel area to consider a component.

    Returns:
        Mask containing only the N largest qualifying components
        (uint8, 0 or 255).
    """
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
        mask, connectivity=8
    )
    # stats columns: x, y, w, h, area. Label 0 is background.
    areas = []
    for i in range(1, num_labels):
        area = stats[i, cv2.CC_STAT_AREA]
        if area >= min_area:
            areas.append((area, i))

    if not areas:
        return np.zeros_like(mask)

    # Sort by area descending, keep top n
    areas.sort(key=lambda x: x[0], reverse=True)
    keep_ids = {label_id for _, label_id in areas[:n]}

    result = np.zeros_like(mask)
    for label_id in keep_ids:
        result[labels == label_id] = 255
    return result


def mask_centroid(mask):
    """Return (cx, cy) centroid of a binary mask.

    Uses image moments. Returns None if the mask is empty.

    Args:
        mask: Binary mask (uint8, 0 or 255).

    Returns:
        Tuple (cx, cy) as floats, or None if mask has no foreground.
    """
    m = cv2.moments(mask, binaryImage=True)
    if m["m00"] < 1e-6:
        return None
    return (m["m10"] / m["m00"], m["m01"] / m["m00"])


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def desaturate(image, factor=0.4):
    """Desaturate an image for use as a contour overlay background.

    Args:
        image: BGR image (uint8).
        factor: Saturation multiplier (0=grayscale, 1=original).

    Returns:
        Desaturated BGR image (uint8).
    """
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[:, :, 1] *= factor
    hsv = np.clip(hsv, 0, 255).astype(np.uint8)
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


# ---------------------------------------------------------------------------
# Contour smoothing
# ---------------------------------------------------------------------------

def smooth_contour_polygon(contour, epsilon):
    """Simplify a contour via Douglas-Peucker approximation.

    Args:
        contour: Contour array, shape (N,1,2) or (N,2).
        epsilon: Approximation accuracy (max distance from original curve).

    Returns:
        Simplified contour as int32 array with shape (M,1,2) where M <= N.
    """
    pts = contour.reshape(-1, 1, 2).astype(np.int32)
    approx = cv2.approxPolyDP(pts, epsilon, closed=True)
    return approx.astype(np.int32)


def smooth_contour_gaussian(contour, sigma=20):
    """Gaussian-smooth a closed contour.

    Treats the contour as a closed curve, wrapping endpoints to avoid
    discontinuities at the seam.  Each coordinate dimension (x, y) is
    smoothed independently with a 1-D Gaussian kernel.

    Args:
        contour: Contour array, shape (N,1,2) or (N,2).
        sigma: Gaussian kernel standard deviation in pixels.

    Returns:
        Smoothed contour as int32 array with shape (N,1,2).
    """
    pts = contour.reshape(-1, 2).astype(np.float64)
    n = len(pts)

    if n < 3 or sigma <= 0:
        return contour.reshape(-1, 1, 2).astype(np.int32)

    pad = min(sigma * 3, n // 2)
    ksize = sigma * 6 + 1
    kernel = cv2.getGaussianKernel(ksize, sigma).flatten()

    for dim in range(2):
        # Wrap endpoints for closed-curve continuity
        padded = np.concatenate([pts[-pad:, dim], pts[:, dim], pts[:pad, dim]])
        smoothed = np.convolve(padded, kernel, mode='same')[pad:pad + n]
        pts[:, dim] = smoothed

    return np.round(pts).astype(np.int32).reshape(-1, 1, 2)


def smooth_material(contour, material_name):
    """Apply material-appropriate smoothing to a contour.

    Smoothing parameters are tuned per material based on physical
    characteristics:

    - **bottom_hBN**: Large flakes with smooth edges corrupted by imaging
      noise.  Gaussian pre-smooth (sigma=30) removes noise, then polygon
      simplification (epsilon=8) extracts the crystallographic facets.
    - **graphite**: Similar to hBN but smaller features.  Gaussian(sigma=15)
      + polygon(epsilon=4).
    - **top_hBN**: Faceted edges visible at high contrast.  Polygon only
      (epsilon=6).
    - **graphene**: Boundary often follows underlying hBN facets.  Polygon
      only (epsilon=4).
    - **default**: Polygon only (epsilon=5) for unrecognized materials.

    Flake boundaries are crystallographic facets -- polygon approximation
    is physically correct, not just a computational shortcut.

    Args:
        contour: Contour array, shape (N,1,2) or (N,2).
        material_name: Material identifier string.

    Returns:
        Smoothed contour as int32 array with shape (M,1,2).
    """
    name = material_name.lower().replace(" ", "_")

    if name == "bottom_hbn":
        smoothed = smooth_contour_gaussian(contour, sigma=30)
        return smooth_contour_polygon(smoothed, epsilon=8)
    elif name == "graphite":
        smoothed = smooth_contour_gaussian(contour, sigma=15)
        return smooth_contour_polygon(smoothed, epsilon=4)
    elif name == "top_hbn":
        return smooth_contour_polygon(contour, epsilon=6)
    elif name == "graphene":
        return smooth_contour_polygon(contour, epsilon=4)
    else:
        # Sensible default for unknown materials
        return smooth_contour_polygon(contour, epsilon=5)


# ---------------------------------------------------------------------------
# Affine warp utilities
# ---------------------------------------------------------------------------

def make_warp(cx_src, cy_src, cx_dst, cy_dst, angle_rad, scale=1.0):
    """Build affine warp matrix: scale + rotate around src centroid, translate to dst.

    The transformation is: translate src centroid to origin, scale, rotate,
    then translate to dst centroid.

    Args:
        cx_src: Source centroid x (pixels).
        cy_src: Source centroid y (pixels).
        cx_dst: Destination centroid x (pixels).
        cy_dst: Destination centroid y (pixels).
        angle_rad: Rotation angle in radians (positive = counter-clockwise).
        scale: Uniform scale factor.

    Returns:
        2x3 float64 affine warp matrix for use with cv2.warpAffine.
    """
    cos_a = math.cos(angle_rad) * scale
    sin_a = math.sin(angle_rad) * scale

    # Rotation + scale around (cx_src, cy_src), then translate to (cx_dst, cy_dst)
    tx = cx_dst - cos_a * cx_src + sin_a * cy_src
    ty = cy_dst - sin_a * cx_src - cos_a * cy_src

    M = np.array([
        [cos_a, -sin_a, tx],
        [sin_a,  cos_a, ty],
    ], dtype=np.float64)

    return M


def warp_contour(contour, M):
    """Transform contour points by affine matrix M.

    Applies the 2x3 affine transformation to each contour point.

    Args:
        contour: Contour array, shape (N,1,2) or (N,2), int or float.
        M: 2x3 affine transformation matrix.

    Returns:
        Transformed contour as (N,1,2) float64 array (OpenCV contour format).
    """
    pts = np.asarray(contour, dtype=np.float64)
    if pts.ndim == 3 and pts.shape[1] == 1:
        pts = pts.squeeze(axis=1)
    if pts.ndim != 2 or pts.shape[1] != 2:
        raise ValueError(f"Contour must be (N,2) or (N,1,2), got shape {pts.shape}")

    n = len(pts)
    # Homogeneous coordinates: (N, 3)
    ones = np.ones((n, 1), dtype=np.float64)
    pts_h = np.hstack([pts, ones])

    # Apply transform: (2, 3) @ (3, N) -> (2, N)
    transformed = (M @ pts_h.T).T

    return transformed.reshape(-1, 1, 2)


def invert_warp(M):
    """Invert a 2x3 affine transformation matrix.

    Args:
        M: 2x3 affine transformation matrix.

    Returns:
        Inverted 2x3 affine matrix.
    """
    return cv2.invertAffineTransform(M)


# ---------------------------------------------------------------------------
# Chamfer + containment cost function
# ---------------------------------------------------------------------------

class ChamferAligner:
    """Chamfer distance + containment cost function for cross-substrate alignment.

    Encapsulates the proven v8 cost function recipe: forward Chamfer squared +
    heavy containment penalty + out-of-bounds penalty.  Used by both sweep.py
    (coarse rotation search) and refine.py (fine optimization).

    Usage:
        aligner = ChamferAligner(source_contour, source_mask,
                                 footprint_contour, footprint_mask)
        cost = aligner.cost([rot_deg, scale, dx, dy])
        metrics = aligner.evaluate([rot_deg, scale, dx, dy])
    """

    def __init__(self, source_contour, source_mask,
                 footprint_contour, footprint_mask,
                 n_source_pts=600, n_fp_pts=800):
        """Initialize the aligner with source and footprint data.

        Args:
            source_contour: Source flake contour, (N,2) or (N,1,2) float64.
            source_mask: Binary mask of source flake (uint8, 0/255).
            footprint_contour: Footprint contour, (N,2) or (N,1,2) float64.
            footprint_mask: Binary mask of footprint (uint8, 0/255).
            n_source_pts: Subsample source contour to this many points.
            n_fp_pts: Subsample footprint contour to this many points.
        """
        from scipy.spatial import KDTree

        # Store masks
        self.source_mask = source_mask
        self.footprint_mask = footprint_mask
        self.h, self.w = footprint_mask.shape[:2]

        # Source centroid
        src_centroid = mask_centroid(source_mask)
        if src_centroid is None:
            raise ValueError("Source mask is empty")
        self.src_cx, self.src_cy = src_centroid

        # Footprint centroid
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

        # Subsample footprint contour and build KDTree
        fp_pts = np.asarray(footprint_contour, dtype=np.float64).reshape(-1, 2)
        if len(fp_pts) > n_fp_pts:
            idx = np.linspace(0, len(fp_pts) - 1, n_fp_pts, dtype=int)
            fp_pts = fp_pts[idx]
        self.fp_pts = fp_pts
        self.fp_tree = KDTree(fp_pts)

    def cost(self, params):
        """Compute the Chamfer + containment cost.

        Args:
            params: [rot_deg, scale, dx, dy] — rotation in degrees,
                uniform scale, translation offsets in pixels.

        Returns:
            float cost value. Lower is better. Returns 1e6 for degenerate
            configurations (>30% out of bounds, or <100px warped area).
        """
        rot_deg, scale, dx, dy = params
        M = make_warp(self.src_cx, self.src_cy,
                      self.fp_cx + dx, self.fp_cy + dy,
                      math.radians(rot_deg), scale)

        # Warp source contour points
        ones = np.ones((len(self.source_pts), 1))
        warped = (M @ np.hstack([self.source_pts, ones]).T).T

        # Out-of-bounds check (early exit if >30%)
        oob = ((warped[:, 0] < 0) | (warped[:, 0] >= self.w) |
               (warped[:, 1] < 0) | (warped[:, 1] >= self.h))
        oob_frac = oob.sum() / len(warped)
        if oob_frac > 0.3:
            return 1e6

        # Forward Chamfer: warped contour -> footprint contour
        dists_fwd, _ = self.fp_tree.query(warped)
        fwd = (dists_fwd ** 2).mean()

        # Containment: warp mask, check fraction outside footprint
        warped_mask = cv2.warpAffine(self.source_mask, M, (self.w, self.h),
                                     flags=cv2.INTER_NEAREST)
        warped_area = (warped_mask > 0).sum()
        if warped_area < 100:
            return 1e6
        outside = cv2.bitwise_and(warped_mask, cv2.bitwise_not(self.footprint_mask))
        outside_frac = (outside > 0).sum() / warped_area

        # Combined cost: Chamfer + containment + OOB
        return fwd + 3000.0 * outside_frac + 500.0 * oob_frac

    def evaluate(self, params, pixel_size_um=1.0):
        """Compute detailed alignment metrics for a given parameter set.

        Args:
            params: [rot_deg, scale, dx, dy].
            pixel_size_um: Microns per pixel for metric conversion.

        Returns:
            Dict with keys: rot_deg, scale, dx, dy, cost,
            fwd_chamfer_mean_um, fwd_chamfer_median_um, fwd_chamfer_p90_um,
            iou, top_containment, fp_containment, outside_fraction,
            warp_matrix.
        """
        rot_deg, scale, dx, dy = params
        M = make_warp(self.src_cx, self.src_cy,
                      self.fp_cx + dx, self.fp_cy + dy,
                      math.radians(rot_deg), scale)

        # Warp contour points
        ones = np.ones((len(self.source_pts), 1))
        warped = (M @ np.hstack([self.source_pts, ones]).T).T

        # Forward Chamfer distances
        dists_fwd, _ = self.fp_tree.query(warped)

        # Warp mask for overlap metrics
        warped_mask = cv2.warpAffine(self.source_mask, M, (self.w, self.h),
                                     flags=cv2.INTER_NEAREST)

        inter = cv2.bitwise_and(warped_mask, self.footprint_mask)
        union = cv2.bitwise_or(warped_mask, self.footprint_mask)

        inter_area = (inter > 0).sum()
        union_area = max((union > 0).sum(), 1)
        warped_area = max((warped_mask > 0).sum(), 1)
        fp_area = max((self.footprint_mask > 0).sum(), 1)

        outside = cv2.bitwise_and(warped_mask, cv2.bitwise_not(self.footprint_mask))

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


# ---------------------------------------------------------------------------
# Material color palette (BGR) — shared by overlay and commit scripts
# ---------------------------------------------------------------------------

MATERIAL_COLORS = {
    "top_hBN": (0, 200, 0),       # green
    "graphene": (0, 0, 255),       # red (BGR)
    "bottom_hBN": (255, 100, 0),   # blue-ish
    "graphite": (0, 200, 255),     # yellow (BGR)
}

# Default layer assignments
LAYER_MAP = {
    "top_hBN": "10/0",
    "graphene": "11/0",
    "bottom_hBN": "12/0",
    "graphite": "13/0",
}

# Default stack order (top to bottom)
STACK_ORDER = ["top_hBN", "graphene", "bottom_hBN", "graphite"]
