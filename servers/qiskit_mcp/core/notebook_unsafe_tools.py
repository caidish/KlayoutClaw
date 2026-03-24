"""Notebook unsafe tool registrar.

Registers tools for cell manipulation in JupyterLab — add, edit, execute, delete.
These tools make code visible in the notebook UI, following instrMCP's pattern.

Requires instrMCP installed for the active_cell_bridge and JupyterLab extension.
"""

import json
import logging
import time
from typing import List, Optional

from mcp.types import TextContent

logger = logging.getLogger(__name__)


class NotebookUnsafeToolRegistrar:
    """Registers notebook cell manipulation tools with the MCP server."""

    def __init__(self, mcp_server, unsafe_backend):
        self.mcp = mcp_server
        self.backend = unsafe_backend

    def register_all(self):
        self._register_read_active_cell()
        self._register_read_active_cell_output()
        self._register_execute_active_cell()
        self._register_add_cell()
        self._register_delete_cell()
        self._register_apply_patch()
        self._register_move_cursor()

    def _register_read_active_cell(self):
        @self.mcp.tool(
            name="notebook_read_active_cell",
            annotations={"readOnlyHint": True, "idempotentHint": True},
        )
        async def read_active_cell() -> List[TextContent]:
            """Read the content of the currently active cell in JupyterLab."""
            start = time.perf_counter()
            try:
                result = await self.backend.read_active_cell()
                logger.debug(f"read_active_cell: {(time.perf_counter()-start)*1000:.0f}ms")
                return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
            except Exception as e:
                return [TextContent(type="text", text=json.dumps({"error": str(e)}))]

    def _register_read_active_cell_output(self):
        @self.mcp.tool(
            name="notebook_read_active_cell_output",
            annotations={"readOnlyHint": True, "idempotentHint": True},
        )
        async def read_active_cell_output() -> List[TextContent]:
            """Read the output of the currently active cell in JupyterLab."""
            start = time.perf_counter()
            try:
                result = await self.backend.read_active_cell_output()
                logger.debug(f"read_active_cell_output: {(time.perf_counter()-start)*1000:.0f}ms")
                return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
            except Exception as e:
                return [TextContent(type="text", text=json.dumps({"error": str(e)}))]

    def _register_execute_active_cell(self):
        @self.mcp.tool(
            name="notebook_execute_active_cell",
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
                "openWorldHint": True,
            },
        )
        async def execute_active_cell(
            timeout: float = 30.0,
        ) -> List[TextContent]:
            """Execute the currently active cell in JupyterLab.

            The cell must be visible and active in the notebook. Execution
            runs via the JupyterLab frontend (NotebookActions.run).
            """
            start = time.perf_counter()
            try:
                result = await self.backend.execute_editing_cell(timeout=timeout)
                logger.debug(f"execute_active_cell: {(time.perf_counter()-start)*1000:.0f}ms")
                return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
            except Exception as e:
                return [TextContent(type="text", text=json.dumps({"error": str(e)}))]

    def _register_add_cell(self):
        @self.mcp.tool(
            name="notebook_add_cell",
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
            },
        )
        async def add_cell(
            cell_type: str = "code",
            position: str = "below",
            content: str = "",
        ) -> List[TextContent]:
            """Add a new cell to the notebook (visible in JupyterLab).

            Args:
                cell_type: "code" or "markdown"
                position: "above" or "below" the current cell
                content: Initial cell content (Qiskit code, markdown, etc.)
            """
            # Normalize escape sequences
            content = content.replace("\\n", "\n").replace("\\t", "\t")
            start = time.perf_counter()
            try:
                result = await self.backend.add_new_cell(
                    cell_type=cell_type, position=position, content=content
                )
                logger.debug(f"add_cell: {(time.perf_counter()-start)*1000:.0f}ms")
                return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
            except Exception as e:
                return [TextContent(type="text", text=json.dumps({"error": str(e)}))]

    def _register_delete_cell(self):
        @self.mcp.tool(
            name="notebook_delete_cell",
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": True,
            },
        )
        async def delete_cell(
            cell_id_notebooks: Optional[str] = None,
        ) -> List[TextContent]:
            """Delete cell(s) from the notebook.

            Args:
                cell_id_notebooks: JSON string of cell index or list of indices.
                    If None, deletes the currently active cell.
                    Examples: "3", "[1, 3, 5]"
            """
            start = time.perf_counter()
            try:
                if cell_id_notebooks is None:
                    result = await self.backend.delete_editing_cell()
                else:
                    parsed = json.loads(cell_id_notebooks)
                    if isinstance(parsed, int):
                        cell_list = [parsed]
                    elif isinstance(parsed, list):
                        cell_list = [int(x) for x in parsed]
                    else:
                        return [TextContent(type="text", text=json.dumps({
                            "success": False,
                            "error": "cell_id_notebooks must be an int or list of ints",
                        }))]
                    result = await self.backend.delete_cells_by_index(cell_list)
                logger.debug(f"delete_cell: {(time.perf_counter()-start)*1000:.0f}ms")
                return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
            except json.JSONDecodeError as e:
                return [TextContent(type="text", text=json.dumps({
                    "success": False, "error": f"Invalid JSON: {e}",
                }))]
            except Exception as e:
                return [TextContent(type="text", text=json.dumps({"error": str(e)}))]

    def _register_apply_patch(self):
        @self.mcp.tool(
            name="notebook_apply_patch",
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        )
        async def apply_patch(
            old_text: str,
            new_text: str,
        ) -> List[TextContent]:
            """Edit the active cell by replacing old_text with new_text.

            The old_text must appear exactly once in the cell content.

            Args:
                old_text: Text to find and replace
                new_text: Replacement text
            """
            # Normalize escape sequences
            old_text = old_text.replace("\\n", "\n").replace("\\t", "\t")
            new_text = new_text.replace("\\n", "\n").replace("\\t", "\t")
            start = time.perf_counter()
            try:
                result = await self.backend.apply_patch(old_text=old_text, new_text=new_text)
                logger.debug(f"apply_patch: {(time.perf_counter()-start)*1000:.0f}ms")
                return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
            except Exception as e:
                return [TextContent(type="text", text=json.dumps({"error": str(e)}))]

    def _register_move_cursor(self):
        @self.mcp.tool(
            name="notebook_move_cursor",
            annotations={
                "readOnlyHint": False,
                "idempotentHint": True,
            },
        )
        async def move_cursor(target: str) -> List[TextContent]:
            """Move the active cell cursor in the notebook.

            Args:
                target: "above", "below", "bottom", or "index:N" (0-based)
            """
            start = time.perf_counter()
            try:
                result = await self.backend.move_cursor(target=target)
                logger.debug(f"move_cursor: {(time.perf_counter()-start)*1000:.0f}ms")
                return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
            except Exception as e:
                return [TextContent(type="text", text=json.dumps({"error": str(e)}))]
