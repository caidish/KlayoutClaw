"""FastMCP server for Jupyter Qiskit integration.

Provides namespace query tools + notebook cell manipulation (via instrMCP).
The agent writes its own Python code in notebook cells — no hidden backends.
"""

import asyncio
import logging
import sys
import threading
from typing import Optional

from fastmcp import FastMCP

from .tools import QiskitToolsFacade
from .core import QiskitCoreToolRegistrar, ResourceRegistrar

logger = logging.getLogger(__name__)

# Active cell bridge (from instrMCP) — enables JupyterLab cell manipulation
try:
    from instrmcp.servers.jupyter_qcodes import active_cell_bridge
    from instrmcp.servers.jupyter_qcodes.active_cell_bridge import register_comm_target
    from .backend.notebook_unsafe import NotebookUnsafeBackend
    from .core.notebook_unsafe_tools import NotebookUnsafeToolRegistrar

    BRIDGE_AVAILABLE = True
except ImportError:
    active_cell_bridge = None
    register_comm_target = None
    NotebookUnsafeBackend = None
    NotebookUnsafeToolRegistrar = None
    BRIDGE_AVAILABLE = False


class JupyterQiskitMCPServer:
    """MCP server for Jupyter Qiskit integration.

    Provides:
    - Namespace query tools (list circuits, variables, backends)
    - Notebook cell manipulation (add, execute, read, edit, delete)
    - Resources with code examples for scqubits, qiskit-aer, etc.

    The agent writes its own Python code in notebook cells.
    All computation is visible in JupyterLab.
    """

    def __init__(
        self,
        ipython,
        host: str = "127.0.0.1",
        port: int = 8124,
    ):
        self.ipython = ipython
        self.host = host
        self.port = port
        self.running = False

        self._server_thread: Optional[threading.Thread] = None
        self._server_loop: Optional[asyncio.AbstractEventLoop] = None
        self._uvicorn_server = None
        self._ready_event = threading.Event()
        self._thread_error: Optional[Exception] = None
        self._server_started = False

        # Namespace query facade
        self.tools = QiskitToolsFacade(ipython)

        # Register active cell bridge comm target (from instrMCP)
        if BRIDGE_AVAILABLE:
            try:
                register_comm_target()
                logger.debug("Active cell bridge comm target registered")
            except Exception as e:
                logger.warning(f"Could not register active cell bridge: {e}")

        # Create FastMCP server
        self.mcp = FastMCP("Jupyter Qiskit MCP Server")
        self._register_resources()
        self._register_tools()

        logger.debug(f"JupyterQiskitMCPServer initialized on {host}:{port}")

    def _register_resources(self):
        resource_registrar = ResourceRegistrar(self.mcp)
        resource_registrar.register_all()

    def _register_tools(self):
        # Namespace query tools (always available)
        core_registrar = QiskitCoreToolRegistrar(self.mcp, self.tools)
        core_registrar.register_all()
        logger.debug("Query tools registered (list_circuits, circuit_info, list_backends, list_variables, read_variable)")

        # Notebook cell manipulation tools (requires instrMCP bridge)
        if BRIDGE_AVAILABLE:
            unsafe_backend = NotebookUnsafeBackend(
                self.tools._state, active_cell_bridge
            )
            unsafe_registrar = NotebookUnsafeToolRegistrar(self.mcp, unsafe_backend)
            unsafe_registrar.register_all()
            logger.debug("Cell tools registered (add_cell, execute, read, delete, patch, move)")
        else:
            logger.warning(
                "Cell manipulation unavailable — install instrMCP for "
                "notebook_add_cell, notebook_execute_active_cell, etc."
            )

    # -- Server lifecycle (unchanged) --------------------------------------

    def _cancel_all_tasks(self, loop: asyncio.AbstractEventLoop) -> None:
        try:
            pending = asyncio.all_tasks(loop)
            if not pending:
                return
            for task in pending:
                task.cancel()
            loop.run_until_complete(asyncio.wait(pending, timeout=2.0))
        except Exception as e:
            logger.debug(f"Error during task cancellation: {e}")

    def _run_server_in_thread(self):
        if sys.platform == "win32":
            policy = asyncio.WindowsSelectorEventLoopPolicy()
            self._server_loop = policy.new_event_loop()
        else:
            self._server_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._server_loop)
        try:
            self._server_loop.run_until_complete(self._async_serve())
        except Exception as e:
            self._thread_error = e
            self._ready_event.set()
            logger.error(f"Server thread error: {e}")
        finally:
            self._server_started = False
            if self._server_loop and not self._server_loop.is_closed():
                self._cancel_all_tasks(self._server_loop)
                try:
                    self._server_loop.close()
                except Exception:
                    pass
            self._server_loop = None

    async def _async_serve(self):
        import uvicorn
        app = self.mcp.http_app()
        config = uvicorn.Config(app, host=self.host, port=self.port, log_level="info", access_log=True)
        self._uvicorn_server = uvicorn.Server(config)
        self._uvicorn_server.install_signal_handlers = lambda: None

        async def signal_ready():
            while not self._uvicorn_server.started:
                if self._uvicorn_server.should_exit:
                    return
                await asyncio.sleep(0.05)
            self._server_started = True
            self._ready_event.set()

        ready_task = asyncio.create_task(signal_ready())
        try:
            await self._uvicorn_server.serve()
        finally:
            self._server_started = False
            ready_task.cancel()
            try:
                await ready_task
            except asyncio.CancelledError:
                pass

    def start_sync(self):
        if self._server_thread and self._server_thread.is_alive():
            return
        self._ready_event.clear()
        self._thread_error = None
        self._uvicorn_server = None
        self._server_started = False
        self._server_thread = threading.Thread(
            target=self._run_server_in_thread, daemon=True, name="Qiskit-MCP-Server"
        )
        self._server_thread.start()
        ready = self._ready_event.wait(timeout=5.0)
        if self._thread_error:
            self._abort_orphaned_thread()
            raise RuntimeError(f"Server startup failed: {self._thread_error}")
        if not ready:
            self._abort_orphaned_thread()
            raise RuntimeError("Server startup timed out")
        self.running = True

    def _abort_orphaned_thread(self):
        if self._uvicorn_server:
            self._uvicorn_server.should_exit = True
        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(timeout=1.0)
        self._server_thread = None
        self._uvicorn_server = None
        self._server_started = False
        self.running = False

    def stop_sync(self) -> bool:
        if not self._server_thread or not self._server_thread.is_alive():
            self._server_thread = None
            self._uvicorn_server = None
            self._server_started = False
            self.running = False
            return True
        if self._uvicorn_server:
            self._uvicorn_server.should_exit = True
        self._server_thread.join(timeout=0.5)
        if self._server_thread.is_alive():
            if self._uvicorn_server:
                self._uvicorn_server.force_exit = True
            if self._server_loop:
                try:
                    self._server_loop.call_soon_threadsafe(self._server_loop.stop)
                except RuntimeError:
                    pass
        self._server_thread.join(timeout=1.5)
        self._server_thread = None
        self._uvicorn_server = None
        self._server_started = False
        self.running = False
        return True

    def is_running(self) -> bool:
        thread_alive = self._server_thread is not None and self._server_thread.is_alive()
        return thread_alive and self._server_started
