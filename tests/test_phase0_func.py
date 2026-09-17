#!/usr/bin/env python
"""Functional MCP integration tests for Phase 0 — gap coverage.

These tests cover scenarios NOT already tested in test_phase0_mcp.py:

1. get_layout_info tool listing in MCP tools/list
2. get_layout_info schema validation (no required params)
3. validate_pixel_size template_gds optionality (schema-level)
4. Multi-layer layout with higher layer numbers (10/0, 11/0, 12/0)

Overlap analysis — the following spec tests are SKIPPED because
test_phase0_mcp.py already covers them:
  - test_returns_status_ok: TestGetLayoutInfoWithGeometry.test_returns_status_ok
  - test_per_layer_shape_counts (1,2,1): Covered with layers 1/0, 2/0 (counts 1,2)
  - test_per_layer_area_correct: Covered for 6500 um2, 200 um2
  - test_per_layer_bbox_correct: Covered for layer 1/0
  - test_num_layers_matches: TestGetLayoutInfoWithGeometry.test_num_layers_matches
  - test_shape_count_includes_child_cell: TestGetLayoutInfoRecursiveChildCell
  - test_area_includes_child_cell_contribution: TestGetLayoutInfoRecursiveChildCell
  - test_validate_pixel_size_in_tools_list: TestValidatePixelSizeToolListing
  - test_pixel_size_is_required_parameter: TestValidatePixelSizeToolListing
  - test_087_maps_to_100x: TestValidatePixelSizeValid
  - test_01_maps_to_50x: TestValidatePixelSizeValid
  - test_025_maps_to_20x: TestValidatePixelSizeValid
  - test_05_maps_to_10x: TestValidatePixelSizeValid
  - test_out_of_range_returns_invalid: TestValidatePixelSizeInvalid
  - test_returns_marker_spacing_null_without_template: TestValidatePixelSizeMarkerSpacing

Marked with @pytest.mark.mcp — skip with: pytest -m "not mcp"
When KLayout is not running, all tests are auto-skipped.
"""

import json
import os
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
        "clientInfo": {"name": "test_phase0_func", "version": "0.1"},
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
# Helper: fetch tools/list once per class
# ---------------------------------------------------------------------------

@pytest.fixture(scope="class")
def tools_list():
    """Fetch the MCP tools/list response once per test class."""
    result = mcp_call("tools/list")
    return result["result"]["tools"]


# ---------------------------------------------------------------------------
# Test Class: get_layout_info Tool Listing
# GAP: test_phase0_mcp.py only checks validate_pixel_size in tools/list
# ---------------------------------------------------------------------------

class TestGetLayoutInfoToolListing:
    """Verify get_layout_info appears in tools/list with correct schema."""

    def test_get_layout_info_in_tools_list(self, tools_list):
        """tools/list must include get_layout_info."""
        tool_names = [t["name"] for t in tools_list]
        assert "get_layout_info" in tool_names, (
            f"get_layout_info not in tools/list. Found: {tool_names}"
        )

    def test_get_layout_info_schema_no_required_params(self, tools_list):
        """get_layout_info inputSchema must have no required parameters.

        The tool takes zero arguments — it inspects whatever layout is
        currently open. The 'required' field should be absent or empty.
        """
        tool = next(t for t in tools_list if t["name"] == "get_layout_info")
        schema = tool["inputSchema"]
        required = schema.get("required", [])
        assert len(required) == 0, (
            f"get_layout_info should have no required params, got: {required}"
        )

    def test_get_layout_info_has_description(self, tools_list):
        """get_layout_info must have a non-trivial description."""
        tool = next(t for t in tools_list if t["name"] == "get_layout_info")
        desc = tool.get("description", "")
        assert len(desc) > 10, (
            f"get_layout_info description too short or missing: '{desc}'"
        )


# ---------------------------------------------------------------------------
# Test Class: validate_pixel_size Schema Details
# GAP: test_phase0_mcp.py checks pixel_size is required but not that
#      template_gds is optional (i.e., NOT in the required list)
# ---------------------------------------------------------------------------

class TestValidatePixelSizeSchemaDetails:
    """Verify validate_pixel_size schema properties beyond basic listing."""

    def test_template_gds_is_optional_parameter(self, tools_list):
        """template_gds must be in properties but NOT in required list."""
        tool = next(t for t in tools_list if t["name"] == "validate_pixel_size")
        schema = tool["inputSchema"]
        props = schema.get("properties", {})
        required = schema.get("required", [])

        assert "template_gds" in props, (
            "template_gds must be declared in inputSchema properties"
        )
        assert "template_gds" not in required, (
            f"template_gds must NOT be required. required={required}"
        )

    def test_pixel_size_type_is_number(self, tools_list):
        """pixel_size property type must be 'number'."""
        tool = next(t for t in tools_list if t["name"] == "validate_pixel_size")
        props = tool["inputSchema"]["properties"]
        assert props["pixel_size"]["type"] == "number", (
            f"pixel_size type must be 'number', got '{props['pixel_size']['type']}'"
        )

    def test_template_gds_type_is_string(self, tools_list):
        """template_gds property type must be 'string'."""
        tool = next(t for t in tools_list if t["name"] == "validate_pixel_size")
        props = tool["inputSchema"]["properties"]
        assert props["template_gds"]["type"] == "string", (
            f"template_gds type must be 'string', got '{props['template_gds']['type']}'"
        )

    def test_image_path_is_optional_parameter(self, tools_list):
        """image_path must be in properties (type=string) but NOT in required list."""
        tool = next(t for t in tools_list if t["name"] == "validate_pixel_size")
        schema = tool["inputSchema"]
        props = schema.get("properties", {})
        required = schema.get("required", [])

        assert "image_path" in props, (
            f"image_path must be declared in inputSchema properties. Found: {list(props.keys())}"
        )
        assert props["image_path"]["type"] == "string", (
            f"image_path type must be 'string', got '{props['image_path']['type']}'"
        )
        assert "image_path" not in required, (
            f"image_path must NOT be required. required={required}"
        )

    def test_validate_pixel_size_has_description(self, tools_list):
        """validate_pixel_size must have a non-trivial description (>10 chars)."""
        tool = next(t for t in tools_list if t["name"] == "validate_pixel_size")
        desc = tool.get("description", "")
        assert len(desc) > 10, (
            f"validate_pixel_size description too short or missing: '{desc}'"
        )


# ---------------------------------------------------------------------------
# Test Class: get_layout_info with Higher Layer Numbers (10/0, 11/0, 12/0)
# GAP: test_phase0_mcp.py uses layers 1/0, 2/0, 3/0. This tests that
#      higher layer indices (10+) are reported correctly with specific
#      shape counts of 1, 2, 1 across three layers.
# ---------------------------------------------------------------------------

class TestGetLayoutInfoMultiLayerHighIndex:
    """Create a layout with layers 10/0, 11/0, 12/0 and verify reporting."""

    @pytest.fixture(autouse=True)
    def setup_multi_layer(self):
        """Create a layout with 3 higher-numbered layers."""
        tool_call("create_layout", name="TEST_MULTI_HIGH", dbu=0.001)
        tool_call_raw("execute_script", code="""
import pya

lv, ly, tc = _get_or_create_view()

# Layer 10/0: one 40x25 um rectangle (area = 1000 um2)
li10 = ly.layer(10, 0)
tc.shapes(li10).insert(pya.Box(0, 0, 40000, 25000))

# Layer 11/0: two 15x10 um rectangles (area = 150 um2 each, total = 300 um2)
li11 = ly.layer(11, 0)
tc.shapes(li11).insert(pya.Box(0, 0, 15000, 10000))
tc.shapes(li11).insert(pya.Box(50000, 0, 65000, 10000))

# Layer 12/0: one 20x20 um square (area = 400 um2)
li12 = ly.layer(12, 0)
tc.shapes(li12).insert(pya.Box(0, 0, 20000, 20000))

_refresh_view()
result = "setup complete"
""")
        yield

    def test_layers_dict_has_all_three_keys(self):
        """layers dict must contain keys '10/0', '11/0', '12/0'."""
        info = tool_call("get_layout_info")
        layers = info["layers"]
        for key in ("10/0", "11/0", "12/0"):
            assert key in layers, (
                f"Layer {key} not found in layers dict. Found: {list(layers.keys())}"
            )

    def test_layer_10_shape_count(self):
        """Layer 10/0 must report exactly 1 shape."""
        info = tool_call("get_layout_info")
        assert info["layers"]["10/0"]["shapes"] == 1

    def test_layer_11_shape_count(self):
        """Layer 11/0 must report exactly 2 shapes."""
        info = tool_call("get_layout_info")
        assert info["layers"]["11/0"]["shapes"] == 2

    def test_layer_12_shape_count(self):
        """Layer 12/0 must report exactly 1 shape."""
        info = tool_call("get_layout_info")
        assert info["layers"]["12/0"]["shapes"] == 1

    def test_layer_10_area(self):
        """Layer 10/0 area must be ~1000 um2 (40 x 25)."""
        info = tool_call("get_layout_info")
        assert info["layers"]["10/0"]["area_um2"] == pytest.approx(1000.0, rel=0.01)

    def test_layer_11_area(self):
        """Layer 11/0 area must be ~300 um2 (two 15x10 rectangles)."""
        info = tool_call("get_layout_info")
        assert info["layers"]["11/0"]["area_um2"] == pytest.approx(300.0, rel=0.01)

    def test_layer_12_area(self):
        """Layer 12/0 area must be ~400 um2 (20 x 20)."""
        info = tool_call("get_layout_info")
        assert info["layers"]["12/0"]["area_um2"] == pytest.approx(400.0, rel=0.01)

    def test_layer_10_bbox(self):
        """Layer 10/0 bbox must be [0, 0, 40, 25] um."""
        info = tool_call("get_layout_info")
        bbox = info["layers"]["10/0"]["bbox_um"]
        assert bbox is not None
        assert bbox[0] == pytest.approx(0.0, abs=0.01)
        assert bbox[1] == pytest.approx(0.0, abs=0.01)
        assert bbox[2] == pytest.approx(40.0, abs=0.01)
        assert bbox[3] == pytest.approx(25.0, abs=0.01)

    def test_layer_11_bbox_spans_both_rects(self):
        """Layer 11/0 bbox must span both rectangles: [0, 0, 65, 10] um."""
        info = tool_call("get_layout_info")
        bbox = info["layers"]["11/0"]["bbox_um"]
        assert bbox is not None
        assert bbox[0] == pytest.approx(0.0, abs=0.01)
        assert bbox[1] == pytest.approx(0.0, abs=0.01)
        assert bbox[2] == pytest.approx(65.0, abs=0.01)
        assert bbox[3] == pytest.approx(10.0, abs=0.01)
