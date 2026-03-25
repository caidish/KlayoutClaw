"""IPython extension for the Jupyter Qiskit MCP server.

Load with: %load_ext qiskit_mcp.jupyter_mcp_extension

The agent writes Python code in notebook cells. This server provides
namespace queries + cell manipulation tools. All computation is visible.
"""

import logging
from typing import Optional

from IPython.core.magic import Magics, line_magic, magics_class

logger = logging.getLogger(__name__)

_server = None
_server_host: str = "127.0.0.1"
_server_port: int = 8124


def _do_start_server(announce: bool = True) -> None:
    global _server

    if _server and _server.is_running():
        if announce:
            print(f"Qiskit MCP server already running on http://{_server.host}:{_server.port}")
        return

    if announce:
        print("Starting Qiskit MCP server...")

    try:
        from IPython.core.getipython import get_ipython
        ipython = get_ipython()
        if not ipython:
            if announce:
                print("ERROR: Could not get IPython instance")
            return

        from .mcp_server import JupyterQiskitMCPServer
        _server = JupyterQiskitMCPServer(ipython, host=_server_host, port=_server_port)
        _server.start_sync()

        if announce:
            print(f"Qiskit MCP server started on http://{_server.host}:{_server.port}")
            from .mcp_server import BRIDGE_AVAILABLE
            features = ["query (5 tools)"]
            if BRIDGE_AVAILABLE:
                features.append("cell manipulation (7 tools)")
            print(f"  Available: {', '.join(features)}")
            print("  All computation runs in visible notebook cells")
            if not BRIDGE_AVAILABLE:
                print("  WARNING: Install instrMCP for cell manipulation tools")

    except Exception as e:
        if announce:
            print(f"ERROR: Failed to start server: {e}")
        logger.error(f"Failed to start Qiskit MCP server: {e}")
        raise


def _do_stop_server(announce: bool = True) -> bool:
    global _server
    if not _server:
        if announce:
            print("Qiskit MCP server not initialized")
        return True
    if not _server.is_running():
        if announce:
            print("Qiskit MCP server already stopped")
        _server = None
        return True
    if announce:
        print("Stopping Qiskit MCP server...")
    try:
        success = _server.stop_sync()
        if success:
            _server = None
            if announce:
                print("Qiskit MCP server stopped")
            return True
        else:
            if announce:
                print("WARNING: Server stop timed out")
            return False
    except Exception as e:
        if announce:
            print(f"ERROR: Failed to stop server: {e}")
        raise


@magics_class
class QiskitMCPMagics(Magics):
    """Magic commands for Qiskit MCP server control."""

    @line_magic
    def qiskit_mcp_start(self, line):
        """Start the Qiskit MCP server."""
        parts = line.strip().split()
        global _server_port
        for i, part in enumerate(parts):
            if part == "--port" and i + 1 < len(parts):
                try:
                    _server_port = int(parts[i + 1])
                except ValueError:
                    print(f"Invalid port: {parts[i + 1]}")
                    return
        _do_start_server(announce=True)

    @line_magic
    def qiskit_mcp_stop(self, line):
        """Stop the Qiskit MCP server."""
        _do_stop_server(announce=True)

    @line_magic
    def qiskit_mcp_restart(self, line):
        """Restart the Qiskit MCP server."""
        _do_stop_server(announce=False)
        _do_start_server(announce=True)

    @line_magic
    def qiskit_mcp_status(self, line):
        """Show Qiskit MCP server status."""
        if _server and _server.is_running():
            print(f"Qiskit MCP server: RUNNING on http://{_server.host}:{_server.port}")
        elif _server:
            print("Qiskit MCP server: INITIALIZED but not running")
        else:
            print("Qiskit MCP server: NOT STARTED")


def load_ipython_extension(ipython):
    try:
        ipython.register_magics(QiskitMCPMagics)
        print("Qiskit MCP extension loaded.")
        print("  %qiskit_mcp_start   Start server (port 8124)")
        print("  %qiskit_mcp_status  Check server status")
        print("  Agent writes code in notebook cells. All computation is visible.")
    except Exception as e:
        logger.error(f"Failed to load Qiskit MCP extension: {e}")
        raise


def unload_ipython_extension(ipython):
    global _server
    try:
        if _server and _server.is_running():
            _server.stop_sync()
            _server = None
    except Exception as e:
        logger.error(f"Error during unload: {e}")
