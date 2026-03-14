# route_worker.py Routing Quality Enhancement — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port graduated damping, pin-aware cost fields, sorted routing order, and kdb-native rasterization from Klayout-Router into `tools/route_worker.py`.

**Architecture:** Replace binary obstacle model with negative-sentinel cost convention (-1/-2/-3 for obstacles/pins/paths), graduated damping via iterated `kdb.Region.rasterize()`, per-pair pin recovery cycle, and distance-sorted routing order. All changes confined to a single file with backward-compatible config.

**Tech Stack:** Python, klayout.db, numpy, scipy.optimize, scikit-image (MCP_Geometric)

**Spec:** `docs/superpowers/specs/2026-03-14-route-worker-quality-design.md`

---

## Chunk 1: Foundation — Rasterization, Cost Convention, Helpers

### Task 1: Replace matplotlib rasterization with kdb.Region.rasterize()

**Files:**
- Modify: `tools/route_worker.py:100-138` (replace `rasterize_region`)

- [ ] **Step 1: Write the new `rasterize_region_kdb()` function**

Replace the entire `rasterize_region()` function (lines 100-138) with:

```python
def rasterize_region_kdb(region: kdb.Region, bbox: kdb.Box,
                         resolution_dbu: int) -> np.ndarray:
    """Rasterize a kdb.Region into a 2D boolean numpy array using KLayout's native rasterizer.

    Grid axes: row = y (bottom-to-top mapped to 0..nrows-1), col = x.
    """
    ncols = max(1, (bbox.width() + resolution_dbu - 1) // resolution_dbu)
    nrows = max(1, (bbox.height() + resolution_dbu - 1) // resolution_dbu)
    origin = kdb.Point(bbox.left, bbox.bottom)
    step = kdb.Vector(resolution_dbu, resolution_dbu)
    raster = np.array(region.rasterize(origin, step, ncols, nrows))
    return raster > 0
```

Also remove the `from matplotlib.path import Path as MplPath` import (line 108 inside the old function).

- [ ] **Step 2: Update the call site in `route()`**

In `route()` (line 358), change:
```python
obs_grid = rasterize_region(obs_region, bbox, resolution_dbu)
```
to:
```python
obs_grid = rasterize_region_kdb(obs_region, bbox, resolution_dbu)
```

- [ ] **Step 3: Run existing tests to verify no regression**

Run: `cd /Users/andrewwayne/testFolder/KlayoutClaw && python tools/route_worker.py --help 2>&1 || echo "no --help, checking import"` and `python -c "import tools.route_worker"` to verify imports work.

- [ ] **Step 4: Commit**

```bash
git add tools/route_worker.py
git commit -m "refactor: replace matplotlib rasterization with kdb.Region.rasterize()"
```

### Task 2: Add conditional_overwrite() and get_damping_raster() helpers

**Files:**
- Modify: `tools/route_worker.py` (add two new functions after coordinate conversion helpers, ~line 158)

- [ ] **Step 1: Add `conditional_overwrite()` helper**

Add after the `grid_to_dbu()` function (after line 158):

```python
def conditional_overwrite(cost: np.ndarray, content: np.ndarray,
                          content_mask: np.ndarray,
                          r0: int, c0: int,
                          condition_fn=None) -> None:
    """Update cost grid subregion where both content_mask and condition_fn are true.

    Args:
        cost: Full cost grid (modified in-place).
        content: New values to write (shape must fit in cost[r0:r0+nrows, c0:c0+ncols]).
        content_mask: Boolean mask — which cells in content are candidates.
        r0, c0: Top-left offset of the subregion in the full cost grid.
        condition_fn: Function (existing_slice, content) -> bool mask.
            Receives the existing cost subregion AND the content array,
            enabling content-dependent conditions like "only increase".
            Default: always True (unconditional overwrite of masked cells).
    """
    nrows, ncols = content.shape
    # Clip to grid bounds
    grid_rows, grid_cols = cost.shape
    r1 = min(r0 + nrows, grid_rows)
    c1 = min(c0 + ncols, grid_cols)
    r0_c = max(r0, 0)
    c0_c = max(c0, 0)
    if r0_c >= r1 or c0_c >= c1:
        return
    # Compute content slice offsets
    cr0 = r0_c - r0
    cc0 = c0_c - c0
    cr1 = cr0 + (r1 - r0_c)
    cc1 = cc0 + (c1 - c0_c)
    content_slice = content[cr0:cr1, cc0:cc1]
    mask_slice = content_mask[cr0:cr1, cc0:cc1]
    region = cost[r0_c:r1, c0_c:c1]
    if condition_fn is not None:
        mask = condition_fn(region, content_slice) & mask_slice
    else:
        mask = mask_slice
    region[mask] = content_slice[mask]
```

- [ ] **Step 2: Add `get_damping_raster()` helper**

Add immediately after `conditional_overwrite()`:

```python
def get_damping_raster(region: kdb.Region, bbox: kdb.Box,
                       resolution_dbu: int, safe_distance_dbu: int,
                       hardness: float,
                       n_steps: int) -> tuple[int, int, np.ndarray]:
    """Build a graduated cost raster around a region.

    Creates n_steps concentric expansions of the region. Each expansion
    adds hardness // n_steps to the cost. Result: cost ramps from 0 at
    safe_distance to hardness at the region boundary.

    Returns (r0, c0, damping) where (r0, c0) is the grid offset of the
    raster's top-left corner relative to the full cost grid (whose origin
    is bbox.left, bbox.bottom). The raster covers only the bounding box
    of the sized union for performance.
    """
    if n_steps <= 0 or safe_distance_dbu <= 0:
        return 0, 0, np.zeros((0, 0), dtype=np.float64)

    union = region.dup()
    for i in range(n_steps):
        sized = region.sized(int(safe_distance_dbu * (i + 1) / n_steps))
        union += sized

    # Clip to routing bbox
    union = union & bbox

    if union.is_empty():
        return 0, 0, np.zeros((0, 0), dtype=np.float64)

    union_bbox = union.bbox()
    # Snap origin to align with the full cost grid's pixel boundaries.
    # This avoids partial-coverage rounding errors at damping field edges.
    origin_x = bbox.left + ((union_bbox.left - bbox.left) // resolution_dbu) * resolution_dbu
    origin_y = bbox.bottom + ((union_bbox.bottom - bbox.bottom) // resolution_dbu) * resolution_dbu
    origin = kdb.Point(origin_x, origin_y)
    # Compute extent to cover the union bbox from the snapped origin
    extent_x = union_bbox.right - origin_x
    extent_y = union_bbox.top - origin_y
    step = kdb.Vector(resolution_dbu, resolution_dbu)
    ncols = max(1, (extent_x + resolution_dbu - 1) // resolution_dbu)
    nrows = max(1, (extent_y + resolution_dbu - 1) // resolution_dbu)

    raster_raw = np.array(union.rasterize(origin, step, ncols, nrows))
    # Normalize: each concentric layer contributes hardness // n_steps
    step_cost = hardness // n_steps if n_steps > 0 else hardness
    damping = (raster_raw // (resolution_dbu * resolution_dbu)) * step_cost
    damping = damping.astype(np.float64)

    # Grid offset: where this raster sits in the full cost grid
    r0 = (origin_y - bbox.bottom) // resolution_dbu
    c0 = (origin_x - bbox.left) // resolution_dbu
    return r0, c0, damping
```

- [ ] **Step 3: Verify import still works**

Run: `python -c "from tools.route_worker import conditional_overwrite, get_damping_raster; print('OK')"`

- [ ] **Step 4: Commit**

```bash
git add tools/route_worker.py
git commit -m "feat: add conditional_overwrite and get_damping_raster helpers"
```

### Task 3: Add build_cost_grid_graduated() alongside old functions

**Files:**
- Modify: `tools/route_worker.py` (add new function after `build_cost_grid`, keep old functions alive)

**Note:** The old `build_cost_grid()` and `add_path_damping()` are NOT removed yet. They remain in the file until Task 5 rewrites the routing loop to use the new functions. This avoids a broken intermediate state.

- [ ] **Step 1: Add `build_cost_grid_graduated()` after the existing `build_cost_grid()`**

Add the new function after the existing `add_path_damping()` (do NOT delete the old functions yet):

```python
def build_cost_grid_graduated(obstacle_grid: np.ndarray,
                              obs_region: kdb.Region,
                              bbox: kdb.Box,
                              resolution_dbu: int,
                              obs_hardness: float,
                              obs_damping_step: int,
                              obs_safe_dbu: int) -> np.ndarray:
    """Build a float cost grid with graduated damping around obstacles.

    Obstacle cells get cost -1 (impassable via MCP_Geometric negative-cost
    convention). Cells near obstacles get stepped damping cost that
    increases toward the obstacle boundary.
    """
    nrows, ncols = obstacle_grid.shape
    cost = np.ones((nrows, ncols), dtype=np.float64)

    # Mark obstacles as impassable
    cost[obstacle_grid] = -1.0

    # Add graduated damping around obstacles
    if obs_damping_step > 0 and obs_safe_dbu > 0:
        r0, c0, damping = get_damping_raster(
            obs_region, bbox, resolution_dbu, obs_safe_dbu,
            obs_hardness, obs_damping_step)
        if damping.size > 0:
            conditional_overwrite(
                cost, damping, damping > 0, r0, c0,
                condition_fn=lambda existing, new: existing >= 0)

    return cost
```

- [ ] **Step 2: Commit**

```bash
git add tools/route_worker.py
git commit -m "feat: add build_cost_grid_graduated with stepped damping"
```

## Chunk 2: Pin-Aware Routing and find_path Updates

### Task 4: Update find_path() for negative sentinel convention

**Files:**
- Modify: `tools/route_worker.py` (`find_path` function)

- [ ] **Step 1: Update `find_path()` to use negative sentinel checks**

Replace the `find_path()` function with:

```python
def find_path(cost: np.ndarray, start: tuple[int, int],
              end: tuple[int, int]) -> list[tuple[int, int]] | None:
    """Find minimum-cost path using MCP_Geometric (Dijkstra on grid).

    Returns list of (row, col) or None if no path found.
    Uses negative-sentinel convention: cost < 0 means impassable.
    """
    # Clamp to grid bounds
    nrows, ncols = cost.shape
    sr = max(0, min(start[0], nrows - 1))
    sc = max(0, min(start[1], ncols - 1))
    er = max(0, min(end[0], nrows - 1))
    ec = max(0, min(end[1], ncols - 1))

    # If start or end is blocked, clear it temporarily.
    # Defensive guard: MCP_Geometric produces silently wrong results
    # when started on a negative-cost cell.
    orig_start = cost[sr, sc]
    orig_end = cost[er, ec]
    if cost[sr, sc] < 0:
        cost[sr, sc] = 1.0
    if cost[er, ec] < 0:
        cost[er, ec] = 1.0

    try:
        mcp = MCP_Geometric(cost, fully_connected=True)
        mcp.find_costs([(sr, sc)])
        path = mcp.traceback((er, ec))
        return path
    except Exception:
        return None
    finally:
        cost[sr, sc] = orig_start
        cost[er, ec] = orig_end
```

- [ ] **Step 2: Commit**

```bash
git add tools/route_worker.py
git commit -m "fix: update find_path for negative sentinel cost convention"
```

### Task 5: Add pin cost field and per-pair recovery to route()

**Files:**
- Modify: `tools/route_worker.py` (the `route()` function)

- [ ] **Step 1: Add new config parameters to `route()`**

In the config parsing section of `route()`, add after the existing parameter parsing (after `map_res_um`):

```python
    # New graduated damping parameters (backward compatible defaults)
    obs_hardness = config.get("obs_hardness", 20.0)
    obs_damping_step = config.get("obs_damping_step", 4)
    pin_safe_a_um = config.get("pin_safe_distance_a_um", 5.0)
    pin_safe_b_um = config.get("pin_safe_distance_b_um", 5.0)
    pin_hardness = config.get("pin_hardness", 20.0)
    pin_damping_step = config.get("pin_damping_step", 4)
    path_hardness = config.get("path_hardness", 10.0)
    path_damping_step = config.get("path_damping_step", 5)
    sort_pairs = config.get("sort_pairs", True)

    # Convert new um params to dbu
    pin_safe_a_dbu = int(round(pin_safe_a_um / dbu))
    pin_safe_b_dbu = int(round(pin_safe_b_um / dbu))
    path_safe_dbu = int(round(path_safe_um / dbu))
```

- [ ] **Step 2: Remove pin exclusion logic and simplify obstacle region**

Remove the entire pin exclusion block (lines 320-347 in the current code — from `raw_obs_region = build_obstacle_region(...)` through `obs_region = obs_region - pin_exclusion`).

Also change the obstacle region construction to pass `0` for safe_distance — the graduated damping gradient now handles the full avoidance zone. Previously `build_obstacle_region` pre-sized the obstacles by `obs_safe_dbu`, and then `get_damping_raster` added another `obs_safe_dbu` on top (doubling the zone). Now the hard obstacle is just the raw shapes, and the damping provides the soft avoidance:

```python
    obs_region = build_obstacle_region(cell, layout, obstacle_layers, 0)
```

No pin subtraction needed — pins will be handled separately in the cost field.

- [ ] **Step 3: Replace cost grid construction**

Replace the call to `build_cost_grid()` with:

```python
    # Rasterize obstacles
    obs_grid = rasterize_region_kdb(obs_region, bbox, resolution_dbu)

    # Build graduated cost grid
    cost = build_cost_grid_graduated(
        obs_grid, obs_region, bbox, resolution_dbu,
        obs_hardness, obs_damping_step, obs_safe_dbu)

    # Add pin costs: mark pin cells as -2 with damping halos
    pin_radius = resolution_dbu  # half-width of pin footprint box
    pin_regions_a = kdb.Region()
    for px, py in pins_a:
        pin_regions_a.insert(kdb.Box(
            px - pin_radius, py - pin_radius,
            px + pin_radius, py + pin_radius))

    pin_regions_b = kdb.Region()
    for px, py in pins_b:
        pin_regions_b.insert(kdb.Box(
            px - pin_radius, py - pin_radius,
            px + pin_radius, py + pin_radius))

    # Rasterize pin footprints as -2 (overwrite everything)
    all_pin_region = pin_regions_a + pin_regions_b
    pin_grid = rasterize_region_kdb(all_pin_region, bbox, resolution_dbu)
    cost[pin_grid] = -2.0

    # Add pin damping halos (Pin A)
    if pin_safe_a_dbu > 0 and pin_damping_step > 0:
        r0, c0, damping = get_damping_raster(
            pin_regions_a, bbox, resolution_dbu, pin_safe_a_dbu,
            pin_hardness, pin_damping_step)
        if damping.size > 0:
            conditional_overwrite(
                cost, damping, damping > 0, r0, c0,
                condition_fn=lambda existing, new: (existing > 0) & (existing < new))

    # Add pin damping halos (Pin B)
    if pin_safe_b_dbu > 0 and pin_damping_step > 0:
        r0, c0, damping = get_damping_raster(
            pin_regions_b, bbox, resolution_dbu, pin_safe_b_dbu,
            pin_hardness, pin_damping_step)
        if damping.size > 0:
            conditional_overwrite(
                cost, damping, damping > 0, r0, c0,
                condition_fn=lambda existing, new: (existing > 0) & (existing < new))
```

- [ ] **Step 4: Add sorted pair ordering**

After the Hungarian matching section, replace the routing loop setup with:

```python
    # Build matched pairs and filter dummy assignments
    pairs = []
    for idx in range(len(row_ind)):
        i, j = row_ind[idx], col_ind[idx]
        if i >= n_a or j >= n_b:
            continue
        pairs.append((i, j))

    # Sort pairs by ascending distance (short pairs first)
    if sort_pairs:
        pairs.sort(key=lambda ij: dist_matrix[ij[0], ij[1]])
```

- [ ] **Step 5: Rewrite the routing loop with per-pair pin recovery**

Replace the existing routing loop with:

```python
    # Route each matched pair with per-pair pin recovery
    result_paths = []
    for pair_idx, (i, j) in enumerate(pairs):
        pa = pins_a[i]
        pb = pins_b[j]

        start_rc = dbu_to_grid(pa[0], pa[1], bbox, resolution_dbu)
        end_rc = dbu_to_grid(pb[0], pb[1], bbox, resolution_dbu)

        # Step 1: Recover this pair's pin cells to walkable (cost=1)
        sr, sc = start_rc
        er, ec = end_rc
        sr = max(0, min(sr, cost.shape[0] - 1))
        sc = max(0, min(sc, cost.shape[1] - 1))
        er = max(0, min(er, cost.shape[0] - 1))
        ec = max(0, min(ec, cost.shape[1] - 1))
        orig_start_cost = cost[sr, sc]
        orig_end_cost = cost[er, ec]
        cost[sr, sc] = 1.0
        cost[er, ec] = 1.0

        # Step 2: Find path
        path_rc = find_path(cost, start_rc, end_rc)
        if path_rc is None:
            errors.append(f"No path found for pin pair {i}->{j}")
            # Restore pin cells
            cost[sr, sc] = orig_start_cost
            cost[er, ec] = orig_end_cost
            continue

        # Step 3: Convert grid path to dbu coordinates
        path_dbu = []
        for r, c in path_rc:
            x, y = grid_to_dbu(r, c, bbox, resolution_dbu)
            path_dbu.append([x, y])
        path_dbu = compress_path(path_dbu)

        result_paths.append({
            "points_dbu": path_dbu,
            "pin_a": list(pa),
            "pin_b": list(pb),
        })

        # Step 4: Mark path as impassable (-3) and add graduated damping
        # Build a kdb.Region from the path for rasterization
        if len(path_rc) >= 2:
            path_points = [kdb.Point(*grid_to_dbu(r, c, bbox, resolution_dbu))
                           for r, c in path_rc]
            path_obj = kdb.Path(path_points, path_width_dbu, path_width_dbu // 2,
                                path_width_dbu // 2, True)
            path_region = kdb.Region(path_obj)
            path_grid = rasterize_region_kdb(path_region, bbox, resolution_dbu)
            cost[path_grid] = -3.0

            # Add graduated path damping
            if path_damping_step > 0 and path_safe_dbu > 0:
                r0, c0, damping = get_damping_raster(
                    path_region, bbox, resolution_dbu, path_safe_dbu,
                    path_hardness, path_damping_step)
                if damping.size > 0:
                    conditional_overwrite(
                        cost, damping, damping > 0, r0, c0,
                        condition_fn=lambda existing, new: (existing > 0) & (existing < new))

        # Step 5: Restore this pair's pin cells to blocked (-2)
        cost[sr, sc] = -2.0
        cost[er, ec] = -2.0
```

- [ ] **Step 6: Commit**

```bash
git add tools/route_worker.py
git commit -m "feat: add pin-aware cost field, per-pair recovery, sorted routing"
```

## Chunk 3: Cleanup and Final Integration

### Task 6: Clean up unused imports and dead code

**Files:**
- Modify: `tools/route_worker.py` (top-level imports and dead functions)

- [ ] **Step 1: Delete old functions that are now dead code**

Remove these functions entirely (they were kept alive through Task 5 to avoid broken intermediate states):
- `rasterize_region()` (old matplotlib-based version, replaced by `rasterize_region_kdb()`)
- `build_cost_grid()` (old distance_transform version, replaced by `build_cost_grid_graduated()`)
- `add_path_damping()` (old per-pixel loop version, replaced by `get_damping_raster()` calls)

- [ ] **Step 2: Remove unused imports and dead variables**

At the top of the file, the `import math` is still needed for `math.sqrt` in the distance matrix. But remove `from scipy.ndimage import distance_transform_edt` — it was imported inside the now-deleted `build_cost_grid()`.

In `route()`, remove these dead variables that were only used by the old functions:
```python
    obs_damping_px = int(round(obs_safe_um / map_res_um))     # DELETE
    path_damping_px = int(round(path_safe_um / map_res_um))   # DELETE
    path_width_px = max(1, int(round(path_width_um / map_res_um)))  # DELETE
```

Verify the import block is:

```python
import json
import sys
import math
import numpy as np
from scipy.optimize import linear_sum_assignment
from skimage.graph import MCP_Geometric
import klayout.db as kdb
```

- [ ] **Step 3: Verify no references to old functions remain**

Search the file for `rasterize_region(`, `build_cost_grid(`, `add_path_damping(`, `obs_damping_px`, `path_damping_px`, `path_width_px` — none should be found.

- [ ] **Step 4: Commit**

```bash
git add tools/route_worker.py
git commit -m "chore: remove old rasterize/cost/damping functions and dead variables"
```

### Task 7: End-to-end verification

**Files:**
- Read: `tools/route_worker.py` (final state)
- Read: `tests/evaluate_routing.py`

- [ ] **Step 1: Read the final route_worker.py and verify completeness**

Read the entire file and verify:
- `rasterize_region_kdb()` replaces old `rasterize_region()`
- `conditional_overwrite()` is present with bounds clipping
- `get_damping_raster()` is present with bbox clipping and offset return
- `build_cost_grid_graduated()` replaces old `build_cost_grid()`
- `find_path()` uses `< 0` checks (not `np.isinf`)
- `route()` has new config params with defaults
- `route()` has no pin exclusion logic
- `route()` has pin region construction + rasterization to -2
- `route()` has pin damping halos for A and B
- `route()` sorts pairs by distance
- `route()` has per-pair pin recovery (set to 1, route, mark path -3, restore to -2)
- `route()` uses `get_damping_raster()` for path damping
- No `matplotlib` imports remain
- No `distance_transform_edt` imports remain
- No `add_path_damping()` function remains
- No `obs_damping_px`, `path_damping_px`, `path_width_px` dead variables remain
- `build_obstacle_region()` is called with `0` for safe_distance (not `obs_safe_dbu`)
- `get_damping_raster()` snaps origin to grid pixel boundaries

- [ ] **Step 2: Run a syntax/import check**

Run: `python -c "import sys; sys.path.insert(0, '.'); from tools.route_worker import route; print('Import OK')"`

- [ ] **Step 3: Commit final state if any fixups were needed**

```bash
git add tools/route_worker.py
git commit -m "fix: final integration fixups for route_worker quality enhancement"
```
