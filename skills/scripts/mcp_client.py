#!/usr/bin/env python
"""Shared MCP client for KlayoutClaw skills.

Provides helpers to call the KlayoutClaw MCP server at 127.0.0.1:8765.
"""

import json
import sys
import urllib.request
import urllib.error

MCP_URL = "http://127.0.0.1:8765/mcp"
_req_id = 0
_session_id = None


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
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.URLError as e:
        print(f"ERROR: Cannot connect to KLayout MCP server at {MCP_URL}", file=sys.stderr)
        print(f"  Make sure KLayout is running with KlayoutClaw plugin.", file=sys.stderr)
        sys.exit(1)
    _session_id = r.headers.get("Mcp-Session-Id", _session_id)
    data = json.loads(r.read().decode())
    if "error" in data:
        raise RuntimeError(f"MCP error: {data['error']}")
    return data


def tool_call(tool_name, timeout=300, **kwargs):
    """Call an MCP tool and return parsed JSON result."""
    result = mcp_call("tools/call", {"name": tool_name, "arguments": kwargs}, timeout=timeout)
    text = result["result"]["content"][0]["text"]
    return json.loads(text)


def execute_script(code):
    """Execute Python/pya code in KLayout via execute_script tool."""
    return tool_call("execute_script", code=code)


def init_session():
    """Initialize MCP session (call once at start)."""
    mcp_call("initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "klayoutclaw-skill", "version": "0.3"},
    })
