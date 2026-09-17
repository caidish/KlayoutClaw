#!/usr/bin/env python
"""MCP integration tests for Phase 0 / Phase 0.5.

These tests connect to a running KLayout instance with the KlayoutClaw
MCP server plugin. They exercise get_layout_info (enhanced) and
validate_pixel_size over the wire.

Marked with @pytest.mark.mcp — skip with: pytest -m "not mcp"
When KLayout is not running, all tests are auto-skipped.
"""

import json
import os
import sys
import urllib.request
import urllib.error

import pytest

from mcp_identity import is_klayoutclaw

# ---------------------------------------------------------------------------
# MCP Client Helpers
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
    # If the server returned an error (isError=True), the text is not JSON
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
        "clientInfo": {"name": "test_phase0_mcp", "version": "0.1"},
    })


# ---------------------------------------------------------------------------
# Skip condition — ALL tests in this module skip when KLayout is not running
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
# Test Class: get_layout_info with Known Geometry
# ---------------------------------------------------------------------------

class TestGetLayoutInfoWithGeometry:
    """Create a layout with known shapes, then verify get_layout_info output."""

    @pytest.fixture(autouse=True)
    def setup_layout(self):
        """Create a fresh layout with known geometry for testing."""
        # Create a layout with a 100x65 um rectangle on layer 1/0
        tool_call("create_layout", name="TEST_PHASE0", dbu=0.001)
        tool_call_raw("execute_script", code="""
import pya

lv, ly, tc = _get_or_create_view()

# Layer 1/0: one rectangle, 100 x 65 um
li1 = ly.layer(1, 0)
tc.shapes(li1).insert(pya.Box(-50000, -32500, 50000, 32500))

# Layer 2/0: two small squares, 10 x 10 um each
li2 = ly.layer(2, 0)
tc.shapes(li2).insert(pya.Box(0, 0, 10000, 10000))
tc.shapes(li2).insert(pya.Box(20000, 0, 30000, 10000))

# Layer 3/0: a text label only (no area contribution)
li3 = ly.layer(3, 0)
tc.shapes(li3).insert(pya.Text("HELLO", pya.Trans(0, 0)))

_refresh_view()
result = "setup complete"
""")
        yield

    def test_returns_status_ok(self):
        """get_layout_info must return status 'ok'."""
        info = tool_call("get_layout_info")
        assert info["status"] == "ok"

    def test_returns_dbu(self):
        """get_layout_info must return the database unit."""
        info = tool_call("get_layout_info")
        assert info["dbu"] == pytest.approx(0.001, abs=1e-6)

    def test_returns_layers_dict(self):
        """get_layout_info must return a 'layers' dict."""
        info = tool_call("get_layout_info")
        assert "layers" in info, "Response must contain 'layers' dict"
        assert isinstance(info["layers"], dict), "'layers' must be a dict"

    def test_layer_1_shapes_count(self):
        """Layer 1/0 must report 1 shape (the rectangle)."""
        info = tool_call("get_layout_info")
        layer_info = info["layers"].get("1/0")
        assert layer_info is not None, "Layer 1/0 not found in layers dict"
        assert layer_info["shapes"] == 1

    def test_layer_1_area(self):
        """Layer 1/0 area must be ~6500 um2 (100 x 65)."""
        info = tool_call("get_layout_info")
        layer_info = info["layers"]["1/0"]
        assert layer_info["area_um2"] == pytest.approx(6500.0, rel=0.01)

    def test_layer_1_bbox(self):
        """Layer 1/0 bbox must be [-50, -32.5, 50, 32.5] um."""
        info = tool_call("get_layout_info")
        layer_info = info["layers"]["1/0"]
        bbox = layer_info["bbox_um"]
        assert bbox is not None, "bbox_um must not be null for a non-empty layer"
        assert len(bbox) == 4
        assert bbox[0] == pytest.approx(-50.0, abs=0.01)
        assert bbox[1] == pytest.approx(-32.5, abs=0.01)
        assert bbox[2] == pytest.approx(50.0, abs=0.01)
        assert bbox[3] == pytest.approx(32.5, abs=0.01)

    def test_layer_2_shapes_count(self):
        """Layer 2/0 must report 2 shapes (two squares)."""
        info = tool_call("get_layout_info")
        layer_info = info["layers"].get("2/0")
        assert layer_info is not None, "Layer 2/0 not found"
        assert layer_info["shapes"] == 2

    def test_layer_2_area(self):
        """Layer 2/0 area must be ~200 um2 (two 10x10 squares)."""
        info = tool_call("get_layout_info")
        layer_info = info["layers"]["2/0"]
        assert layer_info["area_um2"] == pytest.approx(200.0, rel=0.01)

    def test_layer_3_text_only(self):
        """Layer 3/0 with only text: shapes == 1, area == 0."""
        info = tool_call("get_layout_info")
        layer_info = info["layers"].get("3/0")
        assert layer_info is not None, "Layer 3/0 not found"
        assert layer_info["shapes"] == 1, (
            f"Expected exactly 1 shape (the text), got {layer_info['shapes']}"
        )
        assert layer_info["area_um2"] == pytest.approx(0.0, abs=0.001)

    def test_layer_3_text_bbox_is_null(self):
        """Text-only layer: bbox_um must be null (texts have no geometric extent in Region)."""
        info = tool_call("get_layout_info")
        layer_info = info["layers"].get("3/0")
        assert layer_info is not None
        assert layer_info["bbox_um"] is None, (
            "Text-only layers must have bbox_um=null (texts have no geometric extent)"
        )

    def test_num_layers_matches(self):
        """num_layers must match the number of entries in layers dict."""
        info = tool_call("get_layout_info")
        assert info["num_layers"] == len(info["layers"])


# ---------------------------------------------------------------------------
# Test Class: get_layout_info with Empty Layout
# ---------------------------------------------------------------------------

class TestGetLayoutInfoEmpty:
    """Test get_layout_info on a fresh/empty layout."""

    @pytest.fixture(autouse=True)
    def setup_empty_layout(self):
        """Create an empty layout."""
        tool_call("create_layout", name="TEST_EMPTY", dbu=0.001)
        yield

    def test_returns_layers_key(self):
        """Even empty layouts must return a 'layers' key."""
        info = tool_call("get_layout_info")
        assert "layers" in info

    def test_empty_layout_has_no_layer_entries(self):
        """An empty layout should have zero or empty layers dict."""
        info = tool_call("get_layout_info")
        # Empty layout: no layers created, so layers dict should be empty
        assert isinstance(info["layers"], dict)
        assert len(info["layers"]) == 0


# ---------------------------------------------------------------------------
# Test Class: get_layout_info — Existing Layer with No Geometry
# ---------------------------------------------------------------------------

class TestGetLayoutInfoEmptyLayer:
    """Test get_layout_info on a layout with a registered layer that has zero shapes."""

    @pytest.fixture(autouse=True)
    def setup_layout_with_empty_layer(self):
        """Create a layout and register layer 7/0 without adding any shapes."""
        tool_call("create_layout", name="TEST_EMPTY_LAYER", dbu=0.001)
        tool_call_raw("execute_script", code="""
import pya

lv, ly, tc = _get_or_create_view()

# Register layer 7/0 — creates the layer index but adds no shapes
ly.layer(7, 0)

_refresh_view()
result = "setup complete"
""")
        yield

    def test_empty_layer_present_in_layers_dict(self):
        """Layer 7/0 must appear in the layers dict even with no shapes."""
        info = tool_call("get_layout_info")
        assert "7/0" in info["layers"], (
            f"Layer 7/0 not found in layers dict. Found: {list(info['layers'].keys())}"
        )

    def test_empty_layer_shapes_zero(self):
        """Layer 7/0 must report shapes=0."""
        info = tool_call("get_layout_info")
        layer_info = info["layers"]["7/0"]
        assert layer_info["shapes"] == 0, (
            f"Expected shapes=0 for empty layer 7/0, got {layer_info['shapes']}"
        )

    def test_empty_layer_area_zero(self):
        """Layer 7/0 must report area_um2=0.0."""
        info = tool_call("get_layout_info")
        layer_info = info["layers"]["7/0"]
        assert layer_info["area_um2"] == pytest.approx(0.0, abs=0.001), (
            f"Expected area_um2=0.0 for empty layer 7/0, got {layer_info['area_um2']}"
        )

    def test_empty_layer_bbox_null(self):
        """Layer 7/0 must report bbox_um=null (no geometry)."""
        info = tool_call("get_layout_info")
        layer_info = info["layers"]["7/0"]
        assert layer_info["bbox_um"] is None, (
            f"Expected bbox_um=null for empty layer 7/0, got {layer_info['bbox_um']}"
        )


# ---------------------------------------------------------------------------
# Test Class: validate_pixel_size — Valid Inputs
# ---------------------------------------------------------------------------

class TestValidatePixelSizeValid:
    """Test validate_pixel_size with valid pixel_size values."""

    def test_087_is_valid(self):
        """pixel_size=0.087 must be valid and map to 100x objective."""
        result = tool_call("validate_pixel_size", pixel_size=0.087)
        assert result["valid"] is True
        assert "100x" in result["likely_objective"]

    def test_05_maps_to_100x(self):
        """pixel_size=0.05 should map to 100x objective."""
        result = tool_call("validate_pixel_size", pixel_size=0.05)
        assert result["valid"] is True
        assert "100x" in result["likely_objective"]

    def test_01_maps_to_50x(self):
        """pixel_size=0.1 should map to 50x objective."""
        result = tool_call("validate_pixel_size", pixel_size=0.1)
        assert result["valid"] is True
        assert "50x" in result["likely_objective"]

    def test_025_maps_to_20x(self):
        """pixel_size=0.25 should map to 20x objective."""
        result = tool_call("validate_pixel_size", pixel_size=0.25)
        assert result["valid"] is True
        assert "20x" in result["likely_objective"]

    def test_05_maps_to_10x(self):
        """pixel_size=0.5 should map to 10x objective."""
        result = tool_call("validate_pixel_size", pixel_size=0.5)
        assert result["valid"] is True
        assert "10x" in result["likely_objective"]

    def test_returns_status_ok(self):
        """Valid input must return status 'ok'."""
        result = tool_call("validate_pixel_size", pixel_size=0.087)
        assert result["status"] == "ok"

    def test_returns_pixel_size_echo(self):
        """Response must echo back the pixel_size value."""
        result = tool_call("validate_pixel_size", pixel_size=0.087)
        assert result["pixel_size"] == pytest.approx(0.087, abs=0.001)

    def test_returns_warnings_list(self):
        """Response must include a 'warnings' list (may be empty for valid input)."""
        result = tool_call("validate_pixel_size", pixel_size=0.087)
        assert isinstance(result["warnings"], list)


# ---------------------------------------------------------------------------
# Test Class: validate_pixel_size — Invalid Inputs
# ---------------------------------------------------------------------------

class TestValidatePixelSizeInvalid:
    """Test validate_pixel_size with out-of-range or invalid pixel_size values."""

    def test_too_large(self):
        """pixel_size=5.0 is out of range [0.01, 2.0] and must be invalid."""
        result = tool_call("validate_pixel_size", pixel_size=5.0)
        assert result["valid"] is False

    def test_too_small(self):
        """pixel_size=0.0 is below minimum and must be invalid."""
        result = tool_call("validate_pixel_size", pixel_size=0.0)
        assert result["valid"] is False

    def test_negative(self):
        """pixel_size=-1.0 must be invalid."""
        result = tool_call("validate_pixel_size", pixel_size=-1.0)
        assert result["valid"] is False

    def test_boundary_low_valid(self):
        """pixel_size=0.01 (lower bound) should be valid."""
        result = tool_call("validate_pixel_size", pixel_size=0.01)
        assert result["valid"] is True

    def test_boundary_high_valid(self):
        """pixel_size=2.0 (upper bound) should be valid."""
        result = tool_call("validate_pixel_size", pixel_size=2.0)
        assert result["valid"] is True

    def test_invalid_has_warnings(self):
        """Out-of-range pixel_size must produce at least one warning."""
        result = tool_call("validate_pixel_size", pixel_size=5.0)
        assert len(result["warnings"]) > 0

    def test_slightly_below_min(self):
        """pixel_size=0.009 (just below 0.01) must be invalid."""
        result = tool_call("validate_pixel_size", pixel_size=0.009)
        assert result["valid"] is False

    def test_slightly_above_max(self):
        """pixel_size=2.01 (just above 2.0) must be invalid."""
        result = tool_call("validate_pixel_size", pixel_size=2.01)
        assert result["valid"] is False


# ---------------------------------------------------------------------------
# Test Class: validate_pixel_size — Tool Listing
# ---------------------------------------------------------------------------

class TestValidatePixelSizeToolListing:
    """Verify validate_pixel_size appears in the MCP tools/list response."""

    def test_tool_listed_in_mcp(self):
        """tools/list must include validate_pixel_size."""
        result = mcp_call("tools/list")
        tools = result["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        assert "validate_pixel_size" in tool_names, (
            f"validate_pixel_size not in tools/list. Found: {tool_names}"
        )

    def test_tool_schema_has_pixel_size_required(self):
        """validate_pixel_size schema must have pixel_size as required."""
        result = mcp_call("tools/list")
        tools = result["result"]["tools"]
        tool = next(t for t in tools if t["name"] == "validate_pixel_size")
        required = tool["inputSchema"].get("required", [])
        assert "pixel_size" in required


# ---------------------------------------------------------------------------
# Test Class: get_layout_info — Recursive Child Cell Shapes
# ---------------------------------------------------------------------------

class TestGetLayoutInfoRecursiveChildCell:
    """Verify get_layout_info counts shapes in child cells recursively."""

    @pytest.fixture(autouse=True)
    def setup_layout_with_child(self):
        """Create a layout with a child cell containing shapes, instanced in top cell."""
        tool_call("create_layout", name="TEST_RECURSIVE", dbu=0.001)
        tool_call_raw("execute_script", code="""
import pya

lv, ly, tc = _get_or_create_view()

# Create a child cell with a 20x20 um rectangle on layer 4/0
child = ly.create_cell("CHILD")
li4 = ly.layer(4, 0)
child.shapes(li4).insert(pya.Box(0, 0, 20000, 20000))

# Also add a shape directly in the top cell on the same layer
tc.shapes(li4).insert(pya.Box(50000, 0, 60000, 10000))

# Instance the child cell in the top cell (at origin)
tc.insert(pya.CellInstArray(child.cell_index(), pya.Trans()))

_refresh_view()
result = "setup complete"
""")
        yield

    def test_recursive_shape_count_includes_child(self):
        """Shape count on layer 4/0 must include both top cell and child cell shapes."""
        info = tool_call("get_layout_info")
        layer_info = info["layers"].get("4/0")
        assert layer_info is not None, "Layer 4/0 not found in layers dict"
        # 1 shape in top cell + 1 shape in child cell = 2 total
        assert layer_info["shapes"] >= 2, (
            f"Expected at least 2 shapes on layer 4/0 (1 top + 1 child), got {layer_info['shapes']}"
        )

    def test_recursive_area_includes_child(self):
        """Area on layer 4/0 must include child cell contribution."""
        info = tool_call("get_layout_info")
        layer_info = info["layers"]["4/0"]
        # Child: 20x20=400 um2, Top: 10x10=100 um2, total=500 um2
        assert layer_info["area_um2"] == pytest.approx(500.0, rel=0.01), (
            f"Expected area ~500 um2 (400 child + 100 top), got {layer_info['area_um2']}"
        )

    def test_recursive_bbox_spans_both(self):
        """Bounding box on layer 4/0 must span both top cell and child cell shapes."""
        info = tool_call("get_layout_info")
        layer_info = info["layers"]["4/0"]
        bbox = layer_info["bbox_um"]
        assert bbox is not None
        # Child at (0,0)-(20,20), Top at (50,0)-(60,10)
        # Combined bbox: (0,0)-(60,20)
        assert bbox[0] == pytest.approx(0.0, abs=0.1)
        assert bbox[1] == pytest.approx(0.0, abs=0.1)
        assert bbox[2] == pytest.approx(60.0, abs=0.1)
        assert bbox[3] == pytest.approx(20.0, abs=0.1)


# ---------------------------------------------------------------------------
# Test Class: get_layout_info — Mixed Text + Polygon Layer
# ---------------------------------------------------------------------------

class TestGetLayoutInfoMixedTextPolygon:
    """Verify get_layout_info handles layers with both text and polygon shapes."""

    @pytest.fixture(autouse=True)
    def setup_mixed_layer(self):
        """Create a layout with a layer containing both text and polygon shapes."""
        tool_call("create_layout", name="TEST_MIXED", dbu=0.001)
        tool_call_raw("execute_script", code="""
import pya

lv, ly, tc = _get_or_create_view()

# Layer 6/0: one 30x20 um rectangle AND one text label
li6 = ly.layer(6, 0)
tc.shapes(li6).insert(pya.Box(0, 0, 30000, 20000))
tc.shapes(li6).insert(pya.Text("MIXED", pya.Trans(0, 0)))

_refresh_view()
result = "setup complete"
""")
        yield

    def test_mixed_shapes_count_includes_text(self):
        """Shape count must include both polygon and text shapes."""
        info = tool_call("get_layout_info")
        layer_info = info["layers"].get("6/0")
        assert layer_info is not None, "Layer 6/0 not found"
        assert layer_info["shapes"] == 2, (
            f"Expected exactly 2 shapes (1 polygon + 1 text), got {layer_info['shapes']}"
        )

    def test_mixed_area_reflects_only_polygons(self):
        """Area must reflect only polygon geometry, not text."""
        info = tool_call("get_layout_info")
        layer_info = info["layers"]["6/0"]
        # Only the 30x20 um rectangle contributes area = 600 um2
        assert layer_info["area_um2"] == pytest.approx(600.0, rel=0.01), (
            f"Expected area ~600 um2 (only polygon), got {layer_info['area_um2']}"
        )

    def test_mixed_bbox_reflects_polygon(self):
        """Bounding box must at least cover the polygon extent."""
        info = tool_call("get_layout_info")
        layer_info = info["layers"]["6/0"]
        bbox = layer_info["bbox_um"]
        assert bbox is not None, "bbox_um must not be null when polygons exist"
        # The polygon spans (0,0)-(30,20)
        assert bbox[0] == pytest.approx(0.0, abs=0.1), (
            f"Expected bbox min-x ~0.0, got {bbox[0]}"
        )
        assert bbox[1] == pytest.approx(0.0, abs=0.1), (
            f"Expected bbox min-y ~0.0, got {bbox[1]}"
        )
        assert bbox[2] == pytest.approx(30.0, abs=0.1)
        assert bbox[3] == pytest.approx(20.0, abs=0.1)


# ---------------------------------------------------------------------------
# Test Class: validate_pixel_size — marker_spacing_um Field
# ---------------------------------------------------------------------------

class TestValidatePixelSizeMarkerSpacing:
    """Verify marker_spacing_um is returned correctly."""

    def test_no_template_returns_null_marker_spacing(self):
        """Without template_gds, marker_spacing_um must be null/None."""
        result = tool_call("validate_pixel_size", pixel_size=0.087)
        assert "marker_spacing_um" in result, (
            "Response must include 'marker_spacing_um' field even without template_gds"
        )
        assert result["marker_spacing_um"] is None, (
            "marker_spacing_um must be null when no template_gds is provided"
        )


# ---------------------------------------------------------------------------
# Test Class: validate_pixel_size — Template GDS with L5/0 Markers
# ---------------------------------------------------------------------------

class TestValidatePixelSizeTemplateGds:
    """Verify validate_pixel_size computes marker_spacing_um from template GDS."""

    TEMPLATE_PATH = "/tmp/test_phase0_template.gds"

    @pytest.fixture(autouse=True)
    def create_template_gds(self):
        """Create a small GDS with two L5/0 marker rectangles 200um apart."""
        tool_call_raw("execute_script", code="""
import pya

ly = pya.Layout()
ly.dbu = 0.001
top = ly.create_cell("MARKERS")
li = ly.layer(5, 0)
# Marker 1 centered at (0, 0): 10x10 um box
top.shapes(li).insert(pya.Box(-5000, -5000, 5000, 5000))
# Marker 2 centered at (200, 0) um: 10x10 um box, 200um apart
top.shapes(li).insert(pya.Box(195000, -5000, 205000, 5000))
ly.write("/tmp/test_phase0_template.gds")
result = "template written"
""")
        yield

    def test_marker_spacing_returned(self):
        """With template_gds, marker_spacing_um must be returned."""
        result = tool_call(
            "validate_pixel_size",
            pixel_size=0.087,
            template_gds=self.TEMPLATE_PATH,
        )
        assert "marker_spacing_um" in result, (
            "Response must include 'marker_spacing_um' when template_gds is provided"
        )
        assert result["marker_spacing_um"] is not None, (
            "marker_spacing_um must not be null when template_gds with L5/0 markers is provided"
        )

    def test_marker_spacing_value_correct(self):
        """marker_spacing_um must be ~200.0 for markers 200um apart."""
        result = tool_call(
            "validate_pixel_size",
            pixel_size=0.087,
            template_gds=self.TEMPLATE_PATH,
        )
        assert result["marker_spacing_um"] == pytest.approx(200.0, rel=0.01), (
            f"Expected marker_spacing_um ~200.0, got {result['marker_spacing_um']}"
        )

    def test_valid_flag_still_correct(self):
        """valid flag must still be correct when template_gds is provided."""
        result = tool_call(
            "validate_pixel_size",
            pixel_size=0.087,
            template_gds=self.TEMPLATE_PATH,
        )
        assert result["valid"] is True

    def test_template_gds_no_l5_markers(self):
        """GDS with shapes on L1/0 only (no L5/0): valid=true, marker_spacing_um=null."""
        # Create a GDS with only L1/0 geometry, no L5/0 markers
        tool_call_raw("execute_script", code="""
import pya

ly = pya.Layout()
ly.dbu = 0.001
top = ly.create_cell("NO_MARKERS")
li = ly.layer(1, 0)
top.shapes(li).insert(pya.Box(0, 0, 50000, 50000))
ly.write("/tmp/test_template_no_markers.gds")
result = "written"
""")
        result = tool_call(
            "validate_pixel_size",
            pixel_size=0.087,
            template_gds="/tmp/test_template_no_markers.gds",
        )
        assert result["valid"] is True, (
            "pixel_size validity must not depend on marker presence"
        )
        assert result["marker_spacing_um"] is None, (
            f"marker_spacing_um must be null when no L5/0 markers exist, got {result['marker_spacing_um']}"
        )

    def test_template_gds_multi_polygon_markers(self):
        """Multi-polygon markers: marker_spacing_um must reflect inter-marker distance, not intra-marker."""
        # Each marker is a cross shape (2 offset rectangles). The 4 shape centroids
        # in dbu are: (0,0), (5000,0), (200000,0), (205000,0).
        # Min pairwise centroid distance = 5000 dbu = 5um (intra-marker, WRONG).
        # Correct inter-marker spacing = 200um.
        tool_call_raw("execute_script", code="""
import pya

ly = pya.Layout()
ly.dbu = 0.001
top = ly.create_cell("MULTI_POLY_MARKERS")
li = ly.layer(5, 0)
# Marker 1 at ~(0, 0): horizontal bar + offset vertical bar
top.shapes(li).insert(pya.Box(-5000, -1000, 5000, 1000))    # centroid (0, 0)
top.shapes(li).insert(pya.Box(4000, -5000, 6000, 5000))     # centroid (5000, 0)
# Marker 2 at ~(200um, 0): same cross pattern shifted 200um
top.shapes(li).insert(pya.Box(195000, -1000, 205000, 1000))  # centroid (200000, 0)
top.shapes(li).insert(pya.Box(204000, -5000, 206000, 5000))  # centroid (205000, 0)
ly.write("/tmp/test_template_multi_poly.gds")
result = "written"
""")
        result = tool_call(
            "validate_pixel_size",
            pixel_size=0.087,
            template_gds="/tmp/test_template_multi_poly.gds",
        )
        assert result["marker_spacing_um"] is not None, (
            "marker_spacing_um must not be null for template with L5/0 markers"
        )
        # Must be ~200um (inter-marker), NOT ~5um (intra-marker feature spacing)
        assert result["marker_spacing_um"] == pytest.approx(200.0, abs=10.0), (
            f"marker_spacing_um must be ~200.0 (inter-marker distance), "
            f"got {result['marker_spacing_um']} — likely returning intra-marker "
            f"feature spacing instead of inter-marker spacing"
        )

    def test_template_gds_single_marker(self):
        """GDS with exactly one L5/0 shape: marker_spacing_um=null (need pairs)."""
        # Create a GDS with a single L5/0 marker
        tool_call_raw("execute_script", code="""
import pya

ly = pya.Layout()
ly.dbu = 0.001
top = ly.create_cell("ONE_MARKER")
li = ly.layer(5, 0)
top.shapes(li).insert(pya.Box(0, 0, 10000, 10000))
ly.write("/tmp/test_template_one_marker.gds")
result = "written"
""")
        result = tool_call(
            "validate_pixel_size",
            pixel_size=0.087,
            template_gds="/tmp/test_template_one_marker.gds",
        )
        assert result["marker_spacing_um"] is None, (
            f"marker_spacing_um must be null with only one L5/0 shape (need pairs), got {result['marker_spacing_um']}"
        )


# ---------------------------------------------------------------------------
# Test Class: validate_pixel_size — Missing Required Parameter
# ---------------------------------------------------------------------------

class TestValidatePixelSizeMissingParam:
    """Verify server returns an error when pixel_size is missing."""

    def test_missing_pixel_size_returns_error(self):
        """Calling validate_pixel_size without pixel_size must raise an error."""
        # Call without the required pixel_size argument
        with pytest.raises(RuntimeError, match="(?i)(error|missing|required|pixel_size)"):
            # Pass empty arguments — pixel_size is required
            tool_call("validate_pixel_size")
