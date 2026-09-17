#!/usr/bin/env python
"""Phase 4 MCP cross-validation tests.

These tests connect to a running KLayout MCP server and cross-validate:
- MCP tools/list response against documented tools in CLAUDE.md
- Tool input schemas against docs/tools.md parameter tables
- Read-only guarantees for evaluate_design and validate_pixel_size
- Workspace TOOLS.md references against actual MCP tool names
- Skill directory structure against docs/skills.md listings

Marked with @pytest.mark.mcp -- skip with: pytest -m "not mcp"
When KLayout is not running, all tests are auto-skipped.
"""

import json
import os
import re
import subprocess
import urllib.request
import urllib.error

import pytest

from mcp_identity import is_klayoutclaw

# ---------------------------------------------------------------------------
# MCP Client Helpers (copied from test_phase0_mcp.py)
# ---------------------------------------------------------------------------

MCP_URL = os.environ.get("KLAYOUT_MCP_URL", "http://127.0.0.1:8765/mcp")
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


def tool_call(tool_name, **kwargs):
    """Call an MCP tool and return parsed JSON result."""
    result = mcp_call("tools/call", {"name": tool_name, "arguments": kwargs}, timeout=30)
    content = result["result"]["content"][0]
    text = content["text"]
    if result["result"].get("isError"):
        raise RuntimeError(f"MCP tool error: {text}")
    return json.loads(text)


def tool_call_raw(tool_name, **kwargs):
    """Call an MCP tool and return the raw text response (no JSON parse)."""
    result = mcp_call("tools/call", {"name": tool_name, "arguments": kwargs}, timeout=30)
    content = result["result"]["content"][0]
    text = content["text"]
    if result["result"].get("isError"):
        raise RuntimeError(f"MCP tool error: {text}")
    return text


def init_session():
    """Initialize the MCP session."""
    mcp_call("initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "test_phase4_mcp", "version": "0.1"},
    })


# ---------------------------------------------------------------------------
# Skip condition
# ---------------------------------------------------------------------------

_mcp_ok = _mcp_available()

pytestmark = [
    pytest.mark.mcp,
    pytest.mark.skipif(not _mcp_ok, reason=f"KLayout MCP server not reachable at {MCP_URL}"),
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLAUDE_MD = os.path.join(PROJECT_ROOT, "CLAUDE.md")
DOCS_TOOLS = os.path.join(PROJECT_ROOT, "docs", "tools.md")
DOCS_SKILLS = os.path.join(PROJECT_ROOT, "docs", "skills.md")
WORKSPACE_TOOLS = os.path.join(PROJECT_ROOT, "agent", "workspace", "TOOLS.md")
SKILLS_DIR = os.path.join(PROJECT_ROOT, "skills")


def _parse_documented_tools():
    """Parse CLAUDE.md tool table at runtime to extract tool names.

    Looks for rows like: | `create_layout` | Create new layout + top cell |
    Returns a sorted list of tool names extracted from backtick-wrapped column.
    """
    with open(CLAUDE_MD, "r") as f:
        content = f.read()
    # Match table rows with backtick-wrapped tool names in the first column
    # Pattern: | `tool_name` | description |
    tools = re.findall(r"^\|\s*`(\w+)`\s*\|", content, re.MULTILINE)
    # Filter out table header words like "Tool"
    tools = [t for t in tools if t not in ("Tool",)]
    assert len(tools) > 0, (
        f"Failed to parse any tool names from CLAUDE.md tool table at {CLAUDE_MD}"
    )
    return sorted(set(tools))


def _parse_documented_skills():
    """Parse docs/skills.md and CLAUDE.md at runtime to extract skill directory names.

    Uses two strategies:
    1. Match explicit skills/<dirname>/ patterns in docs/skills.md
    2. Parse the CLAUDE.md directory tree under skills/ for subdirectory entries
    Returns unique skill directory names (excludes non-skill dirs like scripts, tests).
    """
    with open(DOCS_SKILLS, "r") as f:
        skills_content = f.read()
    with open(CLAUDE_MD, "r") as f:
        claude_content = f.read()

    # Non-skill subdirectories that appear in the tree but are not skills
    EXCLUDE = {"scripts", "tests"}
    matches = set()

    # Strategy 1: explicit skills/<dirname>/ patterns in both files
    for content in (skills_content, claude_content):
        for m in re.findall(r"skills/([a-z][a-z0-9_]*)/", content):
            if m not in EXCLUDE:
                matches.add(m)

    # Strategy 2: parse CLAUDE.md directory tree section under skills/
    # The tree has lines like:  |   +-- geometry/  or  |   +-- nanodevice_routing/
    # Find the skills/ section and extract immediate children
    in_skills_section = False
    for line in claude_content.splitlines():
        if re.search(r"[├└]── skills/", line):
            in_skills_section = True
            continue
        if in_skills_section:
            # Stop at next top-level sibling (line without box-drawing indent)
            if re.match(r"├── |└── ", line):
                in_skills_section = False
                continue
            # Detect directory entries (children of skills/)
            dir_match = re.search(r"[├└]── ([a-z][a-z0-9_]*)/", line)
            if dir_match:
                dirname = dir_match.group(1)
                if dirname not in EXCLUDE:
                    matches.add(dirname)

    skills = sorted(matches)
    assert len(skills) > 0, (
        f"Failed to parse any skill directory names from {DOCS_SKILLS} and {CLAUDE_MD}"
    )
    return skills


def _parse_docs_tools_params(tool_name):
    """Parse docs/tools.md to extract documented parameter names for a tool.

    Reads the parameter table under the ## <tool_name> section.
    Returns a dict: {param_name: {"type": str, "required": bool}}.
    Returns None if the tool section has no parameter table (e.g. get_layout_info).
    """
    with open(DOCS_TOOLS, "r") as f:
        content = f.read()
    # Find the section for this tool
    pattern = rf"^## {re.escape(tool_name)}\s*$(.*?)(?=^## |\Z)"
    match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
    if not match:
        return None
    section = match.group(1)
    # Parse parameter table rows: | `param` | type | yes/no | ...
    rows = re.findall(
        r"^\|\s*`(\w+)`\s*\|\s*(\w+)\s*\|\s*(yes|no)\s*\|",
        section, re.MULTILINE,
    )
    if not rows:
        return None
    return {
        name: {"type": typ, "required": req == "yes"}
        for name, typ, req in rows
    }


# ---------------------------------------------------------------------------
# Session-scoped setup
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def mcp_session():
    """Initialize MCP session once for all tests in this module."""
    init_session()
    yield


def _get_tools_list():
    """Fetch tools/list from MCP and return the tools array."""
    result = mcp_call("tools/list")
    return result["result"]["tools"]


def _get_tool_by_name(tools, name):
    """Find a specific tool in the tools list by name."""
    for t in tools:
        if t["name"] == name:
            return t
    return None


# ---------------------------------------------------------------------------
# Test Class: MCP tools/list matches documentation
# ---------------------------------------------------------------------------

class TestMCPToolListMatchesDocs:
    """Cross-validate MCP tools/list response against CLAUDE.md documentation."""

    def test_tools_list_count_matches_documented(self):
        """MCP tools/list count must match tools parsed from CLAUDE.md."""
        tools = _get_tools_list()
        tool_names = [t["name"] for t in tools]
        documented = _parse_documented_tools()
        assert len(tools) == len(documented), (
            f"CLAUDE.md documents {len(documented)} tools ({documented}), "
            f"but MCP returned {len(tools)}: {tool_names}"
        )

    def test_all_documented_tools_exist_in_mcp(self):
        """Every tool name parsed from CLAUDE.md must exist in MCP tools/list."""
        tools = _get_tools_list()
        mcp_tool_names = {t["name"] for t in tools}
        documented = _parse_documented_tools()
        missing = [name for name in documented if name not in mcp_tool_names]
        assert len(missing) == 0, (
            f"Tools documented in CLAUDE.md but missing from MCP: {missing}. "
            f"MCP has: {sorted(mcp_tool_names)}"
        )

    def test_evaluate_design_schema_matches_docs(self):
        """evaluate_design inputSchema must match parameters documented in docs/tools.md."""
        tools = _get_tools_list()
        tool = _get_tool_by_name(tools, "evaluate_design")
        assert tool is not None, "evaluate_design not found in tools/list"

        schema = tool["inputSchema"]
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        # Parse expected params from docs/tools.md
        doc_params = _parse_docs_tools_params("evaluate_design")
        assert doc_params is not None, (
            "Could not parse evaluate_design parameters from docs/tools.md"
        )

        # Every documented parameter must exist in the schema
        for param_name, param_info in doc_params.items():
            assert param_name in properties, (
                f"docs/tools.md documents '{param_name}' for evaluate_design "
                f"but it is missing from MCP schema. Schema has: {list(properties.keys())}"
            )
            # Check required/optional matches docs
            if param_info["required"]:
                assert param_name in required, (
                    f"docs/tools.md says '{param_name}' is required, "
                    f"but MCP schema required list is: {required}"
                )
            else:
                assert param_name not in required, (
                    f"docs/tools.md says '{param_name}' is optional, "
                    f"but it appears in MCP schema required: {required}"
                )

        # Verify checks and layer_map are required (current schema)
        assert "checks" in required, (
            f"'checks' must be required in evaluate_design schema. required={required}"
        )
        assert "layer_map" in required, (
            f"'layer_map' must be required in evaluate_design schema. required={required}"
        )
        # checks must be an array type
        checks_prop = properties.get("checks", {})
        assert checks_prop.get("type") == "array", (
            f"'checks' property must be type 'array'. Got: {checks_prop}"
        )

    def test_validate_pixel_size_schema_matches_docs(self):
        """validate_pixel_size inputSchema must match parameters documented in docs/tools.md."""
        tools = _get_tools_list()
        tool = _get_tool_by_name(tools, "validate_pixel_size")
        assert tool is not None, "validate_pixel_size not found in tools/list"

        schema = tool["inputSchema"]
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        # Parse expected params from docs/tools.md
        doc_params = _parse_docs_tools_params("validate_pixel_size")
        assert doc_params is not None, (
            "Could not parse validate_pixel_size parameters from docs/tools.md"
        )

        # Every documented parameter must exist in the schema
        for param_name, param_info in doc_params.items():
            assert param_name in properties, (
                f"docs/tools.md documents '{param_name}' for validate_pixel_size "
                f"but it is missing from MCP schema. Schema has: {list(properties.keys())}"
            )
            if param_info["required"]:
                assert param_name in required, (
                    f"docs/tools.md says '{param_name}' is required, "
                    f"but MCP schema required list is: {required}"
                )
            else:
                assert param_name not in required, (
                    f"docs/tools.md says '{param_name}' is optional, "
                    f"but it appears in MCP schema required: {required}"
                )


# ---------------------------------------------------------------------------
# Test Class: Read-only tool guarantees
# ---------------------------------------------------------------------------

class TestMCPToolReadOnly:
    """Verify that evaluate_design and validate_pixel_size do not mutate layout state."""

    @pytest.fixture(autouse=True)
    def setup_known_layout(self):
        """Create a layout with known geometry so we can detect mutations."""
        tool_call("create_layout", name="TEST_READONLY", dbu=0.001)
        tool_call_raw("execute_script", code="""
import pya

lv, ly, tc = _get_or_create_view()

# Add a simple rectangle on layer 1/0
li = ly.layer(1, 0)
tc.shapes(li).insert(pya.Box(0, 0, 50000, 50000))

_refresh_view()
result = "setup complete"
""")
        yield

    def test_evaluate_design_is_readonly(self):
        """get_layout_info before and after calling evaluate_design in DRC mode must be identical."""
        info_before = tool_call("get_layout_info")

        # Call evaluate_design with checks + layer_map
        layer_map = {
            "mesa": [1, 0],
            "contact_patch": [2, 0],
            "topgate": [3, 0],
            "contact_route": [4, 0],
            "bonding_pad": [5, 0],
        }
        checks = [
            {"name": "component_containment", "args": {"component": "contact_patch", "region": "mesa"}, "weight": 1.0},
        ]
        try:
            tool_call("evaluate_design", layer_map=layer_map, checks=checks)
        except RuntimeError:
            # evaluate_design may error (missing conda env, etc.) -- that is fine,
            # what matters is that the layout was not mutated
            pass

        info_after = tool_call("get_layout_info")

        # Compare key layout properties
        assert info_before["dbu"] == info_after["dbu"], (
            f"dbu changed: {info_before['dbu']} -> {info_after['dbu']}"
        )
        assert info_before["num_layers"] == info_after["num_layers"], (
            f"num_layers changed: {info_before['num_layers']} -> {info_after['num_layers']}"
        )
        assert info_before["num_cells"] == info_after["num_cells"], (
            f"num_cells changed: {info_before['num_cells']} -> {info_after['num_cells']}"
        )
        # Compare per-layer shape counts
        for layer_key, layer_data in info_before["layers"].items():
            assert layer_key in info_after["layers"], (
                f"Layer {layer_key} disappeared after evaluate_design"
            )
            assert layer_data["shapes"] == info_after["layers"][layer_key]["shapes"], (
                f"Layer {layer_key} shape count changed: "
                f"{layer_data['shapes']} -> {info_after['layers'][layer_key]['shapes']}"
            )

    def test_validate_pixel_size_is_readonly(self):
        """Layout state must be unchanged after calling validate_pixel_size."""
        info_before = tool_call("get_layout_info")

        tool_call("validate_pixel_size", pixel_size=0.087)

        info_after = tool_call("get_layout_info")

        assert info_before["dbu"] == info_after["dbu"], (
            f"dbu changed: {info_before['dbu']} -> {info_after['dbu']}"
        )
        assert info_before["num_layers"] == info_after["num_layers"], (
            f"num_layers changed: {info_before['num_layers']} -> {info_after['num_layers']}"
        )
        assert info_before["num_cells"] == info_after["num_cells"], (
            f"num_cells changed: {info_before['num_cells']} -> {info_after['num_cells']}"
        )
        for layer_key, layer_data in info_before["layers"].items():
            assert layer_key in info_after["layers"], (
                f"Layer {layer_key} disappeared after validate_pixel_size"
            )
            assert layer_data["shapes"] == info_after["layers"][layer_key]["shapes"], (
                f"Layer {layer_key} shape count changed: "
                f"{layer_data['shapes']} -> {info_after['layers'][layer_key]['shapes']}"
            )


# ---------------------------------------------------------------------------
# Test Class: Workspace TOOLS.md references real MCP tools
# ---------------------------------------------------------------------------

class TestWorkspaceDocsReferenceRealTools:
    """Verify agent/workspace/TOOLS.md tool names exist in MCP."""

    def test_tools_md_tools_all_exist_in_mcp(self):
        """Every klayout_native_* tool in TOOLS.md must map to a real MCP tool name."""
        # Read TOOLS.md and extract klayout_native_* tool names
        with open(WORKSPACE_TOOLS, "r") as f:
            content = f.read()

        # Extract tool names like klayout_native_create_layout
        workspace_tools = re.findall(r"klayout_native_(\w+)", content)
        # De-duplicate
        workspace_tools = sorted(set(workspace_tools))

        assert len(workspace_tools) > 0, (
            "No klayout_native_* tools found in TOOLS.md"
        )

        # Each workspace tool name (after removing prefix) must exist in MCP
        tools = _get_tools_list()
        mcp_tool_names = {t["name"] for t in tools}

        missing = [name for name in workspace_tools if name not in mcp_tool_names]
        assert len(missing) == 0, (
            f"TOOLS.md references tools not in MCP: {['klayout_native_' + n for n in missing]}. "
            f"MCP tools: {sorted(mcp_tool_names)}"
        )

    def test_workflow_evaluate_iterate_is_callable(self):
        """evaluate_design can be invoked and returns structured response."""
        # This tests that the tool exists, can be called, and returns a
        # meaningful response -- not just that the JSON-RPC envelope is valid.
        layer_map = {
            "mesa": [1, 0],
            "contact_patch": [2, 0],
            "topgate": [3, 0],
            "contact_route": [4, 0],
            "bonding_pad": [5, 0],
        }
        checks = [
            {"name": "component_containment", "args": {"component": "contact_patch", "region": "mesa"}, "weight": 1.0},
        ]
        # We use mcp_call directly to inspect the full response structure
        result = mcp_call("tools/call", {
            "name": "evaluate_design",
            "arguments": {"layer_map": layer_map, "checks": checks},
        }, timeout=60)

        # The MCP server must return a valid JSON-RPC result (not a protocol error)
        assert "result" in result, (
            f"evaluate_design did not return a valid MCP result: {result}"
        )
        content = result["result"]["content"]
        assert len(content) > 0, "evaluate_design returned empty content"
        assert "text" in content[0], (
            f"evaluate_design content[0] missing 'text': {content[0]}"
        )

        text = content[0]["text"]
        is_error = result["result"].get("isError", False)

        if not is_error:
            # On success, the response must be valid JSON with expected fields
            parsed = json.loads(text)
            assert "status" in parsed, (
                f"evaluate_design success response missing 'status'. Got: {list(parsed.keys())}"
            )
            assert "checks" in parsed, (
                f"evaluate_design success response missing 'checks'. Got: {list(parsed.keys())}"
            )
            assert isinstance(parsed["checks"], list), (
                f"evaluate_design 'checks' must be a list. Got: {type(parsed['checks'])}"
            )
            # Each check must have name and score
            for check in parsed["checks"]:
                assert "name" in check, f"Check missing 'name': {check}"
                assert "score" in check, f"Check missing 'score': {check}"
        else:
            # If isError, the text must be a non-empty error message (not empty/garbage)
            assert len(text.strip()) > 0, (
                "evaluate_design returned isError=true but text is empty"
            )


# ---------------------------------------------------------------------------
# Test Class: Skill scanning integration
# ---------------------------------------------------------------------------

class TestSkillScanningIntegration:
    """Verify skill directories match documentation and have SKILL.md files."""

    def test_skill_directories_have_skill_md(self):
        """Every skill directory parsed from docs/skills.md must have a SKILL.md file."""
        documented_skills = _parse_documented_skills()
        missing_skill_md = []
        missing_dirs = []

        for skill_name in documented_skills:
            skill_dir = os.path.join(SKILLS_DIR, skill_name)
            if not os.path.isdir(skill_dir):
                missing_dirs.append(skill_name)
                continue
            skill_md = os.path.join(skill_dir, "SKILL.md")
            if not os.path.isfile(skill_md):
                missing_skill_md.append(skill_name)

        assert len(missing_dirs) == 0, (
            f"Skill directories missing from skills/: {missing_dirs}"
        )
        assert len(missing_skill_md) == 0, (
            f"Skills missing SKILL.md: {missing_skill_md}"
        )

    def test_build_skills_section_functional(self):
        """Run test_phase4_skills_func.ts via npx tsx and verify all assertions pass."""
        agent_dir = os.path.join(PROJECT_ROOT, "agent")
        ts_test = os.path.join(PROJECT_ROOT, "tests", "test_phase4_skills_func.ts")

        if not os.path.isfile(ts_test):
            pytest.skip("test_phase4_skills_func.ts not found")

        # Check that npx is available
        try:
            subprocess.run(
                ["npx", "--version"],
                capture_output=True, timeout=10, check=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            pytest.skip("npx not available")

        # Check that agent/node_modules exists (npm install done)
        if not os.path.isdir(os.path.join(agent_dir, "node_modules")):
            pytest.skip("agent/node_modules not found -- run npm install in agent/")

        result = subprocess.run(
            ["npx", "tsx", ts_test],
            capture_output=True, text=True, timeout=30,
            cwd=agent_dir,
        )

        # The TypeScript wrapper must exit cleanly before we trust its output
        assert result.returncode == 0, (
            f"test_phase4_skills_func.ts exited with code {result.returncode}. "
            f"stdout: {result.stdout[:500]}, stderr: {result.stderr[:500]}"
        )

        output = result.stdout + result.stderr

        # Parse PASS/FAIL lines
        pass_count = len(re.findall(r"^PASS:", output, re.MULTILINE))
        fail_lines = re.findall(r"^FAIL:.*$", output, re.MULTILINE)

        assert pass_count > 0, (
            f"No PASS lines found in test_phase4_skills_func.ts output. "
            f"stdout: {result.stdout[:500]}, stderr: {result.stderr[:500]}"
        )
        assert len(fail_lines) == 0, (
            f"test_phase4_skills_func.ts had failures:\n" +
            "\n".join(fail_lines)
        )
