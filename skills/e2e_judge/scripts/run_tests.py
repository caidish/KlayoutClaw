#!/usr/bin/env python3
"""Agentic E2E Test Runner for KlayoutClaw — 15 tests across 6 groups.

Each test gives Claude Code a natural-language task, lets it autonomously
decide which MCP tools to call, then independently verifies the outcome
via direct MCP calls, and judges both transcript + verification.

Requires:
  - KLayout running with KlayoutClaw plugin (KLAYOUT_MCP_URL, default :8765)
  - Claude Code CLI on PATH
  - Network access to api.physcai.com (LLM judge)

Usage:
    python run_tests.py                     # run all tests
    python run_tests.py --test T2.1         # run a single test
    python run_tests.py --group recon       # run one group
    python run_tests.py --json              # output results as JSON
    python run_tests.py --list              # list all tests
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

# ---------------------------------------------------------------------------
# Artifact paths
# ---------------------------------------------------------------------------
GDS_OUT = "/tmp/klayoutclaw_agentic.gds"
PNG_OUT = "/tmp/klayoutclaw_agentic.png"
HALLBAR_GDS = "/tmp/klayoutclaw_agentic_hallbar.gds"

# ---------------------------------------------------------------------------
# 15 Agentic Test Definitions
# ---------------------------------------------------------------------------
TESTS: list[AgenticTestCase] = [

    # ── Group 0: Reconnaissance ───────────────────────────────────────
    AgenticTestCase(
        id="T0.1",
        group="recon",
        name="Explore available tools",
        task_prompt=(
            "You have access to KlayoutClaw MCP tools for KLayout chip layout design. "
            "List all available tools and briefly describe what each one does."
        ),
        assertion=(
            "The agent discovered and listed the KlayoutClaw MCP tools "
            "(at minimum: create_layout, execute_script, save_layout, get_layout_info, screenshot). "
            "The response shows the agent probed the tool set and understood their purposes."
        ),
        verify_fn="verify_no_op",
        timeout=60,
    ),
    AgenticTestCase(
        id="T0.2",
        group="recon",
        name="Inspect layout state",
        task_prompt=(
            "Using the KlayoutClaw MCP tools, inspect the current layout state. "
            "Report: the top cell name, database unit (dbu), how many cells exist, "
            "and what layers have geometry."
        ),
        assertion=(
            "The agent queried the layout info and reported the top cell name, dbu, "
            "cell count, and layer information. The reported values should be consistent "
            "with the independent verification data."
        ),
        verify_fn="verify_layout_exists",
        timeout=60,
    ),

    # ── Group 1: Layout Creation ──────────────────────────────────────
    AgenticTestCase(
        id="T1.1",
        group="layout",
        name="Create a fresh layout",
        task_prompt=(
            "Create a new KLayout layout with a top cell named 'AGENTIC_TEST' "
            "and a database unit of 0.001 microns."
        ),
        assertion=(
            "The agent created a new layout. The top cell is named 'AGENTIC_TEST' "
            "and the dbu is 0.001. Independent verification confirms this."
        ),
        verify_fn="verify_layout_info",
        verify_args={"expected_top_cell": "AGENTIC_TEST", "expected_dbu": 0.001},
        reset_layout=True,
        timeout=60,
    ),
    AgenticTestCase(
        id="T1.2",
        group="layout",
        name="Create a child cell",
        task_prompt=(
            "In the current layout, create a new cell called 'CONTACT_PAD'. "
            "Do not add any geometry yet — just create the empty cell."
        ),
        assertion=(
            "The agent created a cell named 'CONTACT_PAD' without errors. "
            "Independent verification confirms the cell exists in the layout."
        ),
        verify_fn="verify_cell_exists",
        verify_args={"cell_name": "CONTACT_PAD"},
        depends_on=["T1.1"],
        timeout=60,
    ),
    AgenticTestCase(
        id="T1.3",
        group="layout",
        name="List all cells",
        task_prompt=(
            "Check the current layout and tell me how many cells exist. "
            "List all their names."
        ),
        assertion=(
            "The agent reported at least 2 cells including AGENTIC_TEST and CONTACT_PAD. "
            "The count matches independent verification."
        ),
        verify_fn="verify_cell_count",
        verify_args={"min_cells": 2},
        depends_on=["T1.2"],
        timeout=60,
    ),

    # ── Group 2: Geometry Drawing ─────────────────────────────────────
    AgenticTestCase(
        id="T2.1",
        group="geometry",
        name="Draw a rectangle",
        task_prompt=(
            "Draw a rectangle on cell 'AGENTIC_TEST', layer 1 datatype 0. "
            "The rectangle should be 100 microns wide and 50 microns tall, "
            "centered at the origin."
        ),
        assertion=(
            "The agent drew a rectangle on layer 1/0 in cell AGENTIC_TEST. "
            "Independent verification confirms at least 1 shape on layer 1/0 "
            "with a bounding box approximately 100x50 microns centered at origin."
        ),
        verify_fn="verify_layer_has_shapes",
        verify_args={"cell_name": "AGENTIC_TEST", "layer": 1, "datatype": 0, "min_count": 1},
        depends_on=["T1.1"],
        timeout=90,
    ),
    AgenticTestCase(
        id="T2.2",
        group="geometry",
        name="Draw a polygon",
        task_prompt=(
            "On cell 'AGENTIC_TEST', layer 2 datatype 0, draw a pentagon "
            "(5-sided polygon) with vertices at: (0,0), (100,0), (120,60), "
            "(50,100), (-20,60). All coordinates in microns."
        ),
        assertion=(
            "The agent drew a 5-point polygon on layer 2/0. "
            "Independent verification confirms at least 1 shape on layer 2/0."
        ),
        verify_fn="verify_layer_has_shapes",
        verify_args={"cell_name": "AGENTIC_TEST", "layer": 2, "datatype": 0, "min_count": 1},
        depends_on=["T1.1"],
        timeout=90,
    ),
    AgenticTestCase(
        id="T2.3",
        group="geometry",
        name="Draw a path",
        task_prompt=(
            "On cell 'AGENTIC_TEST', layer 3 datatype 0, draw a path with "
            "width 10 microns that goes from (-100,0) to (0,0) to (0,100) to (100,100). "
            "This is an L-shaped routing path."
        ),
        assertion=(
            "The agent drew a path on layer 3/0 with 4 waypoints and width 10um. "
            "Independent verification confirms geometry exists on layer 3/0."
        ),
        verify_fn="verify_layer_has_shapes",
        verify_args={"cell_name": "AGENTIC_TEST", "layer": 3, "datatype": 0, "min_count": 1},
        depends_on=["T1.1"],
        timeout=90,
    ),

    # ── Group 3: Multi-step Composition ───────────────────────────────
    AgenticTestCase(
        id="T3.1",
        group="compose",
        name="Create cell + place instance",
        task_prompt=(
            "Create a new cell called 'PAD' containing a 100x100 micron square "
            "on layer 5 datatype 0, centered at the origin. Then place an instance "
            "of 'PAD' inside 'AGENTIC_TEST' at position (500, 500) microns."
        ),
        assertion=(
            "The agent performed a multi-step operation: created 'PAD' cell, "
            "added a square, and placed it in the top cell. Independent verification "
            "confirms PAD cell exists with geometry on layer 5/0."
        ),
        verify_fn="verify_cell_exists",
        verify_args={"cell_name": "PAD"},
        depends_on=["T1.1"],
        timeout=90,
    ),
    AgenticTestCase(
        id="T3.2",
        group="compose",
        name="Multi-layer geometry",
        task_prompt=(
            "On cell 'AGENTIC_TEST', add geometry to three layers:\n"
            "1) A 200x25 micron channel on layer 10/0 centered at origin\n"
            "2) Two 50x50 micron contact pads on layer 11/0 at (-150,0) and (150,0)\n"
            "3) Connecting paths on layer 12/0 with width 5um from each pad to the channel ends"
        ),
        assertion=(
            "The agent created geometry on three separate layers (10/0, 11/0, 12/0). "
            "Verification confirms shapes exist on all three layers."
        ),
        verify_fn="verify_multi_layer",
        verify_args={"expected_layers": [[10, 0], [11, 0], [12, 0]]},
        depends_on=["T1.1"],
        timeout=120,
    ),
    AgenticTestCase(
        id="T3.3",
        group="compose",
        name="Query then modify",
        task_prompt=(
            "First, check the current layout to see what geometry already exists "
            "and report what you find. Then add a text label on layer 20 datatype 0 "
            "in the top cell that says 'SAMPLE_ID: A001' at position (0, 200) microns."
        ),
        assertion=(
            "The agent first inspected the layout (showing awareness of existing geometry), "
            "then added a text label. The response demonstrates inspect-then-modify behavior."
        ),
        verify_fn="verify_layer_has_shapes",
        verify_args={"cell_name": "AGENTIC_TEST", "layer": 20, "datatype": 0, "min_count": 1},
        depends_on=["T2.1"],
        timeout=90,
    ),

    # ── Group 4: Export & Artifacts ───────────────────────────────────
    AgenticTestCase(
        id="T4.1",
        group="export",
        name="Save layout as GDS",
        task_prompt=f"Save the current layout to a GDS2 file at {GDS_OUT}.",
        assertion=(
            "The agent saved the layout as a GDS2 file. "
            "Independent verification confirms the file exists on disk with non-zero size."
        ),
        verify_fn="verify_file_exists",
        verify_args={"filepath": GDS_OUT, "min_size": 100},
        depends_on=["T2.1"],
        timeout=60,
    ),
    AgenticTestCase(
        id="T4.2",
        group="export",
        name="Take a screenshot",
        task_prompt=f"Take a screenshot of the current KLayout viewport and save it to {PNG_OUT}.",
        assertion=(
            "The agent captured a screenshot. "
            "The PNG file exists on disk and has valid PNG format."
        ),
        verify_fn="verify_screenshot_exists",
        verify_args={"filepath": PNG_OUT},
        depends_on=["T2.1"],
        timeout=60,
    ),

    # ── Group 5: Full Device Design ───────────────────────────────────
    AgenticTestCase(
        id="T5.1",
        group="device",
        name="Design a Hall bar from spec",
        task_prompt=(
            "Design and create a graphene Hall bar device in KLayout with these specs:\n"
            "- Create a new layout first\n"
            "- Layer 1/0 (Mesa): rectangular channel 100um long, 25um wide, centered at origin, "
            "with 6 side probes (3 per side, each 10um wide and 20um long)\n"
            "- Layer 2/0 (Metal): contact traces connecting each probe to bonding pads\n"
            "- Layer 3/0 (Pads): 8 bonding pads, each 100x100um, around the periphery\n"
            f"Save the result to {HALLBAR_GDS}"
        ),
        assertion=(
            "The agent created a complete Hall bar with geometry on 3 layers "
            "(1/0 mesa, 2/0 metal, 3/0 pads). The GDS was saved. "
            "Independent verification confirms shapes on all expected layers."
        ),
        verify_fn="verify_device_structure",
        verify_args={
            "expected_layers": [[1, 0], [2, 0], [3, 0]],
            "min_shapes_per_layer": {1: 1, 2: 1, 3: 1},
        },
        reset_layout=True,
        timeout=300,
        max_transcript_chars=6000,
    ),
    AgenticTestCase(
        id="T5.2",
        group="device",
        name="Describe the device",
        task_prompt=(
            "Inspect the current layout and give a detailed technical description: "
            "how many layers have geometry, what is the bounding box in microns, "
            "how many shapes per layer, and does this look like a Hall bar device?"
        ),
        assertion=(
            "The agent provided a technical description identifying the layer structure, "
            "approximate dimensions, and Hall bar nature of the device. "
            "The description is consistent with a multi-layer device."
        ),
        verify_fn="verify_layout_exists",
        depends_on=["T5.1"],
        timeout=90,
    ),
]


# ---------------------------------------------------------------------------
# Execution engine
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
    """Run a single agentic test."""
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

    # 1. Run the agent
    agent_result = run_agent(test.task_prompt, timeout=test.timeout)

    # 2. Independent verification
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
        passed=verdict.passed,
        confidence=verdict.confidence,
        reasoning=verdict.reasoning,
        duration=duration,
        transcript_excerpt=agent_result.transcript_excerpt(500),
        verification_passed=verification.passed,
        verification_summary=verification.summary,
    )


def run_tests(tests: list[AgenticTestCase], filter_test: str = None,
              filter_group: str = None) -> list[AgenticTestResult]:
    """Run agentic tests sequentially."""
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
        description="KlayoutClaw Agentic E2E Test Runner (LLM-Judged)"
    )
    parser.add_argument("--test", help="Run a single test by ID (e.g. T2.1)")
    parser.add_argument(
        "--group",
        help="Run one group (recon, layout, geometry, compose, export, device)",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--list", action="store_true", help="List tests without running")
    args = parser.parse_args()

    if args.list:
        print("\nKlayoutClaw Agentic E2E Tests:")
        for t in TESTS:
            deps = f" (depends: {', '.join(t.depends_on)})" if t.depends_on else ""
            reset = " [RESET]" if t.reset_layout else ""
            print(f"  {t.id:6s} [{t.group:10s}] {t.name}{deps}{reset}")
        return

    print()
    print("  KlayoutClaw Agentic E2E Test Runner")
    print("  Agent: claude --print (autonomous tool selection)")
    print(f"  Verifier: direct MCP calls to {KLAYOUT_URL}")
    print("  Judge: gpt-5-mini @ api.physcai.com")
    print()

    results = run_tests(TESTS, filter_test=args.test, filter_group=args.group)

    if args.json:
        print(results_to_json(results))
    else:
        print(format_results(results))

    failures = [r for r in results if not r.passed and not r.skipped]
    sys.exit(len(failures))


if __name__ == "__main__":
    main()
