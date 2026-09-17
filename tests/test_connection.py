#!/usr/bin/env python
"""Protocol-level connection test for the KlayoutClaw MCP server.

Usage:
    python tests/test_connection.py
    python tests/test_connection.py --url http://127.0.0.1:8766/mcp

``KLAYOUT_MCP_URL`` supplies the client URL when ``--url`` is omitted.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from mcp_identity import identity_error


DEFAULT_MCP_URL = "http://127.0.0.1:8765/mcp"
SUPPORTED_PROTOCOL_VERSIONS = {"2025-03-26"}
REQUIRED_TOOLS = {
    "create_layout",
    "execute_script",
    "get_layout_info",
    "save_layout",
}
DEFAULT_WAIT_SECONDS = 30


def initialize_error(data):
    """Return a diagnostic unless *data* is a supported initialize response."""
    if not isinstance(data, dict):
        return "initialize did not return a JSON object"
    if "error" in data:
        return f"initialize returned JSON-RPC error: {data['error']}"
    result = data.get("result")
    if not isinstance(result, dict):
        return f"initialize result must be an object, got {type(result).__name__}"
    server_info = result.get("serverInfo")
    if not isinstance(server_info, dict):
        return "initialize result has no serverInfo object"
    if server_info.get("name") != "KlayoutClaw":
        return f"initialize answered as {server_info.get('name')!r}, not 'KlayoutClaw'"
    protocol = result.get("protocolVersion")
    if protocol not in SUPPORTED_PROTOCOL_VERSIONS:
        return f"unsupported MCP protocol version {protocol!r}"
    return None


def tools_error(data):
    """Return a diagnostic unless tools/list contains the stable core tools."""
    result = data.get("result") if isinstance(data, dict) else None
    tools = result.get("tools") if isinstance(result, dict) else None
    if not isinstance(tools, list):
        return "tools/list result has no tools array"
    names = {
        tool.get("name") for tool in tools
        if isinstance(tool, dict) and isinstance(tool.get("name"), str)
    }
    missing = sorted(REQUIRED_TOOLS - names)
    if missing:
        return "tools/list is missing required tools: " + ", ".join(missing)
    return None


def _port_diagnostic(url):
    parsed = urlparse(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return f"Inspect the listener with: lsof -nP -iTCP:{port} -sTCP:LISTEN"


def wait_for_server(url, timeout=DEFAULT_WAIT_SECONDS):
    """Poll until KlayoutClaw's authenticated GET identity responds."""
    print(f"Waiting for KlayoutClaw MCP server at {url}...")
    start = time.monotonic()
    last_error = "no response"
    while time.monotonic() - start < timeout:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                raw = response.read().decode("utf-8")
            try:
                data = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as ex:
                last_error = f"HTTP responder returned invalid JSON: {ex}"
            else:
                last_error = identity_error(data)
                if last_error is None:
                    print("  PASS: GET /mcp identified KlayoutClaw")
                    return True
                print(f"ERROR: {last_error}")
                print(f"  {_port_diagnostic(url)}")
                return False
        except (urllib.error.URLError, OSError) as ex:
            last_error = str(ex)
        time.sleep(1)
    print(f"ERROR: KlayoutClaw did not respond after {timeout}s: {last_error}")
    print(f"  {_port_diagnostic(url)}")
    return False


def mcp_request(url, method, params=None, req_id=1, session_id=None):
    """Send JSON-RPC and return the HTTP response metadata and parsed JSON."""
    payload = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params:
        payload["params"] = params
    headers = {"Content-Type": "application/json"}
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    response = urllib.request.urlopen(request, timeout=10)
    data = json.loads(response.read().decode())
    return response, data


def mcp_notify(url, method, session_id=None):
    """Send a JSON-RPC notification (no request id)."""
    headers = {"Content-Type": "application/json"}
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    request = urllib.request.Request(
        url,
        data=json.dumps({"jsonrpc": "2.0", "method": method}).encode(),
        headers=headers,
        method="POST",
    )
    urllib.request.urlopen(request, timeout=5).close()


def initialize(url):
    """Initialize MCP and return ``(session_id, error)``."""
    print("\n--- Test: Initialize ---")
    response, data = mcp_request(url, "initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "test_connection", "version": "0.1"},
    })
    error = initialize_error(data)
    if error:
        print(f"  FAIL: {error}")
        print(f"  Response: {json.dumps(data)[:500]}")
        return None, error
    result = data["result"]
    print(f"  PASS: {result['serverInfo']} (protocol {result['protocolVersion']})")
    return response.headers.get("Mcp-Session-Id"), None


def list_tools(url, session_id):
    """List tools and validate the stable required subset."""
    print("\n--- Test: List Tools ---")
    _, data = mcp_request(url, "tools/list", req_id=2, session_id=session_id)
    error = tools_error(data)
    if error:
        print(f"  FAIL: {error}")
        return False
    tools = data["result"]["tools"]
    print(f"  PASS: Found {len(tools)} tools including the required core set")
    return True


def call_get_layout_info(url, session_id):
    """Call a safe tool and validate its MCP result envelope."""
    print("\n--- Test: Call get_layout_info ---")
    _, data = mcp_request(
        url,
        "tools/call",
        {"name": "get_layout_info", "arguments": {}},
        req_id=3,
        session_id=session_id,
    )
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, dict) or result.get("isError"):
        print(f"  FAIL: Invalid tool result: {json.dumps(data)[:500]}")
        return False
    content = result.get("content")
    if not isinstance(content, list) or not content:
        print("  FAIL: Tool result contains no content")
        return False
    print("  PASS: get_layout_info returned data")
    return True


def run_connection_test(url, wait_seconds=DEFAULT_WAIT_SECONDS):
    """Run the complete authenticated connection test."""
    print("=" * 60)
    print("KlayoutClaw MCP Connection Test")
    print("=" * 60)
    if not wait_for_server(url, wait_seconds):
        return 1
    try:
        session_id, error = initialize(url)
        if error:
            print(f"  {_port_diagnostic(url)}")
            return 1
        mcp_notify(url, "notifications/initialized", session_id)
        if not list_tools(url, session_id):
            return 1
        if not call_get_layout_info(url, session_id):
            return 1
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as ex:
        print(f"ERROR: MCP handshake failed: {ex}")
        print(f"  {_port_diagnostic(url)}")
        return 1
    print("\nAll connection tests passed!")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=os.environ.get("KLAYOUT_MCP_URL", DEFAULT_MCP_URL),
        help="MCP URL (default: KLAYOUT_MCP_URL or %(default)s)",
    )
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=DEFAULT_WAIT_SECONDS,
        help="maximum startup wait",
    )
    args = parser.parse_args(argv)
    return run_connection_test(args.url, args.wait_seconds)


if __name__ == "__main__":
    sys.exit(main())
