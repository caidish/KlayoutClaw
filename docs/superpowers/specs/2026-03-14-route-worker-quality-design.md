# route_worker.py Routing Quality Enhancement

**Date**: 2026-03-14
**Status**: Proposed
**Scope**: `tools/route_worker.py`

## Summary

Enhance `route_worker.py` with routing quality improvements ported from the Klayout-Router project (`.klayout_roter/Auto-routing/`). The changes introduce graduated damping cost fields, pin-aware routing with per-pair recovery, sorted routing order, and native kdb rasterization — all backward compatible with existing config schemas.

## Motivation

The current `route_worker.py` uses a binary obstacle model (obstacle=inf, free=1) with basic linear damping via `distance_transform_edt`. This produces routes that:
- Hug obstacles unnecessarily (no graduated cost field to encourage wider corridors)
- Can clip through unrelated pins (pins are carved out of obstacles globally)
- Fail on dense layouts because routing order is arbitrary (short pairs blocked by earlier long detours)
- Run slowly on complex polygons (matplotlib point-in-polygon rasterization)

Klayout-Router solves all of these with a mature cost-field architecture. We port the key techniques.

## Design

### 1. Rasterization: kdb.Region.rasterize()

**Replace**: `rasterize_region()` using `matplotlib.path.Path.contains_points`
**With**: `kdb.Region.rasterize()` — KLayout's native C++ rasterizer

```python
def rasterize_region_kdb(region: kdb.Region, bbox: kdb.Box,
                         resolution_dbu: int) -> np.ndarray:
    ncols = max(1, (bbox.width() + resolution_dbu - 1) // resolution_dbu)
    nrows = max(1, (bbox.height() + resolution_dbu - 1) // resolution_dbu)
    origin = kdb.Point(bbox.left, bbox.bottom)
    step = kdb.Vector(resolution_dbu, resolution_dbu)
    raster = np.array(region.rasterize(origin, step, ncols, nrows))
    return raster > 0
```

**Benefits**: Drops matplotlib dependency, faster on complex multi-polygon regions, consistent with the damping raster technique.

**Coordinate convention**: Same as current — row=y (bottom-to-top → 0..nrows-1), col=x.

### 2. Cost Value Convention

**Current**: obstacles=`np.inf`, free=`1.0`, damping=`1.0..11.0`
**New**: obstacles=`-1`, pins=`-2`, paths=`-3`, free=`1`, damping=`1..hardness`

MCP_Geometric treats any `cost < 0` as impassable (negative costs are skipped, producing infinite cumulative cost). Cells with `cost = 0` are traversable. Our sentinel values (-1, -2, -3) are all negative and therefore impassable.

The cost grid dtype remains `float64` for MCP_Geometric compatibility. Sentinel values are small negative integers.

### 3. Graduated Damping Cost Field

**Replace**: `build_cost_grid()` with `distance_transform_edt` linear decay
**With**: Stepped damping via iterated region sizing + rasterization

```python
def get_damping_raster(region: kdb.Region, resolution_dbu: int,
                       safe_distance_dbu: int, hardness: float,
                       n_steps: int) -> tuple[int, int, np.ndarray]:
    """Build a graduated cost raster around a region.

    Creates n_steps concentric expansions of the region. Each expansion
    adds hardness//n_steps to the cost. Result: cost ramps from 0 at
    safe_distance to hardness at the region boundary.

    Returns (r0, c0, raster) where (r0, c0) is the grid offset of the
    raster's top-left corner relative to the full cost grid. The raster
    covers only the bounding box of the sized union (not the full grid)
    for performance.
    """
    union = region.dup()
    for i in range(n_steps):
        sized = region.sized(int(safe_distance_dbu * (i + 1) / n_steps))
        union += sized

    # Compute raster over the union's own bounding box (not full grid)
    union_bbox = union.bbox()
    origin = kdb.Point(union_bbox.left, union_bbox.bottom)
    step = kdb.Vector(resolution_dbu, resolution_dbu)
    ncols = max(1, (union_bbox.width() + resolution_dbu - 1) // resolution_dbu)
    nrows = max(1, (union_bbox.height() + resolution_dbu - 1) // resolution_dbu)

    raster_raw = np.array(union.rasterize(origin, step, ncols, nrows))
    # Normalize: each concentric layer contributes hardness//n_steps (integer division)
    damping = (raster_raw // (resolution_dbu * resolution_dbu)) * (hardness // n_steps)

    # Grid offset: where this raster sits in the full cost grid
    r0 = (union_bbox.bottom - full_bbox.bottom) // resolution_dbu
    c0 = (union_bbox.left - full_bbox.left) // resolution_dbu
    return r0, c0, damping
```

**Note**: Uses integer division (`hardness // n_steps`) to match the reference Krouter implementation. The raster is positioned at `(r0, c0)` in the full cost grid via `conditional_overwrite()`.

**Note**: `rasterize_region_kdb()` (Section 1) returns a boolean and is used only for initial obstacle presence checks. `get_damping_raster()` uses `kdb.Region.rasterize()` directly to preserve raw overlap counts needed for the stepped gradient.

**Parameters** (new, with defaults):
- `obs_hardness` (default 20): Maximum cost at obstacle boundary
- `obs_damping_step` (default 4): Number of gradient steps within safe distance

**Behavior**: Paths naturally prefer wider corridors (lower cost) but can squeeze through narrow gaps when necessary (soft constraint). This replaces the current hard-cutoff + linear-decay model.

### 4. Pin-Aware Cost Field with Per-Pair Recovery

**Replace**: Global pin exclusion from obstacle region
**With**: Pin rasterization + per-pair recovery cycle

**Map preparation** (before routing loop):
1. Rasterize obstacle region → cost = -1
2. Add obstacle damping raster (Section 3)
3. Rasterize all pin shapes → cost = -2 (overwriting everything including obstacle damping)
4. Add pin damping rasters with asymmetric safe distances:
   - Pin A: `pin_safe_distance_a_um` (default 5.0)
   - Pin B: `pin_safe_distance_b_um` (default 5.0)

**Per-pair routing cycle** (for each matched pair i):
1. Set pair i's pin cells → cost = 1 (temporarily walkable)
2. Run MCP_Geometric pathfinding
3. Mark routed path → cost = -3, add path damping raster
4. Re-mark pair i's pin cells → cost = -2 (re-block)

**Removed**: The entire `pin_exclusion` logic (lines 320-347 of current code) that creates clearance circles and subtracts them from the obstacle region.

**Pin region construction**: For each pin center, create a small box (2*resolution_dbu square) as the pin's footprint on the cost grid. This is simpler than the reference Krouter (which uses actual pin polygon shapes from PinMatcher). The trade-off is acceptable because: (a) `route_worker.py` only extracts pin centers, not full shapes; (b) the pin damping halos provide the real routing protection around the pin area; and (c) pin shapes in our use case are typically small markers, not large pads.

**Parameters** (new, with defaults):
- `pin_safe_distance_a_um` (default 5.0): Halo radius around Pin A shapes
- `pin_safe_distance_b_um` (default 5.0): Halo radius around Pin B shapes
- `pin_hardness` (default 20): Maximum cost at pin halo boundary
- `pin_damping_step` (default 4): Number of gradient steps for pin halos

### 5. Sorted Routing Order

After Hungarian matching, sort matched pairs by ascending Euclidean distance before routing.

```python
pairs = list(zip(row_ind, col_ind))
pairs = [(i, j) for i, j in pairs if i < n_a and j < n_b]
pairs.sort(key=lambda ij: dist_matrix[ij[0], ij[1]])
```

Short pairs route first because:
- They need less space and are less likely to cause detours
- Their path damping halos are smaller, blocking less space for subsequent routes
- Empirically produces higher success rates on dense layouts

**Parameter**: `sort_pairs` (default `True`) — can be disabled if caller has a specific routing order preference.

### 6. Vectorized Path Damping

**Replace**: `add_path_damping()` with its O(pixels * radius^2) per-pixel loop
**With**: Same `get_damping_raster()` technique used for obstacles

After routing each path:
1. Create `kdb.Region` from the path polygon (using path points + width)
2. Call `get_damping_raster()` with `path_safe_distance`, `path_hardness`, `path_damping_step`
3. Apply the damping raster to the cost grid using conditional overwrite (only update cells where new cost > existing cost > 0)

**Parameters** (modified defaults):
- `path_hardness` (default 10): Maximum cost near routed paths
- `path_damping_step` (default 5): Number of gradient steps for path halos
- `path_safe_distance_um` (existing, default 5.0): Unchanged

### 7. Conditional Overwrite Helper

Port the `overwrite()` pattern from Klayout-Router's Map class:

```python
def conditional_overwrite(cost: np.ndarray, content: np.ndarray,
                          content_mask: np.ndarray,
                          r0: int, c0: int,
                          condition_fn=None):
    """Update cost grid subregion where both content_mask and condition_fn are true.

    Args:
        cost: Full cost grid (modified in-place).
        content: New values to write (same shape as the subregion).
        content_mask: Boolean mask — which cells in content are candidates.
        r0, c0: Top-left offset of the subregion in the full cost grid.
        condition_fn: Function (existing_slice, content) -> bool mask.
            Receives the existing cost subregion AND the content array,
            enabling content-dependent conditions like "only increase".
            Default: always True.
    """
    nrows, ncols = content.shape
    region = cost[r0:r0 + nrows, c0:c0 + ncols]
    if condition_fn is not None:
        mask = condition_fn(region, content) & content_mask
    else:
        mask = content_mask
    region[mask] = content[mask]
```

This enables "supplemental overwriting" — the key pattern from the reference:
- **Obstacle damping**: `condition_fn = lambda existing, new: existing >= 0` — only overwrite free/walkable cells
- **Pin/path damping**: `condition_fn = lambda existing, new: (existing > 0) & (existing < new)` — only increase positive costs, never decrease. This prevents weaker pin damping from overriding stronger obstacle damping.

### 8. Config Schema (Backward Compatible)

New parameters with defaults:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `obs_hardness` | float | 20.0 | Max cost at obstacle boundary |
| `obs_damping_step` | int | 4 | Gradient steps for obstacle damping |
| `pin_safe_distance_a_um` | float | 5.0 | Halo radius around Pin A |
| `pin_safe_distance_b_um` | float | 5.0 | Halo radius around Pin B |
| `pin_hardness` | float | 20.0 | Max cost at pin halo boundary |
| `pin_damping_step` | int | 4 | Gradient steps for pin halos |
| `path_hardness` | float | 10.0 | Max cost near routed paths |
| `path_damping_step` | int | 5 | Gradient steps for path halos |
| `sort_pairs` | bool | true | Sort pairs by distance before routing |

Existing parameters unchanged: `obs_safe_distance_um`, `path_safe_distance_um`, `path_width_um`, `map_resolution_um`, `dbu`.

## Function Structure (After Enhancement)

```
route(config) → dict
├── parse config (existing + new params)
├── load GDS, find cell, extract pins (unchanged)
├── build_obstacle_region() (unchanged)
├── compute bbox (unchanged)
├── rasterize_region_kdb() — obstacles → boolean grid        [NEW]
├── build_cost_grid_graduated() — obstacles + damping         [NEW]
│   ├── mark obstacles as -1
│   └── get_damping_raster() for obstacle halo
├── add_pin_costs() — pin footprints + halos                  [NEW]
│   ├── mark pin cells as -2
│   └── get_damping_raster() for each pin set (A, B)
├── Hungarian matching (unchanged)
├── sort pairs by distance                                    [NEW]
├── for each pair:
│   ├── recover_pins() — set pair's cells to 1                [NEW]
│   ├── find_path() via MCP_Geometric (updated for negative sentinels)
│   ├── mark_path() — set path cells to -3                    [NEW]
│   ├── add_path_damping_graduated() via get_damping_raster() [NEW]
│   └── restore_pins() — set pair's cells back to -2          [NEW]
├── compress paths (unchanged)
└── return result dict (unchanged)
```

## find_path() Updates

The current `find_path()` checks `np.isinf(cost[sr, sc])` to detect blocked start/end cells. Under the new negative sentinel convention, this must change:

1. Replace `np.isinf()` checks with `cost[sr, sc] < 0` checks
2. Add a defensive guard: if start or end cell cost is still negative after the per-pair recovery step (due to rasterization rounding mismatch), log an error and return `None` rather than proceeding. scikit-image's MCP_Geometric produces silently wrong results when started on a negative-cost cell (cumulative cost propagates as 0).

```python
if cost[sr, sc] < 0:
    cost[sr, sc] = 1.0  # recovery
if cost[er, ec] < 0:
    cost[er, ec] = 1.0  # recovery
```

## Out of Scope

The following Klayout-Router features are intentionally **not ported** in this enhancement:
- **Self-adaptive multi-round routing** (`self_adaptive_route()`, `adapt_path_density()`) — path density feedback with re-routing rounds. Could be added later.
- **Clockwise sort** — angular ordering around center. We implement distance-based sort only.
- **Manual match reading** (`read_match()`) — user-drawn GDS connections for manual pin pairing. Not applicable to MCP subprocess workflow.
- **Animation/visualization** — generator-based frame yielding. Not needed in subprocess context.

## Removed Code

- `rasterize_region()` (matplotlib-based) — replaced by `rasterize_region_kdb()`
- `build_cost_grid()` — replaced by `build_cost_grid_graduated()`
- `add_path_damping()` (per-pixel loop) — replaced by graduated raster approach
- Pin exclusion logic (lines 320-347) — replaced by pin-aware cost field
- `from matplotlib.path import Path as MplPath` import

## Dependencies

**Removed**: `matplotlib` (no longer needed for rasterization), `scipy.ndimage` (distance_transform_edt no longer used)
**Unchanged**: `numpy`, `scipy.optimize` (linear_sum_assignment), `scikit-image` (MCP_Geometric), `klayout.db`

## Testing

Existing tests (`evaluate_routing.py`, `test_autoroute.sh`) should continue to work since the output format is unchanged. Quality improvements should be visible as:
- Paths maintain better spacing from obstacles and each other
- Fewer "no path found" errors on dense layouts
- No paths clipping through unrelated pins
