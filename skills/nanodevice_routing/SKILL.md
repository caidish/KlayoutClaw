---
name: nanodevice_routing
description: Route nanodevice contacts to bonding pads with multi-window EBL support. Use this skill when the user needs to place bonding pads, route leads from device contacts to pads, set up multi-window EBL routing with different line widths per window, or add boundary connection patches between EBL write fields. Also trigger for "fan out", "route to pads", "bonding pads", "EBL windows", "multi-pass lithography", "connection patches".
---

# Nanodevice Routing

Route nanodevice contacts to bonding pads, with multi-window EBL write field support (different line widths per window, boundary connection patches).

## Prerequisites

- KLayout running with KlayoutClaw plugin (v0.5+)
- A layout open with device geometry (mesa on L1/D0)
- Device contact tip positions known (or pins already placed)
- Python packages: `numpy`, `scipy`, `scikit-image` (in conda env `instrMCPdev`)

## Scripts

### place_pads.py 鈥?Place bonding pads around field perimeter

```bash
python scripts/place_pads.py --field 2000 --pad-size 80 --pads-per-edge 12 [--layer 2/0] [--margin 60]
```

- `--field` 鈥?EBL write field size in um (default: 2000)
- `--pad-size` 鈥?Bonding pad side length in um (default: 80)
- `--pads-per-edge` 鈥?Number of pads per edge (default: 12)
- `--layer` 鈥?Output layer as `layer/datatype` (default: 2/0)
- `--margin` 鈥?Pad center inset from field edge in um (default: 60)

Example 鈥?48 pads (12 per edge) around a 2mm field:
```bash
python scripts/place_pads.py --field 2000 --pad-size 80 --pads-per-edge 12
```

### route_multiwindow.py 鈥?Multi-window EBL routing

Routes device contacts to bonding pads in two passes with different line widths, placing connection patches at the window boundary.

```bash
python scripts/route_multiwindow.py \
    --pin-contacts 100/0 \
    --pin-pads 101/0 \
    --inner-window 800 \
    --outer-window 2000 \
    --inner-width 0.5 \
    --outer-width 1.0 \
    --inner-layer 3/0 \
    --outer-layer 4/0 \
    --patch-layer 5/0 \
    --patch-size 1.0 \
    --obstacle-layers 1/0
```

- `--pin-contacts` 鈥?Layer with pin markers at device contacts (default: 100/0)
- `--pin-pads` 鈥?Layer with pin markers at bonding pads (default: 101/0)
- `--inner-window` 鈥?Inner EBL window size in um (default: 800)
- `--outer-window` 鈥?Outer EBL window size in um (default: 2000)
- `--inner-width` 鈥?Route line width for inner window in um (default: 0.5)
- `--outer-width` 鈥?Route line width for outer window in um (default: 1.0)
- `--inner-layer` 鈥?Output layer for inner routes (default: 3/0)
- `--outer-layer` 鈥?Output layer for outer routes (default: 4/0)
- `--patch-layer` 鈥?Output layer for boundary patches (default: 5/0)
- `--patch-size` 鈥?Boundary patch size in um (default: 1.0)
- `--obstacle-layers` 鈥?Comma-separated obstacle layers (default: 1/0)

The script:
1. Reads contact and pad pin positions from their layers
2. Computes boundary intersection points at the inner window edge
3. Places boundary pins on temporary layers
4. Runs inner auto_route (contacts 鈫?boundary, fine lines)
5. Places connection patches at boundary points
6. Runs outer auto_route (boundary 鈫?pads, coarse lines)
7. Cleans up temporary pin layers

### clear_routes.py 鈥?Remove routes from specified layers

```bash
python scripts/clear_routes.py 3/0 4/0 5/0
```

Clears all shapes from the listed layers. Useful for re-routing without losing device geometry.

## Workflow

1. Create device geometry (mesa, contacts) using geometry skill or execute_script
2. Place pin markers at device contact tips on layer 100/0
3. Place bonding pads with `place_pads.py` (also places pin markers on 101/0)
4. Run `route_multiwindow.py` to connect everything
5. Check result 鈥?if overlapping, adjust parameters and re-run after `clear_routes.py`

## Output Layers Convention

> **Note**: These layers are **task-specific examples**. Always use the layer assignments from the task instruction, which may differ (benchmarks frequently use 20/0 for mesa, 22/0 for topgate, etc.).

| Layer | Purpose | EBL Pass |
|-------|---------|----------|
| 1/0 | Mesa (graphene etch) | Pass 1 |
| 2/0 | Bonding pads | Pass 3 (coarse) |
| 3/0 | Fine routes (<inner window) | Pass 2 (fine) |
| 4/0 | Coarse routes (inner鈫抩uter) | Pass 3 (coarse) |
| 5/0 | Boundary patches | Pass 2 or 3 |
| 100/0 | Pin markers: contacts | (removed after routing) |
| 101/0 | Pin markers: pads | (removed after routing) |

## Layer Format

All layer parameters use the **"L/D" string** format (layer/datatype), not arrays or separate integers. Examples: `"1/0"`, `"3/0"`, `"100/0"`.

This applies to all CLI flags (`--layer`, `--inner-layer`, `--outer-layer`, `--patch-layer`, `--obstacle-layers`, `--pin-contacts`, `--pin-pads`) and to the `auto_route` MCP tool parameters (`pin_layer_a`, `pin_layer_b`, `obstacle_layers`, `output_layer`).

---

## Dense Fan-Out Recipe (tight clusters, 8+ contacts)

When many contacts fan out from a small cluster (e.g. 8 ohmic contacts on a
~30 um device), the **last** route in the bundle used to fail "No path found":
each contact ends up walled in by its sibling-contact pin markers plus the
clearance halos of routes already laid, even though a legal, non-crossing path
to the pad still exists. This capped connectivity (HM08 ended at 3/8 contacts
routed 鈫?score 0.387 despite perfect placement).

`auto_route` now handles this automatically:

- **Per-net rescue (on by default).** Any net that fails the first pathfinding
  pass is retried with a local, then global, relaxation that opens the
  clearance halos + sibling pin-markers blocking it while keeping every
  already-routed path cell HARD 鈥?so the rescued lead reaches its pad without
  crossing any prior route. The response reports `rescued_nets` (how many nets
  needed the rescue) and a `rescue_note`. After a rescue, still run
  `route_inspect` / `evaluate_design` with `contact_isolation` to confirm no
  crossings 鈥?the rescue is designed never to add one, but verify.
- The rescue is a strict no-op on layouts that already route fully, so it never
  perturbs clean cases.

If a net still fails after the rescue (genuine over-saturation 鈥?a contact
sitting directly on top of a prior route), drop the assignment with
`pin_pairs_override` to a topology with more corridor room, widen the field, or
fall back to the manual L-route below for that one lead. Do **not** reflexively
lower `map_resolution`: a finer grid does **not** reliably improve dense-fan-out
connectivity and costs ~4x runtime per halving (鈮? s 鈫?15 s 鈫?66 s at
2.0 鈫?1.0 鈫?0.5 um on a 2 mm field). Use `auto_map_resolution=true` only when
contacts are genuinely small (~3 um) and the default under-resolves them.

## Known Limitations

- In extreme cases a route may still fail after rescue when a contact sits
  directly on a previously-routed path (no non-crossing path exists). Re-assign
  via `pin_pairs_override`, widen the write field, or route that lead manually.
- Auto-router may produce overlapping routes near very tight contact clusters
  (sub-pitch spacing). These are usually junction overlaps near the shared
  cluster that `contact_isolation` filters; increase `path_safe_distance` or
  narrow `path_width` if `route_inspect` flags genuine mid-body crossings.

## auto_route Environment

The `auto_route` MCP tool runs a subprocess on the **host machine** (KLayout runs on the host, not inside the container). By default it activates conda env `instrMCPdev` via `~/miniforge3/etc/profile.d/conda.sh`. This fails if the host uses a different conda distribution (e.g., `anaconda3`).

**Workaround**: Pass the `python_path` parameter to bypass conda activation entirely. To discover the correct path, use `execute_script` (which also runs on the host):

```python
import glob, os
candidates = glob.glob(os.path.expanduser("~/anaconda3/envs/instrMCPdev/bin/python3")) + \
             glob.glob(os.path.expanduser("~/miniforge3/envs/instrMCPdev/bin/python3"))
result = candidates[0] if candidates else "instrMCPdev env not found"
```

Then pass the discovered path as `python_path` in your `auto_route` call.

## Manual Route Fallback

When `auto_route` fails for individual pin pairs (reports "No path found"), create manual L-shaped routes via `execute_script`:

```python
top_cell = _layout.top_cell()
li_route = _layout.layer(3, 0)  # or your route layer

# Create an L-shaped path from contact to pad
x1, y1 = 766.0, 811.4  # contact center (um)
x2, y2 = 928.0, 1825.0  # pad center (um)
mid_x = x2  # route goes horizontal then vertical
width = 1.0  # path width in um

path = pya.DPath([
    pya.DPoint(x1, y1),
    pya.DPoint(mid_x, y1),  # horizontal segment
    pya.DPoint(mid_x, y2),  # vertical segment to pad
], width / _layout.dbu)
top_cell.shapes(li_route).insert(path)
```

Use this for any pairs that `auto_route` couldn't connect. The current MCP response reports `routed_pairs`, `errors`, and route metadata via `route_inspect`; use those fields plus the dry-run `pairs[]` order to identify which contacts need manual routing.
