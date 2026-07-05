/**
 * KLayout nanodevice domain tools.
 *
 * These tools wrap multi-step skill pipelines (flakedetect, gdsalign, routing).
 * They do NOT generate inline pya code — instead they return structured JSON
 * instructions telling the agent which scripts to run, in what order, and what
 * to check.  The agent executes the scripts via bash.
 *
 * Skills NOT registered here (by design):
 *   - nanodevice_e2e_design  (top-level orchestrator, not an atomic tool)
 *   - e2e_judge              (test harness, not a design tool)
 *   - trd                    (dev methodology, not a design tool)
 */

import type { NamespacedTool } from "../../mcp/types.js";

const PREFIX = "klayout";

interface CodeGenTool extends NamespacedTool {
  generateCode: (args: Record<string, unknown>) => string;
}

function makeNanoTool(
  group: string,
  toolName: string,
  description: string,
  properties: Record<string, { type: string; description?: string; items?: { type: string }; default?: unknown }>,
  required: string[],
  generateCode: (args: Record<string, unknown>) => string,
): CodeGenTool {
  return {
    name: `${PREFIX}_${group}_${toolName}`,
    originalName: toolName,
    serverKey: PREFIX,
    group,
    description,
    inputSchema: { type: "object" as const, properties, required },
    generateCode,
  };
}

// ─── helpers ────────────────────────────────────────────────────────────────

/**
 * Serialize a JS value as a Python literal expression.
 *
 * Critical: `JSON.stringify` emits `true`/`false`/`null`, but Python's `exec()`
 * only knows `True`/`False`/`None`. Dumping JSON into pya code raises NameError.
 * This walker produces strings that `exec()` can evaluate directly.
 *
 * String handling: Python's literal syntax accepts JSON-style double-quoted
 * strings with standard backslash escapes (\n, \t, \", \\, \uXXXX). We delegate
 * to `JSON.stringify` for the string case — no ambiguity, no token-regex risk.
 */
export function pyLiteral(v: unknown): string {
  if (v === null || v === undefined) return "None";
  if (v === true) return "True";
  if (v === false) return "False";
  if (typeof v === "number") {
    if (Number.isNaN(v)) return "float('nan')";
    if (v === Infinity) return "float('inf')";
    if (v === -Infinity) return "float('-inf')";
    return String(v);
  }
  if (typeof v === "bigint") return v.toString();
  if (typeof v === "string") return JSON.stringify(v);
  if (Array.isArray(v)) {
    return "[" + v.map(pyLiteral).join(", ") + "]";
  }
  if (typeof v === "object") {
    const entries = Object.entries(v as Record<string, unknown>).map(
      ([k, val]) => `${JSON.stringify(k)}: ${pyLiteral(val)}`,
    );
    return "{" + entries.join(", ") + "}";
  }
  // Fallback: serialize to JSON-safe form
  return JSON.stringify(v);
}

/**
 * Build a Python-literal `result = ...` assignment wrapped as a workflow_plan.
 *
 * Every nanodevice domain tool returns a *plan* (scripts to run, pipeline
 * metadata), not execution results. To make this unambiguous to the agent,
 * every payload is wrapped with `type: "workflow_plan"` as the first key —
 * the agent can see at a glance this is a plan, not a tool execution result.
 *
 * Also fixes the Q1 JSON→Python literal bug (true/false/null → True/False/None).
 */
function instructionResult(obj: Record<string, unknown>): string {
  const wrapped: Record<string, unknown> = { type: "workflow_plan", ...obj };
  return `result = ${pyLiteral(wrapped)}`;
}

// ─── Flake Detection ────────────────────────────────────────────────────────

function registerFlakedetectTools(): CodeGenTool[] {
  return [
    // --- align ---
    makeNanoTool(
      "nanodevice_flakedetect",
      "align",
      [
        "**PLANNER, NOT EXECUTOR.** Returns a `workflow_plan` listing bash commands you must run yourself (via the bash tool).",
        "Register source microscope images to the full_stack coordinate system.",
        "Uses SIFT for same-substrate alignment (bottom→full_stack) and",
        "Chamfer+DE for cross-substrate alignment (top PDMS→full_stack SiO2).",
        "Use after gathering source images and before material detection.",
      ].join(" "),
      {
        full_stack_image: { type: "string", description: "Path to full-stack reference image" },
        bottom_part_image: { type: "string", description: "Path to bottom-part image (SIFT alignment)" },
        top_part_image: { type: "string", description: "Path to top-part image (Chamfer alignment)" },
        pixel_size: { type: "number", description: "Microns per pixel" },
        min_inliers: { type: "number", description: "Minimum RANSAC inliers for SIFT (default: 20, lower to 10 for sparse images)", default: 20 },
        scalebar_bottom: { type: "number", description: "Fraction of image height to mask from bottom for scalebar exclusion (default: 0.08)", default: 0.08 },
        scalebar_right: { type: "number", description: "Fraction of image width to mask from right for scalebar exclusion (default: 0.20)", default: 0.20 },
        output_dir: { type: "string", description: "Output directory for alignment artifacts" },
      },
      ["full_stack_image", "pixel_size", "output_dir"],
      (args) => {
        const scriptsDir = "skills/nanodevice_flakedetect_align/scripts";
        return instructionResult({
          action: "run_alignment_scripts",
          pipeline: "nanodevice_flakedetect_align",
          skill_doc: "skills/nanodevice_flakedetect_align/SKILL.md",
          read_first: "Read the skill_doc for detailed workflow, visual checks, and retry protocol.",
          scripts: {
            "1_sift_align": {
              command: `python ${scriptsDir}/sift_align.py --source <bottom_part_image> --target <full_stack_image> --pixel-size <px> --min-inliers ${args.min_inliers ?? 20} --scalebar-bottom ${args.scalebar_bottom ?? 0.08} --scalebar-right ${args.scalebar_right ?? 0.20} --output-dir <out>/align/`,
              when: "Always run if bottom_part_image provided (same-substrate → fast SIFT)",
              check: "Verify ≥min-inliers inliers and scale ≈ 1.0. Masks bottom-right scalebar region automatically.",
            },
            "2_source_contour": {
              command: `python ${scriptsDir}/source_contour.py --image <top_part_image> --output-dir <out>/align/`,
              when: "Always run if top_part_image provided",
              check: "View output image — contour should trace the full flake outline",
            },
            "3_footprint": {
              command: `python ${scriptsDir}/footprint.py --source <top_part_image> --target <full_stack_image> --bottom <bottom_part_image> --source-contour <out>/align/source_contour.npy --source-mask <out>/align/source_mask.png --pixel-size <px> --output-dir <out>/align/`,
              when: "Always run for cross-substrate alignment",
              check: "CRITICAL: View 03_footprint_candidates.png. Default candidate (#1) is often WRONG. May need --candidate-rank 2 or 3",
            },
            "4_sweep": {
              command: `python ${scriptsDir}/sweep.py --source-contour <out>/align/source_contour.npy --source-mask <out>/align/source_mask.png --footprint-contour <out>/align/footprint_contour.npy --footprint-mask <out>/align/footprint_mask.png --target-image <full_stack_image> --pixel-size <px> --output-dir <out>/align/`,
              when: "After footprint",
              check: "View 05_sweep_grid.png and candidate images",
            },
            "5_refine": {
              command: `python ${scriptsDir}/refine.py --source-contour <out>/align/source_contour.npy --source-mask <out>/align/source_mask.png --footprint-contour <out>/align/footprint_contour.npy --footprint-mask <out>/align/footprint_mask.png --target-image <full_stack_image> --rot-hint <picked_rot> --pixel-size <px> --output-dir <out>/align/`,
              when: "After user selects rotation from sweep results",
              check: "fwd_chamfer < 2.5um, IoU > 0.70, top_containment > 0.90, outside_fraction < 0.10",
              important: "MUST run as foreground blocking bash with timeout=1200000 (20 min). DO NOT background this.",
            },
          },
          outputs: [
            "warp_sift_bottom.npy — SIFT warp matrix",
            "warp_top.npy — Chamfer warp matrix",
            "footprint_mask.png, footprint_contour.npy",
            "alignment_report.json — metrics for all alignments",
          ],
          retry_note: "Never retry refine with same footprint. Fix footprint first (try different --candidate-rank).",
          args,
        });
      },
    ),

    // --- detect ---
    makeNanoTool(
      "nanodevice_flakedetect",
      "detect",
      [
        "**PLANNER, NOT EXECUTOR.** Returns a `workflow_plan` listing bash commands you must run yourself (via the bash tool).",
        "Detect individual material layers from their optimal source images.",
        "Runs 4 independent detections: graphite (bottom_part), graphene (top_part),",
        "bottom_hBN (bottom_part + SIFT warp to full_stack), top_hBN (footprint copy).",
        "Use after alignment completes. Graphite and graphene need candidate review.",
      ].join(" "),
      {
        full_stack_image: { type: "string", description: "Path to full-stack image" },
        bottom_part_image: { type: "string", description: "Path to bottom-part image (for graphite)" },
        top_part_image: { type: "string", description: "Path to top-part image (for graphene)" },
        pixel_size: { type: "number", description: "Microns per pixel" },
        mirror: { type: "boolean", description: "Whether top_part is mirrored (for graphene)" },
        output_dir: { type: "string", description: "Output directory (uses <out>/detect/)" },
      },
      ["full_stack_image", "pixel_size", "output_dir"],
      (args) => {
        const scriptsDir = "skills/nanodevice_flakedetect_detect/scripts";
        return instructionResult({
          action: "run_detection_scripts",
          pipeline: "nanodevice_flakedetect_detect",
          skill_doc: "skills/nanodevice_flakedetect_detect/SKILL.md",
          read_first: "Read the skill_doc for candidate review workflow and cluster-id override protocol.",
          scripts: {
            graphite: {
              command: `python ${scriptsDir}/graphite.py --image <bottom_part_image> --pixel-size <px> --output-dir <out>/detect/`,
              check: "View 00_graphite_candidates.png. Graphite is typically 2nd-darkest, not absolute darkest. Override with --cluster-id <N> if auto-selection wrong.",
              look_for: "Coherent dark elongated strip — not scattered edges or fold lines",
            },
            graphene: {
              command: `python ${scriptsDir}/graphene.py --image <top_part_image> --pixel-size <px> --output-dir <out>/detect/ ${args.mirror ? "--mirror" : ""}`,
              check: "View 00_graphene_candidates.png and read graphene_result.json.top_candidates. The yellow-bordered rank is selected; override with --cluster-id <N> if another panel isolates graphene better.",
              look_for: "Coherent graphene region within the top flake — not artifacts, hBN regions, or overexposure",
            },
            bottom_hbn: {
              command: `python ${scriptsDir}/bottom_hbn.py --image <bottom_part_image> --warp-matrix <out>/align/warp_sift_bottom.npy --target-image <full_stack_image> --pixel-size <px> --output-dir <out>/detect/`,
              check: "Review 03_bottom_hbn_on_full.png — contour should trace the bottom hBN on full_stack. If offset, check SIFT alignment quality.",
            },
            top_hbn: {
              command: `python ${scriptsDir}/top_hbn.py --footprint-mask <out>/align/footprint_mask.png --footprint-contour <out>/align/footprint_contour.npy --image <full_stack_image> --pixel-size <px> --output-dir <out>/detect/`,
              check: "Copies footprint from align step. Automatic.",
            },
          },
          notes: [
            "All 4 scripts can run in parallel (independent).",
            "Graphene writes a real ranked candidate panel at 00_graphene_candidates.png plus selected/top_candidates metadata in graphene_result.json.",
            "After all complete, assemble detections.json by reading each *_result.json.",
            "Include mirrored: true for graphene if --mirror was used.",
          ],
          args,
        });
      },
    ),

    // --- combine ---
    makeNanoTool(
      "nanodevice_flakedetect",
      "combine",
      [
        "**PLANNER, NOT EXECUTOR.** Returns a `workflow_plan` listing bash commands you must run yourself (via the bash tool).",
        "Transform per-material detections into the full_stack coordinate system,",
        "build unified traces.json, and draw overlay visualizations.",
        "Use after align and detect steps are both complete.",
      ].join(" "),
      {
        output_dir: { type: "string", description: "Pipeline output directory (reads <out>/align/ and <out>/detect/)" },
        full_stack_lut_image: { type: "string", description: "Path to LUT-enhanced image (optional, for ECC registration)" },
      },
      ["output_dir"],
      (args) => {
        const scriptsDir = "skills/nanodevice_flakedetect_combine/scripts";
        return instructionResult({
          action: "run_combine_scripts",
          pipeline: "nanodevice_flakedetect_combine",
          skill_doc: "skills/nanodevice_flakedetect_combine/SKILL.md",
          read_first: "Read the skill_doc for script arguments and ECC registration details.",
          scripts: {
            "1_ecc_register": {
              command: `python ${scriptsDir}/ecc_register.py --raw <full_stack_raw> --lut <full_stack_lut> --output-dir <out>/combine/`,
              when: "Only if LUT image is available",
              purpose: "Compute translation between raw and LUT images",
            },
            "2_transform": {
              command: `python ${scriptsDir}/transform.py --detections <out>/detect/detections.json --align-dir <out>/align/ --image <full_stack_image> --pixel-size <px> --output-dir <out>/combine/`,
              when: "Always",
              purpose: "Transform graphite (invert SIFT), graphene (apply top warp + clip to footprint), pass through hBN",
            },
            "3_overlay": {
              command: `python ${scriptsDir}/overlay.py --traces <out>/combine/traces.json --raw <full_stack_image> --output-dir <out>/combine/`,
              when: "Always",
              purpose: "Draw contours on desaturated image: top_hBN=green, graphene=red, bottom_hBN=blue, graphite=yellow",
            },
          },
          check: "View overlay_raw.png — all 4 materials should be present and aligned correctly.",
          outputs: [
            "traces.json — unified material contours in full_stack pixel coords",
            "combine_report.json — transform summary and metrics",
            "overlay_raw.png, overlay_lut.png — visual validation images",
          ],
          args,
        });
      },
    ),

    // --- commit ---
    makeNanoTool(
      "nanodevice_flakedetect",
      "commit",
      [
        "**PLANNER, NOT EXECUTOR.** Returns a `workflow_plan` listing bash commands you must run yourself (via the bash tool).",
        "Insert detected material polygons into KLayout from traces.json.",
        "Loads background image, transforms coordinates (image→KLayout),",
        "and inserts polygons on layers 10-13/0.",
        "Use after combine produces traces.json. Uses execute_script MCP tool directly,",
        "NOT bash scripts.",
      ].join(" "),
      {
        traces_json: { type: "string", description: "Path to traces.json from combine step" },
        full_stack_image: { type: "string", description: "Path to full-stack image for background overlay" },
        pixel_size: { type: "number", description: "Microns per pixel" },
      },
      ["traces_json", "full_stack_image", "pixel_size"],
      (args) => {
        return instructionResult({
          action: "commit_polygons_via_execute_script",
          pipeline: "nanodevice_flakedetect_commit",
          skill_doc: "skills/nanodevice_flakedetect_commit/SKILL.md",
          read_first: "Read the skill_doc for exact pya code patterns, coordinate transform formulas, and common pitfalls.",
          steps: [
            "1. Load background image via execute_script (pya.Image + set_pixel_width/height + centered DCplxTrans)",
            "2. Read traces.json — extract image_size_um, layer_map, materials with contour_um",
            "3. Coordinate transform: image-origin (top-left, y-down) → KLayout centered (y-up)",
            "   kl_x = img_x_um - w_um/2",
            "   kl_y = h_um/2 - img_y_um  ← Y-FLIP (most common error!)",
            "4. Insert polygons via execute_script (pya.DPolygon with transformed points)",
            "5. Take screenshot to verify",
          ],
          layer_map: {
            top_hBN: "10/0",
            graphene: "11/0",
            bottom_hBN: "12/0",
            graphite: "13/0",
          },
          important: [
            "Use execute_script MCP tool, NOT add_polygon.py skill scripts.",
            "The Y-axis flip is the most common error — always apply it.",
            "Center the image at origin: trans = DCplxTrans(1.0, 0, False, DVector(-w_um/2, -h_um/2))",
          ],
          args,
        });
      },
    ),

    // --- review ---
    makeNanoTool(
      "nanodevice_flakedetect",
      "review",
      [
        "**PLANNER, NOT EXECUTOR.** Returns a `workflow_plan` listing bash commands you must run yourself (via the bash tool).",
        "Validate committed flake polygons in KLayout via visual inspection.",
        "Isolates each material layer, takes screenshots, compares with combine",
        "overlays, and produces a PASS/FAIL verdict.",
        "Use after commit step inserts polygons. Pure agent workflow — no scripts.",
      ].join(" "),
      {
        output_dir: { type: "string", description: "Pipeline output directory (reads overlays from <out>/combine/)" },
      },
      ["output_dir"],
      (args) => {
        return instructionResult({
          action: "visual_review_protocol",
          pipeline: "nanodevice_flakedetect_review",
          skill_doc: "skills/nanodevice_flakedetect_review/SKILL.md",
          read_first: "Read the skill_doc for the full validation checklist and PASS/FAIL criteria.",
          steps: [
            "1. Screenshot the full committed layout via MCP screenshot tool",
            "2. Isolate each layer (show_only) and screenshot:",
            "   - 10/0 (top_hBN), 11/0 (graphene), 12/0 (bottom_hBN), 13/0 (graphite)",
            "3. Compare with overlay_raw.png and overlay_lut.png from combine step",
            "4. Answer 5 structured questions:",
            "   a. Top_hBN boundary fit (tight / acceptable / poor)",
            "   b. Graphene containment in top_hBN (fully / mostly / leakage)",
            "   c. Graphite alignment with dark strip in image",
            "   d. Bottom_hBN coverage",
            "   e. Overall quality (excellent / good / needs work)",
            "5. Check quantitative metrics from alignment_report.json and combine_report.json",
            "6. Verdict: PASS or FAIL (with which step to retry and what to change)",
          ],
          pass_criteria: {
            chamfer_mean: "< 3 um",
            iou: "> 0.7",
            outside_fraction: "< 0.1",
            ecc_correlation: "> 0.9 (if LUT used)",
          },
          retry_note: "Max 2 retries per stage. Specify which step failed and parameter adjustments.",
          args,
        });
      },
    ),
  ];
}

// ─── GDS Alignment ──────────────────────────────────────────────────────────

function registerGdsalignTools(): CodeGenTool[] {
  return [
    makeNanoTool(
      "nanodevice_gdsalign",
      "align_to_gds",
      [
        "**PLANNER, NOT EXECUTOR.** Returns a `workflow_plan` listing bash commands you must run yourself (via the bash tool).",
        "Align microscope stack images to a GDS fabrication template using",
        "lithographic marker detection. Computes image→GDS similarity transform",
        "and commits warped image + material contours to KLayout.",
        "Use when a GDS template with L5/0 alignment markers is available.",
      ].join(" "),
      {
        image_path: { type: "string", description: "Path to microscope image (full_stack or warped)" },
        gds_path: { type: "string", description: "Path to GDS template file with L5/0 markers" },
        pixel_size: { type: "number", description: "Microns per pixel" },
        traces_json: { type: "string", description: "Path to traces.json (optional, for contour transform)" },
        output_dir: { type: "string", description: "Output directory. Defaults to <image_dir>/output/gdsalign/" },
      },
      ["image_path", "gds_path", "pixel_size"],
      (args) => {
        const scriptsDir = "skills/nanodevice_gdsalign/scripts";
        return instructionResult({
          action: "run_gdsalign_pipeline",
          pipeline: "nanodevice_gdsalign",
          skill_doc: "skills/nanodevice_gdsalign/SKILL.md",
          read_first: "Read the skill_doc for marker detection verification and metric thresholds.",
          scripts: {
            "1_extract_markers": {
              command: `python ${scriptsDir}/extract_markers.py <gds_path> --output-dir <out>/gdsalign/`,
              purpose: "Parse GDS file, find 4 innermost L5/0 marker pairs → gds_markers.json",
            },
            "2_detect_markers": {
              command: `python ${scriptsDir}/detect_markers.py <image_path> --pixel-size <px> --output-dir <out>/gdsalign/`,
              purpose: "Template-match marker crosses in microscope image → image_markers.json",
              check: "CRITICAL: View 03_detections.png. 4 markers should form roughly square pattern ~300-400um apart, centered near flake.",
            },
            "3_align_gds": {
              command: `python ${scriptsDir}/align_gds.py --output-dir <out>/gdsalign/`,
              purpose: "Match GDS↔image marker pairs, compute reflected similarity transform",
              check: "≥3 markers detected, ≥2 inliers, mean residual < 5um",
            },
            "4_commit_gds": {
              command: `python ${scriptsDir}/commit_gds.py --warp <out>/gdsalign/gds_warp.npy --traces <traces_json> --image <image_path> --pixel-size <px> --gds <gds_path> --output-dir <out>/gdsalign/`,
              purpose: "Warp image + transform contours, commit to KLayout",
            },
          },
          outputs: [
            "gds_markers.json, image_markers.json — marker coordinates",
            "gds_warp.npy — similarity transform matrix",
            "gds_alignment_report.json — metrics",
            "full_stack_gds.png — warped image",
            "traces_gds.json — transformed contours in GDS coords",
            "image_placement.json — KLayout image placement info",
          ],
          args,
        });
      },
    ),
  ];
}

// ─── Routing ────────────────────────────────────────────────────────────────

function registerRoutingTools(): CodeGenTool[] {
  return [
    makeNanoTool(
      "nanodevice_routing",
      "place_pads",
      [
        "**PLANNER, NOT EXECUTOR.** Returns a `workflow_plan` listing bash commands you must run yourself (via the bash tool).",
        "Place bonding pads in a regular pattern around the device perimeter.",
        "Creates pads on a specified layer and places pin markers for routing.",
        "Use after device geometry is created and before routing leads.",
      ].join(" "),
      {
        field: { type: "number", description: "Write field size in microns (default: 2000)" },
        pad_size: { type: "number", description: "Pad size in microns (default: 80)" },
        pads_per_edge: { type: "integer", description: "Number of pads per edge (default: 12)" },
        layer: { type: "string", description: "Pad layer as 'L/D' string (default: '2/0')" },
        margin: { type: "number", description: "Margin from field edge in microns (default: 60)" },
      },
      [],
      (args) => {
        const scriptsDir = "skills/nanodevice_routing/scripts";
        return instructionResult({
          action: "run_pad_placement",
          pipeline: "nanodevice_routing",
          skill_doc: "skills/nanodevice_routing/SKILL.md",
          read_first: "Read the skill_doc for pad layout conventions and pin marker requirements.",
          command: `python ${scriptsDir}/place_pads.py --field ${args.field ?? 2000} --pad-size ${args.pad_size ?? 80} --pads-per-edge ${args.pads_per_edge ?? 12} --layer ${args.layer ?? "2/0"} --margin ${args.margin ?? 60}`,
          notes: [
            "Creates pads on the specified layer (default 2/0)",
            "Also places pin markers on layer 101/0 at pad centers",
            "Device contacts should have pins on layer 100/0 before routing",
          ],
          args,
        });
      },
    ),

    makeNanoTool(
      "nanodevice_routing",
      "route_leads",
      [
        "**PLANNER, NOT EXECUTOR.** Returns a `workflow_plan` listing bash commands you must run yourself (via the bash tool).",
        "Route leads from device contact pins to bonding pad pins using multi-window",
        "EBL support. Inner window uses fine lines, outer window uses coarse lines,",
        "with boundary patches for connectivity.",
        "Use after place_pads and device contacts have pin markers.",
      ].join(" "),
      {
        inner_width: { type: "number", description: "Inner-window line width in microns (default: 0.5)" },
        outer_width: { type: "number", description: "Outer-window line width in microns (default: 1.0)" },
        inner_layer: { type: "string", description: "Inner route layer as 'L/D' (default: '3/0')" },
        outer_layer: { type: "string", description: "Outer route layer as 'L/D' (default: '4/0')" },
        patch_layer: { type: "string", description: "Boundary patch layer as 'L/D' (default: '5/0')" },
      },
      [],
      (args) => {
        const scriptsDir = "skills/nanodevice_routing/scripts";
        return instructionResult({
          action: "run_multiwindow_routing",
          pipeline: "nanodevice_routing",
          skill_doc: "skills/nanodevice_routing/SKILL.md",
          read_first: "Read the skill_doc for multi-window EBL routing details and boundary patch strategy.",
          command: `python ${scriptsDir}/route_multiwindow.py --inner-width ${args.inner_width ?? 0.5} --outer-width ${args.outer_width ?? 1.0} --inner-layer ${args.inner_layer ?? "3/0"} --outer-layer ${args.outer_layer ?? "4/0"} --patch-layer ${args.patch_layer ?? "5/0"}`,
          notes: [
            "Reads pin pairs from layout (100/0 = contact pins, 101/0 = pad pins)",
            "Inner window: fine lines for device area",
            "Outer window: coarse lines to bonding pads",
            "Patches at inner/outer boundary for layer connectivity",
            "For failed pairs, use auto_route MCP tool for manual L-shaped routing",
          ],
          layer_convention: {
            mesa: "1/0",
            pads: "2/0",
            fine_routes: "3/0",
            coarse_routes: "4/0",
            patches: "5/0",
            contact_pins: "100/0",
            pad_pins: "101/0",
          },
          args,
        });
      },
    ),

    makeNanoTool(
      "nanodevice_routing",
      "clear_routes",
      [
        "**PLANNER, NOT EXECUTOR.** Returns a `workflow_plan` listing bash commands you must run yourself (via the bash tool).",
        "Remove all routing geometry from specified layers.",
        "Use to clear failed routes before re-routing with adjusted parameters.",
      ].join(" "),
      {
        layers: { type: "string", description: "Space-separated layer specs to clear (default: '3/0 4/0 5/0')" },
      },
      [],
      (args) => {
        const scriptsDir = "skills/nanodevice_routing/scripts";
        return instructionResult({
          action: "clear_routing_layers",
          pipeline: "nanodevice_routing",
          skill_doc: "skills/nanodevice_routing/SKILL.md",
          command: `python ${scriptsDir}/clear_routes.py ${args.layers ?? "3/0 4/0 5/0"}`,
          notes: [
            "Removes all shapes from specified layers",
            "Default clears fine routes (3/0), coarse routes (4/0), and patches (5/0)",
            "Does NOT remove pads (2/0) or pin markers (100/0, 101/0)",
          ],
          args,
        });
      },
    ),
  ];
}

// ─── Public registration ────────────────────────────────────────────────────

export function registerNanodeviceTools(): CodeGenTool[] {
  return [
    ...registerFlakedetectTools(),
    ...registerGdsalignTools(),
    ...registerRoutingTools(),
  ];
}
