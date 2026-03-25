"""QiskitToolsFacade — namespace query tools only.

The agent writes its own code in notebook cells. This facade provides
read-only queries into the Jupyter namespace (circuits, variables, backends).
All computation happens in visible notebook cells via the instrMCP bridge.
"""

import logging
from typing import Any, Dict, Optional

from .backend.base import SharedState
from .backend.qiskit_core import QiskitCoreBackend

logger = logging.getLogger(__name__)


class QiskitToolsFacade:
    """Facade for read-only namespace queries. No computation."""

    def __init__(self, ipython):
        self.ipython = ipython
        self.namespace = ipython.user_ns
        self._state = SharedState(ipython=ipython, namespace=ipython.user_ns)
        self._core = QiskitCoreBackend(self._state)
        logger.debug("QiskitToolsFacade initialized (namespace queries only)")

    # -- Circuit queries ---------------------------------------------------

    async def list_circuits(self, type_filter: Optional[str] = None) -> Dict[str, Any]:
        return await self._core.list_circuits(type_filter)

    async def get_circuit_info(self, name: str, detailed: bool = False) -> Dict[str, Any]:
        return await self._core.get_circuit_info(name, detailed)

    async def list_backends(self) -> Dict[str, Any]:
        return await self._core.list_backends()

    # -- Variable queries --------------------------------------------------

    async def list_variables(
        self, type_filter: Optional[str] = None, max_items: int = 50
    ) -> Dict[str, Any]:
        """List variables in the notebook namespace."""
        variables = []
        for name, obj in self.namespace.items():
            if name.startswith("_"):
                continue
            type_name = type(obj).__name__
            if type_name in ("module", "function", "builtin_function_or_method", "type"):
                continue
            if type_filter and type_filter.lower() not in type_name.lower():
                continue
            try:
                repr_str = repr(obj)
                if len(repr_str) > 200:
                    repr_str = repr_str[:200] + "..."
            except Exception:
                repr_str = f"<{type_name}>"
            variables.append({"name": name, "type": type_name, "repr": repr_str})
            if len(variables) >= max_items:
                break
        return {
            "status": "success",
            "variables": variables,
            "count": len(variables),
            "truncated": len(variables) >= max_items,
        }

    async def read_variable(
        self, name: str, detailed: bool = False
    ) -> Dict[str, Any]:
        """Read a specific variable from the namespace."""
        obj = self.namespace.get(name)
        if obj is None:
            return {"status": "error", "error": f"Variable '{name}' not found"}
        type_name = type(obj).__name__
        try:
            repr_str = repr(obj)
            if len(repr_str) > 2000:
                repr_str = repr_str[:2000] + "..."
        except Exception:
            repr_str = f"<{type_name}>"
        result = {"status": "success", "name": name, "type": type_name, "repr": repr_str}
        if detailed:
            result["attributes"] = [a for a in dir(obj) if not a.startswith("_")][:50]
        return result

    async def cleanup(self):
        logger.debug("QiskitToolsFacade cleanup")
