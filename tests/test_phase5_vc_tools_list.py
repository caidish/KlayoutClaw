#!/usr/bin/env python
"""Phase 5 + Issue #25 — vc_* tools must NOT be advertised by tools/list.

Originally this test (Phase 5 Task 5.10) asserted that the MCP server
exposed all 9 ``vc_*`` handlers in its ``tools/list`` response. Issue #25
(review session 2026-05-05) inverted that contract: ``vc_init`` was the
only confirmed hang trigger, and the cheapest mitigation is a server-side
off-switch — strip the ``vc_*`` entries from ``_TOOL_DISPATCH`` and from
the advertised ``TOOLS`` list. The handler module
``tools/vc_mcp_handlers`` is still imported and unit-tested separately
(see ``test_phase5_vc_handlers.py``); only the MCP advertisement has been
removed.

Auto-skips when the MCP server is not reachable.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import pytest

from mcp_identity import is_klayoutclaw


MCP_URL = os.environ.get("KLAYOUT_MCP_URL", "http://127.0.0.1:8765/mcp")

_FORBIDDEN_VC_NAMES = [
    "vc_init",
    "vc_checkpoint",
    "vc_history",
    "vc_checkout",
    "vc_diff",
    "vc_branch",
    "vc_tag",
    "vc_export",
    "vc_status",
]


def _mcp_available() -> bool:
    return is_klayoutclaw(MCP_URL)


pytestmark = [
    pytest.mark.mcp,
    pytest.mark.skipif(
        not _mcp_available(),
        reason=f"KLayout MCP server not reachable at {MCP_URL}",
    ),
]


_session_id = None
_req_id = 0


def _mcp_call(method: str, params=None, timeout: int = 30):
    global _req_id, _session_id
    _req_id += 1
    body = {"jsonrpc": "2.0", "id": _req_id, "method": method}
    if params is not None:
        body["params"] = params
    headers = {"Content-Type": "application/json"}
    if _session_id:
        headers["Mcp-Session-Id"] = _session_id
    req = urllib.request.Request(
        MCP_URL, data=json.dumps(body).encode(),
        headers=headers, method="POST",
    )
    r = urllib.request.urlopen(req, timeout=timeout)
    _session_id = r.headers.get("Mcp-Session-Id", _session_id)
    data = json.loads(r.read().decode())
    if "error" in data:
        raise RuntimeError(f"MCP error: {data['error']}")
    return data


@pytest.fixture(scope="module", autouse=True)
def _mcp_init_session():
    _mcp_call("initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "test_phase5_vc_tools_list", "version": "0.2"},
    })
    yield


def _get_tool_names():
    resp = _mcp_call("tools/list")
    tools = resp["result"]["tools"]
    assert isinstance(tools, list) and len(tools) > 0, (
        f"tools/list returned empty/malformed tools array: {resp!r}"
    )
    return [t["name"] for t in tools]


class TestIssue25VcToolsStripped:
    def test_no_vc_tools_in_tools_list(self):
        """Issue #25: the MCP server must not advertise any vc_* tool."""
        names = _get_tool_names()
        leaked = [n for n in names if n in _FORBIDDEN_VC_NAMES]
        assert not leaked, (
            f"vc_* tools are still advertised by tools/list: {leaked}. "
            f"Issue #25 requires these be stripped."
        )

    def test_no_name_starts_with_vc_(self):
        """Defense in depth: no advertised tool name may start with 'vc_'."""
        names = _get_tool_names()
        leaked = [n for n in names if n.startswith("vc_")]
        assert not leaked, (
            f"Tool names beginning with 'vc_' must not be advertised: "
            f"{leaked}."
        )
