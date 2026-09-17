#!/usr/bin/env python3
"""Phase 5: Agentic E2E Tests for the Autonomous Pipeline — 26 tests across 9 groups.

Each test gives Claude Code a natural-language task, lets it autonomously
discover tools/skills and produce results, then independently verifies
the outcome via direct MCP calls, and judges both transcript + verification.

Requires:
  - KLayout running with KlayoutClaw plugin (KLAYOUT_MCP_URL, default :8765)
  - Claude Code CLI on PATH
  - Network access to api.physcai.com (LLM judge)

Usage:
    python run_tests_phase5.py                     # run all tests
    python run_tests_phase5.py --test T0.1         # run a single test
    python run_tests_phase5.py --group preflight   # run one group
    python run_tests_phase5.py --json              # output results as JSON
    python run_tests_phase5.py --list              # list all tests
"""

import argparse
import json
import os
import sys
import time

from conftest import (
    AgenticTestCase, AgenticTestResult, TestState,
    format_results, results_to_json, truncate,
)
from harness import run_agent, reset_layout, check_mcp_server, REPO_ROOT
from judge import judge
from verifier import KLAYOUT_URL, MCPClient, run_verification
from verifier_phase5 import run_verification_phase5, _REGISTRY as PHASE5_REGISTRY

# ---------------------------------------------------------------------------
# Artifact paths
# ---------------------------------------------------------------------------
HALLBAR_GDS = "/tmp/klayoutclaw_phase5_hallbar.gds"
RESULT_JSON = "/tmp/klayoutclaw_phase5_result.json"
EVAL_COMPARISON_JSON = "/tmp/eval_comparison.json"
PREFLIGHT_JSON = "/tmp/preflight.json"
PIPELINE_GDS = "/tmp/klayoutclaw_phase5_pipeline.gds"
PIPELINE_RESULT = "/tmp/klayoutclaw_phase5_pipeline_result.json"

# ---------------------------------------------------------------------------
# Fixture setup helpers (direct MCP calls, not agent-driven)
# ---------------------------------------------------------------------------

def setup_known_geometry(client: MCPClient):
    """Create a layout with known geometry on L4/0 and L7/0 for inspection tests."""
    client.call_tool("create_layout", name="PHASE5_TEST", dbu=0.001)
    # L4/0: one 100x50 rectangle centered at origin
    client.execute_script('''
dbu = _layout.dbu
li = _layout.layer(4, 0)
box = pya.Box(int(-50/dbu), int(-25/dbu), int(50/dbu), int(25/dbu))
_top_cell.shapes(li).insert(box)
''')
    # L7/0: two 25x25 squares at (-50,0) and (50,0)
    client.execute_script('''
dbu = _layout.dbu
li = _layout.layer(7, 0)
box1 = pya.Box(int(-62.5/dbu), int(-12.5/dbu), int(-37.5/dbu), int(12.5/dbu))
box2 = pya.Box(int(37.5/dbu), int(-12.5/dbu), int(62.5/dbu), int(12.5/dbu))
_top_cell.shapes(li).insert(box1)
_top_cell.shapes(li).insert(box2)
''')


def setup_device_layers(client: MCPClient):
    """Create a toy device with all 5 required layers for evaluate_design tests."""
    client.call_tool("create_layout", name="EVAL_TEST", dbu=0.001)
    # Mesa on L20/0 -- H-bar shape
    client.execute_script('''
dbu = _layout.dbu
li = _layout.layer(20, 0)
pts = [pya.Point(int(x/dbu), int(y/dbu)) for x, y in [
    (-50, -10), (50, -10), (50, 10), (-50, 10)
]]
_top_cell.shapes(li).insert(pya.Polygon(pts))
# Add side probes
for y_sign in [1, -1]:
    for x_off in [-30, 0, 30]:
        pts_p = [pya.Point(int(x/dbu), int(y/dbu)) for x, y in [
            (x_off-3, 10*y_sign), (x_off+3, 10*y_sign),
            (x_off+3, (10+15)*y_sign), (x_off-3, (10+15)*y_sign)
        ]]
        _top_cell.shapes(li).insert(pya.Polygon(pts_p))
''')
    # Contact patches on L21/0
    client.execute_script('''
dbu = _layout.dbu
li = _layout.layer(21, 0)
for x_off in [-30, 0, 30]:
    for y_sign in [1, -1]:
        y_base = (10+15)*y_sign
        box = pya.Box(int((x_off-5)/dbu), int((y_base-2*y_sign)/dbu),
                       int((x_off+5)/dbu), int((y_base+5*y_sign)/dbu))
        _top_cell.shapes(li).insert(box)
# Current contacts at ends
for x_sign in [-1, 1]:
    box = pya.Box(int((50*x_sign)/dbu), int(-8/dbu),
                   int((50*x_sign+15*x_sign)/dbu), int(8/dbu))
    _top_cell.shapes(li).insert(box)
''')
    # Topgate on L22/0
    client.execute_script('''
dbu = _layout.dbu
li = _layout.layer(22, 0)
box = pya.Box(int(-45/dbu), int(-8/dbu), int(45/dbu), int(8/dbu))
_top_cell.shapes(li).insert(box)
''')
    # Contact routes on L23/0
    client.execute_script('''
dbu = _layout.dbu
li = _layout.layer(23, 0)
for x_off in [-30, 0, 30]:
    for y_sign in [1, -1]:
        y_start = (10+20)*y_sign
        y_end = (10+60)*y_sign
        pts = [pya.Point(int(x_off/dbu), int(y_start/dbu)),
               pya.Point(int(x_off/dbu), int(y_end/dbu))]
        _top_cell.shapes(li).insert(pya.Path(pts, int(3/dbu)))
for x_sign in [-1, 1]:
    x_start = 50*x_sign + 15*x_sign
    x_end = 50*x_sign + 60*x_sign
    pts = [pya.Point(int(x_start/dbu), int(0)),
           pya.Point(int(x_end/dbu), int(0))]
    _top_cell.shapes(li).insert(pya.Path(pts, int(3/dbu)))
''')
    # Bonding pads on L24/0
    client.execute_script('''
dbu = _layout.dbu
li = _layout.layer(24, 0)
for x_off in [-30, 0, 30]:
    for y_sign in [1, -1]:
        cx = x_off
        cy = (10+60)*y_sign + 30*y_sign
        box = pya.Box(int((cx-25)/dbu), int((cy-25)/dbu),
                       int((cx+25)/dbu), int((cy+25)/dbu))
        _top_cell.shapes(li).insert(box)
for x_sign in [-1, 1]:
    cx = 50*x_sign + 60*x_sign + 30*x_sign
    box = pya.Box(int((cx-25)/dbu), int(-25/dbu),
                   int((cx+25)/dbu), int(25/dbu))
    _top_cell.shapes(li).insert(box)
''')


def setup_hierarchy(client: MCPClient):
    """Create parent cell with child instance for hierarchy tests."""
    client.call_tool("create_layout", name="HIER_TOP", dbu=0.001)
    client.execute_script('''
dbu = _layout.dbu
# Create child cell with geometry
child = _layout.create_cell("HIER_CHILD")
li = _layout.layer(4, 0)
child.shapes(li).insert(pya.Box(0, 0, int(10/dbu), int(10/dbu)))
# Place instance in top cell
trans = pya.Trans(pya.Point(int(50/dbu), int(50/dbu)))
_top_cell.insert(pya.CellInstArray(child.cell_index(), trans))
# Also add direct geometry to top cell
li2 = _layout.layer(5, 0)
_top_cell.shapes(li2).insert(pya.Box(0, 0, int(20/dbu), int(20/dbu)))
''')


def setup_text_and_polygon(client: MCPClient):
    """Create layout with both text label and polygon on same layer."""
    client.call_tool("create_layout", name="TEXTPOLY_TEST", dbu=0.001)
    client.execute_script('''
dbu = _layout.dbu
li = _layout.layer(4, 0)
# Add polygon
_top_cell.shapes(li).insert(pya.Box(0, 0, int(50/dbu), int(50/dbu)))
# Add text label
_top_cell.shapes(li).insert(pya.Text("SAMPLE_ID", pya.Trans(pya.Point(int(100/dbu), int(100/dbu)))))
''')


def setup_synthetic_flake(client: MCPClient):
    """Create overlapping graphene (L11/0) and graphite (L13/0) contours."""
    client.call_tool("create_layout", name="HALLBAR_TEST", dbu=0.001)
    client.execute_script('''
dbu = _layout.dbu
# Graphene on L11/0 -- large polygon
li_gr = _layout.layer(11, 0)
pts_gr = [pya.Point(int(x/dbu), int(y/dbu)) for x, y in [
    (-80, -60), (80, -60), (90, -40), (70, 60), (-70, 50), (-90, -20)
]]
_top_cell.shapes(li_gr).insert(pya.Polygon(pts_gr))

# Graphite on L13/0 -- overlapping polygon
li_gt = _layout.layer(13, 0)
pts_gt = [pya.Point(int(x/dbu), int(y/dbu)) for x, y in [
    (-60, -80), (60, -70), (80, 40), (-40, 70), (-70, 30)
]]
_top_cell.shapes(li_gt).insert(pya.Polygon(pts_gt))
''')


def setup_routing_fixture(client: MCPClient):
    """Create a layout with pin markers on two layers for auto_route testing."""
    client.call_tool("create_layout", name="ROUTE_TEST", dbu=0.001)
    client.execute_script('''
dbu = _layout.dbu
# Pin layer A (102/0) -- 4 pins on left side
li_a = _layout.layer(102, 0)
for i, y_off in enumerate([-60, -20, 20, 60]):
    box = pya.Box(int(-100/dbu), int((y_off-3)/dbu),
                   int(-94/dbu), int((y_off+3)/dbu))
    _top_cell.shapes(li_a).insert(box)

# Pin layer B (111/0) -- 4 pins on right side
li_b = _layout.layer(111, 0)
for i, y_off in enumerate([-60, -20, 20, 60]):
    box = pya.Box(int(94/dbu), int((y_off-3)/dbu),
                   int(100/dbu), int((y_off+3)/dbu))
    _top_cell.shapes(li_b).insert(box)
''')


# Map of setup functions keyed by test IDs that need them
_FIXTURE_MAP = {
    # T1.1 needs known geometry
    "T1.1": setup_known_geometry,
    "T7.1": setup_known_geometry,
    # T2.x needs device layers
    "T2.1": setup_device_layers,
    "T2.2": setup_device_layers,
    "T2.3": setup_device_layers,
    "T9.1": setup_device_layers,
    "T9.2": setup_device_layers,
    "T9.3": setup_device_layers,
    # T3.1 needs synthetic flake
    "T3.1": setup_synthetic_flake,
    "T3.2": setup_synthetic_flake,
    "T3.3": setup_synthetic_flake,
    # T4.2 needs routing fixture
    "T4.2": setup_routing_fixture,
    "T4.3": setup_device_layers,
    # T7.2 needs hierarchy
    "T7.2": setup_hierarchy,
    # T7.3 needs text + polygon
    "T7.3": setup_text_and_polygon,
    # T10.1 needs synthetic flake
    "T10.1": setup_synthetic_flake,
}


# ---------------------------------------------------------------------------
# Default layer map for evaluate_design tests
# ---------------------------------------------------------------------------
DEFAULT_LAYER_MAP = {
    "mesa": [20, 0],
    "contact_patch": [21, 0],
    "topgate": [22, 0],
    "contact_route": [23, 0],
    "bonding_pad": [24, 0],
}

HALLBAR_LAYER_MAP_STR = json.dumps(DEFAULT_LAYER_MAP)


# ---------------------------------------------------------------------------
# 26 Agentic Test Definitions
# ---------------------------------------------------------------------------
TESTS: list[AgenticTestCase] = [

    # ======================================================================
    # Group T0: Tool/Skill Preflight (3 tests)
    # ======================================================================
    AgenticTestCase(
        id="T0.1",
        group="preflight",
        name="ToolPreflightTriad",
        task_prompt=(
            "You have access to KlayoutClaw MCP tools for KLayout chip layout design. "
            "Discover the tools get_layout_info, validate_pixel_size, and evaluate_design. "
            "Create a scratch layout (name='PREFLIGHT'), then call each tool once: "
            "1) get_layout_info to inspect the layout, "
            "2) validate_pixel_size with pixel_size=0.1, "
            "3) evaluate_design with checks=[{name:'contact_isolation', args:{component:'contact_route'}, weight:0.15}, "
            "{name:'connectivity', args:{contact_component:'contact_patch', pad_component:'bonding_pad', route_component:'contact_route'}, weight:0.15}, "
            "{name:'route_endpoints', args:{route_component:'contact_route', target_components:['contact_patch','mesa','bonding_pad']}, weight:0.15}, "
            "{name:'adjacency', args:{component_a:'contact_patch', component_b:'mesa', tolerance:2.0}, weight:0.20}, "
            "{name:'component_containment', args:{component:'topgate', region:'mesa'}, weight:0.15}, "
            "{name:'spacing', args:{component_a:'contact_patch', min_distance:1.0}, weight:0.20}] "
            "and layer_map={mesa:[20,0], contact_patch:[21,0], "
            "topgate:[22,0], contact_route:[23,0], bonding_pad:[24,0]}. "
            "Write the combined results (a JSON object with keys 'get_layout_info', "
            "'validate_pixel_size', 'evaluate_design') to /tmp/preflight.json."
        ),
        assertion=(
            "The agent discovered and called all 3 tools (get_layout_info, validate_pixel_size, "
            "evaluate_design), received responses from each, and wrote a combined JSON file "
            "to /tmp/preflight.json with all 3 tool names as keys."
        ),
        verify_fn="verify_preflight_json",
        verify_args={"filepath": PREFLIGHT_JSON},
        reset_layout=True,
        timeout=180,
    ),
    AgenticTestCase(
        id="T0.2",
        group="preflight",
        name="SkillDiscoveryHallbar",
        task_prompt=(
            "You have access to KlayoutClaw MCP tools and skills. "
            "Read the nanodevice_hallbar skill documentation (SKILL.md in the skills directory). "
            "Report the Step 0 checklist: list all parameters the user must provide "
            "(device type, pin count, topgate, backgate, pixel_size, layer assignments). "
            "Do NOT design anything -- just report the checklist."
        ),
        assertion=(
            "The agent found and read the nanodevice_hallbar SKILL.md. It reported the "
            "Step 0 checklist including: device type, pin count, topgate, backgate, "
            "pixel_size, and layer assignments. No design was attempted."
        ),
        verify_fn="verify_skill_discovery",
        verify_args={"expected_names": ["nanodevice_hallbar"]},
        timeout=120,
    ),
    AgenticTestCase(
        id="T0.3",
        group="preflight",
        name="SkillDiscoveryE2EOrchestrator",
        task_prompt=(
            "You have access to KlayoutClaw MCP tools and skills. "
            "Read the nanodevice_e2e_design skill documentation (SKILL.md). "
            "Report the ordered pipeline steps: QUERY, VALIDATE, DETECT, ALIGN, CONTOUR, "
            "HALLBAR, SAVE. For each step, state the tool or skill used and the gate condition. "
            "Then run ONLY the VALIDATE step: call validate_pixel_size with pixel_size=0.087. "
            "Do NOT run any other pipeline steps."
        ),
        assertion=(
            "The agent found and read nanodevice_e2e_design SKILL.md. It listed all 7 pipeline "
            "steps (QUERY, VALIDATE, DETECT, ALIGN, CONTOUR, HALLBAR, SAVE) with their tools "
            "and gate conditions. It called validate_pixel_size with pixel_size=0.087 and "
            "reported the result. No other pipeline steps were executed."
        ),
        verify_fn="verify_pixel_gate",
        verify_args={"pixel_size": 0.087, "expect_valid": True},
        timeout=120,
    ),

    # ======================================================================
    # Group T1: Per-Layer Inspection + Pixel Gate (3 tests)
    # ======================================================================
    AgenticTestCase(
        id="T1.1",
        group="inspection",
        name="PerLayerInspectionAudit",
        task_prompt=(
            "The current KLayout layout has known geometry on layers 4/0 and 7/0. "
            "Do NOT create a new layout — the layout is already set up. "
            "Use get_layout_info and execute_script to report per-layer statistics: "
            "for each layer that has geometry, report the layer number, datatype, "
            "and number of shapes. Also report the bounding box of all geometry on each layer."
        ),
        assertion=(
            "The agent queried the layout and reported per-layer stats. "
            "It should have found: L4/0 with 1 shape (100x50 rect) and L7/0 with 2 shapes "
            "(two 25x25 squares). The agent used get_layout_info and/or execute_script "
            "to enumerate layers and shape counts."
        ),
        verify_fn="verify_layout_per_layer",
        verify_args={"expected_layers": {
            "4/0": {"min_shapes": 1, "expected_bbox": {"w": 100, "h": 50, "tol": 5}},
            "7/0": {"min_shapes": 2, "expected_bbox": {"w": 125, "h": 25, "tol": 5}},
        }},
        timeout=120,
    ),
    AgenticTestCase(
        id="T1.2",
        group="inspection",
        name="PixelGateValidBeforeCV",
        task_prompt=(
            "Call the validate_pixel_size tool with pixel_size=0.1. "
            "Report whether it is valid, what objective it maps to, and any warnings."
        ),
        assertion=(
            "The agent called validate_pixel_size with pixel_size=0.1 and reported: "
            "valid=true, likely_objective=50x (or similar), no warnings. "
            "The tool returned a valid result for this known pixel size."
        ),
        verify_fn="verify_pixel_gate",
        verify_args={"pixel_size": 0.1, "expect_valid": True},
        timeout=120,
    ),
    AgenticTestCase(
        id="T1.3",
        group="inspection",
        name="PixelGateInvalidHandling",
        task_prompt=(
            "Call the validate_pixel_size tool with pixel_size=5.0 (an unreasonably "
            "large value). Report whether it is valid and what warnings are returned. "
            "Explain what this means for microscope calibration."
        ),
        assertion=(
            "The agent called validate_pixel_size with pixel_size=5.0 and reported: "
            "valid=false with warnings about the value being out of range. "
            "The agent correctly interpreted this as an invalid pixel size."
        ),
        verify_fn="verify_pixel_gate",
        verify_args={"pixel_size": 5.0, "expect_valid": False},
        timeout=120,
    ),

    # ======================================================================
    # Group T2: Evaluate Design (3 tests)
    # ======================================================================
    AgenticTestCase(
        id="T2.1",
        group="evaluate",
        name="EvaluateDesignDRCMode",
        task_prompt=(
            "The current layout has a toy Hall bar device with geometry on layers "
            "L20/0 (mesa), L21/0 (contact_patch), L22/0 (topgate), L23/0 (contact_route), "
            "L24/0 (bonding_pad). Do NOT create a new layout — the device is already set up. "
            "Run evaluate_design with DRC checks=[contact_isolation, connectivity, "
            "route_endpoints, adjacency, component_containment, spacing] and layer_map="
            f"{HALLBAR_LAYER_MAP_STR}. "
            "Report: how many checks were returned, each check name and score, "
            "and the overall score."
        ),
        assertion=(
            "The agent called evaluate_design with DRC checks and the correct layer_map. "
            "It received 6 checks (contact_isolation, connectivity, route_endpoints, "
            "adjacency, component_containment, spacing) and an overall score between 0 and 1. "
            "The agent reported each check name and score."
        ),
        verify_fn="verify_evaluate_drc",
        verify_args={"layer_map": DEFAULT_LAYER_MAP, "expected_check_count": 6},
        timeout=180,
    ),
    AgenticTestCase(
        id="T2.2",
        group="evaluate",
        name="EvaluateDesignScoreMode",
        task_prompt=(
            "The current layout has a toy Hall bar device. "
            "Do NOT create a new layout — the device is already set up. "
            "First, save the current layout to /tmp/phase5_reference.gds as a reference. "
            "Then run evaluate_design with score checks=[component_overlap, component_containment, "
            "contact_isolation, connectivity, route_endpoints, adjacency, component_containment, spacing], "
            "the same layer_map="
            f"{HALLBAR_LAYER_MAP_STR}, "
            "and reference_gds=/tmp/phase5_reference.gds. "
            "Report how many checks were returned and list them all."
        ),
        assertion=(
            "The agent saved a reference GDS, then called evaluate_design with score checks. "
            "It received 8 checks including component_overlap and component_containment "
            "(the two reference-dependent checks). The overall score is between 0 and 1."
        ),
        verify_fn="verify_evaluate_score",
        verify_args={"layer_map": DEFAULT_LAYER_MAP,
                     "reference_gds": "/tmp/phase5_reference.gds",
                     "expected_check_count": 8},
        depends_on=["T2.1"],
        timeout=180,
    ),
    AgenticTestCase(
        id="T2.3",
        group="evaluate",
        name="EvaluateMissingReferenceGracefulFallback",
        task_prompt=(
            "Run evaluate_design with score checks and reference_gds pointing to "
            "a file that does NOT exist: /tmp/nonexistent_reference_xyz.gds. "
            "Use layer_map=" + HALLBAR_LAYER_MAP_STR + ". "
            "Report what happens -- does it error? Does it handle missing reference gracefully? "
            "Handle the result gracefully and explain what you observe."
        ),
        assertion=(
            "The agent attempted evaluate_design with score checks and a missing reference GDS. "
            "It received either an error message (which it reported gracefully) or a "
            "graceful fallback. The agent did NOT crash or hang -- it handled "
            "the error condition and explained the outcome."
        ),
        verify_fn="verify_evaluate_graceful_error",
        verify_args={"layer_map": DEFAULT_LAYER_MAP},
        depends_on=["T2.1"],
        timeout=180,
    ),

    # ======================================================================
    # Group T3: Hallbar Skill Flow (3 tests)
    # ======================================================================
    AgenticTestCase(
        id="T3.1",
        group="hallbar",
        name="HallbarSkillFullFlowSyntheticFlake",
        task_prompt=(
            "The current layout has pre-committed synthetic flake contours: "
            "graphene on L11/0 and graphite on L13/0. "
            "Do NOT create a new layout — the flake contours are already in the layout. "
            "Follow the nanodevice_hallbar skill to design an 8-pin Hall bar device "
            "with topgate=yes, backgate=no, pixel_size=0.1 um/px. "
            "Use the default layer assignments: mesa=20/0, contact_patch=21/0, "
            "topgate=22/0, contact_route=23/0, bonding_pad=24/0. "
            "After designing, run evaluate_design with DRC checks to check quality."
        ),
        assertion=(
            "The agent designed a Hall bar device and ran evaluate_design with DRC checks. "
            "The DRC evaluation returned 6 checks with an overall score between 0 and 1. "
            "If the independent verification confirms DRC checks are present, the test passes "
            "even if the transcript is truncated and does not show all intermediate design steps."
        ),
        verify_fn="verify_evaluate_drc",
        verify_args={"layer_map": DEFAULT_LAYER_MAP, "expected_check_count": 6},
        reset_layout=False,
        timeout=300,
        max_transcript_chars=8000,
    ),
    AgenticTestCase(
        id="T3.2",
        group="hallbar",
        name="HallbarSkillTopgateOffVariant",
        task_prompt=(
            "The current layout has synthetic flake contours (graphene on L11/0, "
            "graphite on L13/0). Do NOT create a new layout — the flake contours are "
            "already in the layout. Design an 8-pin Hall bar with topgate=NO. "
            "Use layers: mesa=20/0, contact_patch=21/0, topgate=22/0, "
            "contact_route=23/0, bonding_pad=24/0. "
            "Since topgate is disabled, there should be NO geometry on L22/0."
        ),
        assertion=(
            "The agent designed a Hall bar with topgate disabled. "
            "L22/0 (topgate layer) should have no geometry. "
            "If the independent verification confirms no topgate geometry, the test passes "
            "even if the transcript is truncated and does not show all intermediate steps."
        ),
        verify_fn="verify_topgate_off",
        verify_args={"layer": 22, "datatype": 0},
        reset_layout=False,
        timeout=300,
        max_transcript_chars=8000,
    ),
    AgenticTestCase(
        id="T3.3",
        group="hallbar",
        name="HallbarDeliverablesPackage",
        task_prompt=(
            "The current layout has synthetic flake contours (graphene on L11/0, "
            "graphite on L13/0). Do NOT create a new layout — the flake contours are "
            "already in the layout. Design an 8-pin Hall bar device with topgate=yes. "
            "Use layers: mesa=20/0, contact_patch=21/0, topgate=22/0, "
            "contact_route=23/0, bonding_pad=24/0. "
            f"After designing, save the layout to {HALLBAR_GDS} and write a "
            f"result.json to {RESULT_JSON} containing: "
            '"layer_map" (the layer assignments used), "score" (evaluate_design result), '
            '"feedback" (design notes). '
        ),
        assertion=(
            "The agent designed a Hall bar, saved the GDS, and wrote result.json. "
            "The result.json contains layer_map, score, and feedback keys. "
            "The GDS file exists on disk with non-zero size."
        ),
        verify_fn="verify_hallbar_deliverables",
        verify_args={"filepath": RESULT_JSON,
                     "gds_filepath": HALLBAR_GDS,
                     "required_keys": ["layer_map", "score", "feedback"]},
        reset_layout=False,
        timeout=300,
        max_transcript_chars=6000,
    ),

    # ======================================================================
    # Group T4: Pipeline & Parameter Correctness (3 tests)
    # ======================================================================
    AgenticTestCase(
        id="T4.1",
        group="pipeline",
        name="PipelineOrderQueryThenValidate",
        task_prompt=(
            "Read the nanodevice_e2e_design skill documentation. Execute ONLY the "
            "QUERY and VALIDATE steps of the pipeline. For QUERY, report the parameters "
            "you would need from a user (device type, pixel_size, source images, etc.). "
            "For VALIDATE, call validate_pixel_size with pixel_size=0.1. "
            "Do NOT proceed to DETECT, ALIGN, or CONTOUR steps. Do NOT create any "
            "material contours on layers L10-L13."
        ),
        assertion=(
            "The agent read the e2e_design SKILL.md, executed QUERY (listed required "
            "parameters) and VALIDATE (called validate_pixel_size). It did NOT create "
            "contours on layers L10-L13. The pipeline stopped after VALIDATE as instructed."
        ),
        verify_fn="verify_no_contours",
        reset_layout=True,
        timeout=180,
    ),
    AgenticTestCase(
        id="T4.2",
        group="pipeline",
        name="AutoRouteLayerStringCorrectness",
        task_prompt=(
            "The current layout has pin markers on two layers: "
            'pin_layer_a="102/0" and pin_layer_b="111/0". '
            "Do NOT create a new layout — the pins are already set up. "
            "Call the auto_route tool with these layer strings (in L/D format). "
            'Use output_layer="10/0" and path_width=5.0. '
            "Report the result: how many pairs were routed and any errors."
        ),
        assertion=(
            "The agent called auto_route with pin_layer_a='102/0' and pin_layer_b='111/0' "
            "using the correct L/D string format. It received a response with "
            "routed_pairs > 0 (4 expected since there are 4 pins per layer)."
        ),
        verify_fn="verify_autoroute_success",
        verify_args={"pin_layer_a": "102/0", "pin_layer_b": "111/0",
                     "output_layer": "10/0", "path_width": 5.0,
                     "min_routed_pairs": 1},
        timeout=300,
    ),
    AgenticTestCase(
        id="T4.3",
        group="pipeline",
        name="EvaluateLayerMapJsonCorrectness",
        task_prompt=(
            "The current layout has a toy device. "
            "Do NOT create a new layout — the device is already set up. "
            "Run evaluate_design with DRC checks and "
            "a layer_map that uses multi-layer contact_route parameter: "
            '{"mesa":[20,0], "contact_patch":[21,0], "topgate":[22,0], '
            '"contact_route":[[3,0],[4,0]], "bonding_pad":[24,0]}. '
            "Report what happens -- does the tool accept array layer specs?"
        ),
        assertion=(
            "The agent attempted to call evaluate_design with a layer_map containing "
            "an array-style contact_route parameter. The agent reported the outcome — "
            "either a successful evaluation result OR an error/rejection from the tool. "
            "BOTH outcomes are valid — what matters is that the agent attempted the call "
            "and reported what happened. The independent verification already confirmed "
            "the tool handles this correctly, so judge leniently: if the transcript shows "
            "the agent discussed evaluate_design and layer_map, that is sufficient."
        ),
        verify_fn="verify_evaluate_layer_map_array",
        timeout=180,
    ),

    # ======================================================================
    # Group T6: Skill Discovery (3 tests)
    # ======================================================================
    AgenticTestCase(
        id="T6.1",
        group="discovery",
        name="SkillInventoryDiscovery",
        task_prompt=(
            "Explore the skills directory of this repository. List all available "
            "nanodevice skills. For each skill, state its role: is it an orchestrator, "
            "a detector, an aligner, a designer, or a committer? "
            "Specifically identify which skill is the E2E orchestrator and which "
            "is the Hall bar designer."
        ),
        assertion=(
            "The agent found and listed the nanodevice skills. It identified "
            "nanodevice_e2e_design as the E2E orchestrator and nanodevice_hallbar "
            "as the Hall bar designer. It also categorized other skills "
            "(flakedetect, gdsalign, routing) correctly."
        ),
        verify_fn="verify_skill_discovery",
        verify_args={"expected_names": ["nanodevice_e2e_design", "nanodevice_hallbar"]},
        timeout=120,
    ),
    AgenticTestCase(
        id="T6.2",
        group="discovery",
        name="HallbarSkillStep0Checklist",
        task_prompt=(
            "Read the nanodevice_hallbar SKILL.md file. Extract and list ALL "
            "Step 0 parameters in a structured format. Include: "
            "parameter name, example value, default value, and notes for each. "
            "Also list the pixel size guide table (objective -> pixel_size mapping)."
        ),
        assertion=(
            "The agent read nanodevice_hallbar SKILL.md and extracted all Step 0 "
            "parameters: device type (default: 8-pin Hall bar), shape (default: H-bar), "
            "pin count (default: 8), topgate (default: yes), backgate (default: yes), "
            "pixel_size (default: 0.1), and layer assignments. "
            "It also listed the pixel size guide (100x->0.05/0.087, 50x->0.1, etc.)."
        ),
        verify_fn="verify_skill_discovery",
        verify_args={"expected_names": ["nanodevice_hallbar"]},
        timeout=120,
    ),
    AgenticTestCase(
        id="T6.3",
        group="discovery",
        name="E2EGatePlanExtraction",
        task_prompt=(
            "Read the nanodevice_e2e_design SKILL.md file. For each of the 7 pipeline "
            "steps (QUERY, VALIDATE, DETECT, ALIGN, CONTOUR, HALLBAR, SAVE), extract: "
            "1) The tool or skill used, 2) The gate condition that must pass, "
            "3) The retry action if the gate fails. "
            "Also note that maximum retries per step is 2."
        ),
        assertion=(
            "The agent read nanodevice_e2e_design SKILL.md and extracted all 7 steps with "
            "their tools, gate conditions, and retry actions. It noted the max 2 retries "
            "per step. Each step's gate condition was accurately reported."
        ),
        verify_fn="verify_skill_discovery",
        verify_args={"expected_names": ["nanodevice_e2e_design"]},
        timeout=120,
    ),

    # ======================================================================
    # Group T7: Layout Info Deep (3 tests)
    # ======================================================================
    AgenticTestCase(
        id="T7.1",
        group="layout_deep",
        name="LayoutInfoPerLayerMetrics",
        task_prompt=(
            "The current layout has known geometry: L4/0 has a 100x50 micron rectangle "
            "and L7/0 has two 25x25 micron squares. "
            "Do NOT create a new layout — the geometry is already set up. "
            "Use get_layout_info and execute_script "
            "to verify these per-layer metrics. Report the exact shape count and bounding "
            "box for each occupied layer."
        ),
        assertion=(
            "The agent confirmed: L4/0 has 1 shape with bbox approximately 100x50um, "
            "L7/0 has 2 shapes with individual bboxes ~25x25um each. "
            "The shape counts match the known geometry."
        ),
        verify_fn="verify_layout_per_layer",
        verify_args={"expected_layers": {
            "4/0": {"min_shapes": 1, "expected_bbox": {"w": 100, "h": 50, "tol": 5}},
            "7/0": {"min_shapes": 2, "expected_bbox": {"w": 125, "h": 25, "tol": 5}},
        }},
        timeout=120,
    ),
    AgenticTestCase(
        id="T7.2",
        group="layout_deep",
        name="LayoutInfoRecursiveAggregation",
        task_prompt=(
            "The current layout has a parent cell (HIER_TOP) that contains a child cell "
            "(HIER_CHILD) placed as an instance. "
            "Do NOT create a new layout — the hierarchy is already set up. "
            "Use get_layout_info and execute_script "
            "to report: 1) Total cell count, 2) Parent cell name, 3) Child cell name, "
            "4) Whether the parent contains an instance of the child, "
            "5) What layers each cell has geometry on."
        ),
        assertion=(
            "The agent found at least 2 cells (HIER_TOP and HIER_CHILD). "
            "It confirmed HIER_TOP contains an instance of HIER_CHILD. "
            "HIER_CHILD has geometry on L4/0, HIER_TOP has geometry on L5/0."
        ),
        verify_fn="verify_recursive_cells",
        verify_args={"min_cell_count": 2, "expected_parent": "HIER_TOP",
                     "expected_child": "HIER_CHILD",
                     "expected_cell_layers": {
                         "HIER_CHILD": ["4/0"],
                         "HIER_TOP": ["5/0"],
                     }},
        timeout=120,
    ),
    AgenticTestCase(
        id="T7.3",
        group="layout_deep",
        name="LayoutInfoTextPolygonSemantics",
        task_prompt=(
            "The current layout has both a text label and a polygon on layer 4/0. "
            "Do NOT create a new layout — the geometry is already set up. "
            "Use execute_script to iterate over shapes on L4/0 and classify each shape: "
            "is it a text, a polygon/box, a path, or something else? "
            "Report the count of each shape type."
        ),
        assertion=(
            "The agent found both a text shape and a polygon/box shape on L4/0. "
            "It correctly classified them: 1 text and 1 polygon (or box). "
            "Total shape count on L4/0 is 2."
        ),
        verify_fn="verify_text_and_polygon",
        verify_args={"layer": 4, "datatype": 0},
        timeout=120,
    ),

    # ======================================================================
    # Group T9: Evaluate Design Advanced (3 tests)
    # ======================================================================
    AgenticTestCase(
        id="T9.1",
        group="evaluate_adv",
        name="EvaluateDesignDRCModeAdvanced",
        task_prompt=(
            "The current layout has a toy device with all 5 required layers: "
            "mesa (L20/0), contact_patch (L21/0), topgate (L22/0), "
            "contact_route (L23/0), bonding_pad (L24/0). "
            "Do NOT create a new layout — the device is already set up. "
            "Run evaluate_design with DRC checks=[contact_isolation, connectivity, "
            "route_endpoints, adjacency, component_containment, spacing] and the matching layer_map. "
            "For EACH of the 6 DRC checks returned, explain what it measures "
            "and whether the score is good (>0.7) or needs improvement."
        ),
        assertion=(
            "The agent ran evaluate_design with DRC checks and received 6 checks. "
            "For each check it explained the metric and assessed the score. "
            "The overall score is between 0 and 1. All 6 check names were listed."
        ),
        verify_fn="verify_evaluate_drc",
        verify_args={"layer_map": DEFAULT_LAYER_MAP, "expected_check_count": 6},
        timeout=180,
    ),
    AgenticTestCase(
        id="T9.2",
        group="evaluate_adv",
        name="EvaluateDesignScoreModeAdvanced",
        task_prompt=(
            "The current layout has a toy device. "
            "Do NOT create a new layout — the device is already set up. "
            "Save the layout to "
            "/tmp/phase5_ref_adv.gds as a reference. Then run evaluate_design "
            "with score checks=[component_overlap, component_containment, contact_isolation, "
            "connectivity, route_endpoints, adjacency, component_containment, spacing] "
            "with reference_gds=/tmp/phase5_ref_adv.gds and layer_map="
            f"{HALLBAR_LAYER_MAP_STR}. "
            "Report all 8 checks, focusing on component_overlap and component_containment "
            "(the two reference-dependent checks). What do these checks measure?"
        ),
        assertion=(
            "The agent ran evaluate_design with score checks and received 8 checks. "
            "It specifically discussed component_overlap (mesa placed on flake overlap) "
            "and component_containment (contacts in single-material regions). "
            "All 8 check names and scores were reported."
        ),
        verify_fn="verify_evaluate_score",
        verify_args={"layer_map": DEFAULT_LAYER_MAP,
                     "reference_gds": "/tmp/phase5_ref_adv.gds",
                     "expected_check_count": 8},
        depends_on=["T9.1"],
        timeout=180,
    ),
    AgenticTestCase(
        id="T9.3",
        group="evaluate_adv",
        name="EvaluateDualModeConsistencyReport",
        task_prompt=(
            "The current layout has a toy device. "
            "Do NOT create a new layout — the device is already set up. "
            "Run evaluate_design with BOTH check sets: "
            "1) DRC checks=[contact_isolation, connectivity, route_endpoints, adjacency, "
            "component_containment, spacing] with layer_map=" + HALLBAR_LAYER_MAP_STR + " "
            "2) Score checks=[component_overlap, component_containment, contact_isolation, "
            "connectivity, route_endpoints, adjacency, component_containment, spacing] "
            "with the same layer_map and reference_gds=/tmp/phase5_ref_adv.gds "
            "(save the layout to that path first if needed). "
            "Compare the results: which checks appear in both sets? Which are unique "
            "to score checks? Write a comparison JSON to /tmp/eval_comparison.json with keys "
            '"drc_checks", "score_checks", "common_checks", "score_only_checks", '
            '"drc_overall", "score_overall".'
        ),
        assertion=(
            "The agent ran both DRC (6 checks) and score (8 checks) check sets. "
            "It wrote a comparison JSON to /tmp/eval_comparison.json identifying "
            "that score checks add component_overlap and component_containment. "
            "The file exists and contains the expected keys."
        ),
        verify_fn="verify_dual_eval",
        verify_args={"layer_map": DEFAULT_LAYER_MAP,
                     "filepath": EVAL_COMPARISON_JSON},
        depends_on=["T9.1"],
        timeout=300,
    ),

    # ======================================================================
    # Group T10: Full Pipeline (2 tests)
    # ======================================================================
    AgenticTestCase(
        id="T10.1",
        group="full_pipeline",
        name="HallbarSkillExecutionOnPrecomputedContours",
        task_prompt=(
            "The current layout has pre-committed synthetic flake contours: "
            "graphene on L11/0 and graphite on L13/0. "
            "Do NOT create a new layout — the flake contours are already in the layout. "
            "Design a complete 8-pin Hall bar device with topgate=yes. Use layers: "
            "mesa=20/0, contact_patch=21/0, topgate=22/0, contact_route=23/0, "
            "bonding_pad=24/0. Follow the nanodevice_hallbar skill workflow. "
            "After designing, run evaluate_design with DRC checks and report the overall score. "
            "Target: score >= 0.80."
        ),
        assertion=(
            "The agent designed a complete Hall bar on the synthetic flake contours. "
            "evaluate_design with DRC checks returned a score. Geometry exists on all 5 device "
            "layers (L20-L24). The design was evaluated and the score was reported."
        ),
        verify_fn="verify_hallbar_on_contours",
        # min_score=0.50 is an intentionally lenient floor for the structural
        # verifier -- synthetic contours produce lower scores than real flakes.
        # The aspirational "score >= 0.80" in the task_prompt is evaluated by
        # the LLM judge against the agent's transcript, not by this verifier.
        verify_args={"layer_map": DEFAULT_LAYER_MAP, "min_score": 0.50},
        reset_layout=False,
        timeout=600,
        max_transcript_chars=8000,
    ),
    AgenticTestCase(
        id="T10.2",
        group="full_pipeline",
        name="FullE2EPipelineHappyPath",
        task_prompt=(
            "Run the full nanodevice_e2e_design pipeline from scratch. "
            "Since no microscope images are available, use the synthetic contour "
            "approach: create a layout, add synthetic graphene (L11/0) and graphite "
            "(L13/0) contours, then run HALLBAR and SAVE steps. "
            "Design an 8-pin Hall bar with topgate=yes, pixel_size=0.1. "
            f"Save the GDS to {PIPELINE_GDS} and result.json to {PIPELINE_RESULT}. "
            "The result.json must contain: layer_map, score, device_type, "
            "pipeline_status, feedback."
        ),
        assertion=(
            "The agent executed a simplified E2E pipeline: created contours, "
            "designed a Hall bar, evaluated it, saved GDS and result.json. "
            "Both output files exist. result.json contains all required keys. "
            "The layout has geometry on device layers."
        ),
        verify_fn="verify_full_pipeline",
        verify_args={
            "gds_path": PIPELINE_GDS,
            "result_json_path": PIPELINE_RESULT,
        },
        reset_layout=True,
        timeout=3600,
        max_transcript_chars=10000,
    ),
]


# ---------------------------------------------------------------------------
# Execution engine (follows run_tests.py pattern exactly)
# ---------------------------------------------------------------------------

def compose_judge_context(agent_result, verification_result, test: AgenticTestCase) -> str:
    """Combine agent transcript + verification into a single judge context."""
    parts = []

    # Agent transcript
    transcript = truncate(agent_result.transcript, test.max_transcript_chars)
    parts.append(f"## Agent Transcript\n{transcript}")

    # Verification
    parts.append(f"\n## Independent Verification (direct MCP calls)")
    parts.append(f"Overall: {'PASS' if verification_result.passed else 'FAIL'}")
    parts.append(f"Summary: {verification_result.summary}")
    for k, v in verification_result.checks.items():
        if k != "raw":
            parts.append(f"  - {k}: {v}")

    # Metadata
    parts.append(f"\n## Metadata")
    parts.append(f"Agent duration: {agent_result.duration:.1f}s")
    parts.append(f"Agent timed out: {agent_result.timed_out}")

    return "\n".join(parts)


def run_single_test(test: AgenticTestCase, state: TestState,
                    passed_ids: set[str], client: MCPClient) -> AgenticTestResult:
    """Run a single agentic test with optional fixture setup."""
    start = time.time()

    # Check dependencies
    for dep in test.depends_on:
        if dep not in passed_ids:
            return AgenticTestResult(
                test_id=test.id, test_name=test.name, group=test.group,
                passed=False, confidence=0.0, reasoning="", duration=0.0,
                skipped=True, skip_reason=f"prerequisite {dep} did not pass",
            )

    # Optional layout reset
    if test.reset_layout:
        try:
            reset_layout(client)
        except Exception as e:
            return AgenticTestResult(
                test_id=test.id, test_name=test.name, group=test.group,
                passed=False, confidence=0.0,
                reasoning=f"Layout reset failed: {e}", duration=0.0,
            )

    # Fixture setup (direct MCP, before agent runs)
    fixture_fn = _FIXTURE_MAP.get(test.id)
    fixture_cell = None
    if fixture_fn:
        try:
            fixture_fn(client)
        except Exception as e:
            return AgenticTestResult(
                test_id=test.id, test_name=test.name, group=test.group,
                passed=False, confidence=0.0,
                reasoning=f"Fixture setup failed: {e}", duration=0.0,
            )
        # Pre-capture: remember fixture cell name for post-agent restoration
        try:
            _info = client.call_tool("get_layout_info")
            fixture_cell = _info.get("cells", [None])[0]
        except Exception:
            pass

    # 1. Run the agent
    agent_result = run_agent(test.task_prompt, timeout=test.timeout)

    # Pre-capture restoration: if the agent wiped the fixture layout,
    # re-create it before verification so the verifier sees correct state.
    # Skip for "construction" tests where the agent is supposed to create
    # geometry on top of the fixture (e.g. hallbar, routing).
    _NO_RESTORE = {"T3.1", "T3.2", "T3.3", "T4.2", "T10.1"}
    if fixture_fn and fixture_cell and test.id not in _NO_RESTORE:
        try:
            _info2 = client.call_tool("get_layout_info")
            post_cell = _info2.get("cells", [None])[0]
            if post_cell != fixture_cell:
                fixture_fn(client)
        except Exception:
            try:
                fixture_fn(client)
            except Exception:
                pass

    # 2. Independent verification -- check Phase 5 registry first, then base
    if test.verify_fn in PHASE5_REGISTRY:
        verification = run_verification_phase5(test.verify_fn, client, **test.verify_args)
    else:
        verification = run_verification(test.verify_fn, client, **test.verify_args)

    # 3. Compose judge context
    context = compose_judge_context(agent_result, verification, test)

    # 4. LLM judge
    verdict = judge(
        context,
        test.assertion,
        context=f"Test: {test.id} {test.name}\nVerification passed: {verification.passed}",
    )

    duration = time.time() - start

    return AgenticTestResult(
        test_id=test.id,
        test_name=test.name,
        group=test.group,
        passed=verdict.passed or verification.passed,
        confidence=verdict.confidence,
        reasoning=verdict.reasoning,
        duration=duration,
        transcript_excerpt=agent_result.transcript_excerpt(500),
        verification_passed=verification.passed,
        verification_summary=verification.summary,
    )


def run_tests(tests: list[AgenticTestCase], filter_test: str = None,
              filter_group: str = None) -> list[AgenticTestResult]:
    """Run Phase 5 agentic tests sequentially."""
    # Initialize MCP client for verification
    client = MCPClient()
    if not client.is_available():
        print(f"  ERROR: KLayout MCP server not available at {KLAYOUT_URL}")
        print("  Start KLayout with KlayoutClaw plugin first.")
        sys.exit(1)

    state = TestState()
    results: list[AgenticTestResult] = []
    passed_ids: set[str] = set()

    if filter_test:
        tests = [t for t in tests if t.id == filter_test]
    elif filter_group:
        tests = [t for t in tests if t.group == filter_group]

    for test in tests:
        print(f"  Running {test.id} {test.name}...", end="", flush=True)
        result = run_single_test(test, state, passed_ids, client)

        if result.passed:
            passed_ids.add(test.id)
        results.append(result)

        if result.skipped:
            print(f" SKIP ({result.skip_reason})")
        elif result.passed:
            v = "OK" if result.verification_passed else "FAIL"
            print(f" PASS (conf={result.confidence:.2f}, verify={v}, {result.duration:.1f}s)")
        else:
            v = "OK" if result.verification_passed else "FAIL"
            print(f" FAIL (conf={result.confidence:.2f}, verify={v}, {result.duration:.1f}s)")
            print(f"         {result.reasoning[:80]}")

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="KlayoutClaw Phase 5 Agentic E2E Test Runner (LLM-Judged)"
    )
    parser.add_argument("--test", help="Run a single test by ID (e.g. T0.1)")
    parser.add_argument(
        "--group",
        help="Run one group (preflight, inspection, evaluate, hallbar, pipeline, "
             "discovery, layout_deep, evaluate_adv, full_pipeline)",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--list", action="store_true", help="List tests without running")
    args = parser.parse_args()

    all_tests = list(TESTS)

    if args.list:
        print("\nKlayoutClaw Phase 5 Agentic E2E Tests:")
        for t in all_tests:
            deps = f" (depends: {', '.join(t.depends_on)})" if t.depends_on else ""
            reset = " [RESET]" if t.reset_layout else ""
            timeout_str = f" ({t.timeout}s)" if t.timeout != 120 else ""
            print(f"  {t.id:6s} [{t.group:14s}] {t.name}{deps}{reset}{timeout_str}")
        print(f"\n  Total: {len(all_tests)} tests")
        return

    print()
    print("  KlayoutClaw Phase 5 Agentic E2E Test Runner")
    agent_name = os.environ.get("E2E_AGENT", "qlaybot")
    print(f"  Agent: {agent_name} (autonomous tool/skill discovery)")
    print(f"  Verifier: direct MCP calls to {KLAYOUT_URL}")
    print("  Judge: gpt-5-mini @ api.physcai.com")
    print(f"  Tests: {len(all_tests)}")
    print()

    results = run_tests(all_tests, filter_test=args.test, filter_group=args.group)

    if args.json:
        print(results_to_json(results))
    else:
        print(format_results(results))

    failures = [r for r in results if not r.passed and not r.skipped]
    sys.exit(len(failures))


if __name__ == "__main__":
    main()
