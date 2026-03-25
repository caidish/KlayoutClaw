#!/usr/bin/env python
"""Static analysis tests for bugs in klayoutclaw_server.lym.

These tests parse the .lym XML file, extract the Python source from
the <text> tag, and use the ast module to verify correctness without
needing a running KLayout instance.

Bug: _refresh_view calls zoom_fit unconditionally.
    _refresh_view() is called after every execute_script invocation.
    Its unconditional `_layout_view.zoom_fit()` at the end overrides
    any zoom_box() that user code set during execution.
    zoom_fit() must only fire when new layers are added.

Tests FAIL against the buggy code and PASS once fixed.
"""
import ast
import os
import xml.etree.ElementTree as ET

import pytest

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
    """Find a FunctionDef with the given name anywhere in the AST."""
    for node in ast.walk(module_ast):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


# ---------------------------------------------------------------------------
# _refresh_view must not call zoom_fit unconditionally
# ---------------------------------------------------------------------------

class TestRefreshViewZoomFit:
    """_refresh_view() is called after every execute_script invocation.

    If it calls _layout_view.zoom_fit() unconditionally, any zoom_box()
    set by user code during execute_script is immediately overridden.
    zoom_fit() must only be called inside a conditional.
    """

    def test_function_exists(self, server_ast):
        func = _find_function(server_ast, "_refresh_view")
        assert func is not None, "_refresh_view not found in source"

    def test_zoom_fit_not_unconditional(self, server_ast):
        """zoom_fit() must not appear as a bare statement in the function
        body -- it must be guarded by an if-condition."""
        func = _find_function(server_ast, "_refresh_view")
        assert func is not None

        for stmt in func.body:
            if not isinstance(stmt, ast.Expr):
                continue
            call = stmt.value
            if not isinstance(call, ast.Call):
                continue
            if isinstance(call.func, ast.Attribute) and call.func.attr == "zoom_fit":
                pytest.fail(
                    "_refresh_view calls zoom_fit() unconditionally "
                    "(line {}). It should be inside an if-block so user "
                    "zoom_box() is preserved.".format(stmt.lineno)
                )

    def test_zoom_fit_not_at_top_of_for_loop(self, server_ast):
        """Every zoom_fit() call must be nested inside an If node,
        not just inside a for-loop or bare function body."""
        func = _find_function(server_ast, "_refresh_view")
        assert func is not None

        zoom_calls = []
        for node in ast.walk(func):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "zoom_fit"):
                zoom_calls.append(node)

        for zc in zoom_calls:
            inside_if = False
            for node in ast.walk(func):
                if isinstance(node, ast.If):
                    for child in ast.walk(node):
                        if child is zc:
                            inside_if = True
                            break
                if inside_if:
                    break
            assert inside_if, (
                "zoom_fit() at line {} is not inside an if-block. "
                "It must be conditional to avoid overriding user "
                "zoom_box().".format(zc.lineno)
            )
