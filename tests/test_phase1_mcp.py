#!/usr/bin/env python
"""MCP integration tests for Phase 1: evaluate_design tool.

These tests connect to a running KLayout instance with the KlayoutClaw
MCP server plugin and exercise the evaluate_design tool over the wire.

Marked with @pytest.mark.mcp -- skip with: pytest -m "not mcp"
When KLayout is not running, all tests are auto-skipped.
"""

import json
import os
import urllib.request
import urllib.error

import pytest

from mcp_identity import is_klayoutclaw

# ---------------------------------------------------------------------------
# MCP Client Helpers (same pattern as test_phase0_mcp.py)
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


def tool_call(tool_name, timeout=120, **kwargs):
    """Call an MCP tool and return parsed JSON result.

    Default timeout is 120s for evaluate_design subprocess calls.
    """
    result = mcp_call("tools/call", {"name": tool_name, "arguments": kwargs}, timeout=timeout)
    content = result["result"]["content"][0]
    text = content["text"]
    if result["result"].get("isError"):
        raise RuntimeError(f"MCP tool error: {text}")
    return json.loads(text)


def tool_call_raw(tool_name, timeout=120, **kwargs):
    """Call an MCP tool and return the raw text response (no JSON parse)."""
    result = mcp_call("tools/call", {"name": tool_name, "arguments": kwargs}, timeout=timeout)
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
        "clientInfo": {"name": "test_phase1_mcp", "version": "0.1"},
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
# Session-scoped setup
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def mcp_session():
    """Initialize MCP session once for all tests in this module."""
    init_session()
    yield


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STANDARD_LAYER_MAP = {
    "mesa": [20, 0],
    "contact_patch": [21, 0],
    "topgate": [22, 0],
    "contact_route": [23, 0],
    "bonding_pad": [24, 0],
}

REFERENCE_GDS_PATH = "/tmp/test_phase1_mcp_reference.gds"

# python_path override: the MCP server's conda activation path may be wrong
# (e.g. hardcoded miniforge3 when anaconda3 is installed). Use python_path
# to bypass conda activation and call the Python with gdstk/shapely directly.
ANACONDA_PYTHON = os.path.expanduser("~/anaconda3/bin/python3")
PYTHON_PATH = ANACONDA_PYTHON if os.path.isfile(ANACONDA_PYTHON) else None

def _eval_kwargs(**extra):
    """Build common kwargs for evaluate_design calls, including python_path if available."""
    kw = {"layer_map": STANDARD_LAYER_MAP}
    if PYTHON_PATH:
        kw["python_path"] = PYTHON_PATH
    kw.update(extra)
    return kw

DRC_CHECK_NAMES = [
    "topgate",
    "contact_isolation",
    "connectivity",
    "route_endpoints",
    "contact_mesa_adjacency",
    "mesa_probes",
]

SCORE_CHECK_NAMES = [
    "mesa_on_overlap",
    "contacts_in_regions",
    "topgate",
    "contact_isolation",
    "connectivity",
    "route_endpoints",
    "contact_mesa_adjacency",
    "mesa_probes",
]

# pya code to create a Hall bar device with all required layers
HALLBAR_DEVICE_PYA = """
import pya

lv, ly, tc = _get_or_create_view()

# --- Layer indices ---
li_mesa = ly.layer(20, 0)
li_contact = ly.layer(21, 0)
li_topgate = ly.layer(22, 0)
li_route = ly.layer(23, 0)
li_pad = ly.layer(24, 0)

# --- Mesa: H-bar shape ---
# Central channel: 60 x 10 um (centered at origin)
tc.shapes(li_mesa).insert(pya.Box(-30000, -5000, 30000, 5000))
# Left top probe: 10 x 30 um
tc.shapes(li_mesa).insert(pya.Box(-35000, 5000, -25000, 35000))
# Left bottom probe: 10 x 30 um
tc.shapes(li_mesa).insert(pya.Box(-35000, -35000, -25000, -5000))
# Right top probe: 10 x 30 um
tc.shapes(li_mesa).insert(pya.Box(25000, 5000, 35000, 35000))
# Right bottom probe: 10 x 30 um
tc.shapes(li_mesa).insert(pya.Box(25000, -35000, 35000, -5000))

# --- Contact patches: 15 x 15 um at probe ends ---
tc.shapes(li_contact).insert(pya.Box(-37500, 20000, -22500, 35000))   # left top
tc.shapes(li_contact).insert(pya.Box(-37500, -35000, -22500, -20000)) # left bottom
tc.shapes(li_contact).insert(pya.Box(22500, 20000, 37500, 35000))     # right top
tc.shapes(li_contact).insert(pya.Box(22500, -35000, 37500, -20000))   # right bottom

# --- Topgate: 40 x 8 um over channel center ---
tc.shapes(li_topgate).insert(pya.Box(-20000, -4000, 20000, 4000))

# --- Contact routes: paths from patches outward ---
tc.shapes(li_route).insert(pya.Box(-37500, 35000, -22500, 60000))   # left top up
tc.shapes(li_route).insert(pya.Box(-37500, -60000, -22500, -35000)) # left bottom down
tc.shapes(li_route).insert(pya.Box(22500, 35000, 37500, 60000))     # right top up
tc.shapes(li_route).insert(pya.Box(22500, -60000, 37500, -35000))   # right bottom down

# --- Bonding pads: 80 x 80 um at route ends ---
tc.shapes(li_pad).insert(pya.Box(-70000, 60000, 10000, 140000))     # left top
tc.shapes(li_pad).insert(pya.Box(-70000, -140000, 10000, -60000))   # left bottom
tc.shapes(li_pad).insert(pya.Box(-10000, 60000, 70000, 140000))     # right top
tc.shapes(li_pad).insert(pya.Box(-10000, -140000, 70000, -60000))   # right bottom

_refresh_view()
result = "hallbar device created"
"""

# pya code to add reference layers (graphene L11/0, graphite L13/0) and save GDS
REFERENCE_LAYERS_PYA = """
import pya

lv, ly, tc = _get_or_create_view()

# --- Reference layers for score mode ---
# Graphene L11/0: large rectangle covering entire mesa area
li_graphene = ly.layer(11, 0)
tc.shapes(li_graphene).insert(pya.Box(-50000, -50000, 50000, 50000))

# Graphite L13/0: overlapping graphene
li_graphite = ly.layer(13, 0)
tc.shapes(li_graphite).insert(pya.Box(-40000, -40000, 40000, 40000))

_refresh_view()

# Save layout as reference GDS
save_opts = pya.SaveLayoutOptions()
save_opts.format = "GDS2"
ly.write("{ref_path}", save_opts)

result = "reference layers added and GDS saved"
""".replace("{ref_path}", REFERENCE_GDS_PATH)


# ---------------------------------------------------------------------------
# Test Class: Tool Listing
# ---------------------------------------------------------------------------

class TestEvaluateDesignToolListing:
    """Verify evaluate_design appears in tools/list with correct schema."""

    def test_evaluate_design_in_tools_list(self):
        """tools/list must include evaluate_design."""
        result = mcp_call("tools/list")
        tools = result["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        assert "evaluate_design" in tool_names, (
            f"evaluate_design not in tools/list. Found: {tool_names}"
        )

    def test_layer_map_is_required(self):
        """layer_map must be listed as required in the tool schema."""
        result = mcp_call("tools/list")
        tools = result["result"]["tools"]
        tool = next(t for t in tools if t["name"] == "evaluate_design")
        required = tool["inputSchema"].get("required", [])
        assert "layer_map" in required, (
            f"layer_map must be required. Required fields: {required}"
        )

    def test_reference_gds_is_optional(self):
        """reference_gds must be present but NOT in required list."""
        result = mcp_call("tools/list")
        tools = result["result"]["tools"]
        tool = next(t for t in tools if t["name"] == "evaluate_design")
        props = tool["inputSchema"]["properties"]
        assert "reference_gds" in props, "reference_gds must be in inputSchema properties"
        required = tool["inputSchema"].get("required", [])
        assert "reference_gds" not in required, (
            "reference_gds must NOT be required (it is optional for DRC mode)"
        )


# ---------------------------------------------------------------------------
# Test Class: evaluate_design current checks/layer_map API.
# (Rewritten from the removed mode="drc"/"score" API — qb-opus48 feedback #7.
# The old tests called evaluate_design(mode="drc"/"score") + reference_gds with
# built-in DRC_CHECK_NAMES; that API no longer exists, so they failed against
# the current configurable-primitive evaluator. These exercise the real API.)
# ---------------------------------------------------------------------------

HALLBAR_CHECKS = [
    {"name": "connectivity",
     "args": {"contact_component": "contact_patch", "pad_component": "bonding_pad",
              "route_component": "contact_route"}, "weight": 0.25},
    {"name": "route_endpoints",
     "args": {"route_component": "contact_route",
              "target_components": ["contact_patch", "bonding_pad"]}, "weight": 0.25},
    {"name": "contact_isolation", "args": {"component": "contact_route"}, "weight": 0.2},
    {"name": "adjacency",
     "args": {"component_a": "contact_patch", "component_b": "mesa"}, "weight": 0.15},
    {"name": "component_overlap",
     "args": {"component": "topgate", "region": "mesa"}, "weight": 0.15},
]


class TestEvaluateDesignChecksApi:
    """evaluate_design with the current configurable-checks API on a hall bar."""

    @pytest.fixture(autouse=True)
    def setup_hallbar(self):
        tool_call("create_layout", name="TEST_EVALUATE_CHECKS", dbu=0.001)
        tool_call_raw("execute_script", code=HALLBAR_DEVICE_PYA)
        yield

    def test_status_ok_and_checks_returned(self):
        res = tool_call("evaluate_design", **_eval_kwargs(checks=HALLBAR_CHECKS))
        assert res["status"] == "ok", json.dumps(res, indent=2)[:500]
        names = [c["name"] for c in res["checks"]]
        assert names == [c["name"] for c in HALLBAR_CHECKS]

    def test_overall_in_range(self):
        res = tool_call("evaluate_design", **_eval_kwargs(checks=HALLBAR_CHECKS))
        assert 0.0 <= res["overall"] <= 1.0

    def test_valid_hallbar_connects(self):
        """A well-formed hall bar: routes reach pads (connectivity > 0) and
        route endpoints land on valid targets (> 0)."""
        res = tool_call("evaluate_design", **_eval_kwargs(checks=HALLBAR_CHECKS))
        by = {c["name"]: c["score"] for c in res["checks"]}
        assert by["connectivity"] > 0.0, f"connectivity should be >0: {by}"
        assert by["route_endpoints"] > 0.0, f"route_endpoints should be >0: {by}"

    def test_solidity_check_reports_raw(self):
        """Issue #2: a solidity check exposes raw_solidity + raw= in detail."""
        res = tool_call("evaluate_design",
                        **_eval_kwargs(checks=[{"name": "solidity",
                                                "args": {"component": "mesa",
                                                         "threshold": 0.9,
                                                         "direction": "below"},
                                                "weight": 1.0}]))
        chk = res["checks"][0]
        assert chk.get("raw_solidity") is not None and "raw=" in chk["detail"]


class TestEvaluateDesignErrorHandling:
    """Current-API error paths (rewritten from the removed mode= API)."""

    @pytest.fixture(autouse=True)
    def setup_layout(self):
        tool_call("create_layout", name="TEST_EVALUATE_ERRORS", dbu=0.001)
        tool_call_raw("execute_script", code=HALLBAR_DEVICE_PYA)
        yield

    def test_unknown_primitive_errors(self):
        with pytest.raises(RuntimeError, match=r"unknown primitive"):
            tool_call("evaluate_design",
                      **_eval_kwargs(checks=[{"name": "no_such_check",
                                              "args": {"component": "mesa"},
                                              "weight": 1.0}]))

    def test_unknown_layer_key_errors(self):
        with pytest.raises(RuntimeError, match=r"not found in layer_map"):
            tool_call("evaluate_design",
                      **_eval_kwargs(checks=[{"name": "solidity",
                                              "args": {"component": "does_not_exist"},
                                              "weight": 1.0}]))

    def test_unknown_arg_key_errors(self):
        """Issue #7: an unknown arg key is rejected, not silently ignored."""
        with pytest.raises(RuntimeError, match=r"unknown arg"):
            tool_call("evaluate_design",
                      **_eval_kwargs(checks=[{"name": "solidity",
                                              "args": {"component": "mesa",
                                                       "bogus_key": 1},
                                              "weight": 1.0}]))

    def test_missing_checks_errors(self):
        with pytest.raises(RuntimeError, match=r"checks"):
            tool_call("evaluate_design", **_eval_kwargs())
