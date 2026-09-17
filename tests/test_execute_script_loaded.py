"""Reproducer + regression for execute_script under heavy state load.

Context: OpenCode ml14 benchmark (2026-04-14/15) collapsed to score 0.169
because execute_script began returning IndexError / TypeError after the
layout accumulated 200+ shapes across multiple layers. The client
timeout bump (30s -> 300s) did NOT fix this — the server was responding,
with a structurally broken payload.

This test:
1. Initializes an MCP session.
2. Seeds 750 boxes (250 each on L20/0, L21/0, L22/0) — well past the
   OC ml14 threshold.
3. Runs 10 back-to-back shape-iteration queries and asserts each
   succeeds with the expected count.
4. Runs 5 back-to-back begin_shapes_rec iteration queries.

Fails before any fix is applied (if the issue reproduces on this
machine). Passes after Task 5.3's fix, and stays in the suite as a
regression guard even if the issue doesn't reproduce.
"""
from __future__ import annotations
import json
import os
import time
import urllib.request
import pytest

from mcp_identity import is_klayoutclaw

MCP_URL = os.environ.get("KLAYOUT_MCP_URL", "http://127.0.0.1:8765/mcp")


def _mcp_call(method, params=None, sid=None, timeout=60):
    payload = {"jsonrpc": "2.0",
               "id": int(time.time()*1000) % 100000,
               "method": method}
    if params is not None:
        payload["params"] = params
    headers = {"Content-Type": "application/json"}
    if sid:
        headers["Mcp-Session-Id"] = sid
    req = urllib.request.Request(
        MCP_URL, data=json.dumps(payload).encode(),
        headers=headers, method="POST")
    resp = urllib.request.urlopen(req, timeout=timeout)
    new_sid = resp.headers.get("Mcp-Session-Id", sid)
    return resp, json.loads(resp.read().decode()), new_sid


def _require_server_up():
    if not is_klayoutclaw(MCP_URL, timeout=5):
        pytest.skip(f"KlayoutClaw MCP not reachable at {MCP_URL}")


def _exec(sid, code, timeout=180):
    return _mcp_call(
        "tools/call",
        {"name": "execute_script", "arguments": {"code": code}},
        sid=sid, timeout=timeout)


def _parse_result(mcp_response_dict) -> dict:
    """Extract and parse the JSON payload that execute_script returns.

    The server returns the user's ``result`` dict merged with
    ``stdout`` / ``stderr`` keys.  A top-level ``error`` key signals
    failure (IndexError, TypeError, etc.).  Success is indicated by
    the absence of ``error``.
    """
    # Check for JSON-RPC level error first
    if "error" in mcp_response_dict:
        return {"_rpc_error": mcp_response_dict["error"]}
    text = mcp_response_dict["result"]["content"][0]["text"]
    return json.loads(text)


def _assert_ok(parsed: dict, label: str = "") -> None:
    """Assert an execute_script result is successful (no error key)."""
    prefix = f"{label}: " if label else ""
    assert "error" not in parsed, (
        f"{prefix}execute_script returned error: {parsed['error']}")
    assert "_rpc_error" not in parsed, (
        f"{prefix}JSON-RPC error: {parsed['_rpc_error']}")


@pytest.fixture(scope="module")
def heavy_session():
    _require_server_up()
    # Initialize a fresh session
    _, _, sid = _mcp_call("initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "heavy_test", "version": "0.1"}})
    assert sid, "initialize did not return a session id"

    # Create a blank layout on this session so we can seed it.
    _, _, sid = _mcp_call(
        "tools/call",
        {"name": "create_layout", "arguments": {"name": "HEAVY"}},
        sid=sid)

    # Seed: 250 boxes each on L20/0, L21/0, L22/0 (750 total).
    seed_code = """
view, layout, cell = _get_or_create_view()
dbu = layout.dbu
li20 = layout.layer(20, 0)
li21 = layout.layer(21, 0)
li22 = layout.layer(22, 0)
import random
random.seed(42)
for i in range(250):
    x = random.randint(0, 10000)
    y = random.randint(0, 10000)
    cell.shapes(li20).insert(pya.Box(x, y, x + 50, y + 50))
    cell.shapes(li21).insert(pya.Box(x + 100, y, x + 150, y + 50))
    cell.shapes(li22).insert(pya.Box(x + 200, y, x + 250, y + 50))
result = {"inserted": 750}
"""
    _, data, sid = _exec(sid, seed_code, timeout=60)
    parsed = _parse_result(data)
    _assert_ok(parsed, "seed")
    assert parsed.get("inserted") == 750, (
        f"seed script returned unexpected result: {parsed}")
    yield sid


class TestExecuteScriptUnderLoad:
    """10 back-to-back queries iterating shapes must all succeed cleanly.
    A regression reproducing OC ml14's failure mode would surface here as
    IndexError / TypeError in one of the later iterations."""

    def test_ten_shape_each_queries_do_not_fail(self, heavy_session):
        query = """
view, layout, cell = _get_or_create_view()
li = layout.layer(20, 0)
n = 0
for s in cell.shapes(li).each():
    n += 1
result = {"count": n}
"""
        for i in range(10):
            _, data, _ = _exec(heavy_session, query, timeout=90)
            parsed = _parse_result(data)
            _assert_ok(parsed, f"iteration {i}")
            assert parsed.get("count") == 250, (
                f"iteration {i}: count mismatch: {parsed}")

    def test_five_recursive_iterations_do_not_fail(self, heavy_session):
        query = """
view, layout, cell = _get_or_create_view()
li = layout.layer(21, 0)
rsi = cell.begin_shapes_rec(li)
n = 0
while not rsi.at_end():
    n += 1
    rsi.next()
result = {"count": n}
"""
        for i in range(5):
            _, data, _ = _exec(heavy_session, query, timeout=90)
            parsed = _parse_result(data)
            _assert_ok(parsed, f"iteration {i}")
            assert parsed.get("count") == 250, (
                f"iteration {i}: count mismatch: {parsed}")

    def test_interleaved_layer_queries_do_not_fail(self, heavy_session):
        """Interleave queries on different layers to stress any shared
        iterator state the server might hold."""
        q_template = """
view, layout, cell = _get_or_create_view()
li = layout.layer({lay}, 0)
n = 0
for s in cell.shapes(li).each():
    n += 1
result = {{"count": n}}
"""
        for i in range(6):
            lay = 20 + (i % 3)
            _, data, _ = _exec(heavy_session,
                               q_template.format(lay=lay), timeout=90)
            parsed = _parse_result(data)
            _assert_ok(parsed, f"iter {i} L{lay}")
            assert parsed.get("count") == 250, (
                f"iter {i} L{lay}: count={parsed.get('count')}")
