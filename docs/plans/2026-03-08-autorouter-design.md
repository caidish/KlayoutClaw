# Autorouter MCP Tool Design

**Date:** 2026-03-08
**Status:** Approved

## Overview

Add an `auto_route` MCP tool to KlayoutClaw that automatically routes connections between pin pairs in KLayout layouts. Adopts the tech stack from Klayout-Router (numpy, scipy, scikit-image) via a hybrid subprocess architecture.

## Architecture

### Hybrid Subprocess Model

The MCP server (inside KLayout) remains dependency-free. Heavy computation runs in a subprocess:

```
KLayout (pya.QTcpServer)              Subprocess (route_worker.py)
─────────────────────────             ──────────────────────────────
auto_route tool called
  1. Save layout to temp GDS  ──────>  1. Load GDS via klayout.db
  2. Spawn subprocess                   2. Extract pins from layers
  3. Wait for result                    3. Rasterize obstacles
                                        4. Hungarian pin matching
                                        5. MCP_Geometric pathfinding
  4. Read paths from JSON    <──────    6. Output paths as JSON
  5. Insert paths via pya
  6. Refresh view
```

### Communication

- **Input:** Temp GDS file + JSON config (layers, constraints)
- **Output:** JSON file with path coordinates (list of point lists in dbu)
- Simple file-based IPC — debuggable, no sockets needed

### Dependencies

**Inside KLayout (MCP server):** No new deps — stdlib + pya only.

**Subprocess (route_worker.py):** Requires conda environment with:
- `klayout` (klayout.db package, standalone)
- `numpy`
- `scipy`
- `scikit-image`

## MCP Tool: `auto_route`

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `pin_layer_a` | string | yes | — | Pin layer A (e.g. "102/0") |
| `pin_layer_b` | string | yes | — | Pin layer B (e.g. "111/0") |
| `obstacle_layers` | string[] | no | [] | Layers to treat as obstacles |
| `output_layer` | string | no | "10/0" | Layer for routed paths |
| `path_width` | float | no | 1.0 | Path width in µm |
| `obs_safe_distance` | float | no | 5.0 | Min distance from obstacles (µm) |
| `path_safe_distance` | float | no | 5.0 | Min distance between paths (µm) |

### Return Value

JSON with:
- `routed_pairs`: number of pairs routed
- `total_pins`: number of pins found on each layer
- `status`: "success" or "partial" (if some pairs failed)
- `errors`: list of error messages (if any)

## Routing Algorithm

Adopted from Klayout-Router's approach:

1. **Pin extraction:** Read shapes from pin_layer_a and pin_layer_b, extract center coordinates
2. **Obstacle rasterization:** Convert obstacle layer polygons to a 2D numpy cost grid
3. **Hungarian matching:** Use `scipy.optimize.linear_sum_assignment` to find optimal pin pairings (minimizes total routing cost)
4. **Cost-based pathfinding:** Use `skimage.graph.MCP_Geometric` (Dijkstra on weighted grid) to find minimum-cost paths
5. **Damping rasters:** Gradually increasing cost near obstacles and existing paths (soft constraints)
6. **Path compression:** Remove redundant waypoints, keep only inflection points
7. **Output:** Convert grid paths back to layout coordinates, write as JSON

### Key Design Choices (from Klayout-Router)

- **Soft constraints** over hard blocking — cost penalties instead of impassable walls
- **Integer arithmetic** in database units to avoid floating-point errors
- **Clockwise pin sorting** to minimize path crossings
- **Multi-round adaptive routing** — re-routes with density penalties between rounds

## File Structure

```
tools/
  route_worker.py              # Subprocess routing engine
tests/
  create_hallbar_unrouted.py   # Hall bar with pin markers, no traces
  evaluate_routing.py          # Structural validation of routed GDS
  test_autoroute.sh            # E2E test script
```

## Test Plan

### Test Device: Hall Bar with Routing Challenge

Extends existing Hall bar test:
- Layer 1/0 (Mesa): channel + 6 side probes (obstacles)
- Layer 3/0 (Pads): 8 bonding pads (obstacles)
- Layer 102/0 (Pin_A): markers at probe tips
- Layer 111/0 (Pin_B): markers at pad centers
- No pre-drawn metal traces — router must create them

### Validation Tiers

| Tier | Script | Checks |
|------|--------|--------|
| Structural | `evaluate_routing.py` | All pins connected, paths on correct layer, no obstacle overlap |
| Visual | `gds_to_image.py` → PNG | Routes look reasonable, no obvious crossings |
| E2E | `test_autoroute.sh` | Full pipeline: create → route → evaluate → screenshot |

### Structural Validation Criteria

- Number of routed paths == number of pin pairs
- Each path starts/ends within pin marker bounding box
- No path polygon intersects obstacle regions
- Paths are on the specified output layer
- Path width matches specification
