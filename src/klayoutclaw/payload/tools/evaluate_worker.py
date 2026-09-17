#!/usr/bin/env python
"""evaluate_worker.py -- Nanodevice DRC + metric evaluator.

CLI: python evaluate_worker.py config.json

Config JSON input:
{
    "gds_path": "result.gds",
    "reference_gds": "reference.gds",  (optional, for checks that need ref layers)
    "layer_map": {"mesa": [20, 0], "contact_patch": [21, 0], ...},
    "checks": [
        {"name": "component_containment", "args": {...}, "weight": 0.2},
        ...
    ],
    "output_path": "output.json"
}

Output JSON: {status, overall, checks: [{name, score, weight, detail}], next_step_suggestion}
"""

import sys
import os
import json

try:
    import gdstk
except ImportError as e:
    print("ERROR: Missing dependency gdstk: {}".format(e), file=sys.stderr)
    sys.exit(1)

try:
    import shapely
    import shapely.geometry as sg
    from shapely.ops import unary_union
    from shapely.validation import make_valid
except ImportError as e:
    print("ERROR: Missing dependency shapely: {}".format(e), file=sys.stderr)
    sys.exit(1)

try:
    import numpy as np
except ImportError as e:
    print("ERROR: Missing dependency numpy: {}".format(e), file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Geometry helpers (unchanged from original)
# ---------------------------------------------------------------------------

def _to_shapely(points):
    try:
        s = sg.Polygon(points)
        if not s.is_valid:
            s = make_valid(s)
        return s if not s.is_empty else None
    except (ValueError, shapely.errors.GEOSException):
        return None


def _extract_shapely(lib, layer, datatype):
    shapes = []
    for cell in lib.cells:
        for poly in cell.get_polygons():
            if poly.layer == layer and poly.datatype == datatype:
                s = _to_shapely(poly.points)
                if s is not None:
                    shapes.append(s)
    if shapes:
        return shapes
    for cell in lib.cells:
        for path in cell.get_paths():
            path_polys = path.to_polygons()
            for i, (l, dt) in enumerate(zip(path.layers, path.datatypes)):
                if l == layer and dt == datatype and i < len(path_polys):
                    s = _to_shapely(path_polys[i])
                    if s is not None:
                        shapes.append(s)
    return shapes


def _normalize_layer_spec(spec):
    """Normalize a layer spec to list of (layer, datatype) pairs.

    Accepts:
      [layer, datatype]              -> [(layer, datatype)]
      [[layer, dt], [layer, dt]]     -> [(layer, dt), ...]
      {"layer": N, "datatype": N}    -> [(N, N)]
      [{"layer": N, "datatype": N}]  -> [(N, N)]
    """
    if isinstance(spec, dict):
        return [(spec["layer"], spec.get("datatype", 0))]
    if isinstance(spec, (list, tuple)) and len(spec) > 0:
        if isinstance(spec[0], dict):
            return [(s["layer"], s.get("datatype", 0)) for s in spec]
        if isinstance(spec[0], (list, tuple)):
            return [tuple(s) for s in spec]
        return [tuple(spec)]
    return []


def _component_union(lib, layer_map, name):
    spec = layer_map.get(name)
    if spec is None:
        return sg.Polygon()
    pairs = _normalize_layer_spec(spec)
    all_shapes = []
    for layer, dt in pairs:
        all_shapes.extend(_extract_shapely(lib, layer, dt))
    return unary_union(all_shapes) if all_shapes else sg.Polygon()


def _component_list(lib, layer_map, name):
    spec = layer_map.get(name)
    if spec is None:
        return []
    pairs = _normalize_layer_spec(spec)
    all_shapes = []
    for layer, dt in pairs:
        all_shapes.extend(_extract_shapely(lib, layer, dt))
    return all_shapes


def _get_spine_endpoints(lib, layer_map, component="contact_route"):
    spec = layer_map.get(component)
    if spec is None:
        return []
    pairs = _normalize_layer_spec(spec)
    endpoints = []
    for layer, dt in pairs:
        layer_eps = []
        for cell in lib.cells:
            for path in cell.get_paths():
                if path.layers[0] == layer and path.datatypes[0] == dt:
                    try:
                        spine = np.array(path.spine())
                    except (AttributeError, TypeError):
                        try:
                            spine = np.array(path.points)
                        except (AttributeError, TypeError):
                            continue
                    if len(spine) >= 2:
                        layer_eps.append((spine[0].copy(), spine[-1].copy()))
        if layer_eps:
            endpoints.extend(layer_eps)
            continue
        for cell in lib.cells:
            for poly in cell.get_polygons():
                if poly.layer == layer and poly.datatype == dt:
                    pts = poly.points
                    if len(pts) < 3:
                        continue
                    dists = np.sqrt(((pts[:, None] - pts[None, :]) ** 2).sum(axis=2))
                    i, j = np.unravel_index(np.argmax(dists), dists.shape)
                    endpoints.append((pts[i].copy(), pts[j].copy()))
    return endpoints


# ---------------------------------------------------------------------------
# Region resolution
# ---------------------------------------------------------------------------

def _resolve_region(out_lib, ref_lib, layer_map, region_spec, region_op="union"):
    """Resolve a region specification to a shapely geometry.

    region_spec: a layer_map key (str) or list of keys.
    region_op: "union", "intersection", or "difference" (applied left-to-right).
    Uses ref_lib if available, falls back to out_lib.
    """
    if isinstance(region_spec, str):
        region_spec = [region_spec]

    lib = ref_lib if ref_lib is not None else out_lib
    regions = []
    for key in region_spec:
        r = _component_union(lib, layer_map, key)
        if r.is_empty:
            r = _component_union(out_lib, layer_map, key)
        regions.append(r)

    if not regions:
        return sg.Polygon()

    result = regions[0]
    for r in regions[1:]:
        if region_op == "intersection":
            result = result.intersection(r)
        elif region_op == "difference":
            result = result.difference(r)
        else:
            result = result.union(r)
    return result


# ---------------------------------------------------------------------------
# Check primitives
# ---------------------------------------------------------------------------

def _prim_component_overlap(out_lib, ref_lib, layer_map, args):
    """Fraction of component area overlapping with region."""
    comp = _component_union(out_lib, layer_map, args["component"])
    if comp.is_empty:
        return 0.0
    region = _resolve_region(out_lib, ref_lib, layer_map,
                             args["region"], args.get("region_op", "union"))
    if region.is_empty:
        return 0.0
    return comp.intersection(region).area / comp.area


def _prim_component_containment(out_lib, ref_lib, layer_map, args):
    """Fraction of component area contained within region."""
    comp = _component_union(out_lib, layer_map, args["component"])
    if comp.is_empty:
        return 0.0
    region = _resolve_region(out_lib, ref_lib, layer_map,
                             args["region"], args.get("region_op", "union"))
    if region.is_empty:
        return 0.0
    return comp.intersection(region).area / comp.area


def _route_endpoints_on_layer(lib, layer, datatype):
    """Extract spine endpoints for each route on a single layer.

    Returns list of (endpoint_a, endpoint_b) numpy arrays.
    Prefers path spine; falls back to polygon most-distant-vertices.
    """
    eps = []
    for cell in lib.cells:
        for path in cell.get_paths():
            if path.layers[0] == layer and path.datatypes[0] == datatype:
                try:
                    spine = np.array(path.spine())
                except (AttributeError, TypeError):
                    try:
                        spine = np.array(path.points)
                    except (AttributeError, TypeError):
                        continue
                if len(spine) >= 2:
                    eps.append((spine[0].copy(), spine[-1].copy()))
    if eps:
        return eps
    for cell in lib.cells:
        for poly in cell.get_polygons():
            if poly.layer == layer and poly.datatype == datatype:
                pts = poly.points
                if len(pts) < 3:
                    continue
                dists = np.sqrt(((pts[:, None] - pts[None, :]) ** 2).sum(axis=2))
                i, j = np.unravel_index(np.argmax(dists), dists.shape)
                eps.append((pts[i].copy(), pts[j].copy()))
    return eps


def _is_junction(intersection_geom, eps_a, eps_b, tolerance=2.0):
    """True if intersection sits near an endpoint of both routes (junction).

    A junction is end-to-end; a crossing is mid-body overlap.
    """
    if intersection_geom.is_empty:
        return True
    centroid = np.array(intersection_geom.centroid.coords[0])
    near_a = any(np.linalg.norm(centroid - ep) <= tolerance for ep in eps_a)
    near_b = any(np.linalg.norm(centroid - ep) <= tolerance for ep in eps_b)
    return near_a and near_b


# Inline format legend for crossing_pairs (surfaced in the evaluate_design
# response so agents don't need to look up the docstring).
_CROSSING_PAIRS_HELP = "[route_idx_A, route_idx_B, overlap_um2] — 0-based indices, A<B, pad junctions excluded"


def _crossing_penalty(crossing):
    """Steep penalty curve for route crossings.

    0 → 1.0, 1-2 → 0.8, 3-5 → linear decay to 0.2, ≥6 → exp collapse.
    """
    if crossing == 0:
        return 1.0
    if crossing <= 2:
        return 0.8
    if crossing <= 5:
        return 0.6 - (crossing - 3) * 0.2
    return 0.1 * (0.5 ** (crossing - 5))


def _prim_contact_isolation(out_lib, ref_lib, layer_map, args):
    """Route crossing check with junction-aware detection.

    Distinguishes junctions (endpoint-to-endpoint) from real crossings
    (mid-body shorts).  Penalises crossings steeply — shorts are
    dead-or-alive, not a fractional metric.

    Returns a dict {"score": float, "extra": {"crossing_pairs": [[i, j, area_um2], ...]}}
    so the caller can surface per-crossing diagnostics in the check detail.
    """
    route_comp = args.get("component", "contact_route")
    spec = layer_map.get(route_comp)
    if spec is None:
        return {"score": 0.0, "extra": {"crossing_pairs": [], "crossing_pairs_format": _CROSSING_PAIRS_HELP}}
    pairs = _normalize_layer_spec(spec)
    if not pairs:
        return {"score": 0.0, "extra": {"crossing_pairs": [], "crossing_pairs_format": _CROSSING_PAIRS_HELP}}

    all_routes = []
    all_eps = []
    for layer, dt in pairs:
        routes = _extract_shapely(out_lib, layer, dt)
        eps = _route_endpoints_on_layer(out_lib, layer, dt)
        all_routes.extend(routes)
        all_eps.extend(eps)

    n = len(all_routes)
    if n < 2:
        score = 1.0 if all_routes else 0.0
        return {"score": score, "extra": {"crossing_pairs": [], "crossing_pairs_format": _CROSSING_PAIRS_HELP}}

    crossing_pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            ix = all_routes[i].intersection(all_routes[j])
            if ix.area <= 0.01:
                continue
            if i < len(all_eps) and j < len(all_eps):
                if _is_junction(ix, all_eps[i], all_eps[j]):
                    continue
            elif ix.area <= 1.0:
                continue
            crossing_pairs.append([i, j, round(float(ix.area), 6)])

    return {
        "score": _crossing_penalty(len(crossing_pairs)),
        "extra": {
            "crossing_pairs": crossing_pairs,
            "crossing_pairs_format": _CROSSING_PAIRS_HELP,
        },
    }


# Inline format legend for violating_routes (surfaced in the evaluate_design
# response so agents don't need to look up the docstring).
_VIOLATING_ROUTES_HELP = (
    "[{route_id, contact_kind, crossed_material, area_um2}] — route_id is the "
    "0-based index into the contact_route layer; contact_kind is the ohmic "
    "contact class the route terminates at (graphene_only/graphite_only); "
    "crossed_material is the opposing flake the route sweeps; area_um2 is the "
    "swept forbidden area. A contact violates when its routes sweep "
    "> tolerance_area um2 of the opposing flake (an electrical short)."
)


def _meaningful_region_hit(patch, region, min_area=0.1, min_fraction=0.10):
    """True when a contact substantially occupies a region.

    Mirrors composite_evaluator._meaningful_region_hit: centroid containment
    keeps compatibility with the old evaluator, while the area threshold
    catches contacts that legitimately straddle an edge.
    """
    if patch is None or patch.is_empty or region is None or region.is_empty:
        return False
    if region.covers(patch.centroid):
        return True
    threshold = max(min_area, min_fraction * patch.area)
    return patch.intersection(region).area >= threshold


def _prim_route_material_compat(out_lib, ref_lib, layer_map, args):
    """Each ohmic route must stay off the opposing flake material.

    Mirrors composite_evaluator._check_route_material_compat (the official
    scorer's Metric 10 / Bug #8). An ohmic contact patch is classified by the
    single flake it sits on:

      - graphene_only  → forbidden region is FULL graphite (includes overlap,
        since the overlap region also contains graphite).
      - graphite_only  → forbidden region is FULL graphene (same in reverse).

    A route *terminates at* a contact when ``route.buffer(attach_buffer)``
    intersects the patch (default attach_buffer 0.5 µm). A contact passes when
    the total forbidden-region area swept by all of its terminating routes is
    ≤ ``tolerance_area`` µm² (numerical-noise allowance). Gate contacts are
    excluded — only graphene_only / graphite_only ohmic contacts are scored,
    exactly as the official metric (a topgate bonding route legitimately
    traverses bare flake en route to the perimeter pad).

    Scoring uses the same steep penalty curve as the scorer:

      0 violators                → 1.0
      v violators (out of denom) → ``0.5^v × passing/denom``

    where ``denom = max(len(classified ohmic contacts), _expected_arms)`` so
    that extra contacts cannot lift passing/total to 1.0 when violators exist.

    Args (all optional):
      route_component   layer_map key for routes      (default "contact_route")
      contact_component layer_map key for patches      (default "contact_patch")
      graphene          layer_map key for graphene     (default "graphene")
      graphite          layer_map key for graphite     (default "graphite")
      tolerance_area    µm² noise allowance per contact (default 0.5)
      attach_buffer     µm route→patch attach distance  (default 0.5)

    Returns a dict {"score": float, "extra": {"violating_routes": [...]}} so the
    agent gets per-route actionable feedback — mirroring contact_isolation's
    crossing_pairs that drives route-route fixes.
    """
    route_comp = args.get("route_component", "contact_route")
    contact_comp = args.get("contact_component", "contact_patch")
    graphene_key = args.get("graphene", "graphene")
    graphite_key = args.get("graphite", "graphite")
    tolerance_area = float(args.get("tolerance_area", 0.5))
    attach_buffer = float(args.get("attach_buffer", 0.5))

    def _empty(score):
        return {
            "score": score,
            "extra": {
                "violating_routes": [],
                "violating_routes_format": _VIOLATING_ROUTES_HELP,
            },
        }

    # Resolve full-flake regions (ref-preferring, out-lib fallback) — same as
    # the official metric's _ref_material(ref_lib, REF_GRAPHENE/REF_GRAPHITE).
    graphene = _resolve_region(out_lib, ref_lib, layer_map, graphene_key, "union")
    graphite = _resolve_region(out_lib, ref_lib, layer_map, graphite_key, "union")
    if graphene.is_empty and graphite.is_empty:
        # The flake material regions could not be resolved. Returning a 0.0 here
        # is MISLEADING — it looks like "every route shorts" with an empty
        # violating_routes list (internally inconsistent). Raise an actionable
        # error instead: an errored check is excluded from the weighted overall
        # and the agent sees exactly what to add. Almost always the cause is that
        # the agent's evaluate_design layer_map omits the flake material layers
        # (the benchmark result.json schema does not include them, but the check
        # needs them).
        missing = [k for k, present in
                   ((graphene_key, graphene_key in layer_map),
                    (graphite_key, graphite_key in layer_map)) if not present]
        raise ValueError(
            "route_material_compat could not resolve the flake material regions "
            "(graphene='{}', graphite='{}'). {} Add the committed graphene/graphite "
            "flake layers to layer_map (or pass graphene=/graphite= args pointing "
            "at them) so the check can tell which material each route crosses."
            .format(graphene_key, graphite_key,
                    "Missing from layer_map: " + ", ".join(missing) + "." if missing
                    else "Both layers are present in layer_map but have no shapes "
                         "— commit the flakes first."))

    graphene_only = graphene.difference(graphite) if not graphene.is_empty else sg.Polygon()
    graphite_only = graphite.difference(graphene) if not graphite.is_empty else sg.Polygon()

    # Forbidden region per ohmic kind: graphene_only contacts must avoid full
    # graphite, graphite_only contacts must avoid full graphene.
    forbidden_by_kind = {
        "graphene_only": graphite,
        "graphite_only": graphene,
    }

    routes = _component_list(out_lib, layer_map, route_comp)
    if not routes:
        return _empty(0.0)

    patches = _component_list(out_lib, layer_map, contact_comp)
    if not patches:
        return _empty(0.0)

    # Classify each ohmic contact patch (gate contacts are not handled here,
    # so a patch that hits neither single-material region is simply unclassified
    # and excluded — matching the official metric, which drops material_kind
    # values outside forbidden_by_kind).
    classified = []
    for patch in patches:
        in_graphene_only = _meaningful_region_hit(patch, graphene_only)
        in_graphite_only = _meaningful_region_hit(patch, graphite_only)
        if in_graphene_only and not in_graphite_only:
            classified.append((patch, "graphene_only"))
        elif in_graphite_only and not in_graphene_only:
            classified.append((patch, "graphite_only"))

    if not classified:
        return _empty(1.0)

    violating_routes = []
    passing = 0
    for patch, kind in classified:
        forb = forbidden_by_kind[kind]
        if forb.is_empty:
            passing += 1
            continue
        crossed_material = "graphite" if kind == "graphene_only" else "graphene"
        bad_area = 0.0
        contact_violations = []
        for idx, r in enumerate(routes):
            if not r.buffer(attach_buffer).intersects(patch):
                continue
            swept = r.intersection(forb).area
            bad_area += swept
            if swept > 0.0:
                contact_violations.append((idx, swept))
        if bad_area <= tolerance_area:
            passing += 1
        else:
            # This contact is shorted — surface every route that swept the
            # opposing flake so the agent can re-route or re-place it.
            for idx, swept in contact_violations:
                violating_routes.append({
                    "route_id": int(idx),
                    "contact_kind": kind,
                    "crossed_material": crossed_material,
                    "area_um2": round(float(swept), 6),
                })

    try:
        expected = int(layer_map.get("_expected_arms", 8))
    except (TypeError, ValueError):
        expected = 8
    if expected <= 0:
        expected = 8
    denom = max(len(classified), expected)
    violators = len(classified) - passing
    if denom == 0:
        score = 0.0
    elif violators == 0:
        score = 1.0
    else:
        score = (0.5 ** violators) * (passing / float(denom))

    return {
        "score": score,
        "extra": {
            "violating_routes": violating_routes,
            "violating_routes_format": _VIOLATING_ROUTES_HELP,
        },
    }


def _prim_connectivity(out_lib, ref_lib, layer_map, args):
    """Fraction of contacts that reach a bonding pad via routes."""
    contact_comp = args.get("contact_component", "contact_patch")
    pad_comp = args.get("pad_component", "bonding_pad")
    route_comp = args.get("route_component", "contact_route")
    tolerance = args.get("tolerance", 15.0)

    patches = _component_list(out_lib, layer_map, contact_comp)
    pads = _component_union(out_lib, layer_map, pad_comp)
    if not patches or pads.is_empty:
        return 0.0
    pads_buf = pads.buffer(tolerance)
    route_eps = _get_spine_endpoints(out_lib, layer_map, route_comp)
    if not route_eps:
        return 0.0
    connected = 0
    for patch in patches:
        center = np.array(patch.centroid.coords[0])
        if _can_reach_pad(center, route_eps, pads_buf, tolerance):
            connected += 1
    return connected / len(patches)


def _can_reach_pad(start, route_eps, pads_buf, tolerance):
    frontier = [np.asarray(start)]
    visited_routes = set()
    for _ in range(20):
        next_frontier = []
        for pt in frontier:
            if pads_buf.contains(sg.Point(pt)):
                return True
            for idx, (ep_a, ep_b) in enumerate(route_eps):
                if idx in visited_routes:
                    continue
                if np.linalg.norm(pt - ep_a) <= tolerance:
                    visited_routes.add(idx)
                    next_frontier.append(ep_b)
                elif np.linalg.norm(pt - ep_b) <= tolerance:
                    visited_routes.add(idx)
                    next_frontier.append(ep_a)
        if not next_frontier:
            break
        frontier = next_frontier
    return False


def _prim_route_endpoints(out_lib, ref_lib, layer_map, args):
    """Fraction of route endpoints that land on valid targets."""
    route_comp = args.get("route_component", "contact_route")
    target_components = args.get("target_components", ["contact_patch", "mesa", "bonding_pad"])
    tolerance = args.get("tolerance", 15.0)

    route_eps = _get_spine_endpoints(out_lib, layer_map, route_comp)
    if not route_eps:
        return 0.0
    target = sg.Polygon()
    for comp_name in target_components:
        g = _component_union(out_lib, layer_map, comp_name)
        if not g.is_empty:
            target = target.union(g) if not target.is_empty else g
    if target.is_empty:
        return 0.0
    target_buf = target.buffer(tolerance)
    all_eps = []
    for ep_a, ep_b in route_eps:
        all_eps.extend([ep_a, ep_b])
    all_eps_arr = np.array(all_eps) if all_eps else np.empty((0, 2))
    correct = 0
    total = 0
    for route_idx, (ep_a, ep_b) in enumerate(route_eps):
        for ep_offset, ep in enumerate([ep_a, ep_b]):
            total += 1
            pt = sg.Point(ep)
            if target_buf.contains(pt):
                correct += 1
            elif len(all_eps_arr) > 0:
                dists = np.linalg.norm(all_eps_arr - ep, axis=1)
                self_idx = 2 * route_idx + ep_offset
                dists[self_idx] = np.inf
                if np.any(dists <= tolerance):
                    correct += 1
    return correct / total if total > 0 else 0.0


def _prim_adjacency(out_lib, ref_lib, layer_map, args):
    """Fraction of component_a shapes within tolerance of component_b."""
    shapes_a = _component_list(out_lib, layer_map, args["component_a"])
    if not shapes_a:
        return 0.0
    comp_b = _component_union(out_lib, layer_map, args["component_b"])
    if comp_b.is_empty:
        return 0.0
    tolerance = args.get("tolerance", 2.0)
    comp_b_buf = comp_b.buffer(tolerance)
    return sum(1 for s in shapes_a if comp_b_buf.intersects(s)) / len(shapes_a)


def _prim_solidity(out_lib, ref_lib, layer_map, args):
    """Score based on shape solidity relative to threshold.

    Returns a dict so the detail field echoes the RAW geometric solidity
    (area / convex-hull area) AND the normalized pass-score separately.
    Previously this returned a bare float; for direction="below" the score is
    (1 - solidity), and the generic formatter then printed that inverted value
    under a "solidity:" label, misreading a passing concave mesa as a fail.
    """
    comp = _component_union(out_lib, layer_map, args["component"])
    threshold = args.get("threshold", 0.5)
    direction = args.get("direction", "below")
    if comp.is_empty:
        return {"score": 0.0,
                "detail": "solidity: component empty (no shapes)",
                "extra": {"raw_solidity": None, "threshold": threshold,
                          "direction": direction}}
    hull = comp.convex_hull
    if hull.is_empty or hull.area == 0:
        return {"score": 0.0,
                "detail": "solidity: degenerate convex hull",
                "extra": {"raw_solidity": None, "threshold": threshold,
                          "direction": direction}}
    solidity = comp.area / hull.area
    if direction == "below":
        passed = solidity < threshold
        score = max(0.0, min(1.0, 1.0 - solidity)) if passed else 0.0
    else:
        passed = solidity >= threshold
        score = max(0.0, min(1.0, solidity)) if passed else 0.0
    detail = ("solidity raw={:.4f} threshold={:.2f} direction={} "
              "-> {} score={:.4f}").format(
                  solidity, threshold, direction,
                  "pass" if passed else "fail", score)
    return {"score": score, "detail": detail,
            "extra": {"raw_solidity": round(solidity, 6),
                      "threshold": threshold, "direction": direction,
                      "passed": bool(passed)}}


def _prim_spacing(out_lib, ref_lib, layer_map, args):
    """Fraction of component pairs meeting minimum distance."""
    shapes_a = _component_list(out_lib, layer_map, args["component_a"])
    comp_b_name = args.get("component_b", args["component_a"])
    if comp_b_name == args["component_a"]:
        shapes_b = shapes_a
    else:
        shapes_b = _component_list(out_lib, layer_map, comp_b_name)
    if not shapes_a or not shapes_b:
        return 0.0
    min_distance = args.get("min_distance", 1.0)
    same = (comp_b_name == args["component_a"])
    total = 0
    ok = 0
    for i, sa in enumerate(shapes_a):
        start_j = i + 1 if same else 0
        for j in range(start_j, len(shapes_b)):
            if same and i == j:
                continue
            total += 1
            if sa.distance(shapes_b[j]) >= min_distance:
                ok += 1
    return ok / total if total > 0 else 1.0


def _prim_bulk_containment(out_lib, ref_lib, layer_map, args):
    """Fraction of component area contained within a caller-declared bulk region.

    Distinct from component_containment: this check is meant to score the
    body of a device against a bulk region, without penalising peripherals
    that intentionally extend into single-material zones.  Two ways to
    specify the bulk:

    - Pass ``bulk_region`` as a region expression (layer_map key or list of
      keys), same semantics as ``component_containment``'s region arg. Pair
      with ``region_op`` (union / intersection / difference).
    - Pass ``materials`` as a list of layer_map keys; the primitive will
      intersect those layers to form the bulk region. Useful when the bulk
      is the overlap of two materials. The caller names the keys; no
      defaults are assumed.

    Optional ``core_bbox=[x1, y1, x2, y2]`` (um) clips the component to a
    rectangular core before containment scoring.

    Raises ValueError if neither bulk_region nor materials is provided.
    """
    comp = _component_union(out_lib, layer_map, args["component"])
    if comp.is_empty:
        return 0.0

    core_bbox = args.get("core_bbox")
    if core_bbox is not None:
        # Validate shape up front: a bare bool/number/short list used to reach
        # len()/unpack and raise a cryptic 'object of type bool has no len()'.
        if (isinstance(core_bbox, bool)
                or not isinstance(core_bbox, (list, tuple))
                or len(core_bbox) != 4
                or any(isinstance(v, bool) or not isinstance(v, (int, float))
                       for v in core_bbox)):
            raise ValueError(
                "bulk_containment: core_bbox must be [x1, y1, x2, y2] in um "
                "(four numbers); got {!r}".format(core_bbox))
        x1, y1, x2, y2 = core_bbox
        core = sg.box(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        comp = comp.intersection(core)
        if comp.is_empty:
            return 0.0

    if "bulk_region" in args:
        region = _resolve_region(out_lib, ref_lib, layer_map,
                                 args["bulk_region"],
                                 args.get("region_op", "union"))
    elif "materials" in args:
        mats = args["materials"]
        if not isinstance(mats, list) or not mats:
            raise ValueError(
                "bulk_containment: 'materials' must be a non-empty list of "
                "layer_map keys (e.g. ['channel_a', 'channel_b']).")
        region = _resolve_region(out_lib, ref_lib, layer_map, mats, "intersection")
    else:
        raise ValueError(
            "bulk_containment requires either 'bulk_region' (single key or "
            "list) OR 'materials' (list of layer_map keys whose intersection "
            "defines the bulk). No default material names are assumed.")

    if region.is_empty:
        return 0.0
    return comp.intersection(region).area / comp.area


def _prim_material_overlap_report(out_lib, ref_lib, layer_map, args):
    """Compute pairwise and multi-way intersections of a set of material
    layers. Returns a dict with score=1.0 (always) plus a ``report`` side-
    data field that main() promotes onto the check.

    Args:
        materials: list of layer_map keys (required, min 2).

    Report schema, per region key:
        {
          "<key>": {
              "area_um2": float,
              "bbox_um": [x1, y1, x2, y2],
              "centroid_um": [cx, cy],
              "num_polygons": int
          }
        }

    Region key naming:
      - "<name>_only"         — that material minus the union of the others
      - "<a>_and_<b>"         — pairwise intersection
      - "<a>_and_<b>_and_<c>" — higher-order intersections
    """
    mats = args.get("materials")
    if not isinstance(mats, list) or len(mats) < 2:
        raise ValueError(
            "material_overlap_report: 'materials' must be a list of at "
            "least two layer_map keys (e.g. ['A', 'B']).")

    # Resolve each material to its shapely region.
    regions = {}
    for key in mats:
        regions[key] = _resolve_region(out_lib, ref_lib, layer_map, key, "union")

    report = {}

    def _summarize(region):
        if region.is_empty:
            return {"area_um2": 0.0, "bbox_um": [0.0, 0.0, 0.0, 0.0],
                    "centroid_um": [0.0, 0.0], "num_polygons": 0}
        minx, miny, maxx, maxy = region.bounds
        c = region.centroid
        try:
            n = len(region.geoms) if hasattr(region, "geoms") else 1
        except Exception:
            n = 1
        return {
            "area_um2": round(float(region.area), 4),
            "bbox_um": [round(float(minx), 4), round(float(miny), 4),
                        round(float(maxx), 4), round(float(maxy), 4)],
            "centroid_um": [round(float(c.x), 4), round(float(c.y), 4)],
            "num_polygons": int(n),
        }

    # "<name>_only" regions: each material minus the union of the others.
    for key in mats:
        others = [regions[k] for k in mats if k != key]
        if others:
            others_union = others[0]
            for r in others[1:]:
                others_union = others_union.union(r)
            only_r = regions[key].difference(others_union)
        else:
            only_r = regions[key]
        report["{}_only".format(key)] = _summarize(only_r)

    # Pairwise + higher-order intersections.
    from itertools import combinations
    for r_size in range(2, len(mats) + 1):
        for combo in combinations(mats, r_size):
            label = "_and_".join(combo)
            inter = regions[combo[0]]
            for k in combo[1:]:
                inter = inter.intersection(regions[k])
            report[label] = _summarize(inter)

    return {
        "score": 1.0,
        "report": report,
        "detail": "material_overlap_report: {} materials, {} regions".format(
            len(mats), len(report)),
    }


def _prim_arm_material_class(out_lib, ref_lib, layer_map, args):
    """Score arms/contacts by how cleanly they land in *exactly one* material class.

    Each shape on the component layer (typically contact_patch or mesa arms)
    is checked against a list of mutually-exclusive class regions (e.g.
    graphene_only, graphite_only, overlap).  A shape "belongs to" a class
    if >= ``containment_threshold`` of its area is inside that class region.
    A shape scores 1.0 if it belongs to exactly one class, 0.0 if it
    belongs to none or straddles multiple.

    Args:
        component: component name (required) — shapes are evaluated individually
        classes: list of classes (required). Each entry may be:
            - a layer_map key:        "graphene"
            - a list of keys (union): ["graphene", "graphite"]
            - a dict:                 {"name": str,
                                       "region": layer_map key or list of keys,
                                       "region_op": "union" (default) /
                                                    "intersection" / "difference"}
        containment_threshold: float, default 0.9
    """
    shapes = _component_list(out_lib, layer_map, args["component"])
    if not shapes:
        return 0.0

    classes_spec = args.get("classes")
    if not isinstance(classes_spec, list) or not classes_spec:
        return 0.0

    threshold = float(args.get("containment_threshold", 0.9))

    resolved_classes = []
    for idx, cls in enumerate(classes_spec):
        # Accept three equivalent ways to express a class so the tool is robust
        # to however the agent phrases it (Postel's law). All resolve to a
        # region + region_op fed to _resolve_region:
        #   "graphene"                        single layer_map key (union)
        #   ["graphene", "graphite"]          list of keys (union)
        #   {"name","region","region_op"}     explicit dict (documented form)
        if isinstance(cls, dict):
            region_spec = cls.get("region")
            region_op = cls.get("region_op", "union")
        elif isinstance(cls, (list, tuple)):
            region_spec = list(cls)
            region_op = "union"
        elif isinstance(cls, str):
            region_spec = cls
            region_op = "union"
        else:
            region_spec = None
            region_op = "union"
        if not region_spec:
            raise ValueError(
                "arm_material_class: classes[{}] = {!r} is not a valid class. "
                "Use a layer_map key (\"graphene\"), a list of keys "
                "([\"graphene\", \"graphite\"]), or a dict "
                "({{\"name\": .., \"region\": \"graphene\", \"region_op\": \"union\"}}).".format(idx, cls)
            )
        region = _resolve_region(out_lib, ref_lib, layer_map,
                                 region_spec, region_op)
        resolved_classes.append(region)

    ok = 0
    for s in shapes:
        if s.area <= 0:
            continue
        memberships = 0
        for region in resolved_classes:
            if region.is_empty:
                continue
            frac = s.intersection(region).area / s.area
            if frac >= threshold:
                memberships += 1
        if memberships == 1:
            ok += 1
    return ok / len(shapes)


# ---------------------------------------------------------------------------
# Primitive registry
# ---------------------------------------------------------------------------

PRIMITIVES = {
    "component_overlap": _prim_component_overlap,
    "component_containment": _prim_component_containment,
    "bulk_containment": _prim_bulk_containment,
    "arm_material_class": _prim_arm_material_class,
    "material_overlap_report": _prim_material_overlap_report,
    "contact_isolation": _prim_contact_isolation,
    "route_material_compat": _prim_route_material_compat,
    "connectivity": _prim_connectivity,
    "route_endpoints": _prim_route_endpoints,
    "adjacency": _prim_adjacency,
    "solidity": _prim_solidity,
    "spacing": _prim_spacing,
}


# ---------------------------------------------------------------------------
# Error output helper
# ---------------------------------------------------------------------------

def _write_error(output_path, message):
    result = {"status": "error", "error": message}
    with open(output_path, "w") as f:
        json.dump(result, f)
    sys.exit(0)


# ---------------------------------------------------------------------------
# next_step_suggestion builder
# ---------------------------------------------------------------------------

def _build_next_step(overall, results, empty_components):
    """Emit a concise, static suggestion pointing the caller at relevant
    inspection tools. Device-agnostic — do not hard-code material names
    or device topology vocabulary."""
    if not results:
        return ("No checks ran. Re-read the task instruction and the saved "
                "checklist, then call evaluate_design with the target "
                "layer_map and checks list.")

    parts = []
    errored = [c for c in results if c.get("status") == "error"]
    low = [c for c in results
           if c.get("status") != "error" and c.get("score", 0.0) < 0.8]

    if errored:
        parts.append(
            "{} check(s) ERRORED and were EXCLUDED from the overall (NOT scored "
            "0): {}. These are tool/usage errors, not design failures — read "
            "each check's 'error' field, fix the call arguments, and re-run.".format(
                len(errored), ", ".join(sorted(c["name"] for c in errored))))

    if overall >= 0.9 and not low:
        parts.append("Overall score passes. Re-read the task instruction "
                     "and checklist to confirm every benchmark requirement "
                     "is met (schema fields, deliverable file names, etc.) "
                     "before save.")
    else:
        parts.append("Re-read the task instruction and checklist. Verify "
                     "the design addresses every requirement before the "
                     "next iteration.")

    names = {c["name"] for c in low}
    if "contact_isolation" in names:
        parts.append(
            "For contact_isolation < 0.8: call route_inspect with the same "
            "route_layer / contact_layers / pad_layer you used when routing, "
            "then screenshot(zoom_box=...) over each crossing to inspect.")
    if "route_material_compat" in names:
        parts.append(
            "For route_material_compat < 0.8: read the violating_routes list "
            "(each names route_id, contact_kind, crossed_material, area_um2 — "
            "a route shorting an ohmic contact onto the opposing flake). "
            "screenshot(zoom_box=...) over each violating route_id and re-route "
            "or re-place that contact so it no longer crosses the wrong flake.")
    if "component_containment" in names or "bulk_containment" in names:
        parts.append(
            "For containment < 0.8: call screenshot(zoom_box=...) on the "
            "component bbox to visually confirm placement. If the component "
            "has a core area plus peripherals that intentionally sit outside "
            "the target region, switch from component_containment to "
            "bulk_containment and pass a bulk_region (or materials list) "
            "matching only the core.")
    if "arm_material_class" in names:
        parts.append(
            "For arm_material_class < 0.8: shapes land in zero classes or "
            "straddle multiple. Screenshot each ambiguous shape and confirm "
            "the class regions you passed cover the intended placement.")
    if "connectivity" in names or "route_endpoints" in names:
        parts.append(
            "For connectivity/route_endpoints < 0.8: call route_inspect to "
            "see which endpoints are unmapped, then screenshot(zoom_box=...) "
            "over the unreached contacts.")
    if "adjacency" in names or "spacing" in names:
        parts.append(
            "For adjacency/spacing < 0.8: call screenshot(zoom_box=...) "
            "near the flagged components to inspect the geometry.")

    if empty_components:
        parts.append(
            "Warnings list empty components — either add geometry to those "
            "layers or drop the corresponding checks before re-running.")

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("Usage: python evaluate_worker.py config.json", file=sys.stderr)
        sys.exit(1)

    config_path = sys.argv[1]
    with open(config_path, "r") as f:
        config = json.load(f)

    gds_path = config.get("gds_path", "")
    reference_gds = config.get("reference_gds", None)
    layer_map = config.get("layer_map", {})
    checks = config.get("checks", None)
    output_path = config.get("output_path", "output.json")

    if checks is None:
        _write_error(output_path, "Config must include 'checks' list.")

    if not isinstance(checks, list) or len(checks) == 0:
        _write_error(output_path, "'checks' must be a non-empty list.")

    if not os.path.isfile(gds_path):
        _write_error(output_path, "GDS file not found: {}".format(gds_path))

    if reference_gds and not os.path.isfile(reference_gds):
        _write_error(output_path, "Reference GDS file not found: {}".format(reference_gds))

    # Validate check names
    for i, check in enumerate(checks):
        name = check.get("name", "")
        if name not in PRIMITIVES:
            _write_error(output_path,
                "Check {}: unknown primitive '{}'. Available: {}".format(
                    i, name, ", ".join(sorted(PRIMITIVES.keys()))))

    # Load GDS files
    out_lib = gdstk.read_gds(gds_path)
    ref_lib = gdstk.read_gds(reference_gds) if reference_gds else None

    # Pre-check: report which layer_map components have zero geometry
    empty_components = []
    for comp_name, spec in layer_map.items():
        if comp_name.startswith("_"):
            continue
        pairs = _normalize_layer_spec(spec)
        if not pairs:
            empty_components.append("{} (invalid layer spec: {})".format(comp_name, spec))
            continue
        has_shapes = False
        for layer, dt in pairs:
            if _extract_shapely(out_lib, layer, dt):
                has_shapes = True
                break
        if not has_shapes:
            empty_components.append("{} (layer {} — no shapes found)".format(comp_name, pairs))

    # Run checks
    results = []
    for check in checks:
        name = check["name"]
        args = check.get("args", {})
        weight = check.get("weight", 1.0 / len(checks))
        extra = None
        raw = None
        check_error = None
        try:
            raw = PRIMITIVES[name](out_lib, ref_lib, layer_map, args)
            # Primitives may return a plain float (score) or a dict
            # {"score": float, "extra": {...}} for diagnostics such as
            # contact_isolation's crossing_pairs.
            if isinstance(raw, dict):
                score = raw.get("score", 0.0)
                extra = raw.get("extra")
            else:
                score = raw
            score = max(0.0, min(1.0, float(score)))
            if isinstance(raw, dict) and "detail" in raw:
                detail = raw["detail"]
            else:
                detail = "{}: {:.4f}".format(name, score)
        except Exception as e:
            score = 0.0
            detail = "{}: ERROR — {}".format(name, e)
            check_error = str(e)
        entry = {
            "name": name,
            "score": score,
            "weight": weight,
            "detail": detail,
        }
        if check_error is not None:
            # A check that RAISED could not be measured — a tool/usage error,
            # NOT a design that genuinely scored 0.0. Flag it and exclude it
            # from the weighted overall below; folding it in as a full-weight
            # zero injected a false zero into the agent's self-evaluation.
            entry["status"] = "error"
            entry["error"] = check_error
        if extra is not None:
            # Promote extra diagnostic fields to the top level of the
            # check result so agents can read them directly (e.g.
            # result.checks[i].crossing_pairs for contact_isolation).
            if isinstance(extra, dict):
                for k, v in extra.items():
                    entry[k] = v
            else:
                entry["extra"] = extra
        # Promote top-level side-data fields from dict-returning primitives.
        if isinstance(raw, dict) and "report" in raw:
            entry["report"] = raw["report"]
        results.append(entry)

    # Compute overall as a weighted average over checks that actually produced
    # a measurement. Checks that ERRORED (status == "error") are excluded from
    # BOTH numerator and denominator so a tool/usage error does not masquerade
    # as a design failure scoring 0.0.
    scored = [c for c in results if c.get("status") != "error"]
    n_errored = len(results) - len(scored)
    total_weight = sum(c["weight"] for c in scored)
    if total_weight > 0:
        overall = round(sum(c["score"] * c["weight"] for c in scored) / total_weight, 6)
    else:
        overall = 0.0

    output = {
        "status": "ok",
        "overall": overall,
        "n_errored_checks": n_errored,
        "checks": results,
        "next_step_suggestion": _build_next_step(overall, results, empty_components),
    }
    if empty_components:
        output["warnings"] = [
            "Components with no geometry in the GDS: " + "; ".join(empty_components),
            "Checks referencing empty components will score 0.0.",
        ]

    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)


if __name__ == "__main__":
    main()
