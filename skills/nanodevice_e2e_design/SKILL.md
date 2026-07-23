---
name: nanodevice_e2e_design
description: Orchestrate end-to-end nanodevice design from user query through optional flake detection, material analysis, device geometry creation, routing, evaluation, and save. Device-agnostic methodology -- the agent derives physics rules from device type and available materials.
---

# nanodevice_e2e_design -- End-to-End Device Design

A device-agnostic methodology for designing nanodevices on material regions in KLayout. The agent follows 7 reasoning steps, deriving device-specific physics rules from context and user input. No device type is hardcoded.

**This is a pure-text orchestrator skill.** No scripts directory. The agent uses MCP tools and sub-skills at each step.

---

## Pipeline Overview

| # | Step | What the agent does | Skip when |
|---|------|---------------------|-----------|
| 1 | QUERY | Check if required info is missing; ask only if needed | User provided everything in initial prompt |
| 2 | PREPARE | Run flake detection + GDS alignment if microscope images provided; otherwise verify existing layout | No images provided / layout already prepared |
| 3 | ANALYZE | Study material regions, compute overlaps/exclusions, identify design-relevant zones | Never |
| 4 | DESIGN | Create device geometry via `execute_script` | Never |
| 5 | ROUTE | Use `auto_route` tool to connect device contacts to bonding pads | Device has no external contacts |
| 6 | EVALUATE | Run nanodevice DRC + metric evaluator, visually inspect failures, iterate | Never |
| 7 | SAVE | Export GDS + write result.json summary | Never |

Each step has a gate condition. Maximum 2 retries per step. If a step fails after retries, report to the user.

---

## Step 1: QUERY

Check the user's initial prompt against this checklist. Only ask about missing items -- skip this step entirely if all required info is provided.

| Parameter | Required? | Notes |
|-----------|-----------|-------|
| Device type | Yes | What to build (Hall bar, FET, QD, etc.) |
| Material regions | Yes | Which layers have which materials, or microscope images to detect them |
| Layer assignments | Yes | Which layers to use for each design component |
| pixel_size | If images provided | Microns per pixel, needed for detection/alignment |
| Device-specific constraints | If any | Gates, contacts, pin count, dimensions |
| Output path | No | Defaults to source directory |

**Gate:** All required parameters are known. Proceed.

---

## Step 2: PREPARE

Conditional step -- only runs if microscope images are provided.

**If microscope images are provided:**
1. Validate pixel_size via `validate_pixel_size` tool
2. Dispatch subagent to run `nanodevice_flakedetect` pipeline (align, detect, combine)
3. Dispatch subagent to run `nanodevice_gdsalign` if a GDS template is provided
4. Dispatch subagent to run `nanodevice_flakedetect_commit` to insert material polygons into KLayout

**If starting from an existing layout:**
- Verify expected material regions are present via `get_layout_info`

**Gate:** Material regions exist as polygons on their designated layers in KLayout.

---

## Step 3: ANALYZE

**Prerequisite:** Material contours must exist as polygons in KLayout, and any reference images must be loaded as background overlays. If they are not present, go back to PREPARE or ask the user.

The agent studies available geometry to inform design decisions:

1. Use `get_layout_info` to identify which layers have material regions
2. Use `execute_script` with `pya.Region` to compute overlaps, exclusions, bounding boxes
3. Use `screenshot` to visually inspect material regions and plan device placement
4. Identify which material zones are relevant for the specific device type

The agent derives material analysis logic from its physics knowledge of the device type. For example:
- A Hall bar needs graphene-graphite overlap for the channel
- A QD needs a gate-definable region
- A JJ needs a superconductor-insulator-superconductor stack

> ### 鈿狅笍 Coordinate-frame contract (read before touching any flake coords)
>
> If `nanodevice_gdsalign` has run, the flake polygons already committed in
> KLayout (the flake layers 鈥?L10-L13 by default, but confirm the mapping from
> your own commit) are in the **GDS reference frame** and are the **single
> source of truth**. Build the device against THOSE 鈥?read them back with
> `pya.Region` in absolute coordinates; do not re-derive flake geometry from raw
> JSON, and treat the committed flake layers as read-only reference (do not
> clear and re-insert flakes from an image-frame source).
>
> - The ONLY valid JSON flake source after gdsalign is
>   `traces_gds.json` 鈫?per-entry **`contour_gds`**. **NEVER** read
>   `contour_um` (the un-warped image frame): it places the device hundreds of
>   um off the GDS template and scores ~0 (the QH07 failure). Do not invent a
>   `device_geom.json` with a hand-labeled `frame`.
> - On **session recovery** (live layout empty / `result.gds` missing), rebuild
>   flakes from `traces_gds.json::contour_gds` ONLY 鈥?never from `contour_um`.
> - If the markers/flakes look off-frame, that is an alignment failure: re-run
>   gdsalign (see its validated-commit contract), do not patch coordinates by
>   hand.

**Gate:** The agent has identified where the device should be placed and what material constraints apply, working in the GDS reference frame (the committed L10-L13 flakes).

---

## Step 4: DESIGN

Create device geometry using `execute_script`. What to create depends on the device type -- the agent applies its physics knowledge:

- Design the active region (mesa, channel, dot, junction, etc.)
- Place contacts appropriate to the device type
- Place gates if needed (topgate, sidegate, backgate, split-gate, etc.)
- Ensure all components respect material region boundaries from ANALYZE

The skill does NOT prescribe dimensions, shapes, or layer numbers. The agent derives these from:
- Device type and physics requirements
- Available material regions from ANALYZE
- Layer assignments agreed in QUERY
- User-specified constraints

Use `screenshot` after each major geometry addition to visually verify placement.

**Gate:** Device geometry is present on designated layers. Visual inspection via `screenshot` confirms correct placement.

**Frame guard (mandatory):** before passing this gate, assert with `pya.Region` that the active region (mesa/channel) **overlaps the material region the device physics requires** 鈥?the SAME region you identified in ANALYZE (for a Hall bar that is the graphene鈭ゞraphite overlap; for a single-material device it is that one flake region; for a JJ/QD it is whatever its stack needs). Read those regions from the committed flake layers using the layer numbers YOU assigned (do not hard-code L11/L13 鈥?confirm the mapping from your own commit). Procedure: (1) confirm the target flake layer(s) are **non-empty** 鈥?an absent material is a detection/commit problem, not a frame error, so skip the guard for it; (2) if the target region is non-empty but its overlap with the active region is empty, the device is in the **wrong frame** 鈥?STOP, fix the coordinate source (see the frame contract in ANALYZE) or re-run gdsalign, and do not proceed to ROUTE.

> ### 鈿狅笍 Material-containment guard 鈥?contacts/arm-tips must land ON the flake (not just the mesa body)
>
> The frame guard above only checks that the mesa *body* overlaps the flake. It
> does **not** catch a mesa whose **probe arms / contact patches extend PAST the
> flake boundary** 鈥?the central body still overlaps, so the frame guard passes,
> yet the arm-tip ohmic contacts sit in void and every material-keyed metric
> (`contacts_in_regions`, `connectivity`, `mesa_probes`, `mesa_on_overlap`)
> silently collapses. This is a distinct, common failure (it caps a structurally
> clean, correctly-framed device well below where it should score).
>
> **Principle 鈥?material-correctness outranks cosmetic shape.** A device's
> contacts and the arm tips that carry them MUST sit inside the required flake
> region. **Never lengthen a probe arm past the flake edge to satisfy a shape
> target** (e.g. a low-`solidity` / "make the mesa concave" goal). Solidity,
> aspect ratio, and similar shape metrics are cosmetic and rank *below* keeping
> every contact on material. If the two conflict, shorten the arm and keep the
> contact on the flake.
>
> **Check before passing this gate** (in addition to the frame guard): with
> `pya.Region`, intersect the **contact-patch** shapes (and the probe-arm tips,
> if separate) with the required flake region. If any contact patch's in-flake
> area fraction is below ~0.9, that arm is **over-extended** 鈥?pull it back so
> the patch is fully on the flake, then re-check. (Equivalently, once you have
> committed flakes you can run `evaluate_design` with `arm_material_class` /
> `component_containment` on the contact component and require a high fraction 鈥?> the same measurement the official scorer makes.) Do not proceed to ROUTE while
> contacts hang off the flake.

---

## Step 5: ROUTE

If the device has contacts that need fan-out to bonding pads:

1. Place pin markers at contact positions via `execute_script` (on a temporary layer)
2. Place bonding pads if not already present (via `execute_script`)
3. Place pin markers at pad positions (on a temporary layer)
4. Call `auto_route` MCP tool with appropriate parameters:
   - `pin_layer_a`: contact pin layer
   - `pin_layer_b`: pad pin layer
   - `output_layer`: route layer
   - `obstacle_layers`: device geometry layers to avoid
   - Line width and safe distance as needed
   - For dense fan-out, use `dry_run=true` first to inspect the ordered-loop assignment, then use `pin_pairs_override` if the required device topology differs.
5. For failed pairs, write custom routing via `execute_script`
6. Clean up temporary pin marker layers

The agent decides routing topology, line widths, and obstacle avoidance based on device layout. The `nanodevice_routing` skill is available as a reference for multi-window EBL routing patterns, but is not required.

**Gate:** All contacts connected to bonding pads. No route crossings.

---

## Step 6: EVALUATE

Run the `evaluate_design` MCP tool as a nanodevice DRC + metric pass, with a check configuration composed by the agent based on what it designed.

**Available check primitives:**

| Primitive | Args | Measures |
|-----------|------|----------|
| `component_overlap` | component, region, region_op | Fraction of component area overlapping with region |
| `component_containment` | component, region, region_op | Fraction of component area contained within region |
| `bulk_containment` | component, bulk_region/materials, region_op, core_bbox | Fraction of a device body/core inside a caller-declared bulk region |
| `arm_material_class` | component, classes, containment_threshold | Fraction of shapes landing in exactly one material class |
| `material_overlap_report` | materials | Pairwise and multi-way material-region report |
| `contact_isolation` | component | Fraction of route pairs that don't cross (promotes `crossing_pairs`) |
| `connectivity` | contact_component, pad_component, route_component, tolerance | Fraction of contacts reaching pads |
| `route_endpoints` | route_component, target_components, tolerance | Fraction of route endpoints on valid targets |
| `route_material_compat` | (optional graphene/graphite keys) | Fraction of ohmic routes that stay OFF the opposing flake material (promotes `violating_routes`) |
| `adjacency` | component_a, component_b, tolerance | Fraction of A shapes within tolerance of B |
| `solidity` | component, threshold, direction | Shape solidity above/below threshold |
| `spacing` | component_a, component_b, min_distance | Fraction of pairs meeting min distance |

The `region` arg can be a single layer_map key or a list of keys combined via `region_op` (union, intersection, difference).

> **Always include `route_material_compat` once routes exist** (for devices with
> distinct ohmic materials, e.g. a Hall bar's graphene vs graphite contacts). A
> route that crosses the opposing flake material is an electrical short and is a
> common, easily-missed cause of a low score. The check returns `violating_routes`
> [{route_id, contact_kind, crossed_material, area_um2}] 鈥?reroute each violator
> (e.g. fan it out away from the opposing flake) and re-check, exactly as you use
> `contact_isolation`'s `crossing_pairs` to fix route-route crossings.

**Example check list for a Hall bar:**
```json
{
  "checks": [
    {"name": "component_containment", "args": {"component": "mesa", "region": ["graphene", "graphite"], "region_op": "intersection"}, "weight": 0.2},
    {"name": "adjacency", "args": {"component_a": "contact_patch", "component_b": "mesa", "tolerance": 2.0}, "weight": 0.15},
    {"name": "solidity", "args": {"component": "mesa", "threshold": 0.5, "direction": "below"}, "weight": 0.15},
    {"name": "contact_isolation", "args": {"component": "contact_route"}, "weight": 0.15},
    {"name": "connectivity", "args": {"contact_component": "contact_patch", "pad_component": "bonding_pad", "route_component": "contact_route"}, "weight": 0.15},
    {"name": "route_endpoints", "args": {"route_component": "contact_route", "target_components": ["contact_patch", "mesa", "bonding_pad"]}, "weight": 0.1},
    {"name": "spacing", "args": {"component_a": "contact_patch", "min_distance": 1.0}, "weight": 0.1}
  ]
}
```

Also take a `screenshot` for visual inspection.

- If score >= 0.80, proceed to SAVE
- If score < 0.80, identify lowest-scoring check and iterate
- Maximum 2 retries

**Gate:** Evaluation score >= 0.80 and visual inspection passes.

---

## Step 7: SAVE

> **BENCHMARK FORMAT WARNING**: If this task is a benchmark run, the required `result.json` schema is defined in the **task prompt itself**. Re-read the task prompt and use the exact schema it specifies. Do **NOT** fall back to the nested general-purpose format below 鈥?a schema mismatch makes the evaluator score 0.0.

> ### Save often 鈥?and make sure your BEST result is the one on disk
>
> Follow the benchmark's own rule: **save early, save often.** A partial result
> always scores higher than no result; an agent that finishes a good device but
> times out before saving scores 0.0. So:
>
> 1. **Save as soon as you have a complete, routed device**, then **re-save
>    whenever you reach a higher EVALUATE score.** Overwriting `output/result.gds`
>    is free 鈥?`save_layout` writes it atomically (temp + rename), so a verifier
>    never observes a half-written GDS; you do not need staging files.
> 2. **Write `result.json` in the exact schema the task prompt specifies** (it is
>    usually a flat object with no `score` field 鈥?do not add one). Keep it in
>    sync with the saved GDS.
> 3. **Don't let a later, worse iteration become the final artifact.** Only
>    re-save when EVALUATE confirms the new design is at least as good; if an
>    experiment makes things worse, revert the geometry before the next save.
> 4. **Confirm the save landed.** Read back `output/result.gds` size /
>    `get_layout_info`. If a tool returns a suspicious, stale, or repeated result
>    (a wedged bridge), call `ping` to check the server is alive, then re-save.

1. Save your BEST design atomically per the save-best block above (stage `.new`, promote both with one `mv`).
2. Write `result.json` 鈥?**check if the task or benchmark specifies a required format first and use that exactly**. If no format is specified, use this general-purpose default:

**General format (non-benchmark):**
   - `device_type`: from QUERY
   - `layer_map`: all layer assignments used (e.g. `{"mesa": [{"layer": 20, "datatype": 0}], ...}`)
   - `score`: final evaluation score from EVALUATE
   - `pixel_size`: if applicable
   - `pipeline_status`: per-step pass/fail/retry counts
   - `checks`: the evaluation check configuration used
   - `feedback`: design notes and any user interventions

**Gate:** GDS file written and result.json contains all required fields.

---

## Retry Protocol

Maximum 2 retries per step. When a gate fails:

| Failed Step | Retry Action |
|-------------|-------------|
| PREPARE | Re-run detection with adjusted parameters |
| ANALYZE | Re-inspect with different region computations |
| DESIGN | Redesign failing component based on gate feedback |
| ROUTE | Re-route with adjusted parameters or manual fallback |
| EVALUATE | Re-run failing design sub-step per lowest-scoring check |

If any step exhausts retries, stop and report to the user.

---

## MCP Wedged? Restart KLayout

MCP is the only path by design -- there is no standalone subprocess fallback. If an MCP call hangs, times out, or stops responding partway through the pipeline, the recovery is to restart KLayout, not to route around it.

**Don't stand and wait. Restart KLayout:**

1. `pkill -f klayout` -- kill any running KLayout processes.
2. `open /Applications/klayout.app` -- relaunch (standalone command; do **not** chain with `&&`).
3. Poll `http://127.0.0.1:8765/mcp` until the server responds (typically 5-10 s after launch).
4. Re-run the failed call.

Keeping MCP as the single canonical path forces real bugs to surface instead of being masked by a fallback. After the restart, resume the pipeline from the step that was in flight when the call wedged -- earlier completed steps remain valid in the saved layout if the layout was checkpointed; otherwise, restart from the last gate that was satisfied.

---

## What This Skill Prescribes

- Step ordering (QUERY -> PREPARE -> ANALYZE -> DESIGN -> ROUTE -> EVALUATE -> SAVE)
- Gate conditions before proceeding
- Retry protocol (max 2 per step)
- Which MCP tools to use: `execute_script`, `auto_route`, `evaluate_design`, `screenshot`, `get_layout_info`, `save_layout`, `validate_pixel_size`
- Query checklist for completeness

## What This Skill Does NOT Prescribe

- Specific device geometries or dimensions
- Specific layer numbers (agreed with user)
- Specific material assumptions (agent derives from device type)
- Specific evaluation weights (agent composes per device)
- Specific routing topology (agent decides)

---

## Conventions

- **Conda env:** `instrMCPdev` (has opencv, numpy, scipy, sklearn, shapely, gdstk)
- **Pixel coordinates:** image origin at top-left; KLayout uses center origin with Y-flip
- **Layer references:** always as `layer/datatype` (e.g., `11/0`)
- **Output directory:** defaults to source image directory if not specified
- **Sub-skill dispatch:** flakedetect and gdsalign sub-skills run as subagents reading their own SKILL.md
