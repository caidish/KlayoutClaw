# KlayoutClaw: QTcpServer-based MCP Server Design

## Problem
KLayout GUI holds the Python GIL during autorun→GUI transition, making it impossible to run uvicorn/asyncio in background threads. All blocking calls (sleep, socket, imports) hang forever in threads during GUI mode.

## Solution
Replace threading+uvicorn+FastMCP with a `pya.QTcpServer` HTTP server running entirely on the Qt main thread. This avoids the GIL issue because no Python threads are needed — Qt's event loop handles networking, and pya calls happen directly on the main thread.

## Architecture

```
Claude Code / AI Client
    │
    │  HTTP POST /mcp  (JSON-RPC 2.0)
    ▼
┌─────────────────────────────────┐
│  pya.QTcpServer (port 8765)     │  ← Qt main thread
│  ┌───────────────────────────┐  │
│  │  HTTP Parser (minimal)    │  │
│  │  - Read headers + body    │  │
│  │  - Route POST /mcp       │  │
│  └──────────┬────────────────┘  │
│             │                   │
│  ┌──────────▼────────────────┐  │
│  │  MCP JSON-RPC Dispatcher  │  │
│  │  - initialize             │  │
│  │  - tools/list             │  │
│  │  - tools/call             │  │
│  └──────────┬────────────────┘  │
│             │                   │
│  ┌──────────▼────────────────┐  │
│  │  Tool Handlers (pya)      │  │  ← Direct pya calls, no dispatch needed
│  │  - create_layout          │  │
│  │  - add_rectangle          │  │
│  │  - save_layout, etc.      │  │
│  └───────────────────────────┘  │
└─────────────────────────────────┘
```

## MCP Protocol Surface (Streamable HTTP, minimal)

We implement only what's needed for tool-calling:

### Endpoints
- `POST /mcp` — JSON-RPC 2.0 requests
- `GET /mcp` — health check (returns 200)

### JSON-RPC Methods
1. **initialize** — returns server info + capabilities
2. **notifications/initialized** — client notification (no response needed)
3. **tools/list** — returns available tools
4. **tools/call** — executes a tool, returns result

### Session Management
- Generate UUID session ID on `initialize`
- Return in `Mcp-Session-Id` response header
- Validate on subsequent requests (optional for local-only server)

### Response Format
All responses are `Content-Type: application/json` (no SSE streaming needed for synchronous tool calls).

## Key Implementation Details

### HTTP Parsing
Based on KLayout's own `qt_server_python.lym` template:
- `QTcpServer.newConnection` → get socket via `nextPendingConnection()`
- Read lines until blank line (headers), then read `Content-Length` bytes (body)
- Use `connection.waitForReadyRead(100)` for buffering
- Clean up via `disconnected()` → `deleteLater()` signal/slot

### Tool Registration
Simple dict mapping tool names to handler functions:
```python
_tools = {
    "create_layout": {"handler": _create_layout, "params": {...}, "description": "..."},
    "add_rectangle": {"handler": _add_rectangle, "params": {...}, "description": "..."},
    ...
}
```

### No Threading Required
Since QTcpServer runs on the main thread, all tool handlers can call `pya` directly. The `run_on_main_thread` / `await_main_thread` dispatch mechanism is no longer needed.

### Server Lifecycle
- Created during autorun macro execution
- Stored as global variable (prevents garbage collection)
- Lives for the duration of the KLayout session
- Binds to `127.0.0.1:8765`

## What Changes from Current Code

| Component | Before | After |
|-----------|--------|-------|
| HTTP server | uvicorn in background thread | pya.QTcpServer on main thread |
| MCP framework | FastMCP | Hand-rolled JSON-RPC |
| Thread dispatch | QTimer + concurrent.futures | Not needed (already on main thread) |
| Dependencies | mcp, uvicorn, httpx | None (stdlib + pya only) |
| Transport | Streamable HTTP via FastMCP | Minimal HTTP/1.0 with JSON-RPC |

## Tools (unchanged)
- `create_layout(name, dbu)`
- `create_cell(name)`
- `add_cell_instance(parent, child, x, y)`
- `add_rectangle(cell, layer, datatype, x1, y1, x2, y2)`
- `add_polygon(cell, layer, datatype, points)`
- `add_path(cell, layer, datatype, points, width)`
- `save_layout(filepath, format)`
- `list_cells()`
- `list_layers()`
- `get_layout_info()`

## Design Decisions (from Codex review)

### HTTP Parsing: Fully Event-Driven
Use `readyRead` signal instead of `waitForReadyRead()`. Accumulate data in a per-connection buffer, parse only when complete headers + body are available. Enforce:
- `Connection: close` (no keep-alive)
- Reject chunked transfer encoding with HTTP 400
- Max body size: 1 MB
- Read timeout via QTimer (10s per request)

### Responses: Plain JSON (No SSE)
Claude Code tolerates non-streaming JSON responses. All responses use `Content-Type: application/json`. No SSE framing needed.

### JSON-RPC Error Handling
Return proper JSON-RPC 2.0 error objects:
```json
{"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "Invalid Request"}}
```
Map to HTTP status codes: 400 for parse/validation errors, 500 for tool exceptions, 404 for unknown methods.

### Long Operations: Offload to Headless Process
Heavy operations like `save_layout` on large GDS files would freeze the main thread. Offload these to `klayout -b -r script.py` as a subprocess. The main thread launches the subprocess, polls completion via QTimer, and responds when done.

### Singleton Startup Guard
Check `EADDRINUSE` before binding. Hold server as module-level global. Log clear error if port is already in use.

## Reference: qtmcp (signal-slot/qtmcp)
A C++20 Qt-native MCP implementation exists at https://github.com/signal-slot/qtmcp. It has `QMcpServer`, `QMcpServerSession`, and `QMcpAbstractHttpServer` classes — validating that MCP-over-Qt-networking is a proven, working pattern. However, it's C++ only (no Python bindings), so we can't use it directly in KLayout's Python scripting environment. Our implementation follows the same architectural pattern but in Python via `pya.QTcpServer`.

## Testing Strategy
Same as before:
1. **TEST 1**: `curl http://127.0.0.1:8765/mcp` with MCP initialize request
2. **TEST 2**: Claude creates a Hall bar via MCP tools, evaluated by Claude + Codex
