"""Qiskit MCP Server — Jupyter-embedded MCP server for quantum device simulation."""

__version__ = "0.1.0"

from .mcp_server import JupyterQiskitMCPServer
from .jupyter_mcp_extension import load_ipython_extension, unload_ipython_extension

__all__ = [
    "JupyterQiskitMCPServer",
    "load_ipython_extension",
    "unload_ipython_extension",
]
