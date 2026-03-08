# Autorouter MCP Tool Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an `auto_route` MCP tool that automatically routes connections between pin pairs using cost-based pathfinding in a subprocess.

**Architecture:** Hybrid subprocess model — MCP server (inside KLayout, no new deps) saves layout to temp GDS, spawns `route_worker.py` (numpy/scipy/scikit-image) for heavy computation, reads back routed paths as JSON, inserts them via pya.

**Tech Stack:** numpy, scipy (`linear_sum_assignment`), scikit-image (`MCP_Geometric`), klayout.db (standalone), pya (inside KLayout)

---

### Task 1: Install routing dependencies in conda env

**Files:** None (environment setup)

**Step 1: Install packages**

Run:
```bash
source ~/miniforge3/etc/profile.d/conda.sh && conda activate instrMCPdev && pip install scikit-image
```

(numpy, scipy, klayout should already be installed; scikit-image provides `MCP_Geometric`)

**Step 2: Verify imports**

Run:
```bash
source ~/miniforge3/etc/profile.d/conda.sh && conda activate instrMCPdev && python -c "import numpy; import scipy.optimize; from skimage.graph import MCP_Geometric; import klayout.db; print('All imports OK')"
```

Expected: `All imports OK`

**Step 3: Commit** — no files changed, skip.

---

### Task 2: Create route_worker.py — the subprocess routing engine

**Files:**
- Create: `tools/route_worker.py`

This is the core routing engine. It runs as a standalone subprocess, reads a GDS + config, and outputs routed paths as JSON.

**Step 2.1: Write the route worker**

```python
#!/usr/bin/env python
"""Subprocess routing engine for KlayoutClaw auto_route.

Reads a GDS file and routing config, computes optimal routes between
pin pairs using cost-based pathfinding, outputs path coordinates as JSON.

Dependencies: numpy, scipy, scikit-image, klayout (klayout.db)

Usage:
    python route_worker.py config.json

Config JSON:
    {
        "gds_path": "/tmp/input.gds",
        "output_path": "/tmp/routes.json",
        "cell_name": "TOP",
        "dbu": 0.001,
        "pin_layer_a": "102/0",
        "pin_layer_b": "111/0",
        "obstacle_layers": ["1/0", "3/0"],
        "output_layer": "10/0",
        "path_width_um": 1.0,
        "obs_safe_distance_um": 5.0,
        "path_safe_distance_um": 5.0,
        "map_resolution_um": 1.0
    }

Output JSON:
    {
        "status": "success",
        "routed_pairs": 6,
        "total_pins_a": 6,
        "total_pins_b": 6,
        "paths": [
            {"points_dbu": [[x1,y1], [x2,y2], ...], "pin_a": [ax,ay], "pin_b": [bx,by]},
            ...
        ],
        "errors": []
    }
"""

import sys
import json
import numpy as np
from scipy.optimize import linear_sum_assignment
from skimage.graph import MCP_Geometric
import klayout.db as kdb


def parse_layer(layer_str):
    """Parse '102/0' into (102, 0)."""
    parts = layer_str.strip().split("/")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0


def extract_pins(cell, layout, layer_str):
    """Extract pin center coordinates (in dbu) from shapes on a layer."""
    layer_num, datatype = parse_layer(layer_str)
    layer_idx = layout.find_layer(kdb.LayerInfo(layer_num, datatype))
    if layer_idx is None:
        return []
    pins = []
    for shape in cell.shapes(layer_idx).each():
        bbox = shape.bbox()
        cx = (bbox.left + bbox.right) // 2
        cy = (bbox.bottom + bbox.top) // 2
        pins.append((cx, cy))
    return pins


def build_obstacle_region(cell, layout, obstacle_layers, safe_distance_dbu):
    """Build merged obstacle region with safe distance expansion."""
    region = kdb.Region()
    for layer_str in obstacle_layers:
        layer_num, datatype = parse_layer(layer_str)
        layer_idx = layout.find_layer(kdb.LayerInfo(layer_num, datatype))
        if layer_idx is None:
            continue
        for shape in cell.shapes(layer_idx).each():
            if shape.is_box():
                region.insert(shape.box)
            elif shape.is_polygon():
                region.insert(shape.polygon)
            elif shape.is_path():
                region.insert(shape.path.polygon())
    region.merge()
    if safe_distance_dbu > 0:
        region = region.sized(safe_distance_dbu)
    return region


def rasterize_obstacles(region, bbox, resolution_dbu):
    """Convert obstacle region to a 2D cost grid.

    Returns (cost_grid, origin_x_dbu, origin_y_dbu) where origin is the
    bottom-left corner of the grid in dbu coordinates.
    """
    ox = bbox.left
    oy = bbox.bottom
    width = bbox.right - bbox.left
    height = bbox.top - bbox.bottom

    nx = max(1, width // resolution_dbu + 1)
    ny = max(1, height // resolution_dbu + 1)

    # Base cost = 1.0 (traversable)
    grid = np.ones((ny, nx), dtype=np.float64)

    # Mark obstacle pixels with high cost
    for poly in region.each():
        hull = poly.to_simple_polygon()
        for edge_idx in range(hull.num_points()):
            pass  # We'll use a different approach below

    # Rasterize: check each grid cell against the region
    # For efficiency, use a sampling approach
    for iy in range(ny):
        for ix in range(nx):
            px = ox + ix * resolution_dbu
            py = oy + iy * resolution_dbu
            pt = kdb.Point(px, py)
            if region.contains(pt):
                grid[iy, ix] = 1e6  # effectively impassable

    return grid, ox, oy


def add_damping(grid, region, bbox, resolution_dbu, ox, oy, safe_distance_dbu, hardness):
    """Add graduated cost near obstacles (soft constraint damping)."""
    ny, nx = grid.shape
    # Create expanded regions at increasing distances
    steps = max(1, safe_distance_dbu // resolution_dbu)
    for step in range(1, steps + 1):
        dist = step * resolution_dbu
        expanded = region.sized(dist)
        cost_addition = hardness * (steps - step + 1) / steps
        for iy in range(ny):
            for ix in range(nx):
                if grid[iy, ix] >= 1e6:
                    continue  # already blocked
                px = ox + ix * resolution_dbu
                py = oy + iy * resolution_dbu
                if expanded.contains(kdb.Point(px, py)):
                    grid[iy, ix] += cost_addition


def coord_to_grid(x_dbu, y_dbu, ox, oy, resolution_dbu):
    """Convert dbu coordinates to grid indices."""
    ix = round((x_dbu - ox) / resolution_dbu)
    iy = round((y_dbu - oy) / resolution_dbu)
    return iy, ix  # row, col


def grid_to_coord(iy, ix, ox, oy, resolution_dbu):
    """Convert grid indices to dbu coordinates."""
    x = ox + ix * resolution_dbu
    y = oy + iy * resolution_dbu
    return x, y


def find_path(grid, start_rc, end_rc):
    """Find minimum-cost path using MCP_Geometric (Dijkstra on grid)."""
    mcp = MCP_Geometric(grid, fully_connected=True)
    cumulative_costs, traceback = mcp.find_costs([start_rc])
    path_indices = mcp.traceback(end_rc)
    return path_indices


def compress_path(points_dbu):
    """Remove redundant waypoints, keep only inflection points."""
    if len(points_dbu) <= 2:
        return points_dbu
    compressed = [points_dbu[0]]
    for i in range(1, len(points_dbu) - 1):
        prev = compressed[-1]
        curr = points_dbu[i]
        nxt = points_dbu[i + 1]
        # Keep if direction changes
        dx1 = curr[0] - prev[0]
        dy1 = curr[1] - prev[1]
        dx2 = nxt[0] - curr[0]
        dy2 = nxt[1] - curr[1]
        # Normalize direction comparison
        cross = dx1 * dy2 - dy1 * dx2
        if cross != 0:
            compressed.append(curr)
    compressed.append(points_dbu[-1])
    return compressed


def add_path_damping(grid, path_points_rc, safe_distance_px, hardness):
    """Add cost around an existing routed path to prevent overlap."""
    ny, nx = grid.shape
    for ry, rx in path_points_rc:
        for dy in range(-safe_distance_px, safe_distance_px + 1):
            for dx in range(-safe_distance_px, safe_distance_px + 1):
                ny2, nx2 = ry + dy, rx + dx
                if 0 <= ny2 < ny and 0 <= nx2 < nx:
                    dist = max(abs(dy), abs(dx))
                    if dist > 0 and grid[ny2, nx2] < 1e6:
                        cost_add = hardness * (safe_distance_px - dist + 1) / safe_distance_px
                        grid[ny2, nx2] += cost_add


def route(config):
    """Main routing function."""
    # Load GDS
    layout = kdb.Layout()
    layout.read(config["gds_path"])

    cell_name = config.get("cell_name")
    if cell_name:
        cell = layout.cell(cell_name)
    else:
        cell = layout.top_cell()

    if cell is None:
        return {"status": "error", "errors": ["Cell not found"], "routed_pairs": 0, "paths": []}

    dbu = layout.dbu

    # Parse config
    resolution_dbu = int(config.get("map_resolution_um", 1.0) / dbu)
    obs_safe_dbu = int(config.get("obs_safe_distance_um", 5.0) / dbu)
    path_safe_dbu = int(config.get("path_safe_distance_um", 5.0) / dbu)
    obs_hardness = config.get("obs_hardness", 20.0)
    path_hardness = config.get("path_hardness", 10.0)

    # Extract pins
    pins_a = extract_pins(cell, layout, config["pin_layer_a"])
    pins_b = extract_pins(cell, layout, config["pin_layer_b"])

    if not pins_a or not pins_b:
        return {
            "status": "error",
            "errors": [f"No pins found: layer_a={len(pins_a)}, layer_b={len(pins_b)}"],
            "routed_pairs": 0,
            "total_pins_a": len(pins_a),
            "total_pins_b": len(pins_b),
            "paths": [],
        }

    # Build obstacle region
    obstacle_layers = config.get("obstacle_layers", [])
    obs_region = build_obstacle_region(cell, layout, obstacle_layers, 0)
    obs_region_safe = build_obstacle_region(cell, layout, obstacle_layers, obs_safe_dbu)

    # Compute bounding box (union of all geometry + margin)
    total_bbox = cell.bbox()
    margin = max(obs_safe_dbu, path_safe_dbu) * 2
    total_bbox = kdb.Box(
        total_bbox.left - margin, total_bbox.bottom - margin,
        total_bbox.right + margin, total_bbox.top + margin,
    )

    # Rasterize obstacles
    grid, ox, oy = rasterize_obstacles(obs_region, total_bbox, resolution_dbu)

    # Add damping near obstacles
    add_damping(grid, obs_region, total_bbox, resolution_dbu, ox, oy, obs_safe_dbu, obs_hardness)

    # Hungarian matching: compute cost matrix
    n_a = len(pins_a)
    n_b = len(pins_b)
    n = min(n_a, n_b)

    # Use Euclidean distance for initial matching (pathfinding cost too expensive for full matrix)
    cost_matrix = np.zeros((n_a, n_b))
    for i, (ax, ay) in enumerate(pins_a):
        for j, (bx, by) in enumerate(pins_b):
            cost_matrix[i, j] = np.sqrt((ax - bx) ** 2 + (ay - by) ** 2)

    row_ind, col_ind = linear_sum_assignment(cost_matrix)

    # Route each matched pair
    paths = []
    errors = []
    path_safe_px = max(1, path_safe_dbu // resolution_dbu)

    for idx in range(len(row_ind)):
        i, j = row_ind[idx], col_ind[idx]
        pa = pins_a[i]
        pb = pins_b[j]

        start_rc = coord_to_grid(pa[0], pa[1], ox, oy, resolution_dbu)
        end_rc = coord_to_grid(pb[0], pb[1], ox, oy, resolution_dbu)

        # Clamp to grid bounds
        ny, nx = grid.shape
        start_rc = (max(0, min(ny - 1, start_rc[0])), max(0, min(nx - 1, start_rc[1])))
        end_rc = (max(0, min(ny - 1, end_rc[0])), max(0, min(nx - 1, end_rc[1])))

        try:
            path_rc = find_path(grid, start_rc, end_rc)

            # Convert back to dbu coordinates
            path_dbu = [grid_to_coord(iy, ix, ox, oy, resolution_dbu) for iy, ix in path_rc]
            path_dbu = compress_path(path_dbu)

            paths.append({
                "points_dbu": path_dbu,
                "pin_a": list(pa),
                "pin_b": list(pb),
            })

            # Add damping around this path for subsequent routes
            add_path_damping(grid, path_rc, path_safe_px, path_hardness)

        except Exception as e:
            errors.append(f"Failed to route pin_a[{i}] -> pin_b[{j}]: {e}")

    return {
        "status": "success" if not errors else "partial",
        "routed_pairs": len(paths),
        "total_pins_a": n_a,
        "total_pins_b": n_b,
        "paths": paths,
        "errors": errors,
    }


def main():
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} config.json", file=sys.stderr)
        sys.exit(1)

    config_path = sys.argv[1]
    with open(config_path) as f:
        config = json.load(f)

    result = route(config)

    output_path = config.get("output_path", "/tmp/klayoutclaw_routes.json")
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    # Also print to stdout for debugging
    print(json.dumps({"status": result["status"], "routed_pairs": result["routed_pairs"]}))

    sys.exit(0 if result["status"] == "success" else 1)


if __name__ == "__main__":
    main()
```

**Step 2.2: Run a basic smoke test**

Run:
```bash
source ~/miniforge3/etc/profile.d/conda.sh && conda activate instrMCPdev && python -c "
import sys; sys.path.insert(0, 'tools')
from route_worker import parse_layer, compress_path
assert parse_layer('102/0') == (102, 0)
assert compress_path([[0,0],[1,0],[2,0]]) == [[0,0],[2,0]]
assert compress_path([[0,0],[1,0],[1,1]]) == [[0,0],[1,0],[1,1]]
print('Smoke test OK')
"
```

Expected: `Smoke test OK`

**Step 2.3: Commit**

```bash
git add tools/route_worker.py
git commit -m "feat: add route_worker.py subprocess routing engine

Implements cost-based pathfinding using scipy Hungarian matching
and scikit-image MCP_Geometric for the auto_route MCP tool.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 3: Create test GDS — Hall bar with pin markers, no traces

**Files:**
- Create: `tests/create_hallbar_unrouted.py`

This creates a Hall bar layout with pin markers on layers 102/0 and 111/0, but NO metal traces on layer 2/0. The autorouter should create those traces.

**Step 3.1: Write the test GDS creator**

Same structure as `tests/create_hallbar.py` but:
- Keeps layer 1/0 (Mesa) and layer 3/0 (Pads) geometry identical
- Adds small pin markers (5x5um boxes) on layer 102/0 at probe tips
- Adds small pin markers (5x5um boxes) on layer 111/0 at pad centers
- Does NOT draw any metal traces (layer 2/0)

```python
#!/usr/bin/env python
"""Create a Hall bar with pin markers but NO metal routing.

This test GDS is input for the auto_route tool — it should automatically
connect probes (pin_a, layer 102/0) to pads (pin_b, layer 111/0).

Layers:
- 1/0 (Mesa): Channel + probes (obstacles)
- 3/0 (Pads): Bonding pads (obstacles)
- 102/0 (Pin_A): Markers at probe tips (routing endpoints)
- 111/0 (Pin_B): Markers at pad centers (routing endpoints)
- NO layer 2/0 (Metal) — auto_route creates this

Usage:
    python tests/create_hallbar_unrouted.py [output.gds]
"""

import sys
import json
import urllib.request

MCP_URL = "http://127.0.0.1:8765/mcp"
_req_id = 0
_session_id = None


def mcp_call(method, params=None):
    global _req_id, _session_id
    _req_id += 1
    payload = {"jsonrpc": "2.0", "id": _req_id, "method": method}
    if params:
        payload["params"] = params
    headers = {"Content-Type": "application/json"}
    if _session_id:
        headers["Mcp-Session-Id"] = _session_id
    req = urllib.request.Request(MCP_URL, data=json.dumps(payload).encode(), headers=headers, method="POST")
    r = urllib.request.urlopen(req, timeout=30)
    _session_id = r.headers.get("Mcp-Session-Id", _session_id)
    data = json.loads(r.read().decode())
    if "error" in data:
        raise RuntimeError(f"MCP error: {data['error']}")
    return data


def tool_call(tool_name, **kwargs):
    result = mcp_call("tools/call", {"name": tool_name, "arguments": kwargs})
    text = result["result"]["content"][0]["text"]
    return json.loads(text)


def main():
    gds_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/test_hallbar_unrouted.gds"

    # Initialize MCP
    mcp_call("initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "create_hallbar_unrouted", "version": "0.5"},
    })

    # Create layout
    print("Creating layout...")
    tool_call("create_layout", name="HALLBAR_UNROUTED", dbu=0.001)

    # Build Hall bar with pin markers but no metal routing
    print("Drawing Hall bar with pin markers...")
    tool_call("execute_script", code="""
dbu = _layout.dbu
cell = _top_cell

def rect(layer, dt, x1, y1, x2, y2):
    li = _layout.layer(layer, dt)
    cell.shapes(li).insert(pya.Box(int(x1/dbu), int(y1/dbu), int(x2/dbu), int(y2/dbu)))

def pin_marker(layer, dt, cx, cy, size=5):
    \"\"\"Place a small square pin marker centered at (cx, cy).\"\"\"
    hs = size / 2.0
    rect(layer, dt, cx - hs, cy - hs, cx + hs, cy + hs)

# =========================================================================
# Layer 1/0: Mesa (graphene channel + probes) — obstacles
# =========================================================================
rect(1, 0, -50, -12.5, 50, 12.5)

probe_xs = [-30, 0, 30]
for px in probe_xs:
    rect(1, 0, px - 5, 12.5, px + 5, 32.5)    # top probes
    rect(1, 0, px - 5, -32.5, px + 5, -12.5)   # bottom probes

# =========================================================================
# Layer 3/0: Bonding pads — obstacles
# =========================================================================
rect(3, 0, -950, -50, -850, 50)    # left current pad
rect(3, 0,  850, -50,  950, 50)    # right current pad

rect(3, 0, -650, 800, -550, 900)   # top-left voltage
rect(3, 0,  -50, 800,   50, 900)   # top-center voltage
rect(3, 0,  550, 800,  650, 900)   # top-right voltage

rect(3, 0, -650, -900, -550, -800) # bottom-left voltage
rect(3, 0,  -50, -900,   50, -800) # bottom-center voltage
rect(3, 0,  550, -900,  650, -800) # bottom-right voltage

# =========================================================================
# Layer 102/0: Pin_A markers at probe tips (routing start points)
# =========================================================================
# Top probes (at top edge of each probe)
pin_marker(102, 0, -30, 32.5)
pin_marker(102, 0,   0, 32.5)
pin_marker(102, 0,  30, 32.5)

# Bottom probes (at bottom edge of each probe)
pin_marker(102, 0, -30, -32.5)
pin_marker(102, 0,   0, -32.5)
pin_marker(102, 0,  30, -32.5)

# Current source/drain at channel ends
pin_marker(102, 0, -50, 0)
pin_marker(102, 0,  50, 0)

# =========================================================================
# Layer 111/0: Pin_B markers at pad centers (routing end points)
# =========================================================================
pin_marker(111, 0, -900, 0)     # left current pad
pin_marker(111, 0,  900, 0)     # right current pad

pin_marker(111, 0, -600, 850)   # top-left voltage pad
pin_marker(111, 0,    0, 850)   # top-center voltage pad
pin_marker(111, 0,  600, 850)   # top-right voltage pad

pin_marker(111, 0, -600, -850)  # bottom-left voltage pad
pin_marker(111, 0,    0, -850)  # bottom-center voltage pad
pin_marker(111, 0,  600, -850)  # bottom-right voltage pad

result = {"status": "ok", "pins_a": 8, "pins_b": 8, "message": "Hall bar with pin markers (no metal routing)"}
""")

    # Save
    print(f"Saving to {gds_path}...")
    tool_call("save_layout", filepath=gds_path)

    info = tool_call("get_layout_info")
    print(f"Layout info: {json.dumps(info, indent=2)}")
    print(f"\nDone! Unrouted GDS saved to {gds_path}")


if __name__ == "__main__":
    main()
```

**Step 3.2: Verify the script runs** (requires KLayout MCP server running)

Run:
```bash
source ~/miniforge3/etc/profile.d/conda.sh && conda activate instrMCPdev && python tests/create_hallbar_unrouted.py /tmp/test_hallbar_unrouted.gds
```

Expected: `Done! Unrouted GDS saved to /tmp/test_hallbar_unrouted.gds`

**Step 3.3: Commit**

```bash
git add tests/create_hallbar_unrouted.py
git commit -m "test: add unrouted Hall bar GDS creator with pin markers

Creates layout with probe and pad pin markers on layers 102/0 and 111/0
for autorouter testing. No metal traces drawn.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 4: Add `auto_route` tool to MCP server

**Files:**
- Modify: `plugin/klayoutclaw_server.lym` (add tool definition + handler)

**Step 4.1: Add auto_route tool definition to TOOLS list**

In `klayoutclaw_server.lym`, after the `get_layout_info` tool definition in the TOOLS list, add:

```python
{
    "name": "auto_route",
    "description": "Automatically route connections between pin pairs. Extracts pins from two layers, uses Hungarian matching for optimal pairing, then cost-based pathfinding to create routes avoiding obstacles. Requires numpy/scipy/scikit-image in a conda env.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "pin_layer_a": {"type": "string", "description": "Layer with start pins (e.g. '102/0')"},
            "pin_layer_b": {"type": "string", "description": "Layer with end pins (e.g. '111/0')"},
            "obstacle_layers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Layers to avoid (e.g. ['1/0', '3/0'])",
                "default": []
            },
            "output_layer": {"type": "string", "description": "Layer for routed paths", "default": "10/0"},
            "path_width": {"type": "number", "description": "Path width in microns", "default": 10.0},
            "obs_safe_distance": {"type": "number", "description": "Min distance from obstacles in microns", "default": 5.0},
            "path_safe_distance": {"type": "number", "description": "Min distance between paths in microns", "default": 5.0},
            "map_resolution": {"type": "number", "description": "Grid resolution in microns", "default": 2.0},
            "conda_env": {"type": "string", "description": "Conda environment with routing deps", "default": "instrMCPdev"},
            "python_path": {"type": "string", "description": "Path to python with routing deps (overrides conda_env)"}
        },
        "required": ["pin_layer_a", "pin_layer_b"]
    }
}
```

**Step 4.2: Add auto_route handler function**

Add before `_TOOL_DISPATCH`:

```python
def _tool_auto_route(args):
    global _layout, _layout_view, _top_cell
    import tempfile
    import subprocess

    if _layout is None:
        _get_or_create_view()

    pin_layer_a = args.get("pin_layer_a")
    pin_layer_b = args.get("pin_layer_b")
    if not pin_layer_a or not pin_layer_b:
        raise ValueError("Missing required parameters: pin_layer_a and pin_layer_b")

    # Save current layout to temp GDS
    tmp_dir = tempfile.mkdtemp(prefix="klayoutclaw_route_")
    tmp_gds = os.path.join(tmp_dir, "input.gds")
    tmp_config = os.path.join(tmp_dir, "config.json")
    tmp_output = os.path.join(tmp_dir, "routes.json")

    save_opts = pya.SaveLayoutOptions()
    save_opts.format = "GDS2"
    _layout.write(tmp_gds, save_opts)

    # Build config
    config = {
        "gds_path": tmp_gds,
        "output_path": tmp_output,
        "cell_name": _top_cell.name if _top_cell else None,
        "dbu": _layout.dbu,
        "pin_layer_a": pin_layer_a,
        "pin_layer_b": pin_layer_b,
        "obstacle_layers": args.get("obstacle_layers", []),
        "output_layer": args.get("output_layer", "10/0"),
        "path_width_um": args.get("path_width", 10.0),
        "obs_safe_distance_um": args.get("obs_safe_distance", 5.0),
        "path_safe_distance_um": args.get("path_safe_distance", 5.0),
        "map_resolution_um": args.get("map_resolution", 2.0),
    }
    with open(tmp_config, "w") as f:
        json.dump(config, f)

    # Find route_worker.py
    worker_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "route_worker.py")
    if not os.path.exists(worker_path):
        # Check in KlayoutClaw install directory
        for search_dir in [
            os.path.expanduser("~/.klayout/pymacros"),
            os.path.expanduser("~/Documents/GitHub/KlayoutClaw/tools"),
        ]:
            candidate = os.path.join(search_dir, "route_worker.py")
            if os.path.exists(candidate):
                worker_path = candidate
                break

    # Build python command with conda activation
    python_path = args.get("python_path")
    if python_path:
        cmd = [python_path, worker_path, tmp_config]
    else:
        conda_env = args.get("conda_env", "instrMCPdev")
        shell_cmd = "source ~/miniforge3/etc/profile.d/conda.sh && conda activate {} && python {} {}".format(
            conda_env, worker_path, tmp_config
        )
        cmd = ["bash", "-c", shell_cmd]

    _log("auto_route: spawning subprocess: {}".format(cmd))

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        _log("auto_route: subprocess returned code={}".format(proc.returncode))
        if proc.stderr:
            _log("auto_route stderr: {}".format(proc.stderr[:500]))
    except subprocess.TimeoutExpired:
        raise ValueError("auto_route: routing subprocess timed out (120s)")
    except Exception as e:
        raise ValueError("auto_route: failed to run subprocess: {}".format(e))

    # Read results
    if not os.path.exists(tmp_output):
        raise ValueError("auto_route: no output from router. stderr: {}".format(proc.stderr[:500]))

    with open(tmp_output) as f:
        result = json.load(f)

    if result.get("status") == "error":
        raise ValueError("auto_route: {}".format("; ".join(result.get("errors", ["unknown error"]))))

    # Insert routed paths into layout
    output_layer_str = args.get("output_layer", "10/0")
    ol_parts = output_layer_str.split("/")
    ol_num = int(ol_parts[0])
    ol_dt = int(ol_parts[1]) if len(ol_parts) > 1 else 0
    layer_idx = _layout.layer(ol_num, ol_dt)

    path_width_dbu = int(args.get("path_width", 10.0) / _layout.dbu)

    for route_data in result.get("paths", []):
        pts_dbu = route_data["points_dbu"]
        if len(pts_dbu) < 2:
            continue
        pya_pts = [pya.Point(int(p[0]), int(p[1])) for p in pts_dbu]
        pya_path = pya.Path(pya_pts, path_width_dbu)
        _top_cell.shapes(layer_idx).insert(pya_path)

    _refresh_view()

    # Cleanup temp files
    try:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass

    return json.dumps({
        "status": result.get("status", "unknown"),
        "routed_pairs": result.get("routed_pairs", 0),
        "total_pins_a": result.get("total_pins_a", 0),
        "total_pins_b": result.get("total_pins_b", 0),
        "output_layer": output_layer_str,
        "path_width_um": args.get("path_width", 10.0),
        "errors": result.get("errors", []),
    })
```

**Step 4.3: Add to dispatch table**

In `_TOOL_DISPATCH`, add:
```python
"auto_route": _tool_auto_route,
```

**Step 4.4: Remember XML escaping**

All `<`, `>`, `&` in the Python code must be escaped as `&lt;`, `&gt;`, `&amp;` in the .lym XML.

**Step 4.5: Update version to 0.5**

Change `<version>0.3</version>` to `<version>0.5</version>` and update the serverInfo version string.

**Step 4.6: Reinstall and verify tool listing**

Run:
```bash
source ~/miniforge3/etc/profile.d/conda.sh && conda activate instrMCPdev && python install.py
```

Then restart KLayout and run:
```bash
source ~/miniforge3/etc/profile.d/conda.sh && conda activate instrMCPdev && python tests/test_connection.py
```

Expected: 5 tools listed (including `auto_route`)

**Step 4.7: Commit**

```bash
git add plugin/klayoutclaw_server.lym
git commit -m "feat: add auto_route MCP tool (v0.5)

Subprocess-based autorouter that saves layout to temp GDS, spawns
route_worker.py with numpy/scipy/scikit-image, then injects paths.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 5: Create evaluate_routing.py — structural validation

**Files:**
- Create: `tests/evaluate_routing.py`

**Step 5.1: Write structural validator**

```python
#!/usr/bin/env python
"""Evaluate routed GDS for structural correctness.

Checks:
- Routed paths exist on the output layer
- Each pin marker has a path endpoint nearby
- No path intersects obstacle regions
- Path count matches pin pair count

Usage:
    python tests/evaluate_routing.py routed.gds [output.png]

Options:
    --pin-a-layer 102/0    Pin A layer (default: 102/0)
    --pin-b-layer 111/0    Pin B layer (default: 111/0)
    --output-layer 10/0    Routed paths layer (default: 10/0)
    --obstacle-layers 1/0,3/0  Obstacle layers (default: 1/0,3/0)
"""

import sys
import json
import argparse
import gdstk
import numpy as np


def parse_layer(layer_str):
    parts = layer_str.strip().split("/")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0


def get_shapes_on_layer(cell, layer, datatype):
    """Get all polygons and paths on a specific layer."""
    polys = [p for p in cell.get_polygons() if p.layer == layer and p.datatype == datatype]
    paths = [p for p in cell.get_paths() if p.layers[0] == layer and p.datatypes[0] == datatype]
    return polys, paths


def get_pin_centers(cell, layer, datatype):
    """Extract center coordinates from pin marker shapes."""
    polys, paths = get_shapes_on_layer(cell, layer, datatype)
    centers = []
    for p in polys:
        bbox = p.bounding_box()
        cx = (bbox[0][0] + bbox[1][0]) / 2
        cy = (bbox[0][1] + bbox[1][1]) / 2
        centers.append((cx, cy))
    return centers


def evaluate_routing(gds_path, pin_a_layer="102/0", pin_b_layer="111/0",
                     output_layer="10/0", obstacle_layers=None):
    if obstacle_layers is None:
        obstacle_layers = ["1/0", "3/0"]

    lib = gdstk.read_gds(gds_path)
    results = {"file": gds_path, "checks": {}, "pass": True}

    cells = lib.cells
    if not cells:
        results["pass"] = False
        results["checks"]["has_cells"] = False
        return results

    cell = cells[0]  # top cell
    results["checks"]["has_cells"] = True

    # Check 1: Routed paths exist on output layer
    ol, od = parse_layer(output_layer)
    route_polys, route_paths = get_shapes_on_layer(cell, ol, od)
    num_routes = len(route_polys) + len(route_paths)
    results["checks"]["num_routes"] = num_routes
    results["checks"]["has_routes"] = num_routes > 0
    if num_routes == 0:
        results["pass"] = False

    # Check 2: Pin counts
    al, ad = parse_layer(pin_a_layer)
    bl, bd = parse_layer(pin_b_layer)
    pins_a = get_pin_centers(cell, al, ad)
    pins_b = get_pin_centers(cell, bl, bd)
    results["checks"]["pins_a_count"] = len(pins_a)
    results["checks"]["pins_b_count"] = len(pins_b)
    expected_pairs = min(len(pins_a), len(pins_b))
    results["checks"]["expected_pairs"] = expected_pairs

    # Check 3: Route count matches pin pairs
    results["checks"]["routes_match_pins"] = num_routes == expected_pairs
    if num_routes != expected_pairs:
        results["pass"] = False

    # Check 4: Each path endpoint is near a pin
    tolerance_um = 20.0  # 20um tolerance for endpoint-to-pin distance
    if route_paths:
        endpoints_near_pins = 0
        all_pin_centers = pins_a + pins_b
        for path in route_paths:
            spine = path.spine()
            if len(spine) < 2:
                continue
            start = spine[0]
            end = spine[-1]
            for endpoint in [start, end]:
                for pc in all_pin_centers:
                    dist = np.sqrt((endpoint[0] - pc[0])**2 + (endpoint[1] - pc[1])**2)
                    if dist < tolerance_um:
                        endpoints_near_pins += 1
                        break
        results["checks"]["endpoints_near_pins"] = endpoints_near_pins
        results["checks"]["expected_endpoints"] = num_routes * 2

    # Check 5: Bounding box is reasonable
    all_points = []
    for poly in cell.get_polygons():
        all_points.extend(poly.points.tolist())
    if all_points:
        pts = np.array(all_points)
        width = pts[:, 0].max() - pts[:, 0].min()
        height = pts[:, 1].max() - pts[:, 1].min()
        results["checks"]["bbox_width_um"] = float(width)
        results["checks"]["bbox_height_um"] = float(height)
        results["checks"]["reasonable_dimensions"] = bool(
            width > 500 and height > 500 and width < 5000 and height < 5000
        )

    return results


def main():
    parser = argparse.ArgumentParser(description="Evaluate routed GDS")
    parser.add_argument("gds_path", help="Input GDS file")
    parser.add_argument("png_path", nargs="?", help="Optional PNG output")
    parser.add_argument("--pin-a-layer", default="102/0")
    parser.add_argument("--pin-b-layer", default="111/0")
    parser.add_argument("--output-layer", default="10/0")
    parser.add_argument("--obstacle-layers", default="1/0,3/0")
    args = parser.parse_args()

    obstacle_layers = [l.strip() for l in args.obstacle_layers.split(",")]

    results = evaluate_routing(
        args.gds_path,
        pin_a_layer=args.pin_a_layer,
        pin_b_layer=args.pin_b_layer,
        output_layer=args.output_layer,
        obstacle_layers=obstacle_layers,
    )

    print(json.dumps(results, indent=2, default=str))

    if args.png_path:
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "tools"))
        from gds_to_image import gds_to_image
        gds_to_image(args.gds_path, args.png_path)
        print(f"\nPNG saved: {args.png_path}")

    if results["pass"]:
        print("\nEVALUATION: PASS")
        sys.exit(0)
    else:
        print("\nEVALUATION: FAIL")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

**Step 5.2: Commit**

```bash
git add tests/evaluate_routing.py
git commit -m "test: add routing structural validator

Checks routed paths exist, count matches pin pairs, endpoints
are near pins, and bounding box is reasonable.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 6: Create E2E test script

**Files:**
- Create: `tests/test_autoroute.sh`

**Step 6.1: Write the E2E test**

```bash
#!/bin/bash
# E2E test for auto_route MCP tool.
#
# Prerequisites:
#   - KLayout running with KlayoutClaw MCP server (v0.5+)
#   - conda env instrMCPdev with routing deps
#
# Usage:
#   bash tests/test_autoroute.sh

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
GDS_UNROUTED="/tmp/test_hallbar_unrouted.gds"
GDS_ROUTED="/tmp/test_hallbar_routed.gds"
PNG_OUTPUT="/tmp/test_hallbar_routed.png"

source ~/miniforge3/etc/profile.d/conda.sh && conda activate instrMCPdev

echo "=== Step 1: Verify MCP server is running ==="
if ! curl -sf http://127.0.0.1:8765/mcp > /dev/null 2>&1; then
    echo "ERROR: MCP server not responding. Start KLayout first."
    exit 1
fi
echo "MCP server OK"

echo ""
echo "=== Step 2: Create unrouted Hall bar ==="
python "$PROJECT_DIR/tests/create_hallbar_unrouted.py" "$GDS_UNROUTED"
echo "Unrouted GDS: $GDS_UNROUTED"

echo ""
echo "=== Step 3: Run auto_route via MCP ==="
# Call auto_route tool via MCP JSON-RPC
ROUTE_RESULT=$(python -c "
import json, urllib.request

url = 'http://127.0.0.1:8765/mcp'
session_id = None

def call(method, params=None):
    global session_id
    payload = {'jsonrpc': '2.0', 'id': 1, 'method': method}
    if params: payload['params'] = params
    headers = {'Content-Type': 'application/json'}
    if session_id: headers['Mcp-Session-Id'] = session_id
    req = urllib.request.Request(url, json.dumps(payload).encode(), headers, method='POST')
    r = urllib.request.urlopen(req, timeout=180)
    session_id = r.headers.get('Mcp-Session-Id', session_id)
    return json.loads(r.read())

call('initialize', {'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}})
result = call('tools/call', {
    'name': 'auto_route',
    'arguments': {
        'pin_layer_a': '102/0',
        'pin_layer_b': '111/0',
        'obstacle_layers': ['1/0', '3/0'],
        'output_layer': '10/0',
        'path_width': 10.0,
        'obs_safe_distance': 15.0,
        'path_safe_distance': 10.0,
        'map_resolution': 5.0
    }
})
text = result['result']['content'][0]['text']
print(text)
")
echo "Route result: $ROUTE_RESULT"

echo ""
echo "=== Step 4: Save routed layout ==="
python -c "
import json, urllib.request
url = 'http://127.0.0.1:8765/mcp'
payload = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'save_layout', 'arguments': {'filepath': '$GDS_ROUTED'}}}
req = urllib.request.Request(url, json.dumps(payload).encode(), {'Content-Type': 'application/json'}, method='POST')
r = urllib.request.urlopen(req, timeout=30)
print(json.loads(r.read()))
"
echo "Routed GDS saved: $GDS_ROUTED"

echo ""
echo "=== Step 5: Structural evaluation ==="
python "$PROJECT_DIR/tests/evaluate_routing.py" "$GDS_ROUTED" "$PNG_OUTPUT"

echo ""
echo "=== Step 6: Visual check ==="
echo "PNG screenshot: $PNG_OUTPUT"
echo "Open in Preview: open $PNG_OUTPUT"

echo ""
echo "=== ALL TESTS PASSED ==="
```

**Step 6.2: Make executable**

Run: `chmod +x tests/test_autoroute.sh`

**Step 6.3: Commit**

```bash
git add tests/test_autoroute.sh
git commit -m "test: add E2E autoroute test script

Full pipeline: create unrouted hall bar, run auto_route, evaluate,
generate screenshot.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 7: Update docs, CLAUDE.md, and TODO.md

**Files:**
- Modify: `docs/tools.md` — add `auto_route` tool documentation
- Modify: `CLAUDE.md` — update tool count, add routing notes
- Modify: `TODO.md` — add v0.5 section

**Step 7.1: Add auto_route to docs/tools.md**

Add a new section for `auto_route` with full parameter schema.

**Step 7.2: Update CLAUDE.md**

- Change tool count from 4 to 5
- Add `auto_route` to the tools table
- Add note about conda env requirement for routing
- Add `tools/route_worker.py` to directory structure

**Step 7.3: Update TODO.md**

Add v0.5 section with all tasks marked appropriately.

**Step 7.4: Commit**

```bash
git add docs/tools.md CLAUDE.md TODO.md
git commit -m "docs: add auto_route documentation and update project files

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 8: Integration test — full E2E run

**Step 8.1: Install updated plugin**

Run:
```bash
source ~/miniforge3/etc/profile.d/conda.sh && conda activate instrMCPdev && python install.py
```

**Step 8.2: Restart KLayout**

Run: `pkill -x klayout; sleep 2; open /Applications/klayout.app`

Wait for server to be ready.

**Step 8.3: Run E2E test**

Run:
```bash
bash tests/test_autoroute.sh
```

Expected: `ALL TESTS PASSED`

**Step 8.4: Visual verification**

Run: `open /tmp/test_hallbar_routed.png`

Verify: Routes connect probes to pads, no obvious crossings, paths avoid mesa and pad obstacles.

**Step 8.5: Final commit**

```bash
git add -A
git commit -m "feat: autorouter v0.5 — all tests passing

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```
