"""Ordered-loop pairing engine for the auto_route assignment pre-pass.

The algorithm prevents inter-net crossings PRIOR to pathfinding by matching two
CCW-ordered cyclic loops:

  - inner: pin_a centroids ordered by angle around their centroid.
  - outer: pin_b centroids ordered by angle around the chip centroid (or pin_b
           centroid; we pick one consistent reference for both loops).

A non-crossing assignment is one where the cyclic order of inner-loop indices
maps to a strictly cyclic-monotonic sequence of outer-loop indices.

Solve in O(n * m^2) per anchor pair; try all m anchors → O(n * m^3) total.
With n=11 contacts, m=43 pads this is ~9M operations — milliseconds.
"""
from __future__ import annotations

import math
from typing import Sequence


def _angle(cx: float, cy: float, x: float, y: float) -> float:
    return math.atan2(y - cy, x - cx)


def _ccw_order(points: Sequence[tuple[float, float]],
               ref: tuple[float, float] | None = None) -> list[int]:
    """Return CCW-ordered indices around `ref` (or centroid of points)."""
    if ref is None:
        cx = sum(p[0] for p in points) / len(points)
        cy = sum(p[1] for p in points) / len(points)
    else:
        cx, cy = ref
    return sorted(range(len(points)), key=lambda i: _angle(cx, cy, points[i][0], points[i][1]))


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return math.sqrt(dx * dx + dy * dy)


def ordered_loop_match(inner_pts: Sequence[tuple[float, float]],
                       outer_pts: Sequence[tuple[float, float]],
                       inner_ref: tuple[float, float] | None = None,
                       outer_ref: tuple[float, float] | None = None,
                       ) -> tuple[list[tuple[int, int]], float]:
    """Return [(inner_idx, outer_idx), ...] cyclic-monotonic assignment + total cost.

    n = len(inner_pts), m = len(outer_pts), n must be <= m.
    Cyclic monotonic constraint: walking the inner loop in CCW order, the
    matched outer indices form a strictly increasing subsequence of the outer
    cyclic order. Matches `1→C, 2→E, 3→G, 4→I, 5→K` style legal example.

    Returns assignment in INPUT-INDEX order (not cyclic order).
    """
    n = len(inner_pts)
    m = len(outer_pts)
    if n == 0:
        return [], 0.0
    if n > m:
        raise ValueError(f"ordered_loop_match: n_inner ({n}) > n_outer ({m})")

    inner_order = _ccw_order(inner_pts, inner_ref)
    outer_order = _ccw_order(outer_pts, outer_ref)

    inner_cyc = [inner_pts[i] for i in inner_order]
    outer_cyc = [outer_pts[j] for j in outer_order]

    # cyclic DP: try every anchor (i_inner=0 -> j_outer=anchor)
    # For each anchor, solve linear DP on rotated outer order.
    best_cost = math.inf
    best_match_cyc: list[int] | None = None  # indices into outer_cyc
    INF = math.inf

    for anchor in range(m):
        # Rotate outer so anchor is at position 0
        rot_outer = outer_cyc[anchor:] + outer_cyc[:anchor]
        # rot_outer_orig_pos[k] = original outer_cyc position
        # but we just need distances — compute distance matrix on the rotated order.
        d = [[_dist(inner_cyc[i], rot_outer[j]) for j in range(m)] for i in range(n)]

        # cost[i][j] = min total cost to match inner_cyc[0..i] using rot_outer[0..j]
        # with inner_cyc[i] -> rot_outer[j]
        # Force inner_cyc[0] -> rot_outer[0] (anchor)
        # transition: cost[i][j] = d[i][j] + min(cost[i-1][k] for k<j)
        cost = [[INF] * m for _ in range(n)]
        parent = [[-1] * m for _ in range(n)]

        cost[0][0] = d[0][0]
        # We require the wrap-around (last inner -> something, then back to anchor 0)
        # to be non-crossing too. With anchor pinning inner_cyc[0] -> rot_outer[0],
        # the constraint reduces to: rot_outer[0] < rot_outer[k_last] (in cyclic
        # rotated order, naturally true because index 0 comes first), and the
        # wrap edge from inner_cyc[n-1] -> rot_outer[k_last] back to
        # inner_cyc[0] -> rot_outer[0] doesn't cross any earlier edge IF AND
        # ONLY IF k_last < m (always true) and the linear DP is monotonic. So
        # the linear DP per anchor is sufficient. Reference: cyclic monotonic
        # assignment is well-defined once an anchor is fixed.

        # Build prefix-min of cost[i-1][.] for fast transitions.
        for i in range(1, n):
            best_prev = INF
            best_prev_k = -1
            running_min = INF
            running_min_k = -1
            for j in range(i, m):  # need j >= i for monotonic
                # update running min from cost[i-1][j-1] (since k must be < j, and k>=i-1)
                if j - 1 >= i - 1:
                    if cost[i-1][j-1] < running_min:
                        running_min = cost[i-1][j-1]
                        running_min_k = j - 1
                if running_min < INF:
                    cand = running_min + d[i][j]
                    if cand < cost[i][j]:
                        cost[i][j] = cand
                        parent[i][j] = running_min_k

        # Best terminal at row n-1
        local_best = INF
        local_j = -1
        for j in range(n - 1, m):
            if cost[n-1][j] < local_best:
                local_best = cost[n-1][j]
                local_j = j

        if local_best < best_cost:
            # Reconstruct path
            path_rot = [0] * n
            j_cur = local_j
            for i in range(n - 1, -1, -1):
                path_rot[i] = j_cur
                if i > 0:
                    j_cur = parent[i][j_cur]
            # Map rotated outer index back to outer_cyc position
            path_outer_cyc = [(rot_idx + anchor) % m for rot_idx in path_rot]
            best_cost = local_best
            best_match_cyc = path_outer_cyc

    if best_match_cyc is None:
        return [], math.inf

    # Map (cyclic position) back to original input indices.
    assignment = []
    for i_cyc, j_cyc in enumerate(best_match_cyc):
        i_orig = inner_order[i_cyc]
        j_orig = outer_order[j_cyc]
        assignment.append((i_orig, j_orig))

    return assignment, best_cost


def _self_test():
    # Toy example from plan: 5 inner on a small circle, 11 outer on a larger circle.
    inner = [(math.cos(2 * math.pi * i / 5) * 1.0,
              math.sin(2 * math.pi * i / 5) * 1.0) for i in range(5)]
    outer = [(math.cos(2 * math.pi * i / 11) * 5.0,
              math.sin(2 * math.pi * i / 11) * 5.0) for i in range(11)]
    asg, cost = ordered_loop_match(inner, outer)
    print(f"5->11 cost={cost:.3f}")
    for i, j in asg:
        print(f"  inner[{i}] = {inner[i]} -> outer[{j}] = {outer[j]}")

    # Verify cyclic-monotonic: rotate inner_order -> outer_idx sequence must be
    # cyclic-monotonic (strictly increasing in outer_cyc with at most one wrap).
    inner_ord = _ccw_order(inner)
    outer_ord = _ccw_order(outer)
    inner_pos = {orig: cyc for cyc, orig in enumerate(inner_ord)}
    outer_pos = {orig: cyc for cyc, orig in enumerate(outer_ord)}
    cyc_pairs = sorted([(inner_pos[i], outer_pos[j]) for i, j in asg])
    out_seq = [j for _, j in cyc_pairs]
    print(f"  out_seq cyclic order = {out_seq}")
    # Cyclic monotonic check: at most one position where seq[k+1] < seq[k] (the wrap).
    descents = sum(1 for k in range(len(out_seq) - 1) if out_seq[k+1] < out_seq[k])
    descents_with_wrap = descents + (1 if out_seq[-1] < out_seq[0] else 0)
    assert descents_with_wrap <= 1, f"NOT cyclic monotonic, descents={descents}"
    print(f"  cyclic monotonic OK (descents={descents})")


if __name__ == "__main__":
    _self_test()
