#!/usr/bin/env python
"""Claude Desktop/Code STDIO launcher for the Qiskit MCP server.

Bridges Claude's STDIO transport to the HTTP backend on port 8124.
"""

import asyncio
import logging
import os
import sys

# Suppress noisy logs for clean STDIO
logging.getLogger().setLevel(logging.WARNING)
logging.getLogger("fastmcp").setLevel(logging.ERROR)
logging.getLogger("mcp").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def main():
    """Main launcher entry point."""
    from .stdio_proxy import create_stdio_proxy_server, check_http_server

    async def check_and_setup():
        running = await check_http_server()
        if not running:
            raise RuntimeError(
                "Qiskit MCP server is not running on port 8124.\n"
                "Start it in Jupyter with:\n"
                "  %load_ext servers.qiskit_mcp.jupyter_mcp_extension\n"
                "  %qiskit_mcp_start"
            )
        return create_stdio_proxy_server()

    mcp = asyncio.run(check_and_setup())
    mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        if os.getenv("DEBUG"):
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
