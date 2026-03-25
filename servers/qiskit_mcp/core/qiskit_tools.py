"""Core tool registrar — namespace query tools.

These tools let the agent inspect what's in the Jupyter namespace:
circuits, variables, backends. No computation — the agent writes
its own code in notebook cells for that.
"""

import json
import logging
import time
from typing import List, Optional

from mcp.types import TextContent

logger = logging.getLogger(__name__)


class QiskitCoreToolRegistrar:
    """Registers read-only namespace query tools."""

    def __init__(self, mcp_server, tools):
        self.mcp = mcp_server
        self.tools = tools

    def register_all(self):
        self._register_list_circuits()
        self._register_circuit_info()
        self._register_list_backends()
        self._register_list_variables()
        self._register_read_variable()

    def _register_list_circuits(self):
        @self.mcp.tool(
            name="qiskit_list_circuits",
            annotations={"readOnlyHint": True, "idempotentHint": True},
        )
        async def list_circuits(
            type_filter: Optional[str] = None,
        ) -> List[TextContent]:
            """List all QuantumCircuit objects in the Jupyter namespace."""
            start = time.perf_counter()
            try:
                result = await self.tools.list_circuits(type_filter)
                logger.debug(f"list_circuits: {(time.perf_counter()-start)*1000:.0f}ms")
                return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
            except Exception as e:
                return [TextContent(type="text", text=json.dumps({"error": str(e)}))]

    def _register_circuit_info(self):
        @self.mcp.tool(
            name="qiskit_circuit_info",
            annotations={"readOnlyHint": True, "idempotentHint": True},
        )
        async def circuit_info(
            name: str, detailed: bool = False,
        ) -> List[TextContent]:
            """Get detailed info about a QuantumCircuit by variable name."""
            start = time.perf_counter()
            try:
                result = await self.tools.get_circuit_info(name, detailed)
                logger.debug(f"circuit_info: {(time.perf_counter()-start)*1000:.0f}ms")
                return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
            except Exception as e:
                return [TextContent(type="text", text=json.dumps({"error": str(e)}))]

    def _register_list_backends(self):
        @self.mcp.tool(
            name="qiskit_list_backends",
            annotations={"readOnlyHint": True, "idempotentHint": True},
        )
        async def list_backends() -> List[TextContent]:
            """List available Qiskit simulator backends."""
            start = time.perf_counter()
            try:
                result = await self.tools.list_backends()
                logger.debug(f"list_backends: {(time.perf_counter()-start)*1000:.0f}ms")
                return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
            except Exception as e:
                return [TextContent(type="text", text=json.dumps({"error": str(e)}))]

    def _register_list_variables(self):
        @self.mcp.tool(
            name="qiskit_list_variables",
            annotations={"readOnlyHint": True, "idempotentHint": True},
        )
        async def list_variables(
            type_filter: Optional[str] = None, max_items: int = 50,
        ) -> List[TextContent]:
            """List variables in the Jupyter kernel namespace."""
            start = time.perf_counter()
            try:
                result = await self.tools.list_variables(type_filter=type_filter, max_items=max_items)
                logger.debug(f"list_variables: {(time.perf_counter()-start)*1000:.0f}ms")
                return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
            except Exception as e:
                return [TextContent(type="text", text=json.dumps({"error": str(e)}))]

    def _register_read_variable(self):
        @self.mcp.tool(
            name="qiskit_read_variable",
            annotations={"readOnlyHint": True, "idempotentHint": True},
        )
        async def read_variable(
            name: str, detailed: bool = False,
        ) -> List[TextContent]:
            """Read a specific variable from the Jupyter kernel namespace."""
            start = time.perf_counter()
            try:
                result = await self.tools.read_variable(name=name, detailed=detailed)
                logger.debug(f"read_variable: {(time.perf_counter()-start)*1000:.0f}ms")
                return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
            except Exception as e:
                return [TextContent(type="text", text=json.dumps({"error": str(e)}))]
