#!/usr/bin/env python
"""Multi-window EBL routing: route contacts to pads via boundary patches.

Routes in two passes:
  1. Inner (fine): contact pins → boundary pins (narrow lines)
  2. Outer (coarse): boundary pins → pad pins (wider lines)
  3. Connection patches placed at boundary intersection points

Usage:
    python route_multiwindow.py \
        --pin-contacts 100/0 --pin-pads 101/0 \
        --inner-window 800 --outer-window 2000 \
        --inner-width 0.5 --outer-width 1.0 \
        --inner-layer 3/0 --outer-layer 4/0 \
        --patch-layer 5/0 --patch-size 1.0 \
        --obstacle-layers 1/0
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "scripts"))
from mcp_client import init_session, execute_script, tool_call


def parse_layer(s):
    parts = s.split("/")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0


def layer_str(l, d):
    return f"{l}/{d}"


def main():
    parser = argparse.ArgumentParser(description="Multi-window EBL routing")
    parser.add_argument("--pin-contacts", type=str, default="100/0",
                        help="Layer with contact pin markers (default: 100/0)")
    parser.add_argument("--pin-pads", type=str, default="101/0",
                        help="Layer with pad pin markers (default: 101/0)")
    parser.add_argument("--inner-window", type=float, default=800.0,
                        help="Inner EBL window size in um (default: 800)")
    parser.add_argument("--outer-window", type=float, default=2000.0,
                        help="Outer EBL window size in um (default: 2000)")
    parser.add_argument("--inner-width", type=float, default=0.5,
                        help="Inner route line width in um (default: 0.5)")
    parser.add_argument("--outer-width", type=float, default=1.0,
                        help="Outer route line width in um (default: 1.0)")
    parser.add_argument("--inner-layer", type=str, default="3/0",
                        help="Output layer for inner routes (default: 3/0)")
    parser.add_argument("--outer-layer", type=str, default="4/0",
                        help="Output layer for outer routes (default: 4/0)")
    parser.add_argument("--patch-layer", type=str, default="5/0",
                        help="Output layer for boundary patches (default: 5/0)")
    parser.add_argument("--patch-size", type=float, default=1.0,
                        help="Boundary patch size in um (default: 1.0)")
    parser.add_argument("--obstacle-layers", type=str, default="1/0",
                        help="Comma-separated obstacle layers (default: 1/0)")
    parser.add_argument("--inner-safe-dist", type=float, default=1.0,
                        help="Path safe distance for inner routing (default: 1.0)")
    parser.add_argument("--outer-safe-dist", type=float, default=2.0,
                        help="Path safe distance for outer routing (default: 2.0)")
    args = parser.parse_args()

    contact_l, contact_d = parse_layer(args.pin_contacts)
    pad_l, pad_d = parse_layer(args.pin_pads)
    inner_l, inner_d = parse_layer(args.inner_layer)
    outer_l, outer_d = parse_layer(args.outer_layer)
    patch_l, patch_d = parse_layer(args.patch_layer)
    obstacle_strs = [s.strip() for s in args.obstacle_layers.split(",")]

    boundary_half = args.inner_window / 2.0
    ps = args.patch_size

    # Temp pin layers for boundary points
    bnd_inner_layer = "102/0"
    bnd_outer_layer = "103/0"

    init_session()

    # Step 1: Read pad positions and compute boundary intersection points
    print("Step 1: Computing boundary intersection points...")
    result = execute_script(f"""
import math, json

dbu = _layout.dbu
cell = _top_cell
boundary_half = {boundary_half}
ps = {ps}

def um(v):
    return int(round(v / dbu))

def add_box(layer, x1, y1, x2, y2):
    cell.shapes(layer).insert(pya.Box(um(x1), um(y1), um(x2), um(y2)))

# Read pad pin positions
l_pad = _layout.layer({pad_l}, {pad_d})
pad_positions = []
for shape in cell.shapes(l_pad).each():
    b = shape.bbox()
    cx = (b.left + b.right) / 2.0 * dbu
    cy = (b.bottom + b.top) / 2.0 * dbu
    pad_positions.append((cx, cy))

# Compute boundary points: where ray from (0,0) to each pad hits boundary square
l_bnd_inner = _layout.layer(102, 0)
l_bnd_outer = _layout.layer(103, 0)
l_patch = _layout.layer({patch_l}, {patch_d})

# Clear temp layers
cell.shapes(l_bnd_inner).clear()
cell.shapes(l_bnd_outer).clear()

boundary_positions = []
for (px, py) in pad_positions:
    if abs(px) < 1e-6 and abs(py) < 1e-6:
        boundary_positions.append((0, 0))
        continue
    sx = boundary_half / abs(px) if abs(px) > 1e-6 else 1e6
    sy = boundary_half / abs(py) if abs(py) > 1e-6 else 1e6
    s = min(sx, sy)
    bx, by = px * s, py * s
    boundary_positions.append((bx, by))

pin_s = 3.0
for (x, y) in boundary_positions:
    add_box(l_bnd_inner, x - pin_s/2, y - pin_s/2, x + pin_s/2, y + pin_s/2)
    add_box(l_bnd_outer, x - pin_s/2, y - pin_s/2, x + pin_s/2, y + pin_s/2)
    add_box(l_patch, x - ps/2, y - ps/2, x + ps/2, y + ps/2)

# Read contact pin count
l_contact = _layout.layer({contact_l}, {contact_d})
n_contacts = 0
for shape in cell.shapes(l_contact).each():
    n_contacts += 1

_refresh_view()
result = {{
    "status": "ok",
    "n_contacts": n_contacts,
    "n_pads": len(pad_positions),
    "n_boundary": len(boundary_positions),
}}
""")

    n_contacts = result["n_contacts"]
    n_pads = result["n_pads"]
    n_boundary = result["n_boundary"]
    print(f"  Contacts: {n_contacts}, Pads: {n_pads}, Boundary points: {n_boundary}")

    if n_contacts != n_pads:
        print(f"WARNING: contact count ({n_contacts}) != pad count ({n_pads})")

    # Step 2: Inner routing (contacts → boundary)
    print(f"Step 2: Inner routing ({args.inner_width} um lines)...")
    inner_result = tool_call(
        "auto_route",
        pin_layer_a=layer_str(contact_l, contact_d),
        pin_layer_b=bnd_inner_layer,
        output_layer=layer_str(inner_l, inner_d),
        obstacle_layers=obstacle_strs,
        path_width=args.inner_width,
        path_safe_distance=args.inner_safe_dist,
        obs_safe_distance=args.inner_safe_dist,
        map_resolution=1,
    )
    inner_routed = inner_result.get("routed_pairs", 0)
    print(f"  Routed: {inner_routed}/{n_contacts}")

    # Step 3: Outer routing (boundary → pads)
    print(f"Step 3: Outer routing ({args.outer_width} um lines)...")
    outer_obstacles = obstacle_strs + [layer_str(inner_l, inner_d)]
    outer_result = tool_call(
        "auto_route",
        pin_layer_a=bnd_outer_layer,
        pin_layer_b=layer_str(pad_l, pad_d),
        output_layer=layer_str(outer_l, outer_d),
        obstacle_layers=outer_obstacles,
        path_width=args.outer_width,
        path_safe_distance=args.outer_safe_dist,
        obs_safe_distance=args.outer_safe_dist,
        map_resolution=2,
    )
    outer_routed = outer_result.get("routed_pairs", 0)
    print(f"  Routed: {outer_routed}/{n_pads}")

    # Step 4: Clean up temp pin layers
    print("Step 4: Cleaning up temp layers...")
    execute_script("""
cell = _top_cell
cell.shapes(_layout.layer(102, 0)).clear()
cell.shapes(_layout.layer(103, 0)).clear()
_refresh_view()
result = {"status": "ok"}
""")

    print(f"\nDone!")
    print(f"  Inner routes: {inner_routed} on L{inner_l}/{inner_d} ({args.inner_width} um)")
    print(f"  Outer routes: {outer_routed} on L{outer_l}/{outer_d} ({args.outer_width} um)")
    print(f"  Patches: {n_boundary} on L{patch_l}/{patch_d} ({ps}x{ps} um)")


if __name__ == "__main__":
    main()
