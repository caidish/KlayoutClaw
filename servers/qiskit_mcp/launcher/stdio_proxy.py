"""STDIO proxy for the Qiskit MCP server.

Creates a FastMCP proxy that forwards STDIO requests to the HTTP backend.
"""

import logging

logger = logging.getLogger(__name__)


def create_stdio_proxy_server(
    base_url: str = "http://127.0.0.1:8124",
    server_name: str = "Qiskit MCP Proxy",
):
    """Create an MCP proxy server that forwards requests to the HTTP backend.

    Uses FastMCP's built-in proxy pattern which automatically mirrors
    all tools and resources from the HTTP server.
    """
    from fastmcp import FastMCP
    from fastmcp.server.proxy import ProxyClient

    mcp_endpoint = f"{base_url.rstrip('/')}/mcp"
    proxy = FastMCP.as_proxy(ProxyClient(mcp_endpoint), name=server_name)
    logger.info(f"Created FastMCP proxy to {mcp_endpoint}")
    return proxy


async def check_http_server(host: str = "127.0.0.1", port: int = 8124) -> bool:
    """Check if the Qiskit MCP HTTP server is running."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as client:
            endpoint = f"http://{host}:{port}/mcp"
            resp = await client.post(
                endpoint,
                json={
                    "jsonrpc": "2.0",
                    "id": "check",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "Proxy Check", "version": "1.0"},
                    },
                },
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            )
            return resp.status_code == 200
    except Exception:
        return False
