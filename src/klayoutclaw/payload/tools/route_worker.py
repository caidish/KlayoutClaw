#!/usr/bin/env python
"""Route worker v2 — issue #28 baseline.

Drop-in replacement for tools/route_worker.py with:

  - Ordered-loop pairing (cyclic monotonic DP) as the default assignment engine.
    Prevents inter-pair crossings BEFORE pathfinding runs. n=len(pin_a),
    m=len(pin_b), n<=m. Cost: sum of Euclidean distances.

  - `freeze_completed_routes_as_obstacles_with_margin: float` (default 1.0
    um) — every pair sees prior routes as hard obstacles inflated by margin.

  - `bus_pairs: [[a_idx, b_idx_list], ...]` — optional multi-pin nets. Each
    net's source is a single pin on layer A; sinks are 1+ pins on layer B.
    Singleton bus_pairs (b_idx_list of length 1) is equivalent to 1:1.
    For multi-sink nets, the longest sink is routed first per-pair, then
    each subsequent sink is routed from its B-pin to the nearest point on
    the existing path (Steiner-tree approximation). Branches are inserted
    as additional path objects sharing the net id (encoded in `net_id` in
    the result entry).

Backward compat:
  - When `bus_pairs` is omitted: behaves like 1:1 pairing using ordered-loop.
  - When the caller supplies `pin_pairs_override`: that takes precedence
    (no automatic ordered-loop assignment).
  - All existing config keys (obs_safe_distance_um, path_safe_distance_um,
    map_resolution_um, sort_pairs, dry_run, per_pair_obstacle_layers,
    auto_map_resolution, etc.) continue to work.

Output schema additions:
  - paths[].net_id : index into `bus_pairs` (None when no bus_pairs).
  - pairs[]       : present in dry_run AND success modes (the assignment
                    chosen by ordered-loop, with inner/outer cyclic positions).
  - assignment_engine : "ordered_loop" | "override".
"""
from __future__ import annotations
import json
import math
import os
import sys
from typing import Any, Sequence

import numpy as np
from skimage.graph import MCP_Geometric
import klayout.db as kdb

# Ordered-loop module is co-located.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ordered_loop import ordered_loop_match  # noqa: E402
from two_level import (  # noqa: E402
    detect_inner_outer, compute_boundary_point, should_two_level,
    coords_span, DEFAULT_MAX_GRID_CELLS)


# ---------------------------------------------------------------------------
# Helpers (carried over from tools/route_worker.py)
# ---------------------------------------------------------------------------

def parse_layer(spec: str) -> tuple[int, int]:
    parts = spec.strip().split("/")
    layer = int(parts[0])
    datatype = int(parts[1]) if len(parts) > 1 else 0
    return (layer, datatype)


def compress_path(points: list[list[int]]) -> list[list[int]]:
    if len(points) <= 2:
        return list(points)
    result = [points[0]]
    for i in range(1, len(points) - 1):
        dx1 = points[i][0] - points[i - 1][0]
        dy1 = points[i][1] - points[i - 1][1]
        dx2 = points[i + 1][0] - points[i][0]
        dy2 = points[i + 1][1] - points[i][1]
        cross = dx1 * dy2 - dy1 * dx2
        if cross != 0:
            result.append(points[i])
    result.append(points[-1])
    return result


def extract_pin_centers(cell, layout, layer_num, datatype):
    layer_idx = layout.find_layer(layer_num, datatype)
    if layer_idx is None:
        return []
    centers = []
    for shape in cell.shapes(layer_idx).each():
        bb = shape.bbox()
        cx = (bb.left + bb.right) // 2
        cy = (bb.bottom + bb.top) // 2
        centers.append((cx, cy))
    return centers


def extract_pin_bboxes(cell, layout, layer_num, datatype):
    layer_idx = layout.find_layer(layer_num, datatype)
    if layer_idx is None:
        return []
    bbs = []
    for s in cell.shapes(layer_idx).each():
        b = s.bbox()
        bbs.append((b.left, b.bottom, b.right, b.top))
    return bbs


def min_pin_edge_um(bb_a, bb_b, dbu):
    if not bb_a or not bb_b:
        return None
    me = None
    for bb in bb_a + bb_b:
        e = min(bb[2] - bb[0], bb[3] - bb[1])
        if e <= 0:
            continue
        if me is None or e < me:
            me = e
    return None if me is None else me * dbu


def build_obstacle_region(cell, layout, obstacle_layers, safe_distance_dbu):
    region = kdb.Region()
    for spec in obstacle_layers:
        ln, dt = parse_layer(spec)
        li = layout.find_layer(ln, dt)
        if li is None:
            continue
        region += kdb.Region(cell.shapes(li))
    if safe_distance_dbu > 0:
        region = region.sized(safe_distance_dbu)
    region.merge()
    return region


def rasterize_region_kdb(region, bbox, resolution_dbu):
    ncols = max(1, (bbox.width() + resolution_dbu - 1) // resolution_dbu)
    nrows = max(1, (bbox.height() + resolution_dbu - 1) // resolution_dbu)
    origin = kdb.Point(bbox.left, bbox.bottom)
    step = kdb.Vector(resolution_dbu, resolution_dbu)
    return np.array(region.rasterize(origin, step, ncols, nrows)) > 0


def dbu_to_grid(x, y, bbox, resolution_dbu):
    col = (x - bbox.left) // resolution_dbu
    row = (y - bbox.bottom) // resolution_dbu
    return (row, col)


def grid_to_dbu(row, col, bbox, resolution_dbu):
    x = bbox.left + col * resolution_dbu + resolution_dbu // 2
    y = bbox.bottom + row * resolution_dbu + resolution_dbu // 2
    return (x, y)


def conditional_overwrite(cost, content, content_mask, r0, c0, condition_fn=None):
    nrows, ncols = content.shape
    grid_rows, grid_cols = cost.shape
    r1 = min(r0 + nrows, grid_rows)
    c1 = min(c0 + ncols, grid_cols)
    r0_c = max(r0, 0)
    c0_c = max(c0, 0)
    if r0_c >= r1 or c0_c >= c1:
        return
    cr0 = r0_c - r0
    cc0 = c0_c - c0
    cr1 = cr0 + (r1 - r0_c)
    cc1 = cc0 + (c1 - c0_c)
    cs = content[cr0:cr1, cc0:cc1]
    ms = content_mask[cr0:cr1, cc0:cc1]
    region = cost[r0_c:r1, c0_c:c1]
    if condition_fn is not None:
        mask = condition_fn(region, cs) & ms
    else:
        mask = ms
    region[mask] = cs[mask]


def get_damping_raster(region, bbox, resolution_dbu, safe_distance_dbu, hardness, n_steps):
    if n_steps <= 0 or safe_distance_dbu <= 0:
        return 0, 0, np.zeros((0, 0), dtype=np.float64)
    union = region.dup()
    for i in range(n_steps):
        union += region.sized(int(safe_distance_dbu * (i + 1) / n_steps))
    union = union & bbox
    if union.is_empty():
        return 0, 0, np.zeros((0, 0), dtype=np.float64)
    ub = union.bbox()
    origin_x = bbox.left + ((ub.left - bbox.left) // resolution_dbu) * resolution_dbu
    origin_y = bbox.bottom + ((ub.bottom - bbox.bottom) // resolution_dbu) * resolution_dbu
    origin = kdb.Point(origin_x, origin_y)
    extent_x = ub.right - origin_x
    extent_y = ub.top - origin_y
    step = kdb.Vector(resolution_dbu, resolution_dbu)
    ncols = max(1, (extent_x + resolution_dbu - 1) // resolution_dbu)
    nrows = max(1, (extent_y + resolution_dbu - 1) // resolution_dbu)
    raw = np.array(union.rasterize(origin, step, ncols, nrows))
    step_cost = hardness // n_steps if n_steps > 0 else hardness
    damping = (raw // (resolution_dbu * resolution_dbu)) * step_cost
    damping = damping.astype(np.float64)
    r0 = (origin_y - bbox.bottom) // resolution_dbu
    c0 = (origin_x - bbox.left) // resolution_dbu
    return r0, c0, damping


def build_cost_grid_graduated(obstacle_grid, obs_region, bbox, resolution_dbu,
                              obs_hardness, obs_damping_step, obs_safe_dbu):
    nrows, ncols = obstacle_grid.shape
    cost = np.ones((nrows, ncols), dtype=np.float64)
    cost[obstacle_grid] = -1.0
    if obs_damping_step > 0 and obs_safe_dbu > 0:
        r0, c0, damping = get_damping_raster(
            obs_region, bbox, resolution_dbu, obs_safe_dbu, obs_hardness, obs_damping_step)
        if damping.size > 0:
            conditional_overwrite(cost, damping, damping > 0, r0, c0,
                                  condition_fn=lambda existing, new: existing >= 0)
    return cost


def find_path(cost, start, end):
    nrows, ncols = cost.shape
    sr = max(0, min(start[0], nrows - 1))
    sc = max(0, min(start[1], ncols - 1))
    er = max(0, min(end[0], nrows - 1))
    ec = max(0, min(end[1], ncols - 1))
    orig_start = cost[sr, sc]
    orig_end = cost[er, ec]
    if cost[sr, sc] < 0:
        cost[sr, sc] = 1.0
    if cost[er, ec] < 0:
        cost[er, ec] = 1.0
    try:
        mcp = MCP_Geometric(cost, fully_connected=True)
        mcp.find_costs([(sr, sc)])
        return mcp.traceback((er, ec))
    except Exception:
        return None
    finally:
        cost[sr, sc] = orig_start
        cost[er, ec] = orig_end


# ---------------------------------------------------------------------------
# Steiner branch helper (for bus mode)
# ---------------------------------------------------------------------------

def find_path_to_existing(cost, start_rc, existing_path_grid):
    """Find shortest path from start_rc to any cell in existing_path_grid.

    existing_path_grid is a boolean mask of cells that were marked by a
    previously-routed segment of THIS net. Returns the path as a list of
    (row, col) ending at the first existing-path cell it hits.

    Implementation: temporarily make existing_path_grid cells walkable
    (cost=1) so MCP_Geometric can terminate ON them. Restore after.
    """
    import sys as _sys
    nrows, ncols = cost.shape
    sr = max(0, min(start_rc[0], nrows - 1))
    sc = max(0, min(start_rc[1], ncols - 1))
    orig_start = cost[sr, sc]
    if cost[sr, sc] < 0:
        cost[sr, sc] = 1.0
    saved_path_costs = cost[existing_path_grid].copy()
    cost[existing_path_grid] = 1.0
    try:
        ends = list(zip(*np.where(existing_path_grid)))
        if not ends:
            return None
        mcp = MCP_Geometric(cost, fully_connected=True)
        # MCP_Geometric.find_costs returns (cumulative_costs, traceback);
        # there is no `.costs` attribute. Use the returned array.
        costs_arr, _tb = mcp.find_costs([(sr, sc)])
        best_end = None
        best_c = math.inf
        for r, c in ends:
            v = costs_arr[r, c]
            if math.isfinite(v) and v < best_c:
                best_c = v
                best_end = (r, c)
        if best_end is None:
            return None
        path = mcp.traceback(best_end)
        return path
    except Exception:
        return None
    finally:
        cost[sr, sc] = orig_start
        cost[existing_path_grid] = saved_path_costs


# ---------------------------------------------------------------------------
# Main routing logic
# ---------------------------------------------------------------------------

def route(config: dict) -> dict:
    errors: list[str] = []

    # --- Adaptive two-level dispatch ---------------------------------------
    # When the two pin groups span the whole layout (inner contacts clustered
    # at centre, bonding pads on the perimeter) a single fine grid OOMs. If
    # auto-detected as that topology, route in two passes (inner fine / outer
    # coarse) joined by boundary patches — reusing this same route() unchanged
    # for each pass. Returns None to fall through to single-pass.
    if config.get("two_level", "auto") != "off" and not config.get("_tl_subpass"):
        try:
            _tl = route_two_level(config)
        except Exception as _e:
            # Never let a two-level orchestration failure break routing — fall
            # back to the proven single-pass engine on the original config.
            _tl = None
        if _tl is not None:
            return _tl

    # --- Parse config ---
    gds_path = config["gds_path"]
    cell_name = config.get("cell_name", "TOP")
    dbu = config.get("dbu", 0.001)
    pin_layer_a = config["pin_layer_a"]
    pin_layer_b = config["pin_layer_b"]
    obstacle_layers = config.get("obstacle_layers", [])
    path_width_um = config.get("path_width_um", 1.0)
    obs_safe_um = config.get("obs_safe_distance_um", 5.0)
    path_safe_um = config.get("path_safe_distance_um", 5.0)
    map_res_um = config.get("map_resolution_um", 1.0)
    obs_hardness = config.get("obs_hardness", 20.0)
    obs_damping_step = int(config.get("obs_damping_step", 4))
    pin_safe_a_um = config.get("pin_safe_distance_a_um", 5.0)
    pin_safe_b_um = config.get("pin_safe_distance_b_um", 5.0)
    pin_hardness = config.get("pin_hardness", 20.0)
    pin_damping_step = int(config.get("pin_damping_step", 4))
    # C3 differentiator: stronger graduated path avoidance.
    # path_hardness 10->25 (steeper barrier near completed routes),
    # path_damping_step 5->8 (smoother gradient -> Dijkstra picks detours
    # earlier rather than scraping the edge of the freeze halo).
    path_hardness = config.get("path_hardness", 25.0)
    path_damping_step = int(config.get("path_damping_step", 8))
    sort_pairs = bool(config.get("sort_pairs", True))
    dry_run = bool(config.get("dry_run", False))
    per_pair_obs_layers = config.get("per_pair_obstacle_layers", None)
    auto_map_res = bool(config.get("auto_map_resolution", False))

    # New schema fields
    bus_pairs = config.get("bus_pairs", None)
    # C3: default freeze margin 1.0 -> 2.5 um — route-route crossings on
    # singleton fixtures came in at 2-9 um^2 areas, i.e. routes scraping
    # within ~1 um of prior routes. 2.5 um margin pushes that gap wider.
    freeze_margin_um = float(config.get(
        "freeze_completed_routes_as_obstacles_with_margin", 2.5))
    pin_pairs_override = config.get("pin_pairs_override", None)

    # C3: default strategy is hybrid:
    #   singleton net (1 sink)  -> per-pair Dijkstra
    #   multi-sink net (>=2)    -> Steiner tree
    # User-facing strategy values: "hybrid" (default) | "per_pair" | "steiner".
    strategy = config.get("routing_strategy", "hybrid")

    # C3: auto-bus-detection threshold. When bus_pairs is None and pin_layer_a
    # has multiple pins clustered within this distance, treat them as a
    # multi-sink bus. Default 0 (disabled — typical fixtures are singleton
    # Hall bars, so we don't want to falsely cluster their 11 contacts).
    bus_auto_threshold_um = float(config.get("bus_auto_threshold_um", 0.0))

    # C3: experimented with sort_pairs_reverse=True (longest first) — caused
    # connectivity drops because long routes claimed corridors that shorter
    # routes critically needed. Default False (shortest first, baseline order).
    sort_pairs_reverse = bool(config.get("sort_pairs_reverse", False))

    # Dense-fan-out rescue (HM08 fix). When a net fails "No path found", its
    # own endpoint is usually walled in by sibling-pin markers + the freeze
    # halos of routes already laid. The rescue retries that single net ONCE
    # with a SMALL local disk around its own endpoints opened (halo + sibling
    # pin-marker cells softened to walkable), while keeping every real
    # routed-path cell hard — so the rescued lead reaches its pad without
    # crossing any prior route. Set rescue_unrouted_nets=False to disable.
    rescue_unrouted_nets = bool(config.get("rescue_unrouted_nets", True))

    # --- Convert um→dbu (resolution recomputed after auto-map) ---
    obs_safe_dbu = int(round(obs_safe_um / dbu))
    path_width_dbu = int(round(path_width_um / dbu))
    pin_safe_a_dbu = int(round(pin_safe_a_um / dbu))
    pin_safe_b_dbu = int(round(pin_safe_b_um / dbu))
    path_safe_dbu = int(round(path_safe_um / dbu))
    freeze_margin_dbu = int(round(freeze_margin_um / dbu))

    # --- Load GDS ---
    layout = kdb.Layout()
    layout.read(gds_path)
    layout.dbu = dbu

    cell = None
    for ci in range(layout.cells()):
        c = layout.cell(ci)
        if c.name == cell_name:
            cell = c
            break
    if cell is None:
        return {
            "status": "error", "routed_pairs": 0, "total_pins_a": 0,
            "total_pins_b": 0, "paths": [],
            "errors": [f"Cell '{cell_name}' not found"],
        }

    la, da = parse_layer(pin_layer_a)
    lb, db = parse_layer(pin_layer_b)
    pins_a = extract_pin_centers(cell, layout, la, da)
    pins_b = extract_pin_centers(cell, layout, lb, db)

    if not pins_a or not pins_b:
        missing = []
        if not pins_a:
            missing.append(f"pin_layer_a '{pin_layer_a}' has 0 pins")
        if not pins_b:
            missing.append(f"pin_layer_b '{pin_layer_b}' has 0 pins")
        return {
            "status": "error", "routed_pairs": 0,
            "total_pins_a": len(pins_a), "total_pins_b": len(pins_b),
            "paths": [],
            "errors": [f"No pins found: {'; '.join(missing)}."],
        }

    n_a = len(pins_a)
    n_b = len(pins_b)

    # --- auto_map_resolution ---
    auto_map_note = None
    if auto_map_res:
        bb_a = extract_pin_bboxes(cell, layout, la, da)
        bb_b = extract_pin_bboxes(cell, layout, lb, db)
        m = min_pin_edge_um(bb_a, bb_b, dbu)
        # Contact PITCH: nearest-neighbour distance between pin centres. The grid
        # must resolve the gap between adjacent contacts (map_resolution <=
        # pitch/2) or a tight fan-out grid-snaps into false crossings (issue #8).
        # For well-separated square markers edge/3 already satisfies this, but the
        # pitch floor handles large/overlapping/irregular markers where edge/3
        # would otherwise be coarser than half the contact spacing.
        pitch_um = None
        all_pins = pins_a + pins_b
        for ip in range(len(all_pins)):
            for jp in range(ip + 1, len(all_pins)):
                d = math.hypot(all_pins[ip][0] - all_pins[jp][0],
                               all_pins[ip][1] - all_pins[jp][1]) * dbu
                if d > 0 and (pitch_um is None or d < pitch_um):
                    pitch_um = d
        candidates = []
        if m is not None and m > 0:
            candidates.append(m / 3.0)
        if pitch_um is not None and pitch_um > 0:
            candidates.append(pitch_um / 2.0)
        if candidates:
            chosen = max(0.2, min(5.0, round(min(candidates), 1)))
            auto_map_note = (
                "auto_map_resolution: min_pin_edge_um={}, pitch_um={}, "
                "map_resolution_um set to {}".format(
                    "{:.2f}".format(m) if m else "n/a",
                    "{:.2f}".format(pitch_um) if pitch_um else "n/a",
                    chosen))
            map_res_um = chosen

    resolution_dbu = int(round(map_res_um / dbu))

    # ---------------------------------------------------------------------
    # PRE-PASS: assignment engine (run BEFORE cost-grid build, so we can
    # restrict pin_exclusion to active pins only). This prevents the
    # well-known pad-overrun bug — when there are 43 pad pins on L101/0
    # but the router only uses 11, the original logic clears around all
    # 43, leaving unchosen pads walkable.
    # ---------------------------------------------------------------------
    assignment_engine = "ordered_loop"
    nets: list[dict[str, Any]] = []  # [{a_idx, b_idxs}, ...]

    if pin_pairs_override is not None:
        if not isinstance(pin_pairs_override, list):
            return {"status": "failed", "routed_pairs": 0,
                    "total_pins_a": n_a, "total_pins_b": n_b,
                    "paths": [],
                    "errors": ["pin_pairs_override must be a list of [a_idx, b_idx] pairs."]}
        bad = []
        for k, ent in enumerate(pin_pairs_override):
            if (not isinstance(ent, (list, tuple)) or len(ent) != 2
                    or not isinstance(ent[0], int) or not isinstance(ent[1], int)):
                bad.append(f"entry {k}: must be [a_idx, b_idx]; got {ent!r}")
                continue
            if not (0 <= ent[0] < n_a):
                bad.append(f"entry {k}: a_idx {ent[0]} out of range (n_a={n_a})")
            if not (0 <= ent[1] < n_b):
                bad.append(f"entry {k}: b_idx {ent[1]} out of range (n_b={n_b})")
        if len(pin_pairs_override) != min(n_a, n_b):
            bad.append(f"length {len(pin_pairs_override)} != expected pair count {min(n_a, n_b)}")
        if bad:
            return {"status": "failed", "routed_pairs": 0,
                    "total_pins_a": n_a, "total_pins_b": n_b,
                    "paths": [],
                    "errors": ["pin_pairs_override validation errors:"] + bad}
        for a, b in pin_pairs_override:
            nets.append({"a_idx": int(a), "b_idxs": [int(b)]})
        assignment_engine = "override"

    elif bus_pairs is None and bus_auto_threshold_um > 0.0:
        # Auto-bus detection: cluster pins on layer A that fall within the
        # threshold, then assign each clustered A-pin to a nearest unused
        # B-pin. Implementation: use a union-find on pin distances.
        threshold_dbu = bus_auto_threshold_um / dbu
        parent = list(range(n_a))

        def _find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def _union(i, j):
            ri, rj = _find(i), _find(j)
            if ri != rj:
                parent[ri] = rj
        for i in range(n_a):
            for j in range(i + 1, n_a):
                d = math.hypot(pins_a[i][0] - pins_a[j][0],
                               pins_a[i][1] - pins_a[j][1])
                if d <= threshold_dbu:
                    _union(i, j)
        clusters: dict[int, list[int]] = {}
        for i in range(n_a):
            clusters.setdefault(_find(i), []).append(i)
        # Each cluster -> (cluster_a_idxs, sorted by closeness). Use the
        # cluster centroid to choose the nearest k available B pins, where
        # k = len(cluster). This collapses correctly to singleton when
        # threshold is too tight to merge.
        used_b: set[int] = set()
        for src_a_list in clusters.values():
            # Centroid in dbu
            cx = sum(pins_a[i][0] for i in src_a_list) / len(src_a_list)
            cy = sum(pins_a[i][1] for i in src_a_list) / len(src_a_list)
            # Need len(src_a_list) sinks — pick nearest unused B-pins.
            avail = [(math.hypot(pins_b[bi][0] - cx, pins_b[bi][1] - cy), bi)
                     for bi in range(n_b) if bi not in used_b]
            avail.sort()
            need = len(src_a_list)
            picked = [bi for _, bi in avail[:need]]
            for bi in picked:
                used_b.add(bi)
            if len(src_a_list) == 1:
                # singleton: standard 1:1
                nets.append({"a_idx": src_a_list[0], "b_idxs": [picked[0]]})
            else:
                # bus: pick the pin nearest the centroid as the source pin,
                # rest are sinks too via Steiner from that source's path.
                # Simpler — just designate the closest A as the bus source,
                # ignore other A-pins (caller can split if they don't want
                # this). Actually for general buses there's only one source
                # per net, so we route each A as its own net for safety
                # unless explicit bus_pairs are passed. So fall back to
                # singletons here.
                for ai in src_a_list:
                    if not picked:
                        break
                    nets.append({"a_idx": ai, "b_idxs": [picked.pop(0)]})
        assignment_engine = "auto_cluster"

    elif bus_pairs is not None:
        if not isinstance(bus_pairs, list):
            return {"status": "failed", "routed_pairs": 0,
                    "total_pins_a": n_a, "total_pins_b": n_b,
                    "paths": [],
                    "errors": ["bus_pairs must be a list of [a_idx, [b_idx,...]] entries."]}
        for k, ent in enumerate(bus_pairs):
            if (not isinstance(ent, (list, tuple)) or len(ent) != 2
                    or not isinstance(ent[0], int) or not isinstance(ent[1], (list, tuple))):
                return {"status": "failed", "routed_pairs": 0,
                        "total_pins_a": n_a, "total_pins_b": n_b,
                        "paths": [],
                        "errors": [f"bus_pairs entry {k}: must be [a_idx, [b_idx,...]]; got {ent!r}"]}
            a_idx = int(ent[0])
            b_idxs = [int(x) for x in ent[1]]
            if not (0 <= a_idx < n_a):
                return {"status": "failed", "routed_pairs": 0,
                        "total_pins_a": n_a, "total_pins_b": n_b,
                        "paths": [],
                        "errors": [f"bus_pairs entry {k}: a_idx {a_idx} out of range"]}
            if not b_idxs:
                return {"status": "failed", "routed_pairs": 0,
                        "total_pins_a": n_a, "total_pins_b": n_b,
                        "paths": [],
                        "errors": [f"bus_pairs entry {k}: empty b_idx list"]}
            for bi in b_idxs:
                if not (0 <= bi < n_b):
                    return {"status": "failed", "routed_pairs": 0,
                            "total_pins_a": n_a, "total_pins_b": n_b,
                            "paths": [],
                            "errors": [f"bus_pairs entry {k}: b_idx {bi} out of range"]}
            nets.append({"a_idx": a_idx, "b_idxs": b_idxs})

    else:
        # Ordered-loop pairing on the full pin sets.
        if n_a <= n_b:
            inner = pins_a
            outer = pins_b
            swapped = False
        else:
            inner = pins_b
            outer = pins_a
            swapped = True
        try:
            assignment, total_cost = ordered_loop_match(inner, outer)
        except ValueError as e:
            return {"status": "failed", "routed_pairs": 0,
                    "total_pins_a": n_a, "total_pins_b": n_b,
                    "paths": [],
                    "errors": [f"ordered_loop_match: {e}"]}
        for ii, oo in assignment:
            if swapped:
                a_idx, b_idx = oo, ii
            else:
                a_idx, b_idx = ii, oo
            nets.append({"a_idx": int(a_idx), "b_idxs": [int(b_idx)]})

    # Active pin sets — only pins participating in any net are "active".
    # Inactive pins remain blocked by global obstacles.
    active_a = sorted({net["a_idx"] for net in nets})
    active_b = sorted({bi for net in nets for bi in net["b_idxs"]})
    active_pins_a = [pins_a[i] for i in active_a]
    active_pins_b = [pins_b[i] for i in active_b]

    # GLOBAL obstacle inflation = path_width/2. Forces path centerlines
    # to stay path_width/2 from any obstacle, so the path POLYGON
    # (centerline ± path_width/2) doesn't brush. Pin clearance correctly
    # carves the per-pin-shape exception via individual-polygon logic.
    obs_region = build_obstacle_region(cell, layout, obstacle_layers,
                                        path_width_dbu // 2)

    pin_radius = resolution_dbu
    pin_regions_a = kdb.Region()
    for px, py in active_pins_a:
        pin_regions_a.insert(kdb.Box(px - pin_radius, py - pin_radius,
                                     px + pin_radius, py + pin_radius))
    pin_regions_b = kdb.Region()
    for px, py in active_pins_b:
        pin_regions_b.insert(kdb.Box(px - pin_radius, py - pin_radius,
                                     px + pin_radius, py + pin_radius))

    # PER-PAIR pin clearance — defer to routing loop. Globally clearing
    # all active pins (v1 behaviour) opens corridors through OTHER chosen
    # pads/contacts, allowing route_i to take a shortcut through pad_j.
    # Instead, each pair temporarily clears ONLY its own pin shapes during
    # its own pathfinding pass, then restores them.
    raw_obs_region = build_obstacle_region(cell, layout, obstacle_layers, 0)
    pin_clear_margin_dbu = obs_safe_dbu + resolution_dbu * 2

    # Pre-compute per-pin clearance regions.
    #
    # CRITICAL discovery (round-2 investigation, HM05):
    # `raw_obs_region.interacting(pt_box)` returns the MERGED obstacle blob.
    # Because contacts straddle the mesa edge, the mesa+all-contacts merge
    # into ONE polygon — so `own` ends up being the entire mesa+contacts blob.
    # Inflating that by `pin_clear_margin` clears the area around EVERY
    # contact, defeating the "this pin only" intent and re-introducing the
    # original adjacent-contact-brush bug.
    #
    # Fix: Index INDIVIDUAL contact and pad polygons (not the merged region)
    # so pin clearance can identify the SPECIFIC shape under the pin.
    other_obs_keepout_dbu = path_width_dbu // 2

    # Index every individual obstacle shape from each obstacle layer.
    individual_obs_polys: list[kdb.Polygon] = []
    for spec in obstacle_layers:
        ln, dt = parse_layer(spec)
        li = layout.find_layer(ln, dt)
        if li is None:
            continue
        for shape in cell.shapes(li).each():
            try:
                individual_obs_polys.append(shape.polygon)
            except Exception:
                pass

    def _shape_containing(px, py):
        """Return the smallest individual obstacle polygon whose bbox
        contains (px, py) and whose interior includes the point. None if
        the pin sits in free space."""
        candidates = []
        for poly in individual_obs_polys:
            bb = poly.bbox()
            if bb.left <= px <= bb.right and bb.bottom <= py <= bb.top:
                if poly.inside(kdb.Point(px, py)):
                    candidates.append((bb.area(), poly))
        if not candidates:
            return None
        candidates.sort(key=lambda t: t[0])
        return candidates[0][1]

    def pin_clearance_region(px, py):
        """Return a kdb.Region around (px,py) sized by pin_clear_margin_dbu,
        with neighbour-obstacle keepout subtracted. Uses the INDIVIDUAL
        contact/pad polygon under the pin (not the merged obstacle blob)."""
        own_poly = _shape_containing(px, py)
        if own_poly is None:
            return kdb.Region(kdb.Box(
                px - pin_clear_margin_dbu, py - pin_clear_margin_dbu,
                px + pin_clear_margin_dbu, py + pin_clear_margin_dbu))
        own_region = kdb.Region(own_poly)
        cleared = own_region.sized(pin_clear_margin_dbu)
        # Subtract every OTHER obstacle (each individual polygon) inflated
        # by path_width/2, so the cleared zone never includes cells within
        # path_width/2 of a neighbour obstacle.
        others = kdb.Region()
        for p in individual_obs_polys:
            if p is own_poly:
                continue
            others.insert(p)
        if not others.is_empty():
            cleared = cleared - others.sized(other_obs_keepout_dbu)
        return cleared

    pin_a_clear = [pin_clearance_region(px, py) for (px, py) in pins_a]
    pin_b_clear = [pin_clearance_region(px, py) for (px, py) in pins_b]
    # No global pin_exclusion — keep obs_region full.

    # ---------------------------------------------------------------------
    # Routing window: crop the cost grid to the PIN sub-region (+ detour
    # slack), clamped to the cell — NOT the whole cell.bbox(). Gridding the
    # full cell made fine map_resolution OOM-kill (exit -9/137) or time out
    # when pads sat on a far perimeter or unrelated geometry inflated the
    # cell bbox. The window can only SHRINK vs the legacy full-cell window,
    # so layouts that already fit route identically.
    # ---------------------------------------------------------------------
    cell_bbox = cell.bbox()
    margin = max(obs_safe_dbu, resolution_dbu * 10)
    cell_win = kdb.Box(cell_bbox.left - margin, cell_bbox.bottom - margin,
                       cell_bbox.right + margin, cell_bbox.top + margin)

    routing_bbox_um = config.get("routing_bbox")  # optional [x1,y1,x2,y2] um
    if routing_bbox_um is not None and len(routing_bbox_um) == 4:
        rx1, ry1, rx2, ry2 = routing_bbox_um
        win = kdb.Box(int(round(min(rx1, rx2) / dbu)),
                      int(round(min(ry1, ry2) / dbu)),
                      int(round(max(rx1, rx2) / dbu)),
                      int(round(max(ry1, ry2) / dbu)))
        bbox = kdb.Box(win.left - margin, win.bottom - margin,
                       win.right + margin, win.top + margin)
    else:
        pin_union = kdb.Region()
        for r in (pin_regions_a + pin_regions_b):
            pin_union.insert(r)
        pin_union.merge()
        if pin_union.is_empty():
            pb = cell_bbox
        else:
            pb = pin_union.bbox()
        span_diag = math.hypot(pb.width(), pb.height())
        win_margin = max(margin, int(0.30 * span_diag))  # detour slack
        bbox = kdb.Box(pb.left - win_margin, pb.bottom - win_margin,
                       pb.right + win_margin, pb.top + win_margin)

    # Clamp so the window never exceeds the legacy full-cell+margin window.
    bbox = bbox & cell_win
    if bbox.empty():
        bbox = cell_win

    # Hard grid-cell-count guard: auto-coarsen resolution before numpy OOM.
    MAX_GRID_CELLS = 25_000_000

    def _grid_dims(res):
        nc = max(1, (bbox.width() + res - 1) // res)
        nr = max(1, (bbox.height() + res - 1) // res)
        return int(nc), int(nr)

    ncols_w, nrows_w = _grid_dims(resolution_dbu)
    if ncols_w * nrows_w > MAX_GRID_CELLS:
        scale = math.sqrt((ncols_w * nrows_w) / float(MAX_GRID_CELLS))
        resolution_dbu = max(1, int(math.ceil(resolution_dbu * scale)))
        map_res_um = resolution_dbu * dbu
        ncols_w, nrows_w = _grid_dims(resolution_dbu)
        _guard_note = ("auto_map_resolution: routing grid exceeded {} cells; "
                       "map_resolution coarsened to {:.3f} um to avoid OOM"
                       ).format(MAX_GRID_CELLS, map_res_um)
        auto_map_note = (auto_map_note + "; " + _guard_note
                         if auto_map_note else _guard_note)

    routing_grid_info = {
        "rows": int(nrows_w), "cols": int(ncols_w),
        "cells": int(ncols_w * nrows_w),
        "window_um": [round(bbox.left * dbu, 3), round(bbox.bottom * dbu, 3),
                      round(bbox.right * dbu, 3), round(bbox.top * dbu, 3)],
    }

    obs_grid = rasterize_region_kdb(obs_region, bbox, resolution_dbu)
    cost = build_cost_grid_graduated(
        obs_grid, obs_region, bbox, resolution_dbu,
        obs_hardness, obs_damping_step, obs_safe_dbu)

    all_pin_region = pin_regions_a + pin_regions_b
    pin_grid = rasterize_region_kdb(all_pin_region, bbox, resolution_dbu)
    cost[pin_grid] = -2.0

    # Pin damping halos
    if pin_safe_a_dbu > 0 and pin_damping_step > 0:
        r0, c0, damping = get_damping_raster(
            pin_regions_a, bbox, resolution_dbu, pin_safe_a_dbu,
            pin_hardness, pin_damping_step)
        if damping.size > 0:
            conditional_overwrite(cost, damping, damping > 0, r0, c0,
                                  condition_fn=lambda e, n: (e > 0) & (e < n))
    if pin_safe_b_dbu > 0 and pin_damping_step > 0:
        r0, c0, damping = get_damping_raster(
            pin_regions_b, bbox, resolution_dbu, pin_safe_b_dbu,
            pin_hardness, pin_damping_step)
        if damping.size > 0:
            conditional_overwrite(cost, damping, damping > 0, r0, c0,
                                  condition_fn=lambda e, n: (e > 0) & (e < n))

    # Route assigned nets in a stable distance order. Default is shortest
    # first; sort_pairs_reverse=True switches to longest first for layouts
    # where long corridors should be claimed early.
    if sort_pairs and assignment_engine != "override":
        def primary_dist(net):
            a = pins_a[net["a_idx"]]
            ds = []
            for bi in net["b_idxs"]:
                b = pins_b[bi]
                ds.append(math.hypot(a[0] - b[0], a[1] - b[1]))
            return min(ds)
        nets.sort(key=primary_dist, reverse=sort_pairs_reverse)

    # --- dry_run early exit ---
    if dry_run:
        preview = []
        for net in nets:
            a = pins_a[net["a_idx"]]
            for bi in net["b_idxs"]:
                b = pins_b[bi]
                preview.append({
                    "pin_a_idx": net["a_idx"],
                    "pin_b_idx": bi,
                    "pin_a_um": [round(a[0] * dbu, 4), round(a[1] * dbu, 4)],
                    "pin_b_um": [round(b[0] * dbu, 4), round(b[1] * dbu, 4)],
                    "distance_um": round(math.hypot(a[0] - b[0], a[1] - b[1]) * dbu, 4),
                    "net_id": nets.index(net),
                })
        notes = [f"dry_run: matching only ({assignment_engine}), no routes inserted."]
        if auto_map_note:
            notes.append(auto_map_note)
        return {
            "status": "dry_run", "routed_pairs": 0,
            "total_pins_a": n_a, "total_pins_b": n_b,
            "paths": [], "pairs": preview,
            "errors": notes,
            "assignment_engine": assignment_engine,
            "map_resolution_um_used": map_res_um,
            "routing_grid": routing_grid_info,
        }

    # Validate per_pair_obs_layers length when supplied.
    flat_pair_count = sum(len(net["b_idxs"]) for net in nets)
    if per_pair_obs_layers is not None:
        if not isinstance(per_pair_obs_layers, list):
            errors.append(
                f"per_pair_obstacle_layers must be a list of lists; got {type(per_pair_obs_layers).__name__}")
            per_pair_obs_layers = None
        elif len(per_pair_obs_layers) != flat_pair_count:
            errors.append(
                f"per_pair_obstacle_layers length ({len(per_pair_obs_layers)}) != "
                f"flat pair count ({flat_pair_count}); ignoring per-pair obstacles.")
            per_pair_obs_layers = None

    # ---------------------------------------------------------------------
    # Routing loop
    # ---------------------------------------------------------------------
    result_paths: list[dict[str, Any]] = []
    pair_global_idx = 0  # index into per_pair_obs_layers (flattened across nets)

    # Accumulator: cells occupied by completed routes + their freeze halos.
    # Per-pair pin clearance MUST NOT overwrite these — clearing a cell that
    # belongs to a prior route's frozen halo opens a corridor through that
    # route, producing route-route crossings.
    frozen_routes_grid = np.zeros_like(cost, dtype=bool)
    rescued_nets = 0

    for net_id, net in enumerate(nets):
        a_idx = net["a_idx"]
        b_idxs = list(net["b_idxs"])
        pa_dbu = pins_a[a_idx]

        # Decide routing approach for this net
        is_bus = len(b_idxs) > 1
        if is_bus and strategy in ("steiner", "hybrid"):
            # Steiner mode: route longest sink first, then branch others.
            # Sort sinks by descending Euclidean distance from source.
            b_idxs.sort(key=lambda bi: -math.hypot(
                pins_b[bi][0] - pa_dbu[0], pins_b[bi][1] - pa_dbu[1]))
        # else: per-pair sequential, default order.

        net_grid_mask = np.zeros_like(cost, dtype=bool)  # union of this net's path cells

        for sink_local_idx, b_idx in enumerate(b_idxs):
            pb_dbu = pins_b[b_idx]
            start_rc = dbu_to_grid(pa_dbu[0], pa_dbu[1], bbox, resolution_dbu)
            end_rc = dbu_to_grid(pb_dbu[0], pb_dbu[1], bbox, resolution_dbu)

            # Per-pair pin recovery — clear THIS pair's pin shapes (their
            # containing obstacle, sized by margin) so the path can reach
            # both endpoints. Other pads/contacts stay obstacled.
            # CRITICAL: don't clear cells that belong to PRIOR ROUTES'
            # frozen halos — clearing them would let this route cross
            # through that prior route. Subtract frozen_routes_grid.
            pair_pin_region = pin_a_clear[a_idx] + pin_b_clear[b_idx]
            pair_pin_grid_full = rasterize_region_kdb(pair_pin_region, bbox, resolution_dbu)
            pair_pin_grid = pair_pin_grid_full & ~frozen_routes_grid
            saved_pin_costs = cost[pair_pin_grid].copy()
            cost[pair_pin_grid] = 1.0

            # No per-pair keepout — global obstacle inflation (in obs_region)
            # already enforces the path_width/2 centerline-to-obstacle gap.
            # Use empty masks so the restore code is a no-op.
            hard_keepout_mask = np.zeros_like(cost, dtype=bool)
            saved_hard_costs = np.array([], dtype=cost.dtype)
            soft_keepout_mask = np.zeros_like(cost, dtype=bool)
            saved_soft_costs = np.array([], dtype=cost.dtype)

            # Per-pair custom obstacles (caller-specified extras).
            per_pair_grid = None
            saved_per_pair_costs = None
            if per_pair_obs_layers is not None:
                try:
                    extra = per_pair_obs_layers[pair_global_idx]
                except IndexError:
                    extra = None
                if extra:
                    extra_region = build_obstacle_region(cell, layout, list(extra), 0)
                    extra_region = extra_region - pair_pin_region
                    per_pair_grid = rasterize_region_kdb(extra_region, bbox, resolution_dbu)
                    if per_pair_grid.any():
                        saved_per_pair_costs = cost[per_pair_grid].copy()
                        cost[per_pair_grid] = -1.0

            # ---- ROUTE ----
            this_path_rc = None
            steiner_branch = bool(is_bus and strategy in ("steiner", "hybrid")
                                  and sink_local_idx > 0 and net_grid_mask.any())
            if steiner_branch:
                # Branch: route from sink B back to nearest cell on the
                # existing net path. Source = pin_b, target set = net cells.
                this_path_rc = find_path_to_existing(cost, end_rc, net_grid_mask)
            else:
                this_path_rc = find_path(cost, start_rc, end_rc)

            # ---- DENSE-FAN-OUT RESCUE ----
            # When a net fails "No path found" it is almost always because its
            # own endpoint(s) are walled in: in a tight 8-contact bundle the
            # contact is ringed by sibling-pin markers (-2.0) and the freeze
            # halos (-1.0) of routes already laid, leaving no escape corridor
            # — even though a legal, non-crossing path to the pad exists (this
            # is the HM08 connectivity cap). The default per-pair clearance is
            # deliberately suppressed where it overlaps a prior route's frozen
            # halo (`& ~frozen_routes_grid` above), which is what seals the
            # corridor shut.
            #
            # Rescue (two tiers, both keep real routed-path cells (-3.0) HARD so
            # no rescued lead can ever cross a prior route — contact_isolation /
            # crossing_pairs stay clean):
            #
            #   Tier 1 (local, most conservative): open a small disk around this
            #     net's OWN endpoints, softening halo (-1.0) and sibling-pin
            #     marker (-2.0) cells to walkable. Frees the common case where
            #     the contact is merely ringed in by its neighbours.
            #
            #   Tier 2 (global halo/marker relax): if Tier 1 still fails, soften
            #     ALL halo + sibling-pin-marker cells (still keeping -3.0 route
            #     cells hard). The pin markers are tiny (~1 um) pin-CENTRE boxes,
            #     not the pad/contact polygons (those, when declared, live in
            #     obs_region and stay hard), so this cannot cut a route through a
            #     declared obstacle, and -3.0 hardness still forbids route
            #     crossings. Handles mid-corridor (non-endpoint) blockage.
            #
            # Disable both with rescue_unrouted_nets=False.
            rescued_this_pair = False
            if (this_path_rc is None and rescue_unrouted_nets
                    and not steiner_branch):
                soft_cost = 1.0 + max(0.0, path_hardness * 0.5)
                # ---- Tier 1: local endpoint disk ----
                rescue_disk_dbu = max(pin_clear_margin_dbu, resolution_dbu * 3)
                rdisk_cells = max(3, int(round(rescue_disk_dbu / resolution_dbu)))
                rdisk_cells = min(rdisk_cells, 12)  # keep the opening local
                nrows_g, ncols_g = cost.shape
                yy = np.arange(nrows_g)[:, None]
                xx = np.arange(ncols_g)[None, :]
                disk = np.zeros_like(cost, dtype=bool)
                for (pr, pc) in (start_rc, end_rc):
                    pr_c = max(0, min(int(pr), nrows_g - 1))
                    pc_c = max(0, min(int(pc), ncols_g - 1))
                    disk |= ((yy - pr_c) ** 2 + (xx - pc_c) ** 2) <= rdisk_cells ** 2
                relax_mask = disk & (cost <= -1.0) & (cost > -2.5)
                if relax_mask.any():
                    saved_relax_costs = cost[relax_mask].copy()
                    cost[relax_mask] = soft_cost
                    this_path_rc = find_path(cost, start_rc, end_rc)
                    cost[relax_mask] = saved_relax_costs
                    if this_path_rc is not None:
                        rescued_this_pair = True
                        rescued_nets += 1
                # ---- Tier 2: global halo + pin-marker relax ----
                if this_path_rc is None:
                    relax_mask2 = (cost <= -1.0) & (cost > -2.5)
                    if relax_mask2.any():
                        saved_relax_costs2 = cost[relax_mask2].copy()
                        cost[relax_mask2] = soft_cost
                        this_path_rc = find_path(cost, start_rc, end_rc)
                        cost[relax_mask2] = saved_relax_costs2
                        if this_path_rc is not None:
                            rescued_this_pair = True
                            rescued_nets += 1

            if this_path_rc is None:
                errors.append(
                    f"No path found for net_id={net_id} a_idx={a_idx} -> b_idx={b_idx}"
                    + (" (steiner branch)" if steiner_branch else ""))
                if per_pair_grid is not None and saved_per_pair_costs is not None:
                    cost[per_pair_grid] = saved_per_pair_costs
                cost[hard_keepout_mask] = saved_hard_costs
                cost[soft_keepout_mask] = saved_soft_costs
                cost[pair_pin_grid] = saved_pin_costs
                pair_global_idx += 1
                continue

            # Restore per-pair custom obstacles + keepout before global cost-update.
            if per_pair_grid is not None and saved_per_pair_costs is not None:
                cost[per_pair_grid] = saved_per_pair_costs
            cost[hard_keepout_mask] = saved_hard_costs
            cost[soft_keepout_mask] = saved_soft_costs

            # Convert to dbu + snap endpoints
            path_dbu = [list(grid_to_dbu(r, c, bbox, resolution_dbu)) for r, c in this_path_rc]
            if steiner_branch:
                # For a branch, path_rc starts at pin_b (we ran search from
                # end_rc) and ends at nearest existing-path cell. Swap so the
                # output goes "from existing junction towards pin_b" — same
                # data either way; consumers shouldn't care about direction.
                path_dbu[0] = list(pb_dbu)
            else:
                path_dbu[0] = list(pa_dbu)
                path_dbu[-1] = list(pb_dbu)
            path_dbu = compress_path(path_dbu)

            result_paths.append({
                "points_dbu": path_dbu,
                "pin_a": list(pa_dbu),
                "pin_b": list(pb_dbu),
                "net_id": net_id,
                "branch_index": sink_local_idx,
                "steiner_branch": steiner_branch,
                "rescued": rescued_this_pair,
            })

            # Mark this segment in cost grid (impassable for downstream pairs)
            # and accumulate into net_grid_mask for Steiner.
            if len(this_path_rc) >= 2:
                pts = [kdb.Point(*grid_to_dbu(r, c, bbox, resolution_dbu))
                       for r, c in this_path_rc]
                path_obj = kdb.Path(pts, path_width_dbu, path_width_dbu // 2,
                                    path_width_dbu // 2, True)
                path_region = kdb.Region(path_obj)
                pg = rasterize_region_kdb(path_region, bbox, resolution_dbu)
                cost[pg] = -3.0
                net_grid_mask |= pg

                # Path damping: standard path_safe_distance.
                if path_damping_step > 0 and path_safe_dbu > 0:
                    r0, c0, damping = get_damping_raster(
                        path_region, bbox, resolution_dbu, path_safe_dbu,
                        path_hardness, path_damping_step)
                    if damping.size > 0:
                        conditional_overwrite(
                            cost, damping, damping > 0, r0, c0,
                            condition_fn=lambda e, n: (e > 0) & (e < n))

                # ALSO: freeze with margin (issue #28 explicit request) —
                # a hard inflation of the routed path PLUS path_safe_distance.
                # Default margin 1um. Set to 0 to disable.
                # Track frozen cells in `frozen_routes_grid` so subsequent
                # per-pair pin clearance can't accidentally re-open them.
                if freeze_margin_dbu > 0:
                    frozen = path_region.sized(freeze_margin_dbu)
                    fg = rasterize_region_kdb(frozen, bbox, resolution_dbu)
                    cost[fg & (cost > 0)] = -1.0  # don't overwrite pins/sinks not yet routed
                    frozen_routes_grid |= fg
                    # Steiner: include freeze halo in net_grid_mask so the
                    # next branch's find_path_to_existing can approach the
                    # path through its own halo (otherwise the halo
                    # surrounds the path on all sides and Dijkstra can't
                    # reach the path cells).
                    net_grid_mask |= fg
                else:
                    frozen_routes_grid |= pg

            # Restore this pair's pin to blocked baseline
            cost[pair_pin_grid] = saved_pin_costs

            pair_global_idx += 1

    if auto_map_note is not None:
        errors.append(auto_map_note)

    info_only = all(n.startswith("auto_map_resolution") for n in errors)

    result = {
        "status": "success" if (not errors or info_only) else "partial",
        "routed_pairs": len(result_paths),
        "total_pins_a": n_a,
        "total_pins_b": n_b,
        "paths": result_paths,
        "errors": errors,
        "map_resolution_um_used": map_res_um,
        "assignment_engine": assignment_engine,
        "n_nets": len(nets),
        "rescued_nets": rescued_nets,
        "routing_grid": routing_grid_info,
    }
    if rescued_nets > 0:
        # Informational only — does NOT flip status to partial. These nets DID
        # route; they just needed the freeze-halo relaxed to find a corridor.
        result["rescue_note"] = (
            "{} net(s) routed via dense-fan-out rescue (relaxed the soft "
            "freeze halo of prior routes; real path cells, pins, and obstacles "
            "stayed hard). Verify with route_inspect / contact_isolation that "
            "no crossings were introduced.".format(rescued_nets))
    return result


# ---------------------------------------------------------------------------
# Adaptive two-level (inner-fine / outer-coarse) orchestration
# ---------------------------------------------------------------------------

def _min_pitch_dbu(coords):
    """Nearest-neighbour distance (dbu) among a list of (x,y) coords, or None."""
    best = None
    n = len(coords)
    for i in range(n):
        for j in range(i + 1, n):
            d = math.hypot(coords[i][0] - coords[j][0], coords[i][1] - coords[j][1])
            if d > 0 and (best is None or d < best):
                best = d
    return best


def route_two_level(config: dict):
    """Adaptive two-pass routing. Returns a merged result dict when two-level
    fires, or None to fall back to single-pass route(). Reuses route() unchanged
    for each pass (with two_level='off', _tl_subpass=True).

    Pass 1 routes the inner (sample) contacts to per-net boundary points on the
    inner-window edge at FINE resolution; pass 2 routes those boundary points to
    the outer (perimeter) pads at COARSE resolution, with the inner routes added
    as an obstacle layer. A small patch at each boundary point bridges the two
    write fields on the output layer.
    """
    mode = config.get("two_level", "auto")
    if mode == "off":
        return None
    # per-pair obstacle layers use a flat cross-net accumulator incompatible
    # with the two-pass split — fall back to single-pass.
    if config.get("per_pair_obstacle_layers"):
        return None

    dbu = config.get("dbu", 0.001)
    cell_name = config.get("cell_name", "TOP")
    gds_path = config["gds_path"]
    la, da = parse_layer(config["pin_layer_a"])
    lb, db = parse_layer(config["pin_layer_b"])

    layout = kdb.Layout()
    layout.read(gds_path)
    layout.dbu = dbu
    cell = None
    for ci in range(layout.cells()):
        c = layout.cell(ci)
        if c.name == cell_name:
            cell = c
            break
    if cell is None:
        cell = layout.top_cell()
    if cell is None:
        return None

    pins_a = extract_pin_centers(cell, layout, la, da)
    pins_b = extract_pin_centers(cell, layout, lb, db)
    if len(pins_a) < 3 or len(pins_b) < 3:
        return None
    side = detect_inner_outer(pins_a, pins_b)
    if side is None:
        return None
    if side == "a":
        inner_layer, inner_pins, inner_ld = config["pin_layer_a"], pins_a, (la, da)
        outer_layer, outer_pins = config["pin_layer_b"], pins_b
    else:
        inner_layer, inner_pins, inner_ld = config["pin_layer_b"], pins_b, (lb, db)
        outer_layer, outer_pins = config["pin_layer_a"], pins_a

    # Inner window from the inner pin bounding boxes, centred on their centroid.
    inner_bbs = extract_pin_bboxes(cell, layout, inner_ld[0], inner_ld[1])
    il = min(b[0] for b in inner_bbs)
    ib = min(b[1] for b in inner_bbs)
    ir = max(b[2] for b in inner_bbs)
    it = max(b[3] for b in inner_bbs)
    cx, cy = (il + ir) // 2, (ib + it) // 2
    inner_w, inner_h = ir - il, it - ib

    pitch_dbu = _min_pitch_dbu(inner_pins) or max(inner_w, inner_h, 1)
    inner_margin_um = config.get("inner_margin")
    if inner_margin_um is not None:
        margin_dbu = int(round(inner_margin_um / dbu))
    else:
        margin_dbu = max(int(2 * pitch_dbu), int(0.15 * max(inner_w, inner_h)))
    Hx = inner_w // 2 + margin_dbu
    Hy = inner_h // 2 + margin_dbu
    # Clamp half-extents so the window stays inside the cell, centred on (cx,cy).
    cellbb = cell.bbox()
    Hx = max(1, min(Hx, cx - cellbb.left, cellbb.right - cx))
    Hy = max(1, min(Hy, cy - cellbb.bottom, cellbb.top - cy))
    win = kdb.Box(cx - Hx, cy - Hy, cx + Hx, cy + Hy)

    # Resolutions: inner fine from contact pitch/edge; outer coarse from config.
    _edges = [min(b[2] - b[0], b[3] - b[1]) for b in inner_bbs
              if min(b[2] - b[0], b[3] - b[1]) > 0]
    edge_um = (min(_edges) * dbu) if _edges else None
    pitch_um = pitch_dbu * dbu
    fine_candidates = [pitch_um / 2.0]
    if edge_um and edge_um > 0:
        fine_candidates.append(edge_um / 3.0)
    fine_res_um = max(0.2, min(5.0, round(min(fine_candidates), 3)))
    coarse_res_um = float(config.get("map_resolution_um", 2.0))
    fine_res_dbu = int(round(fine_res_um / dbu))
    coarse_res_dbu = int(round(coarse_res_um / dbu))

    _, outer_span = coords_span(outer_pins)
    ok, reason = should_two_level(
        len(inner_pins), len(outer_pins),
        inner_span_dbu=max(inner_w, inner_h), outer_span_dbu=outer_span,
        full_w_dbu=cellbb.width(), full_h_dbu=cellbb.height(),
        inner_w_dbu=2 * Hx, inner_h_dbu=2 * Hy,
        fine_res_dbu=fine_res_dbu, coarse_res_dbu=coarse_res_dbu,
        max_cells=DEFAULT_MAX_GRID_CELLS)
    if mode == "auto" and not ok:
        return None

    # Pairing: ordered_loop_match requires the smaller-count set first.
    if len(inner_pins) <= len(outer_pins):
        assign, _ = ordered_loop_match(inner_pins, outer_pins)
        pairs = [(ii, oo) for (ii, oo) in assign]            # (inner_idx, outer_idx)
    else:
        assign, _ = ordered_loop_match(outer_pins, inner_pins)
        pairs = [(ii, oo) for (oo, ii) in assign]            # remap to (inner, outer)
    if not pairs:
        return None

    # Boundary point per net (ray from window centre toward the outer pin).
    boundary = []
    for (ii, oo) in pairs:
        px, py = outer_pins[oo]
        bp = compute_boundary_point(cx, cy, Hx, Hy, px, py)
        if bp is None:
            return None  # pad inside window / degenerate -> single-pass fallback
        boundary.append(bp)

    # Pick scratch layer numbers that the layout does NOT already use, so we
    # never clobber the caller's geometry / obstacle / pin layers.
    _used = set()
    for _li in layout.layer_indices():
        _info = layout.get_info(_li)
        _used.add((_info.layer, _info.datatype))

    def _free_layer(start):
        n = start
        while (n, 0) in _used:
            n += 1
        _used.add((n, 0))
        return n
    bnd_ln = _free_layer(9001)
    obs_ln = _free_layer(9002)
    bnd_spec = "{}/0".format(bnd_ln)
    obs_spec = "{}/0".format(obs_ln)

    # Write boundary-pin markers on the scratch layer; save a temp GDS for pass 1.
    bnd_idx = layout.layer(bnd_ln, 0)
    cell.shapes(bnd_idx).clear()
    pin_s = max(fine_res_dbu * 2, int(round(2.0 / dbu)))
    for (bx, by) in boundary:
        cell.shapes(bnd_idx).insert(
            kdb.Box(bx - pin_s // 2, by - pin_s // 2, bx + pin_s // 2, by + pin_s // 2))

    # Map each net to the boundary pin's ITERATION index (shape iteration order
    # need not equal insertion order, and route() reads pins by iteration order)
    # so pin_pairs_override references the correct boundary pin in both passes.
    bnd_centers = extract_pin_centers(cell, layout, bnd_ln, 0)

    def _bnd_index(coord):
        for j, c in enumerate(bnd_centers):
            if abs(c[0] - coord[0]) <= 1 and abs(c[1] - coord[1]) <= 1:
                return j
        return None
    jk = [_bnd_index(boundary[k]) for k in range(len(boundary))]
    if any(j is None for j in jk):
        return None  # boundary pin lookup failed -> single-pass fallback
    # Injectivity guard: two outer pads nearly collinear with the window centre
    # round to the SAME boundary point, which would wire net k's contact to net
    # j's pad (silent mis-route). If the net->marker map is not 1:1, bail to
    # single-pass rather than emit scrambled topology.
    if len(set(jk)) != len(jk):
        return None

    import tempfile
    tmp_dir = tempfile.mkdtemp(prefix="route_two_level_")

    inner_width_um = float(config.get("inner_width", config.get("path_width_um", 1.0)))
    outer_width_um = float(config.get("outer_width", config.get("path_width_um", 2.0)))

    def _subcfg(**over):
        c = dict(config)
        c["two_level"] = "off"
        c["_tl_subpass"] = True
        c["output_path"] = None
        c["per_pair_obstacle_layers"] = None
        c.pop("routing_bbox", None)
        c.update(over)
        return c

    try:
        tmp1 = os.path.join(tmp_dir, "pass1.gds")
        layout.write(tmp1)
        pass1 = _subcfg(
            gds_path=tmp1, pin_layer_a=inner_layer, pin_layer_b=bnd_spec,
            map_resolution_um=fine_res_um, auto_map_resolution=False,
            path_width_um=inner_width_um,
            routing_bbox=[win.left * dbu, win.bottom * dbu, win.right * dbu, win.top * dbu],
            pin_pairs_override=[[pairs[k][0], jk[k]] for k in range(len(pairs))],
        )
        inner_res = route(pass1)
        inner_paths = inner_res.get("paths", [])

        # Write the inner routes as an obstacle layer; save a temp GDS for pass 2.
        obs_idx = layout.layer(obs_ln, 0)
        cell.shapes(obs_idx).clear()
        iw_dbu = max(1, int(round(inner_width_um / dbu)))
        for p in inner_paths:
            pts = [kdb.Point(int(round(x)), int(round(y))) for x, y in p["points_dbu"]]
            if len(pts) >= 2:
                cell.shapes(obs_idx).insert(kdb.Path(pts, iw_dbu))
        tmp2 = os.path.join(tmp_dir, "pass2.gds")
        layout.write(tmp2)

        obstacles2 = list(config.get("obstacle_layers", [])) + [obs_spec]
        pass2 = _subcfg(
            gds_path=tmp2, pin_layer_a=bnd_spec, pin_layer_b=outer_layer,
            obstacle_layers=obstacles2,
            map_resolution_um=coarse_res_um, auto_map_resolution=False,
            path_width_um=outer_width_um,
            pin_pairs_override=[[jk[k], pairs[k][1]] for k in range(len(pairs))],
        )
        outer_res = route(pass2)
        outer_paths = outer_res.get("paths", [])
    finally:
        # Exception-safe temp cleanup.
        try:
            for _f in os.listdir(tmp_dir):
                os.remove(os.path.join(tmp_dir, _f))
            os.rmdir(tmp_dir)
        except OSError:
            pass

    # Keep ONLY nets that routed in BOTH passes — drop orphan single-pass
    # segments (and below, their patches) so we never insert a floating trace
    # or a patch connected on only one side. Dropped nets are reported via the
    # partial status; that is strictly better than dangling geometry.
    inner_nets = {p["net_id"] for p in inner_paths}
    outer_nets = {p["net_id"] for p in outer_paths}
    routed_set = inner_nets & outer_nets
    inner_paths = [p for p in inner_paths if p["net_id"] in routed_set]
    outer_paths = [p for p in outer_paths if p["net_id"] in routed_set]

    # Boundary patches: large enough to bridge fine/coarse grid snap + widths.
    # Only for fully-routed nets (patch k corresponds to net k).
    fine_used_dbu = int(round(inner_res.get("map_resolution_um_used", fine_res_um) / dbu))
    coarse_used_dbu = int(round(outer_res.get("map_resolution_um_used", coarse_res_um) / dbu))
    user_patch_dbu = int(round(float(config.get("patch_size", 0.0)) / dbu))
    patch_dbu = max(user_patch_dbu,
                    int(round(outer_width_um / dbu)) + 2 * coarse_used_dbu,
                    int(round(inner_width_um / dbu)) + 2 * fine_used_dbu)
    patches = [{"x_dbu": boundary[k][0], "y_dbu": boundary[k][1], "size_dbu": patch_dbu}
               for k in range(len(boundary)) if k in routed_set]

    # Tag each path with its write-field width so the inserter uses inner vs
    # outer line width (a single output_layer carries both).
    for p in inner_paths:
        p["width_um"] = inner_width_um
    for p in outer_paths:
        p["width_um"] = outer_width_um

    nets_routed = len(routed_set)
    errors = list(inner_res.get("errors", [])) + list(outer_res.get("errors", []))
    info_only = all(n.startswith("auto_map_resolution") for n in errors)
    status = "success" if (nets_routed == len(pairs) and (not errors or info_only)) else "partial"

    return {
        "status": status,
        "two_level": True,
        "two_level_reason": reason if mode == "auto" else "forced on",
        "routed_pairs": nets_routed,
        "inner_routed": len(inner_paths),
        "outer_routed": len(outer_paths),
        "total_pins_a": len(pins_a),
        "total_pins_b": len(pins_b),
        "n_nets": len(pairs),
        "paths": inner_paths + outer_paths,
        "patches": patches,
        "inner_grid": inner_res.get("routing_grid"),
        "outer_grid": outer_res.get("routing_grid"),
        "map_resolution_um_used": {
            "inner": inner_res.get("map_resolution_um_used"),
            "outer": outer_res.get("map_resolution_um_used"),
        },
        "assignment_engine": "two_level",
        "boundary_points_um": [[round(bx * dbu, 4), round(by * dbu, 4)] for bx, by in boundary],
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <config.json>", file=sys.stderr)
        sys.exit(1)
    config_path = sys.argv[1]
    with open(config_path) as f:
        config = json.load(f)
    result = route(config)
    output_path = config.get("output_path")
    if output_path:
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)
        print(f"Routes written to {output_path}")
    else:
        print(json.dumps(result, indent=2))
    if result["status"] == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
