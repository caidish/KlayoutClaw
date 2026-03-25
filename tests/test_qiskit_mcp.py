#!/usr/bin/env python
"""Test Qiskit MCP server connection and tool listing.

Usage:
    python tests/test_qiskit_mcp.py              # Test Qiskit server only
    python tests/test_qiskit_mcp.py --both       # Test both KLayout + Qiskit
"""

import json
import sys
import urllib.request
import urllib.error
import argparse

QISKIT_MCP_URL = "http://127.0.0.1:8124/mcp"
KLAYOUT_MCP_URL = "http://127.0.0.1:8765/mcp"
INSTRMCP_URL = "http://127.0.0.1:8123/mcp"

_req_id = 0
_sessions = {}


def mcp_call(url, method, params=None, timeout=10):
    """Send a JSON-RPC 2.0 request to an MCP server."""
    global _req_id
    _req_id += 1
    payload = {"jsonrpc": "2.0", "id": _req_id, "method": method}
    if params:
        payload["params"] = params
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    session_id = _sessions.get(url)
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    req = urllib.request.Request(url, json.dumps(payload).encode(), headers, method="POST")
    r = urllib.request.urlopen(req, timeout=timeout)
    _sessions[url] = r.headers.get("Mcp-Session-Id", _sessions.get(url))
    body = r.read().decode()
    ct = r.headers.get("Content-Type", "")
    if "text/event-stream" in ct:
        for line in body.split("\n"):
            line = line.strip()
            if line.startswith("data: "):
                return json.loads(line[6:])
    return json.loads(body)


def test_connection(url, name):
    print(f"\n--- {name} at {url} ---")
    try:
        result = mcp_call(url, "initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0"},
        })
        print(f"  [+] Connected")
        return True
    except Exception as e:
        print(f"  [X] FAILED: {e}")
        return False


def test_tools_list(url, name):
    try:
        result = mcp_call(url, "tools/list", {})
        tools = result.get("result", {}).get("tools", [])
        names = sorted([t["name"] for t in tools])
        print(f"  [+] {len(names)} tools:")
        for t in names:
            print(f"      - {t}")
        return True
    except Exception as e:
        print(f"  [X] Tools list FAILED: {e}")
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--both", action="store_true")
    args = parser.parse_args()

    results = []

    # Test Qiskit MCP
    ok = test_connection(QISKIT_MCP_URL, "Qiskit MCP")
    results.append(("Qiskit connection", ok))
    if ok:
        ok = test_tools_list(QISKIT_MCP_URL, "Qiskit MCP")
        results.append(("Qiskit tools", ok))

    if args.both:
        # Test KLayout MCP
        ok = test_connection(KLAYOUT_MCP_URL, "KLayout MCP")
        results.append(("KLayout connection", ok))
        if ok:
            test_tools_list(KLAYOUT_MCP_URL, "KLayout MCP")

        # Test instrMCP
        ok = test_connection(INSTRMCP_URL, "instrMCP")
        results.append(("instrMCP connection", ok))
        if ok:
            test_tools_list(INSTRMCP_URL, "instrMCP")

    print("\n=== Summary ===")
    all_ok = True
    for name, ok in results:
        print(f"  [{'+'if ok else'X'}] {name}")
        if not ok:
            all_ok = False
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
