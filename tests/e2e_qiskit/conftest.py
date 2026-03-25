"""Shared fixtures and MCP client helpers for E2E tests.

E2E tests require:
  1. KLayout running with KlayoutClaw plugin → port 8765
  2. Jupyter kernel with Qiskit MCP server   → port 8124

Start them before running tests:
  - KLayout: open /Applications/klayout.app (plugin auto-starts on 8765)
  - Qiskit MCP: in Jupyter, run %load_ext qiskit_mcp.jupyter_mcp_extension
                then %qiskit_mcp_start
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

import pytest

# ---------------------------------------------------------------------------
# MCP client classes
# ---------------------------------------------------------------------------

KLAYOUT_URL = os.environ.get("KLAYOUT_MCP_URL", "http://127.0.0.1:8765/mcp")
QISKIT_URL = os.environ.get("QISKIT_MCP_URL", "http://127.0.0.1:8124/mcp")


class MCPClient:
    """Lightweight MCP JSON-RPC 2.0 client over HTTP.

    Handles both plain JSON responses (KLayout server) and
    SSE event-stream responses (FastMCP 3.x / Qiskit server).
    """

    def __init__(self, url: str, client_name: str = "e2e-test"):
        self.url = url
        self.client_name = client_name
        self._req_id = 0
        self._session_id = None

    # -- low-level --------------------------------------------------------

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _request(self, method: str, params: dict | None = None,
                 timeout: int = 30) -> dict:
        """Send a JSON-RPC request and return the parsed response dict."""
        payload = {"jsonrpc": "2.0", "id": self._next_id(), "method": method}
        if params is not None:
            payload["params"] = params

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        req = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=timeout)

        # Store session ID from response headers
        sid = resp.headers.get("Mcp-Session-Id")
        if sid:
            self._session_id = sid

        body = resp.read().decode()
        ct = resp.headers.get("Content-Type", "")

        # FastMCP 3.x returns SSE:  "event: message\r\ndata: {...}\r\n\r\n"
        if "text/event-stream" in ct:
            for line in body.split("\n"):
                line = line.strip()
                if line.startswith("data: "):
                    return json.loads(line[6:])
            raise ValueError(f"No data: line in SSE response: {body[:200]}")

        # Plain JSON (KLayout server)
        return json.loads(body)

    # -- high-level -------------------------------------------------------

    def initialize(self) -> dict:
        """Initialize MCP session. Must be called before other methods."""
        data = self._request("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": self.client_name, "version": "1.0"},
        })
        return data.get("result", data)

    def list_tools(self) -> list[dict]:
        """Return list of available tool descriptors."""
        data = self._request("tools/list", {})
        return data.get("result", {}).get("tools", [])

    def call_tool(self, tool_name: str, timeout: int = 60, **kwargs) -> dict:
        """Call a tool and return the parsed JSON result.

        Automatically extracts the text content and parses it as JSON.
        Raises RuntimeError on MCP-level or tool-level errors.
        """
        data = self._request(
            "tools/call",
            {"name": tool_name, "arguments": kwargs},
            timeout=timeout,
        )
        if "error" in data:
            raise RuntimeError(f"MCP error calling {tool_name}: {data['error']}")

        result = data.get("result", {})
        is_error = result.get("isError", False)
        text = result["content"][0]["text"]

        if is_error:
            raise RuntimeError(f"Tool '{tool_name}' error: {text}")

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"_raw_text": text}

    def execute_script(self, code: str, timeout: int = 30) -> dict:
        """Shorthand for KLayout execute_script tool."""
        return self.call_tool("execute_script", timeout=timeout, code=code)

    def is_available(self, timeout: int = 3) -> bool:
        """Check if the MCP server is reachable."""
        try:
            self.initialize()
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def klayout(request):
    """KLayout MCP client (port 8765). Skips all tests if server unavailable."""
    client = MCPClient(KLAYOUT_URL, client_name="e2e-klayout")
    if not client.is_available():
        pytest.skip("KLayout MCP server not running on port 8765")
    return client


@pytest.fixture(scope="session")
def qiskit(request):
    """Qiskit MCP client (port 8124). Skips all tests if server unavailable."""
    client = MCPClient(QISKIT_URL, client_name="e2e-qiskit")
    if not client.is_available():
        pytest.skip("Qiskit MCP server not running on port 8124")
    return client


@pytest.fixture(scope="session")
def output_dir(tmp_path_factory):
    """Shared output directory for E2E test artifacts."""
    d = tmp_path_factory.mktemp("e2e_qiskit")
    return str(d)
