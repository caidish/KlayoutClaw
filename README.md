# KlayoutClaw

MCP server plugin for KLayout — lets AI tools (Claude, Codex, etc.) control KLayout's layout engine via the [Model Context Protocol](https://modelcontextprotocol.io/).

> **macOS only** for now. Linux/Windows support is planned but untested.

## How It Works

KlayoutClaw runs inside KLayout as an autorun macro. It starts a JSON-RPC 2.0 server on `127.0.0.1:8765` that speaks MCP over HTTP. AI tools connect to this endpoint and can create layouts, run arbitrary pya scripts, and save GDS/OASIS files — all executed on KLayout's main Qt thread with zero external dependencies.

```
┌─────────────┐       HTTP/JSON-RPC        ┌─────────────────┐
│  Claude /    │  ◄──────────────────────►  │  KLayout GUI    │
│  Codex /     │    127.0.0.1:8765/mcp      │  + KlayoutClaw  │
│  Any MCP     │                            │    plugin        │
│  client      │                            │                  │
└─────────────┘                            └─────────────────┘
```

## Quick Start

```bash
# 1. Clone
git clone https://github.com/caidish/KlayoutClaw.git
cd KlayoutClaw

# 2. Install plugin into KLayout
python install.py

# 3. Launch KLayout
open /Applications/klayout.app

# 4. Test the connection
python tests/test_connection.py
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `create_layout` | Create a new layout with a top cell |
| `execute_script` | Run arbitrary Python/pya code in KLayout |
| `save_layout` | Save layout as GDS2 or OASIS |
| `get_layout_info` | Get layout summary (cells, layers, dbu) |

`execute_script` is the power tool — it runs any Python code inside KLayout with access to `pya`, the current layout, and view. The other three handle lifecycle. See [docs/tools.md](docs/tools.md) for full parameter schemas.

### Example: Create a rectangle via MCP

```python
import json, urllib.request

def mcp(method, params=None, req_id=1):
    payload = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params: payload["params"] = params
    req = urllib.request.Request("http://127.0.0.1:8765/mcp",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req).read())

# Initialize + create layout
mcp("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
    "clientInfo": {"name": "example", "version": "0.1"}})
mcp("tools/call", {"name": "create_layout", "arguments": {"name": "TOP"}}, 2)

# Draw a 100x50um rectangle on layer 1/0
mcp("tools/call", {"name": "execute_script", "arguments": {"code": """
dbu = _layout.dbu
li = _layout.layer(1, 0)
_top_cell.shapes(li).insert(pya.Box(int(-50/dbu), int(-25/dbu), int(50/dbu), int(25/dbu)))
result = {"status": "ok", "shape": "rectangle"}
"""}}, 3)

# Save
mcp("tools/call", {"name": "save_layout",
    "arguments": {"filepath": "/tmp/example.gds"}}, 4)
```

## Using with Claude Code

```bash
# Add KlayoutClaw as an MCP server
claude mcp add klayoutclaw --type http --url http://127.0.0.1:8765/mcp

# Or use the config file
claude --mcp-config mcp_config.json
```

Then just ask Claude to create layouts:

> "Create a Hall bar device with a 100x25um graphene channel, 6 side probes, metal contacts, and bonding pads. Save it as hallbar.gds."

## UI Plugin

The UI plugin (`klayoutclaw_ui.lym`) adds a status indicator and command history panel to KLayout — no source modifications needed.

- **Status bar**: Shows `MCP: Running ● :8765` in green when active
- **Dock panel**: Scrollable command history with timestamps and pass/fail indicators

See [docs/ui-plugin.md](docs/ui-plugin.md) for details.

## Project Structure

```
KlayoutClaw/
├── plugin/
│   ├── klayoutclaw_server.lym    # MCP server (v0.3)
│   └── klayoutclaw_ui.lym        # UI panel + status bar
├── tools/
│   └── gds_to_image.py           # GDS → PNG converter
├── tests/
│   ├── test_connection.py        # Protocol-level MCP test
│   ├── test_connection.sh        # E2E connection test
│   ├── create_hallbar.py         # Hall bar creation test
│   ├── evaluate_gds.py           # Structural evaluation
│   └── test_hallbar.sh           # E2E Hall bar test
├── docs/
│   ├── tools.md                  # MCP tool reference
│   ├── skills.md                 # Skills CLI reference
│   ├── ui-plugin.md              # UI plugin docs
│   └── plans/                    # Architecture design docs
├── install.py                    # Plugin installer
└── mcp_config.json               # Claude Code MCP config
```

## Architecture

- **`pya.QTcpServer`** on Qt main thread — no Python threads, no GIL issues
- **No external dependencies** — only Python stdlib + pya
- **JSON-RPC 2.0** over HTTP (plain JSON, no SSE)
- All pya calls execute on the main thread directly

See [docs/plans/](docs/plans/) for design decisions and the threading problem that led to this architecture.

## Tests

```bash
# Protocol-level connection test (requires KLayout running)
python tests/test_connection.py

# Create a Hall bar and verify structure
python tests/create_hallbar.py /tmp/hallbar.gds
python tests/evaluate_gds.py /tmp/hallbar.gds

# Full E2E (installs plugin, launches KLayout, tests connection)
bash tests/test_connection.sh
```

## License

MIT
