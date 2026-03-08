#!/usr/bin/env python
"""TEST 1: Protocol-level connection test for KlayoutClaw MCP server.

Tests the MCP server at http://127.0.0.1:8765/mcp by sending
raw MCP protocol messages via HTTP. No Claude CLI needed.

Usage:
    python tests/test_connection.py
"""

import sys
import time
import json
import urllib.request
import urllib.error

MCP_URL = "http://127.0.0.1:8765/mcp"
TIMEOUT = 30  # seconds to wait for server


def wait_for_server():
    """Poll the MCP endpoint until it responds."""
    print(f"Waiting for MCP server at {MCP_URL}...")
    start = time.time()
    while time.time() - start < TIMEOUT:
        try:
            r = urllib.request.urlopen(MCP_URL, timeout=2)
            print(f"  Server responded with status {r.status}")
            return True
        except urllib.error.URLError:
            time.sleep(1)
    print(f"ERROR: Server not responding after {TIMEOUT}s")
    return False


def mcp_request(method: str, params: dict = None, req_id: int = 1,
                session_id: str = None):
    """Send a JSON-RPC request to the MCP server and return (response_obj, parsed_json)."""
    payload = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
    }
    if params:
        payload["params"] = params

    headers = {"Content-Type": "application/json"}
    if session_id:
        headers["Mcp-Session-Id"] = session_id

    req = urllib.request.Request(
        MCP_URL,
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    r = urllib.request.urlopen(req, timeout=10)
    body = r.read().decode()
    data = json.loads(body)
    return r, data


def mcp_notify(method: str, session_id: str):
    """Send a JSON-RPC notification (no id) to the MCP server."""
    payload = {
        "jsonrpc": "2.0",
        "method": method,
    }
    headers = {
        "Content-Type": "application/json",
        "Mcp-Session-Id": session_id,
    }
    req = urllib.request.Request(
        MCP_URL,
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    urllib.request.urlopen(req, timeout=5)


def test_initialize():
    """Send MCP initialize and return session ID."""
    print("\n--- Test: Initialize ---")
    r, data = mcp_request("initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "test_connection", "version": "0.1"},
    })
    print(f"  Status: {r.status}")

    session_id = r.headers.get("Mcp-Session-Id")
    print(f"  Session ID: {session_id}")

    if "result" in data:
        print(f"  Server: {data['result'].get('serverInfo', {})}")
        print("  PASS: Initialize succeeded")
    else:
        print(f"  Response: {json.dumps(data)[:500]}")
        print("  FAIL: No 'result' in response")

    return session_id, data


def test_tools_list(session_id: str):
    """List available tools and verify count."""
    print("\n--- Test: List Tools ---")
    r, data = mcp_request("tools/list", req_id=2, session_id=session_id)
    print(f"  Status: {r.status}")

    tools = []
    if "result" in data:
        tools = data["result"].get("tools", [])

    if tools:
        print(f"  Found {len(tools)} tools:")
        for t in tools:
            print(f"    - {t['name']}: {t.get('description', '')[:60]}")
        if len(tools) == 4:
            print("  PASS: Tools listed successfully (4 tools)")
        else:
            print(f"  FAIL: Expected 4 tools, got {len(tools)}")
    else:
        print(f"  Response: {json.dumps(data)[:500]}")
        print("  FAIL: Could not parse tools list")

    return tools


def test_get_layout_info(session_id: str):
    """Call get_layout_info tool."""
    print("\n--- Test: Call get_layout_info ---")
    r, data = mcp_request("tools/call", {
        "name": "get_layout_info",
        "arguments": {},
    }, req_id=3, session_id=session_id)
    print(f"  Status: {r.status}")
    print(f"  Response: {json.dumps(data)[:500]}")

    if "result" in data:
        print("  PASS: get_layout_info returned data")
        return True
    else:
        print("  FAIL: No 'result' in response")
        return False


def main():
    print("=" * 60)
    print("KlayoutClaw MCP Connection Test")
    print("=" * 60)

    if not wait_for_server():
        sys.exit(1)

    session_id, init_data = test_initialize()

    # Send initialized notification
    if session_id:
        mcp_notify("notifications/initialized", session_id)

    test_tools_list(session_id)
    test_get_layout_info(session_id)

    print("\n" + "=" * 60)
    print("All connection tests completed!")
    print("=" * 60)


if __name__ == "__main__":
    main()
