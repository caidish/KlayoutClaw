"""Notebook unsafe backend — cell manipulation and execution via active cell bridge.

Wraps instrMCP's active_cell_bridge to provide notebook cell operations:
- Add cells with content (visible in JupyterLab)
- Edit cell content (visible in JupyterLab)
- Execute cells
- Delete cells
- Read active cell content

Requires instrMCP to be installed in the Jupyter environment for the
active_cell_bridge module and JupyterLab extension.
"""

import asyncio
import logging
import sys
import time
from typing import Any, Dict, List, Optional

from .base import BaseBackend

logger = logging.getLogger(__name__)


class NotebookUnsafeBackend(BaseBackend):
    """Backend for notebook cell manipulation using instrMCP's active cell bridge."""

    def __init__(self, state, bridge_module):
        """Initialize with shared state and the active_cell_bridge module.

        Args:
            state: SharedState instance
            bridge_module: The active_cell_bridge module (from instrMCP)
        """
        super().__init__(state)
        self.bridge = bridge_module

    async def get_editing_cell(
        self, fresh_ms: Optional[int] = None
    ) -> Dict[str, Any]:
        """Get the content of the currently active/editing cell in JupyterLab."""
        try:
            snapshot = self.bridge.get_active_cell(fresh_ms=fresh_ms)
            if snapshot is None:
                return {
                    "status": "error",
                    "error": "No cell snapshot available. Is JupyterLab open with a notebook?",
                }
            return {
                "status": "success",
                "cell_content": snapshot.get("text", ""),
                "cell_type": snapshot.get("cell_type", "code"),
                "cell_id": snapshot.get("id"),
                "index": snapshot.get("index"),
                "notebook_path": snapshot.get("path"),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def execute_editing_cell(
        self, timeout: float = 30.0
    ) -> Dict[str, Any]:
        """Execute the currently active cell in JupyterLab.

        The cell must be visible and active in the JupyterLab frontend.
        Execution runs via NotebookActions.run() on the frontend side.
        """
        try:
            # Capture initial execution count for completion detection
            ipython = self.ipython
            initial_count = ipython.execution_count

            # Trigger execution via bridge
            exec_result = self.bridge.execute_active_cell(timeout_s=min(timeout, 5.0))

            if not exec_result.get("success", False):
                return {
                    "status": "error",
                    "executed": False,
                    "error": exec_result.get("error", "Execution request failed"),
                }

            # Wait for execution to complete
            result = await self._wait_for_execution(initial_count, timeout)

            # Try to get output
            try:
                output = self.bridge.get_active_cell_output(timeout_s=min(timeout, 10.0))
                if output and output.get("success"):
                    result["has_output"] = True
                    result["outputs"] = output.get("outputs", [])
                else:
                    result["has_output"] = False
            except Exception as e:
                logger.debug(f"Could not get cell output: {e}")
                result["has_output"] = False

            return result

        except Exception as e:
            logger.error(f"Error in execute_editing_cell: {e}")
            return {
                "status": "error",
                "executed": False,
                "error": str(e),
            }

    async def _wait_for_execution(
        self, initial_count: int, timeout: float = 30.0
    ) -> Dict[str, Any]:
        """Wait for cell execution to complete by monitoring execution_count."""
        ipython = self.ipython
        start = time.time()
        initial_result = ipython.last_execution_result

        while time.time() - start < timeout:
            # Check if execution count increased
            if ipython.execution_count > initial_count:
                # Check if result changed (execution completed)
                current_result = ipython.last_execution_result
                if current_result is not initial_result:
                    # Check for errors
                    has_error = hasattr(sys, "last_traceback") and sys.last_traceback is not None
                    if current_result and not current_result.success:
                        has_error = True

                    return {
                        "status": "error" if has_error else "completed",
                        "executed": True,
                        "has_error": has_error,
                        "cell_number": ipython.execution_count - 1,
                    }

            await asyncio.sleep(0.1)

        return {
            "status": "timeout",
            "executed": True,
            "error": f"Execution did not complete within {timeout}s",
        }

    async def add_new_cell(
        self,
        cell_type: str = "code",
        position: str = "below",
        content: str = "",
    ) -> Dict[str, Any]:
        """Add a new cell to the notebook (visible in JupyterLab).

        Args:
            cell_type: "code" or "markdown"
            position: "above" or "below" the current cell
            content: Initial cell content
        """
        try:
            result = self.bridge.add_new_cell(
                cell_type=cell_type,
                position=position,
                content=content,
                timeout_s=2.0,
            )
            return {
                "success": result.get("success", False),
                "error": result.get("error"),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def delete_editing_cell(self) -> Dict[str, Any]:
        """Delete the currently active cell."""
        try:
            result = self.bridge.delete_editing_cell(timeout_s=2.0)
            return {
                "success": result.get("success", False),
                "error": result.get("error"),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def delete_cells_by_index(
        self, cell_id_notebooks: List[int]
    ) -> Dict[str, Any]:
        """Delete cells by their position index."""
        try:
            result = self.bridge.delete_cells_by_index(
                cell_id_notebooks=cell_id_notebooks, timeout_s=2.0
            )
            return {
                "success": result.get("success", False),
                "error": result.get("error"),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def apply_patch(
        self, old_text: str, new_text: str
    ) -> Dict[str, Any]:
        """Apply a text replacement patch to the active cell."""
        try:
            result = self.bridge.apply_patch(
                old_text=old_text, new_text=new_text, timeout_s=2.0
            )
            return {
                "success": result.get("success", False),
                "error": result.get("error"),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def move_cursor(self, target: str) -> Dict[str, Any]:
        """Move the cursor to a different cell.

        Args:
            target: "above", "below", "bottom", or "index:N"
        """
        try:
            result = self.bridge.move_cursor(target=target, timeout_s=2.0)
            return {
                "success": result.get("success", False),
                "error": result.get("error"),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def read_active_cell(self) -> Dict[str, Any]:
        """Read the content of the currently active cell."""
        return await self.get_editing_cell()

    async def read_active_cell_output(
        self, timeout: float = 10.0
    ) -> Dict[str, Any]:
        """Read the output of the currently active cell."""
        try:
            result = self.bridge.get_active_cell_output(timeout_s=timeout)
            if result and result.get("success"):
                return {
                    "status": "success",
                    "has_output": True,
                    "outputs": result.get("outputs", []),
                }
            return {
                "status": "success",
                "has_output": False,
                "outputs": [],
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_notebook_content(self) -> Dict[str, Any]:
        """Get the full notebook structure (cell types, indices, no source code)."""
        try:
            result = self.bridge.get_notebook_structure(timeout_s=2.0)
            return result
        except Exception as e:
            return {"status": "error", "error": str(e)}
