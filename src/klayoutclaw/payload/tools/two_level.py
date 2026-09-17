"""Pure helpers for adaptive two-level (inner-fine / outer-coarse) auto_route.

Dependency-free (math only) so they are unit-testable in-process without
KLayout / skimage. The orchestration that uses these (route_two_level) lives in
route_worker.py and reuses route() unchanged for each of the two passes.

Geometry & units: all coordinates and sizes are in DBU (integer database
units; typically 1 um = 1000 dbu). Spans/dims are dbu ints.
"""
from __future__ import annotations

import math

# Default grid-cell cap, kept in sync with route_worker's MAX_GRID_CELLS so the
# trigger reasons about the same OOM bound the single-pass guard enforces.
DEFAULT_MAX_GRID_CELLS = 25_000_000


def coords_span(coords):
    """Return ((l, b, r, t) bbox, max-extent span) of a list of (x, y) dbu coords."""
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    l, b, r, t = min(xs), min(ys), max(xs), max(ys)
    return (l, b, r, t), max(r - l, t - b)


def detect_inner_outer(pins_a, pins_b):
    """Which pin group is the inner (sample) region: the one with the SMALLER
    bounding-box span. Returns 'a', 'b', or None if either group is empty.

    Rationale: in a real nanodevice the inner ohmic contacts cluster at the
    centre (small bbox) and the bonding pads sit on the perimeter (large bbox),
    so the smaller-span group is the sample.
    """
    if not pins_a or not pins_b:
        return None
    _, span_a = coords_span(pins_a)
    _, span_b = coords_span(pins_b)
    return "a" if span_a <= span_b else "b"


def compute_boundary_point(cx, cy, hx, hy, px, py, eps=1):
    """Cast a ray from window centre (cx,cy) toward outer pin (px,py) and clamp
    it to the rectangle with half-extents (hx,hy). Returns the (bx,by) dbu point
    ON the rectangle edge, or None when:
      - the outer pin is INSIDE the window (s >= 1), or
      - the direction is degenerate (pin coincident with centre).
    None means "route this net single-pass" (it does not cross the boundary).
    """
    dx = px - cx
    dy = py - cy
    if abs(dx) < eps and abs(dy) < eps:
        return None
    sx = hx / abs(dx) if abs(dx) > eps else math.inf
    sy = hy / abs(dy) if abs(dy) > eps else math.inf
    s = min(sx, sy)
    if s >= 1.0:
        return None
    return (int(round(cx + dx * s)), int(round(cy + dy * s)))


def grid_cells(width_dbu, height_dbu, res_dbu):
    """Number of grid cells for a window of (width,height) dbu at res_dbu."""
    res = max(1, int(res_dbu))
    nc = max(1, (int(width_dbu) + res - 1) // res)
    nr = max(1, (int(height_dbu) + res - 1) // res)
    return nc * nr


def should_two_level(n_inner, n_outer, inner_span_dbu, outer_span_dbu,
                     full_w_dbu, full_h_dbu, inner_w_dbu, inner_h_dbu,
                     fine_res_dbu, coarse_res_dbu,
                     max_cells=DEFAULT_MAX_GRID_CELLS, min_pins=3, sep_ratio=3.0):
    """Decide whether adaptive two-level routing should fire.

    Returns (should: bool, reason: str). Fires ONLY when all hold:
      1. >= min_pins per group,
      2. clear inner/outer separation: outer_span >= sep_ratio * inner_span,
      3. a single grid fine enough for the inner contacts would OOM over the
         FULL field (cells(full, fine_res) > max_cells) — i.e. single-pass
         cannot route the inner contacts at fine resolution anyway,
      4. the INNER window at fine_res fits under the cap (inner_fine <= max_cells)
         — the whole point is to route the dense inner contacts at fine
         resolution. The OUTER pass is NOT gated on the cap: it routes long
         perimeter runs at coarse_res and route()'s own cell-count guard will
         coarsen it further if needed (coarseness is fine out there).
    Otherwise single-pass (today's behaviour) is kept — so small/uniform
    layouts are unaffected.
    """
    if n_inner < min_pins or n_outer < min_pins:
        return False, "too few pins per group (need >= {})".format(min_pins)
    if inner_span_dbu <= 0 or outer_span_dbu < sep_ratio * inner_span_dbu:
        return False, "insufficient inner/outer span separation (< {}x)".format(sep_ratio)
    full_fine = grid_cells(full_w_dbu, full_h_dbu, fine_res_dbu)
    if full_fine <= max_cells:
        return False, "single fine grid fits ({} cells <= cap); no split needed".format(full_fine)
    inner_fine = grid_cells(inner_w_dbu, inner_h_dbu, fine_res_dbu)
    if inner_fine > max_cells:
        return False, ("inner window does not fit at fine res under cap "
                       "(inner_fine={} > {}); split cannot help".format(inner_fine, max_cells))
    outer_coarse = grid_cells(full_w_dbu, full_h_dbu, coarse_res_dbu)
    return True, ("two-level beneficial: full-fine={} > cap, "
                  "inner_fine={}, outer_coarse={} (outer self-coarsens if needed)".format(
                      full_fine, inner_fine, outer_coarse))
