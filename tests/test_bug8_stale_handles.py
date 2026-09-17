#!/usr/bin/env python
"""Tests for stale handle validation in klayoutclaw_server.lym.

After layout-replacing operations (e.g. calling create_layout twice),
cached _layout/_layout_view/_top_cell handles become invalid.  The server
must detect and refresh them instead of crashing.

Two test classes:
  1. AST-based static analysis — verifies the defensive functions exist
     and are called at the top of each tool handler (no running KLayout).
  2. MCP integration tests — exercises the actual bug scenario over the wire
     (requires a running KLayout with the MCP server plugin).
"""
import ast
import os
import xml.etree.ElementTree as ET

import pytest

from mcp_identity import is_klayoutclaw

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LYM_PATH = os.path.join(PROJECT_ROOT, "plugin", "klayoutclaw_server.lym")


# ---------------------------------------------------------------------------
# Fixture: extract Python source from .lym and parse into an AST
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def server_ast():
    """Parse klayoutclaw_server.lym and return the AST of its Python source."""
    tree = ET.parse(LYM_PATH)
    root = tree.getroot()
    text_elem = root.find("text")
    assert text_elem is not None, "No <text> element found in .lym file"
    source = text_elem.text
    assert source and len(source.strip()) > 0, "<text> element is empty"
    return ast.parse(source, filename="klayoutclaw_server.lym")


def _find_function(module_ast, name):
    """Find a top-level FunctionDef with the given name."""
    for node in ast.walk(module_ast):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


# ---------------------------------------------------------------------------
# AST tests — structural verification (no running KLayout needed)
# ---------------------------------------------------------------------------

class TestStaleHandleFunctionsExist:
    """Verify _handles_stale and _ensure_valid_handles are defined."""

    def test_handles_stale_exists(self, server_ast):
        func = _find_function(server_ast, "_handles_stale")
        assert func is not None, (
            "_handles_stale() function not found in klayoutclaw_server.lym"
        )

    def test_ensure_valid_handles_exists(self, server_ast):
        func = _find_function(server_ast, "_ensure_valid_handles")
        assert func is not None, (
            "_ensure_valid_handles() function not found in klayoutclaw_server.lym"
        )


class TestEnsureValidHandlesCalledInToolHandlers:
    """Every tool handler that uses layout globals must call
    _ensure_valid_handles() near its top, not just check `if _layout is None`.
    """

    TOOL_HANDLERS = [
        "_tool_execute_script",
        "_tool_save_layout",
        "_tool_get_layout_info",
        "_tool_screenshot",
        "_tool_auto_route",
        "_tool_evaluate_design",
    ]

    @pytest.fixture(params=TOOL_HANDLERS)
    def handler_func(self, request, server_ast):
        name = request.param
        func = _find_function(server_ast, name)
        assert func is not None, f"{name} not found in source"
        return name, func

    def test_calls_ensure_valid_handles(self, handler_func):
        name, func = handler_func
        # Walk the function body for a call to _ensure_valid_handles
        found = False
        for node in ast.walk(func):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == "_ensure_valid_handles":
                    found = True
                    break
        assert found, (
            f"{name}() does not call _ensure_valid_handles(). "
            "It must call _ensure_valid_handles() to refresh stale layout handles."
        )

    def test_ensure_valid_handles_before_global_access(self, handler_func):
        """_ensure_valid_handles() must be called before any access to _layout,
        _layout_view, or _top_cell in the handler body."""
        name, func = handler_func
        globals_accessed = {"_layout", "_layout_view", "_top_cell"}

        # Find line numbers of _ensure_valid_handles() call and first global access
        ensure_line = None
        first_global_line = None

        for node in ast.walk(func):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == "_ensure_valid_handles":
                    if ensure_line is None or node.lineno < ensure_line:
                        ensure_line = node.lineno
            if isinstance(node, ast.Name) and node.id in globals_accessed:
                # Skip the 'global' statement itself
                if first_global_line is None or node.lineno < first_global_line:
                    # Check this isn't inside the global declaration
                    first_global_line = node.lineno

        # Find the global statement line to exclude it
        global_stmt_line = None
        for stmt in func.body:
            if isinstance(stmt, ast.Global):
                global_stmt_line = stmt.lineno
                break

        # Re-scan for first global access AFTER the global statement
        first_access_line = None
        for node in ast.walk(func):
            if isinstance(node, ast.Name) and node.id in globals_accessed:
                if global_stmt_line and node.lineno == global_stmt_line:
                    continue
                if first_access_line is None or node.lineno < first_access_line:
                    first_access_line = node.lineno

        if ensure_line is not None and first_access_line is not None:
            assert ensure_line <= first_access_line, (
                f"{name}(): _ensure_valid_handles() at line {ensure_line} is called "
                f"AFTER first global access at line {first_access_line}. "
                "It must be called before any use of _layout/_layout_view/_top_cell."
            )


# ---------------------------------------------------------------------------
# MCP integration tests — runtime verification (requires running KLayout)
# ---------------------------------------------------------------------------

import json
import urllib.request
import urllib.error

MCP_URL = os.environ.get("KLAYOUT_MCP_URL", "http://127.0.0.1:8765/mcp")
_req_id = 0
_session_id = None


def _mcp_available():
    return is_klayoutclaw(MCP_URL)


def mcp_call(method, params=None, timeout=30):
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
    result = mcp_call("tools/call", {"name": tool_name, "arguments": kwargs}, timeout=30)
    content = result["result"]["content"][0]
    text = content["text"]
    if result["result"].get("isError"):
        raise RuntimeError(f"MCP tool error: {text}")
    return json.loads(text)


def tool_call_raw(tool_name, **kwargs):
    result = mcp_call("tools/call", {"name": tool_name, "arguments": kwargs}, timeout=30)
    content = result["result"]["content"][0]
    text = content["text"]
    if result["result"].get("isError"):
        raise RuntimeError(f"MCP tool error: {text}")
    return text


def init_session():
    mcp_call("initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "test_stale_handles", "version": "0.1"},
    })


_mcp_ok = _mcp_available()


@pytest.mark.mcp
@pytest.mark.skipif(not _mcp_ok, reason=f"KLayout MCP server not reachable at {MCP_URL}")
class TestStaleHandlesMCP:
    """MCP integration tests: create_layout twice then use other tools."""

    @pytest.fixture(scope="class", autouse=True)
    def mcp_session(self):
        init_session()
        yield

    def test_get_layout_info_after_double_create(self):
        """create_layout twice, then get_layout_info must succeed."""
        tool_call("create_layout", name="STALE_A", dbu=0.001)
        tool_call("create_layout", name="STALE_B", dbu=0.001)
        info = tool_call("get_layout_info")
        assert info["status"] == "ok"

    def test_correct_cell_name_after_double_create(self):
        """After two create_layout calls, get_layout_info must reflect the second cell."""
        tool_call("create_layout", name="FIRST_CELL", dbu=0.001)
        tool_call("create_layout", name="SECOND_CELL", dbu=0.001)
        info = tool_call("get_layout_info")
        assert "SECOND_CELL" in info["cells"], (
            f"Expected 'SECOND_CELL' in cells list, got {info['cells']}"
        )

    def test_execute_script_after_double_create(self):
        """execute_script must work after two create_layout calls."""
        tool_call("create_layout", name="ES_A", dbu=0.001)
        tool_call("create_layout", name="ES_B", dbu=0.001)
        raw = tool_call_raw("execute_script", code='result = {"cell": _top_cell.name}')
        data = json.loads(raw)
        assert data["cell"] == "ES_B", (
            f"Expected _top_cell.name == 'ES_B', got {data['cell']}"
        )

    def test_save_layout_after_double_create(self):
        """save_layout must work after two create_layout calls."""
        tool_call("create_layout", name="SL_A", dbu=0.001)
        tool_call("create_layout", name="SL_B", dbu=0.001)
        import tempfile
        _tf = tempfile.NamedTemporaryFile(suffix=".gds", prefix="test_stale_save_", delete=False)
        _tf.close()
        result = tool_call("save_layout", filepath=_tf.name)
        assert result["status"] == "ok"

    def test_screenshot_after_double_create(self):
        """screenshot must work after two create_layout calls."""
        tool_call("create_layout", name="SS_A", dbu=0.001)
        tool_call("create_layout", name="SS_B", dbu=0.001)
        import tempfile
        _tf = tempfile.NamedTemporaryFile(suffix=".png", prefix="test_stale_screenshot_", delete=False)
        _tf.close()
        result = tool_call("screenshot", filepath=_tf.name)
        assert result["status"] == "ok"

    def test_triple_create_then_get_info(self):
        """Three sequential create_layout calls, then get_layout_info must succeed."""
        tool_call("create_layout", name="TRI_A", dbu=0.001)
        tool_call("create_layout", name="TRI_B", dbu=0.001)
        tool_call("create_layout", name="TRI_C", dbu=0.001)
        info = tool_call("get_layout_info")
        assert info["status"] == "ok"
        assert "TRI_C" in info["cells"]

    def test_execute_script_get_or_create_view_after_double_create(self):
        """execute_script calling _get_or_create_view() must work after double create."""
        tool_call("create_layout", name="GOC_A", dbu=0.001)
        tool_call("create_layout", name="GOC_B", dbu=0.001)
        raw = tool_call_raw("execute_script", code="""
lv, ly, tc = _get_or_create_view()
result = {"cell": tc.name, "dbu": ly.dbu}
""")
        data = json.loads(raw)
        assert data["cell"] == "GOC_B"

    def test_evaluate_design_after_double_create(self):
        """evaluate_design must not crash from stale handles after double create.
        It may error due to missing conda env — that's fine, the point is it
        doesn't crash from stale layout/top_cell references."""
        tool_call("create_layout", name="EVAL_A", dbu=0.001)
        tool_call("create_layout", name="EVAL_B", dbu=0.001)
        layer_map = {"mesa": [1, 0], "contact": [2, 0]}
        checks = [{"name": "component_containment",
                    "args": {"component": "contact", "region": "mesa"},
                    "weight": 1.0}]
        try:
            tool_call("evaluate_design", layer_map=layer_map, checks=checks)
        except RuntimeError as e:
            err = str(e)
            # Acceptable errors: missing conda env, worker not found, etc.
            # NOT acceptable: stale handle / destroyed object errors
            assert "destroyed" not in err.lower() and "stale" not in err.lower(), (
                f"evaluate_design crashed with stale handle error: {err}"
            )

    def test_auto_route_after_double_create(self):
        """auto_route must not crash from stale handles after double create.
        It may error due to missing pins/conda — that's fine, the point is
        it doesn't crash from stale layout references."""
        tool_call("create_layout", name="AR_A", dbu=0.001)
        tool_call("create_layout", name="AR_B", dbu=0.001)
        try:
            tool_call("auto_route", pin_layer_a="102/0", pin_layer_b="111/0")
        except RuntimeError as e:
            err = str(e)
            assert "destroyed" not in err.lower() and "stale" not in err.lower(), (
                f"auto_route crashed with stale handle error: {err}"
            )
