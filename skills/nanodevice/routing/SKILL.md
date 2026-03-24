---
name: nanodevice:routing
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

### place_pads.py — Place bonding pads around field perimeter

```bash
python scripts/place_pads.py --field 2000 --pad-size 80 --pads-per-edge 12 [--layer 2/0] [--margin 60]
```

- `--field` — EBL write field size in um (default: 2000)
- `--pad-size` — Bonding pad side length in um (default: 80)
- `--pads-per-edge` — Number of pads per edge (default: 12)
- `--layer` — Output layer as `layer/datatype` (default: 2/0)
- `--margin` — Pad center inset from field edge in um (default: 60)

Example — 48 pads (12 per edge) around a 2mm field:
```bash
python scripts/place_pads.py --field 2000 --pad-size 80 --pads-per-edge 12
```

### route_multiwindow.py — Multi-window EBL routing

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

- `--pin-contacts` — Layer with pin markers at device contacts (default: 100/0)
- `--pin-pads` — Layer with pin markers at bonding pads (default: 101/0)
- `--inner-window` — Inner EBL window size in um (default: 800)
- `--outer-window` — Outer EBL window size in um (default: 2000)
- `--inner-width` — Route line width for inner window in um (default: 0.5)
- `--outer-width` — Route line width for outer window in um (default: 1.0)
- `--inner-layer` — Output layer for inner routes (default: 3/0)
- `--outer-layer` — Output layer for outer routes (default: 4/0)
- `--patch-layer` — Output layer for boundary patches (default: 5/0)
- `--patch-size` — Boundary patch size in um (default: 1.0)
- `--obstacle-layers` — Comma-separated obstacle layers (default: 1/0)

The script:
1. Reads contact and pad pin positions from their layers
2. Computes boundary intersection points at the inner window edge
3. Places boundary pins on temporary layers
4. Runs inner auto_route (contacts → boundary, fine lines)
5. Places connection patches at boundary points
6. Runs outer auto_route (boundary → pads, coarse lines)
7. Cleans up temporary pin layers

### clear_routes.py — Remove routes from specified layers

```bash
python scripts/clear_routes.py 3/0 4/0 5/0
```

Clears all shapes from the listed layers. Useful for re-routing without losing device geometry.

## Workflow

1. Create device geometry (mesa, contacts) using geometry skill or execute_script
2. Place pin markers at device contact tips on layer 100/0
3. Place bonding pads with `place_pads.py` (also places pin markers on 101/0)
4. Run `route_multiwindow.py` to connect everything
5. Check result — if overlapping, adjust parameters and re-run after `clear_routes.py`

## Output Layers Convention

| Layer | Purpose | EBL Pass |
|-------|---------|----------|
| 1/0 | Mesa (graphene etch) | Pass 1 |
| 2/0 | Bonding pads | Pass 3 (coarse) |
| 3/0 | Fine routes (<inner window) | Pass 2 (fine) |
| 4/0 | Coarse routes (inner→outer) | Pass 3 (coarse) |
| 5/0 | Boundary patches | Pass 2 or 3 |
| 100/0 | Pin markers: contacts | (removed after routing) |
| 101/0 | Pin markers: pads | (removed after routing) |

## Known Limitations

- Auto-router may produce overlapping routes in dense fan-out scenarios (48+ pins from a small cluster)
- Workaround: increase `path_safe_distance`, or manually adjust problem routes via execute_script

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

Use this for any pairs that `auto_route` couldn't connect. Check the auto_route result for `failed_pairs` to identify which contacts need manual routing.
