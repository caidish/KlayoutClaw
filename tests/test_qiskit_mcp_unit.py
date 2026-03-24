#!/usr/bin/env python
"""Unit tests for the Qiskit MCP server (rebuilt architecture).

Tests: server init, tool registration, namespace queries, HTTP lifecycle.
No computation backend tests — agent writes code in cells now.
"""

import asyncio
import json
import os
import sys
import time
import unittest
from unittest.mock import MagicMock


class MockIPython:
    """Minimal mock of IPython.InteractiveShell for testing."""

    def __init__(self):
        self.user_ns = {}
        self.execution_count = 0
        self.last_execution_result = None
        self.events = MagicMock()

    def run_cell(self, code, store_history=True):
        result = MagicMock()
        try:
            exec(code, self.user_ns)
            result.success = True
            result.result = None
            result.error_in_exec = None
        except Exception as e:
            result.success = False
            result.result = None
            result.error_in_exec = e
        if store_history:
            self.execution_count += 1
            self.last_execution_result = result
        return result


# ============================================================================
# Test 1: Namespace queries (QiskitCoreBackend)
# ============================================================================
class TestNamespaceQueries(unittest.TestCase):
    """Test read-only namespace inspection."""

    def setUp(self):
        from qiskit_mcp.backend.base import SharedState
        from qiskit_mcp.backend.qiskit_core import QiskitCoreBackend
        self.ip = MockIPython()
        self.state = SharedState(ipython=self.ip)
        self.backend = QiskitCoreBackend(self.state)

    def test_list_circuits_empty(self):
        result = asyncio.run(self.backend.list_circuits())
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["count"], 0)

    def test_list_circuits_finds_qc(self):
        from qiskit import QuantumCircuit
        qc = QuantumCircuit(2)
        qc.h(0)
        qc.cx(0, 1)
        self.ip.user_ns["bell"] = qc
        result = asyncio.run(self.backend.list_circuits())
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["circuits"][0]["name"], "bell")

    def test_circuit_info(self):
        from qiskit import QuantumCircuit
        qc = QuantumCircuit(2, 2)
        qc.h(0)
        qc.cx(0, 1)
        qc.measure([0, 1], [0, 1])
        self.ip.user_ns["my_circ"] = qc
        result = asyncio.run(self.backend.get_circuit_info("my_circ", detailed=True))
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["num_qubits"], 2)
        self.assertIn("gate_counts", result)

    def test_circuit_info_not_found(self):
        result = asyncio.run(self.backend.get_circuit_info("nonexistent"))
        self.assertEqual(result["status"], "error")

    def test_list_backends(self):
        result = asyncio.run(self.backend.list_backends())
        self.assertEqual(result["status"], "success")
        self.assertGreater(result["count"], 0)


# ============================================================================
# Test 2: Facade (tools.py)
# ============================================================================
class TestFacade(unittest.TestCase):
    """Test QiskitToolsFacade namespace query methods."""

    def setUp(self):
        from qiskit_mcp.tools import QiskitToolsFacade
        self.ip = MockIPython()
        self.facade = QiskitToolsFacade(self.ip)

    def test_list_variables(self):
        self.ip.user_ns["x"] = 42
        self.ip.user_ns["y"] = "hello"
        result = asyncio.run(self.facade.list_variables())
        self.assertEqual(result["status"], "success")
        names = [v["name"] for v in result["variables"]]
        self.assertIn("x", names)

    def test_list_variables_filter(self):
        self.ip.user_ns["my_int"] = 42
        self.ip.user_ns["my_str"] = "hello"
        result = asyncio.run(self.facade.list_variables(type_filter="int"))
        types = [v["type"] for v in result["variables"]]
        self.assertTrue(all(t == "int" for t in types))

    def test_read_variable(self):
        self.ip.user_ns["data"] = {"key": "value"}
        result = asyncio.run(self.facade.read_variable("data"))
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["type"], "dict")

    def test_read_variable_not_found(self):
        result = asyncio.run(self.facade.read_variable("nonexistent"))
        self.assertEqual(result["status"], "error")

    def test_list_circuits(self):
        result = asyncio.run(self.facade.list_circuits())
        self.assertEqual(result["status"], "success")


# ============================================================================
# Test 3: Server init and tool registration
# ============================================================================
class TestServerInit(unittest.TestCase):

    def test_server_creates_fastmcp(self):
        from qiskit_mcp.mcp_server import JupyterQiskitMCPServer
        ip = MockIPython()
        server = JupyterQiskitMCPServer(ip, port=18124)
        self.assertIsNotNone(server.mcp)
        self.assertFalse(server.running)

    def test_server_has_12_tools(self):
        from qiskit_mcp.mcp_server import JupyterQiskitMCPServer
        ip = MockIPython()
        server = JupyterQiskitMCPServer(ip, port=18125)
        tools = asyncio.run(server.mcp.list_tools())
        names = sorted([t.name for t in tools])
        print(f"\nTools ({len(names)}):")
        for n in names:
            print(f"  {n}")

        # 5 query + 7 cell manipulation = 12
        self.assertEqual(len(names), 12)
        self.assertIn("qiskit_list_circuits", names)
        self.assertIn("qiskit_list_variables", names)
        self.assertIn("notebook_add_cell", names)
        self.assertIn("notebook_execute_active_cell", names)

    def test_server_has_4_resources(self):
        from qiskit_mcp.mcp_server import JupyterQiskitMCPServer
        ip = MockIPython()
        server = JupyterQiskitMCPServer(ip, port=18126)
        resources = asyncio.run(server.mcp.list_resources())
        uris = sorted([str(r.uri) for r in resources])
        print(f"\nResources ({len(uris)}):")
        for u in uris:
            print(f"  {u}")
        self.assertEqual(len(uris), 4)


# ============================================================================
# Test 4: HTTP lifecycle
# ============================================================================
def _parse_sse_json(body: str) -> dict:
    for line in body.split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise ValueError(f"No data: line in SSE: {body[:200]}")


def _mcp_http(url, payload, session_id=None, timeout=10):
    import urllib.request
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    req = urllib.request.Request(url, json.dumps(payload).encode(), headers, method="POST")
    resp = urllib.request.urlopen(req, timeout=timeout)
    body = resp.read().decode()
    sid = resp.headers.get("Mcp-Session-Id", session_id)
    ct = resp.headers.get("Content-Type", "")
    data = _parse_sse_json(body) if "text/event-stream" in ct else json.loads(body)
    return data, sid


class TestHTTPLifecycle(unittest.TestCase):

    def test_start_stop(self):
        from qiskit_mcp.mcp_server import JupyterQiskitMCPServer
        ip = MockIPython()
        server = JupyterQiskitMCPServer(ip, port=18127)
        server.start_sync()
        self.assertTrue(server.is_running())
        try:
            data, _ = _mcp_http("http://127.0.0.1:18127/mcp", {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "test", "version": "1.0"}},
            })
            self.assertIn("result", data)
        finally:
            server.stop_sync()
        self.assertFalse(server.is_running())

    def test_list_tools_via_http(self):
        from qiskit_mcp.mcp_server import JupyterQiskitMCPServer
        ip = MockIPython()
        server = JupyterQiskitMCPServer(ip, port=18128)
        server.start_sync()
        try:
            _, sid = _mcp_http("http://127.0.0.1:18128/mcp", {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "test", "version": "1.0"}},
            })
            data, _ = _mcp_http("http://127.0.0.1:18128/mcp",
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                session_id=sid)
            tools = data["result"]["tools"]
            names = [t["name"] for t in tools]
            self.assertIn("qiskit_list_circuits", names)
            self.assertIn("notebook_add_cell", names)
            self.assertEqual(len(names), 12)
        finally:
            server.stop_sync()


# ============================================================================
# Test 5: Bridge availability
# ============================================================================
class TestBridgeAvailability(unittest.TestCase):

    def test_bridge_detected(self):
        from qiskit_mcp.mcp_server import BRIDGE_AVAILABLE
        self.assertIsInstance(BRIDGE_AVAILABLE, bool)
        print(f"\n  Bridge available: {BRIDGE_AVAILABLE}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
