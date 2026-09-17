#!/usr/bin/env python
"""Functional cross-validation tests for Phase 2 + Phase 3 skills.

Replaces test_phase2_phase3_skills.py (structural string-matching) with
functional tests that cross-reference SKILL.md content against live MCP
tool schemas and runtime behavior.

Skills under test:
  - nanodevice_hallbar (10-step Hall bar design protocol)
  - nanodevice_e2e_design (7-step orchestrator pipeline)

Test classes:
  TestHallbarSkillExists        -- document-only (no MCP)
  TestE2ESkillExists            -- document-only (no MCP)
  TestHallbarSkillToolRefs      -- MCP cross-validation
  TestHallbarSkillLayerConv     -- MCP cross-validation
  TestE2ESkillGateConditions    -- MCP cross-validation

Marked with @pytest.mark.mcp where MCP is needed.
Skip MCP tests with: pytest -m "not mcp"
"""

import json
import os
import re
import urllib.request
import urllib.error

import pytest

from mcp_identity import is_klayoutclaw
import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HALLBAR_SKILL = os.path.join(PROJECT_ROOT, "skills", "nanodevice_hallbar", "SKILL.md")
E2E_SKILL = os.path.join(PROJECT_ROOT, "skills", "nanodevice_e2e_design", "SKILL.md")

MCP_URL = os.environ.get("KLAYOUT_MCP_URL", "http://127.0.0.1:8765/mcp")

# python_path override: the MCP server's conda activation path may be wrong
# (e.g. hardcoded miniforge3 when anaconda3 is installed). Use python_path
# to bypass conda activation and call the Python with gdstk/shapely directly.
_ANACONDA_PYTHON = os.path.expanduser("~/anaconda3/bin/python3")
PYTHON_PATH = _ANACONDA_PYTHON if os.path.isfile(_ANACONDA_PYTHON) else None

# ---------------------------------------------------------------------------
# SKILL.md Helpers
# ---------------------------------------------------------------------------

def _read_skill(path):
    """Read a SKILL.md file and return its full text."""
    with open(path, "r") as f:
        return f.read()


def _parse_frontmatter(text):
    """Extract YAML frontmatter from --- delimiters and return parsed dict."""
    match = re.match(r'^---\s*\n(.*?)\n---', text, re.DOTALL)
    assert match is not None, "No YAML frontmatter found (missing --- delimiters)"
    return yaml.safe_load(match.group(1))

# ---------------------------------------------------------------------------
# MCP Client Helpers (same pattern as test_phase0_func.py)
# ---------------------------------------------------------------------------

_req_id = 0
_session_id = None


def _mcp_available():
    """Check if the KLayout MCP server is reachable."""
    return is_klayoutclaw(MCP_URL)


def mcp_call(method, params=None, timeout=30):
    """Send a JSON-RPC 2.0 request to the MCP server."""
    global _req_id, _session_id
    _req_id += 1
    payload = {"jsonrpc": "2.0", "id": _req_id, "method": method}
    if params:
        payload["params"] = params
    headers = {"Content-Type": "application/json"}
    if _session_id:
        headers["Mcp-Session-Id"] = _session_id
    req = urllib.request.Request(
        MCP_URL, data=json.dumps(payload).encode(),
        headers=headers, method="POST",
    )
    r = urllib.request.urlopen(req, timeout=timeout)
    _session_id = r.headers.get("Mcp-Session-Id", _session_id)
    data = json.loads(r.read().decode())
    if "error" in data:
        raise RuntimeError(f"MCP error: {data['error']}")
    return data


def tool_call(tool_name, timeout=120, **kwargs):
    """Call an MCP tool and return parsed JSON result."""
    result = mcp_call("tools/call", {"name": tool_name, "arguments": kwargs}, timeout=timeout)
    content = result["result"]["content"][0]
    text = content["text"]
    if result["result"].get("isError"):
        raise RuntimeError(f"MCP tool error: {text}")
    return json.loads(text)


def init_session():
    """Initialize the MCP session."""
    mcp_call("initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "test_phase2_phase3_func", "version": "0.1"},
    })


def get_tools_list():
    """Return the list of tool dicts from tools/list."""
    result = mcp_call("tools/list")
    return result["result"]["tools"]


def find_tool(tools, name):
    """Find a tool by name in the tools list, or None."""
    for t in tools:
        if t["name"] == name:
            return t
    return None


# ===================================================================
# Document-only tests (no MCP dependency)
# ===================================================================


class TestHallbarSkillExists:
    """Verify nanodevice_hallbar SKILL.md structure and content."""

    def test_file_exists(self):
        """SKILL.md file exists on disk at the expected path."""
        assert os.path.isfile(HALLBAR_SKILL), (
            f"nanodevice_hallbar/SKILL.md not found at {HALLBAR_SKILL}"
        )

    def test_has_valid_frontmatter(self):
        """YAML frontmatter parses correctly with required fields."""
        text = _read_skill(HALLBAR_SKILL)
        fm = _parse_frontmatter(text)
        assert fm["name"] == "nanodevice_hallbar", (
            f"Expected name='nanodevice_hallbar', got '{fm.get('name')}'"
        )
        assert isinstance(fm.get("description"), str), "description must be a string"
        assert len(fm["description"]) > 20, (
            "description should be a meaningful sentence, not a stub"
        )

    def test_has_all_ten_steps(self):
        """SKILL.md contains headings for Steps 0 through 9."""
        text = _read_skill(HALLBAR_SKILL)
        for step_num in range(10):
            pattern = rf"##\s+Step\s+{step_num}\b"
            match = re.search(pattern, text)
            assert match is not None, (
                f"Missing heading for Step {step_num} in nanodevice_hallbar SKILL.md"
            )


class TestE2ESkillExists:
    """Verify nanodevice_e2e_design SKILL.md structure and content."""

    def test_file_exists(self):
        """SKILL.md file exists on disk at the expected path."""
        assert os.path.isfile(E2E_SKILL), (
            f"nanodevice_e2e_design/SKILL.md not found at {E2E_SKILL}"
        )

    def test_has_valid_frontmatter(self):
        """YAML frontmatter parses correctly with required fields."""
        text = _read_skill(E2E_SKILL)
        fm = _parse_frontmatter(text)
        assert fm["name"] == "nanodevice_e2e_design", (
            f"Expected name='nanodevice_e2e_design', got '{fm.get('name')}'"
        )
        assert isinstance(fm.get("description"), str), "description must be a string"
        assert len(fm["description"]) > 20, (
            "description should be a meaningful sentence, not a stub"
        )


# ===================================================================
# MCP cross-validation tests
# ===================================================================

# Module-level skipif for all MCP tests in classes marked with @pytest.mark.mcp
_mcp_skip = pytest.mark.skipif(
    not _mcp_available(),
    reason=f"KLayout MCP server not reachable at {MCP_URL}",
)


@pytest.mark.mcp
@_mcp_skip
class TestHallbarSkillToolRefs:
    """Cross-validate: tools mentioned in hallbar SKILL.md exist in MCP tools/list."""

    @pytest.fixture(autouse=True, scope="class")
    def _setup(self, request):
        """Initialize MCP session and fetch tools list once for the class."""
        init_session()
        request.cls.tools = get_tools_list()
        request.cls.skill_text = _read_skill(HALLBAR_SKILL)

    def test_get_layout_info_mentioned_and_exists(self):
        """SKILL.md mentions get_layout_info AND MCP server exposes it."""
        assert "get_layout_info" in self.skill_text, (
            "SKILL.md does not mention get_layout_info"
        )
        tool = find_tool(self.tools, "get_layout_info")
        assert tool is not None, (
            "get_layout_info not found in MCP tools/list"
        )
        # Verify it has a schema with properties (even if empty required list)
        assert "inputSchema" in tool, "get_layout_info missing inputSchema"

    def test_evaluate_design_mentioned_and_exists(self):
        """SKILL.md mentions evaluate_design AND MCP server exposes it."""
        assert "evaluate_design" in self.skill_text, (
            "SKILL.md does not mention evaluate_design"
        )
        tool = find_tool(self.tools, "evaluate_design")
        assert tool is not None, (
            "evaluate_design not found in MCP tools/list"
        )
        schema = tool["inputSchema"]
        # evaluate_design requires a gds_path parameter at minimum
        assert "properties" in schema, "evaluate_design schema missing properties"
        assert len(schema["properties"]) >= 1, (
            "evaluate_design should have at least one parameter"
        )

    def test_save_layout_mentioned_and_exists(self):
        """SKILL.md mentions save_layout AND MCP server exposes it."""
        assert "save_layout" in self.skill_text, (
            "SKILL.md does not mention save_layout"
        )
        tool = find_tool(self.tools, "save_layout")
        assert tool is not None, (
            "save_layout not found in MCP tools/list"
        )
        schema = tool["inputSchema"]
        # Must require a filepath parameter
        assert "filepath" in schema.get("properties", {}), (
            "save_layout schema must have a filepath property"
        )
        assert "filepath" in schema.get("required", []), (
            "save_layout schema must require filepath"
        )


@pytest.mark.mcp
@_mcp_skip
class TestHallbarSkillLayerConv:
    """Cross-validate: layer conventions in SKILL.md work with actual MCP tools."""

    @pytest.fixture(autouse=True, scope="class")
    def _setup(self, request):
        """Initialize MCP session."""
        init_session()

    def test_layer_map_matches_evaluate_design(self):
        """Create geometry on ALL 5 hallbar layers (20-24/0), verify via
        get_layout_info, then call evaluate_design with the hallbar layer_map
        to confirm the tool accepts those layer conventions."""
        # Create a fresh layout
        tool_call("create_layout", top_cell="test_layers")
        # Place minimal geometry on ALL 5 hallbar design layers
        layer_map = {
            "mesa": (20, 0),
            "contact_patch": (21, 0),
            "topgate": (22, 0),
            "contact_route": (23, 0),
            "bonding_pad": (24, 0),
        }
        layer_geometry = {
            "mesa":          (0, 0, 50000, 20000),
            "contact_patch": (10000, 5000, 15000, 8000),
            "topgate":       (5000, 2000, 45000, 18000),
            "contact_route": (0, 0, 5000, 1000),
            "bonding_pad":   (-20000, -20000, -10000, -10000),
        }
        for name, (layer, dt) in layer_map.items():
            x1, y1, x2, y2 = layer_geometry[name]
            tool_call("execute_script", code=f"""
ly = pya.Application.instance().main_window().current_view().active_cellview().layout()
cell = ly.top_cell()
li = ly.layer({layer}, {dt})
cell.shapes(li).insert(pya.Box({x1}, {y1}, {x2}, {y2}))
""")
        # Step 1: get_layout_info should report shapes on all 5 layers
        info = tool_call("get_layout_info")
        reported_layers = set(info.get("layers", {}).keys())
        for name, (layer, dt) in layer_map.items():
            expected = f"{layer}/{dt}"
            assert expected in reported_layers, (
                f"Layer {expected} ({name}) not reported by get_layout_info"
            )
            assert info["layers"][expected]["shapes"] >= 1, (
                f"Layer {expected} ({name}) has zero shapes"
            )
        # Step 2: call evaluate_design with the hallbar layer_map to verify
        # the tool accepts L20-24/0 layer conventions from SKILL.md
        hallbar_layer_map = {
            "mesa": [20, 0],
            "contact_patch": [21, 0],
            "topgate": [22, 0],
            "contact_route": [23, 0],
            "bonding_pad": [24, 0],
        }
        eval_kwargs = {
            "layer_map": hallbar_layer_map,
            "mode": "drc",
        }
        if PYTHON_PATH:
            eval_kwargs["python_path"] = PYTHON_PATH
        result = tool_call("evaluate_design", timeout=120, **eval_kwargs)
        assert isinstance(result, dict), (
            f"evaluate_design should return a dict, got {type(result).__name__}"
        )
        # evaluate_design must return a score (overall or score key)
        score_key = "overall" if "overall" in result else "score"
        assert score_key in result, (
            f"evaluate_design result missing both 'overall' and 'score' keys: {list(result.keys())}"
        )

    def test_reference_layers_11_13_exist(self):
        """Create geometry on reference layers (11/0, 13/0) and verify
        get_layout_info reports them correctly."""
        tool_call("create_layout", top_cell="test_ref_layers")
        # Place geometry on graphene (11/0) and graphite (13/0) reference layers
        for layer_num in [11, 13]:
            tool_call("execute_script", code=f"""
ly = pya.Application.instance().main_window().current_view().active_cellview().layout()
cell = ly.top_cell()
li = ly.layer({layer_num}, 0)
cell.shapes(li).insert(pya.Box(0, 0, 20000, 10000))
""")
        info = tool_call("get_layout_info")
        reported_layers = set(info.get("layers", {}).keys())
        assert "11/0" in reported_layers, (
            "Graphene reference layer 11/0 not reported by get_layout_info"
        )
        assert "13/0" in reported_layers, (
            "Graphite reference layer 13/0 not reported by get_layout_info"
        )
        # Verify shape counts are non-zero
        for layer_spec in ("11/0", "13/0"):
            assert info["layers"][layer_spec]["shapes"] >= 1, (
                f"Layer {layer_spec} has zero shapes after insertion"
            )


@pytest.mark.mcp
@_mcp_skip
class TestE2ESkillGateConditions:
    """Cross-validate: gate condition tools referenced in e2e SKILL.md are callable
    and return the expected result structure."""

    @pytest.fixture(autouse=True, scope="class")
    def _setup(self, request):
        """Initialize MCP session."""
        init_session()

    def test_validate_gate_pixel_size_testable(self):
        """Call validate_pixel_size(0.1) and verify it returns a valid=true
        result with expected fields, matching the VALIDATE gate in e2e SKILL.md."""
        # The e2e skill says: "validate_pixel_size returns a confirmed pixel_size"
        skill_text = _read_skill(E2E_SKILL)
        assert "validate_pixel_size" in skill_text, (
            "e2e SKILL.md does not mention validate_pixel_size"
        )
        result = tool_call("validate_pixel_size", pixel_size=0.1)
        # Must return a dict with valid=true for 0.1 um/px (50x objective)
        assert isinstance(result, dict), (
            f"validate_pixel_size should return a dict, got {type(result).__name__}"
        )
        assert result.get("valid") is True, (
            f"validate_pixel_size(0.1) should return valid=true, got {result}"
        )
        # Must include likely_objective and pixel_size fields
        assert "likely_objective" in result, (
            "validate_pixel_size result missing 'likely_objective' field"
        )
        assert result["likely_objective"] == "50x", (
            f"Expected likely_objective='50x' for 0.1 um/px, got '{result['likely_objective']}'"
        )
        assert result["pixel_size"] == 0.1, (
            f"Expected pixel_size=0.1, got {result['pixel_size']}"
        )

    def test_evaluate_gate_score_threshold_testable(self):
        """Call evaluate_design on a minimal layout and verify it returns
        an overall score in [0, 1], matching the HALLBAR gate in e2e SKILL.md."""
        # The e2e skill says: "evaluate_design score >= 0.80"
        skill_text = _read_skill(E2E_SKILL)
        assert "evaluate_design" in skill_text, (
            "e2e SKILL.md does not mention evaluate_design"
        )
        assert "score" in skill_text.lower() and "0.80" in skill_text, (
            "e2e SKILL.md does not mention the 0.80 score threshold"
        )
        # Create a minimal layout with geometry on hallbar layers
        tool_call("create_layout", top_cell="test_eval_gate")
        # Place geometry on all required layers (mesa, contact_patch, topgate,
        # contact_route, bonding_pad) so evaluate_design can process them
        layer_specs = [
            (20, 0, 0, 0, 50000, 20000),       # mesa
            (21, 0, 10000, 5000, 15000, 8000),  # contact_patch
            (22, 0, 5000, 2000, 45000, 18000),  # topgate
            (23, 0, 0, 0, 5000, 1000),          # contact_route
            (24, 0, -20000, -20000, -10000, -10000),  # bonding_pad
        ]
        for layer, dt, x1, y1, x2, y2 in layer_specs:
            tool_call("execute_script", code=f"""
ly = pya.Application.instance().main_window().current_view().active_cellview().layout()
cell = ly.top_cell()
li = ly.layer({layer}, {dt})
cell.shapes(li).insert(pya.Box({x1}, {y1}, {x2}, {y2}))
""")
        # Call evaluate_design in DRC mode (no reference_gds needed)
        # with the hallbar layer_map convention from the SKILL.md
        hallbar_layer_map = {
            "mesa": [20, 0],
            "contact_patch": [21, 0],
            "topgate": [22, 0],
            "contact_route": [23, 0],
            "bonding_pad": [24, 0],
        }
        eval_kwargs = {
            "layer_map": hallbar_layer_map,
            "mode": "drc",
        }
        if PYTHON_PATH:
            eval_kwargs["python_path"] = PYTHON_PATH
        result = tool_call("evaluate_design", timeout=120, **eval_kwargs)
        assert isinstance(result, dict), (
            f"evaluate_design should return a dict, got {type(result).__name__}"
        )
        # The result must contain an 'overall' or 'score' key in [0, 1]
        score_key = "overall" if "overall" in result else "score"
        assert score_key in result, (
            f"evaluate_design result missing both 'overall' and 'score' keys: {list(result.keys())}"
        )
        score = result[score_key]
        assert isinstance(score, (int, float)), (
            f"Score must be numeric, got {type(score).__name__}"
        )
        assert 0.0 <= score <= 1.0, (
            f"Score must be in [0, 1], got {score}"
        )
