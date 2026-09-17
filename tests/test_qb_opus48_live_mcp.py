#!/usr/bin/env python
"""Live MCP end-to-end validation of the qb-opus48 feedback fixes.

Runs against the real KLayout MCP server selected by ``KLAYOUT_MCP_URL`` (or
the default endpoint) through JSON-RPC — the same path qlaybot uses. Validates
the .lym + worker changes that
the unit tests can only check statically:
  #7  describe_checks introspection + unknown-arg rejection (server-side)
  #2  solidity check exposes raw_solidity + raw= in detail
  #3  auto_route reports routing_grid (pin-bbox crop)
  #14 route_inspect tolerance_um default == 15.0 (tool schema)

Skips cleanly if the server is unreachable.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_phase1_mcp import (  # noqa: E402
    mcp_call, tool_call, tool_call_raw, init_session, _mcp_available,
    MCP_URL, PYTHON_PATH,
)

pytestmark = pytest.mark.skipif(
    not _mcp_available(), reason=f"KLayout MCP server not reachable at {MCP_URL}")


@pytest.fixture(scope="module", autouse=True)
def _session():
    init_session()
    yield


def _eval(**kw):
    if PYTHON_PATH:
        kw.setdefault("python_path", PYTHON_PATH)
    return kw


# --- geometry helpers (build a tiny scene via execute_script) ---------------

SCENE_PYA = """
import pya
lv, ly, tc = _get_or_create_view()
li_mesa = ly.layer(20, 0)
li_pa = ly.layer(100, 0)
li_pb = ly.layer(101, 0)
# concave H-mesa on layer 20 (solidity < 1)
tc.shapes(li_mesa).insert(pya.Box(-30000, -5000, 30000, 5000))
tc.shapes(li_mesa).insert(pya.Box(-15000, -20000, -10000, 20000))
tc.shapes(li_mesa).insert(pya.Box(10000, -20000, 15000, 20000))
# pin columns for auto_route (clustered) on 100/101
tc.shapes(li_pa).insert(pya.Box(0, 0, 2000, 2000))
tc.shapes(li_pa).insert(pya.Box(0, 50000, 2000, 52000))
tc.shapes(li_pb).insert(pya.Box(40000, 0, 42000, 2000))
tc.shapes(li_pb).insert(pya.Box(40000, 50000, 42000, 52000))
_refresh_view()
result = "scene built"
"""


@pytest.fixture(scope="module")
def scene():
    tool_call("execute_script", code=SCENE_PYA)
    return True


# === #7: describe_checks ====================================================

class TestDescribeChecksLive:
    def test_describe_returns_registry(self):
        res = tool_call("evaluate_design", **_eval(describe_checks=True))
        assert res.get("status") == "ok"
        d = res["describe_checks"]
        assert "route_material_compat" in d["primitives"]
        assert "solidity" in d["allowed_args"]
        # allowed_args must include optional keys, not just required
        assert "direction" in d["allowed_args"]["solidity"]
        assert set(d["primitives"]) == set(d["required_args"].keys())


# === #7: unknown-arg rejection ==============================================

class TestUnknownArgRejectedLive:
    def test_unknown_arg_errors(self, scene):
        layer_map = {"mesa": [20, 0]}
        # 'treshold' is a typo for 'threshold' — must be rejected (isError),
        # not silently ignored. tool_call_raw raises RuntimeError on isError;
        # the rejection message is carried in the exception text.
        with pytest.raises(RuntimeError) as ei:
            tool_call_raw(
                "evaluate_design",
                **_eval(layer_map=layer_map,
                        checks=[{"name": "solidity",
                                 "args": {"component": "mesa", "treshold": 0.5},
                                 "weight": 1.0}]))
        msg = str(ei.value)
        assert "unknown arg" in msg.lower() and "treshold" in msg, (
            f"unknown arg key must be rejected with an actionable error; got: {msg[:200]}")

    def test_known_args_accepted(self, scene):
        layer_map = {"mesa": [20, 0]}
        res = tool_call(
            "evaluate_design",
            **_eval(layer_map=layer_map,
                    checks=[{"name": "solidity",
                             "args": {"component": "mesa", "threshold": 0.9,
                                      "direction": "below"},
                             "weight": 1.0}]))
        assert res["status"] == "ok"


# === #2: solidity raw exposed through MCP ===================================

class TestSolidityRawLive:
    def test_solidity_detail_has_raw(self, scene):
        layer_map = {"mesa": [20, 0]}
        res = tool_call(
            "evaluate_design",
            **_eval(layer_map=layer_map,
                    checks=[{"name": "solidity",
                             "args": {"component": "mesa", "threshold": 0.9,
                                      "direction": "below"},
                             "weight": 1.0}]))
        chk = res["checks"][0]
        assert chk.get("raw_solidity") is not None, f"no raw_solidity: {chk}"
        assert 0.0 < chk["raw_solidity"] < 1.0
        assert "raw=" in chk["detail"]


# === #3: auto_route routing_grid through MCP ================================

class TestAutoRouteGridLive:
    def test_routing_grid_reported(self, scene):
        res = tool_call(
            "auto_route", timeout=120,
            pin_layer_a="100/0", pin_layer_b="101/0", output_layer="10/0",
            map_resolution_um=1.0)
        grid = res.get("routing_grid")
        assert grid is not None, f"auto_route must report routing_grid: {sorted(res.keys())}"
        assert grid["cols"] < 300 and grid["rows"] < 300, (
            f"grid not cropped to pins: {grid}")


# === two-level adaptive routing through MCP ================================

TL_SCENE_PYA = """
import pya
lv, ly, tc = _get_or_create_view()
import math
li_c = ly.layer(110, 0)   # inner contacts (centre cluster)
li_p = ly.layer(111, 0)   # bonding pads (perimeter ring)
def box(li, cx, cy, h):
    tc.shapes(li).insert(pya.Box(int((cx-h)/ly.dbu), int((cy-h)/ly.dbu),
                                 int((cx+h)/ly.dbu), int((cy+h)/ly.dbu)))
# 6 contacts ~3um pitch clustered at origin
for (x, y) in [(-3,-3),(0,-3),(3,-3),(-3,3),(0,3),(3,3)]:
    box(li_c, x, y, 1.0)
# 6 pads on a 400um ring
for i in range(6):
    ang = 2*math.pi*i/6
    box(li_p, 400.0*math.cos(ang), 400.0*math.sin(ang), 25.0)
_refresh_view()
result = "tl scene built"
"""


class TestTwoLevelLive:
    @pytest.fixture(scope="class")
    def tl_scene(self):
        tool_call("execute_script", code=TL_SCENE_PYA)
        return True

    def test_two_level_forced_routes_and_patches(self, tl_scene):
        res = tool_call(
            "auto_route", timeout=180,
            pin_layer_a="110/0", pin_layer_b="111/0", output_layer="12/0",
            two_level="on", inner_width=0.6, outer_width=2.0,
            map_resolution_um=10.0)
        assert res.get("two_level") is True, f"two-level did not engage: {res.get('errors')}"
        assert res.get("inner_grid") and res.get("outer_grid")
        assert res.get("routed_pairs", 0) >= 1, res.get("errors")
        assert res.get("patches_inserted", 0) >= 1
        # boundary points reported for the agent
        assert res.get("boundary_points_um")


# === #14: route_inspect tolerance default in schema =========================

class TestRouteInspectToleranceSchemaLive:
    def test_tolerance_default_is_15(self):
        res = mcp_call("tools/list")
        tools = res["result"]["tools"]
        ri = next(t for t in tools if t["name"] == "route_inspect")
        default = ri["inputSchema"]["properties"]["tolerance_um"].get("default")
        assert default == 15.0, f"route_inspect tolerance default {default}, expected 15.0"
