"""QiskitCoreBackend — read-only namespace queries.

Inspects the Jupyter namespace for QuantumCircuit objects and available backends.
No computation — the agent writes code in notebook cells for that.
"""

import logging
from typing import Any, Dict, List, Optional

from .base import BaseBackend

logger = logging.getLogger(__name__)


class QiskitCoreBackend(BaseBackend):
    """Read-only inspection of Qiskit objects in the namespace."""

    async def list_circuits(
        self, type_filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """List all QuantumCircuit objects in the notebook namespace."""
        try:
            from qiskit import QuantumCircuit
        except ImportError:
            return {"status": "success", "circuits": [], "count": 0}

        circuits = []
        for name, obj in self.namespace.items():
            if name.startswith("_"):
                continue
            if isinstance(obj, QuantumCircuit):
                if type_filter and type_filter.lower() not in name.lower():
                    continue
                circuits.append({
                    "name": name,
                    "num_qubits": obj.num_qubits,
                    "num_clbits": obj.num_clbits,
                    "depth": obj.depth(),
                    "size": obj.size(),
                    "num_parameters": obj.num_parameters,
                })
        return {"status": "success", "circuits": circuits, "count": len(circuits)}

    async def get_circuit_info(
        self, name: str, detailed: bool = False
    ) -> Dict[str, Any]:
        """Get detailed information about a specific circuit."""
        try:
            from qiskit import QuantumCircuit
        except ImportError:
            return {"status": "error", "error": "qiskit is not installed"}

        obj = self.namespace.get(name)
        if obj is None:
            return {"status": "error", "error": f"Variable '{name}' not found in namespace"}
        if not isinstance(obj, QuantumCircuit):
            return {"status": "error", "error": f"'{name}' is {type(obj).__name__}, not QuantumCircuit"}

        info = {
            "status": "success",
            "name": name,
            "num_qubits": obj.num_qubits,
            "num_clbits": obj.num_clbits,
            "depth": obj.depth(),
            "size": obj.size(),
            "num_parameters": obj.num_parameters,
            "global_phase": str(obj.global_phase),
        }
        if obj.num_parameters > 0:
            info["parameters"] = [p.name for p in obj.parameters]
        if detailed:
            gate_counts = {}
            for instruction in obj.data:
                gate_name = instruction.operation.name
                gate_counts[gate_name] = gate_counts.get(gate_name, 0) + 1
            info["gate_counts"] = gate_counts
            try:
                info["diagram"] = str(obj.draw(output="text"))
            except Exception:
                info["diagram"] = "(diagram unavailable)"
        return info

    async def list_backends(self) -> Dict[str, Any]:
        """List available Aer simulator backends."""
        backends = []
        try:
            from qiskit_aer import AerSimulator
            backends.append({"name": "aer_simulator", "description": "General-purpose Aer simulator"})
        except ImportError:
            pass
        try:
            from qiskit_aer import StatevectorSimulator
            backends.append({"name": "statevector_simulator", "description": "Exact statevector simulator"})
        except ImportError:
            pass
        try:
            from qiskit_aer import QasmSimulator
            backends.append({"name": "qasm_simulator", "description": "QASM measurement simulator"})
        except ImportError:
            pass
        return {"status": "success", "backends": backends, "count": len(backends)}
