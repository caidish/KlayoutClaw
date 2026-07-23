#!/usr/bin/env python
"""Rank every (graphene_i, graphite_j) candidate pair by downstream overlap
plausibility, so agents don't iterate through rank-0 candidates that happen
to have no overlap.

Context: flakedetect_detect produces candidate lists per material in raw
detection order (not ranked). On ml09/ml11 benchmark runs, the auto-picked
default pair had zero graphene鈭ゞraphite overlap, forcing agents to
brute-force compare candidates before a usable Hall-bar geometry could even
be designed. This script emits a precomputed ranking so the agent's first
attempt is its best attempt.

Usage:
    conda run -n instrMCPdev python rank_candidate_pairs.py \\
        --traces path/to/traces.json \\
        [--output candidate_ranking.json] \\
        [--top-k 5]

Outputs:
    candidate_ranking.json 鈥?ordered list of pairs with overlap area,
        individual areas, and centroid distance.

Dependencies: shapely (already present in instrMCPdev env).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

try:
    import shapely.geometry as sg
    from shapely.ops import unary_union
    from shapely.validation import make_valid
except ImportError as exc:
    print(f"ERROR: shapely is required (install in your conda env): {exc}",
          file=sys.stderr)
    sys.exit(1)


def _to_polygon(contour: list[list[float]]) -> sg.Polygon | sg.MultiPolygon | None:
    """Convert an Nx2 contour to a shapely polygon, repairing self-intersections."""
    if contour is None or len(contour) < 3:
        return None
    try:
        poly = sg.Polygon(contour)
        if not poly.is_valid:
            poly = make_valid(poly)
        if poly.is_empty:
            return None
        return poly
    except (ValueError, Exception):
        return None


def _material_polys(traces: dict, material: str) -> list[dict]:
    """Return [{id, polygon, area, centroid}, ...] for a material's entries.

    Uses contour_gds if present (post-gdsalign), else contour_um.
    """
    mats = traces.get("materials", {})
    entries = mats.get(material, []) or []
    out = []
    for entry in entries:
        contour = entry.get("contour_gds") or entry.get("contour_um")
        poly = _to_polygon(contour)
        if poly is None:
            continue
        area = float(poly.area)
        centroid = poly.centroid
        out.append({
            "id": entry.get("id"),
            "polygon": poly,
            "area_um2": round(area, 3),
            "centroid": (float(centroid.x), float(centroid.y)),
            "area_from_trace": entry.get("area_um2"),
        })
    return out


def _rank_pairs(traces: dict, mat_a: str = "graphene", mat_b: str = "graphite") -> list[dict]:
    polys_a = _material_polys(traces, mat_a)
    polys_b = _material_polys(traces, mat_b)
    pairs = []
    for a in polys_a:
        for b in polys_b:
            try:
                inter = a["polygon"].intersection(b["polygon"])
                overlap = float(inter.area)
            except Exception:
                overlap = 0.0
            dx = a["centroid"][0] - b["centroid"][0]
            dy = a["centroid"][1] - b["centroid"][1]
            cent_dist = (dx * dx + dy * dy) ** 0.5
            pairs.append({
                f"{mat_a}_id": a["id"],
                f"{mat_b}_id": b["id"],
                "overlap_um2": round(overlap, 3),
                f"{mat_a}_area_um2": a["area_um2"],
                f"{mat_b}_area_um2": b["area_um2"],
                "centroid_distance_um": round(cent_dist, 3),
            })
    # Sort by overlap DESC, then by centroid distance ASC
    pairs.sort(key=lambda p: (-p["overlap_um2"], p["centroid_distance_um"]))
    for rank, pair in enumerate(pairs):
        pair["rank"] = rank
    return pairs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rank (graphene, graphite) candidate pairs by overlap plausibility.")
    parser.add_argument("--traces", required=True,
                        help="Path to traces.json from flakedetect_combine (either pre- or post-gdsalign).")
    parser.add_argument("--output", default=None,
                        help="Path for candidate_ranking.json (default: alongside traces.json).")
    parser.add_argument("--top-k", type=int, default=5,
                        help="How many top pairs to emit on stdout (default 5). The output JSON always contains all pairs.")
    parser.add_argument("--material-a", default="graphene",
                        help="First material key in traces.materials (default graphene).")
    parser.add_argument("--material-b", default="graphite",
                        help="Second material key in traces.materials (default graphite).")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if not os.path.isfile(args.traces):
        print(f"ERROR: traces file not found: {args.traces}", file=sys.stderr)
        return 1

    with open(args.traces) as f:
        traces = json.load(f)

    pairs = _rank_pairs(traces, args.material_a, args.material_b)

    if not pairs:
        print(f"WARNING: no candidate pairs found for ({args.material_a}, {args.material_b})",
              file=sys.stderr)

    output_path = args.output or os.path.join(
        os.path.dirname(os.path.abspath(args.traces)), "candidate_ranking.json")

    out_doc: dict[str, Any] = {
        "material_a": args.material_a,
        "material_b": args.material_b,
        "num_pairs": len(pairs),
        "pairs": pairs,
    }
    with open(output_path, "w") as f:
        json.dump(out_doc, f, indent=2)
    print(f"Saved {output_path} ({len(pairs)} pairs)")

    # Stdout: top-K for shell pipelines (jq, awk, etc.)
    top_k = max(0, int(args.top_k))
    if top_k > 0 and pairs:
        print()
        print(f"Top {min(top_k, len(pairs))} pairs:")
        for p in pairs[:top_k]:
            print(
                f"  rank={p['rank']} {args.material_a}_id={p[f'{args.material_a}_id']} "
                f"{args.material_b}_id={p[f'{args.material_b}_id']} "
                f"overlap={p['overlap_um2']} um^2 "
                f"centroid_distance={p['centroid_distance_um']} um")
    return 0


if __name__ == "__main__":
    sys.exit(main())
