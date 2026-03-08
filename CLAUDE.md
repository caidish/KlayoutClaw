# KlayoutClaw

MCP server plugin for KLayout GUI — enables AI tools to control KLayout via MCP protocol over HTTP on `127.0.0.1:8765`. macOS only for now.

## Directory Structure

```
KlayoutClaw/
├── plugin/
│   ├── klayoutclaw_server.lym    # KLayout autorun macro (MCP server, v0.5)
│   └── klayoutclaw_ui.lym        # KLayout autorun macro (UI panel + status bar)
├── tools/
│   ├── gds_to_image.py           # GDS → PNG converter (gdstk + matplotlib)
│   └── route_worker.py           # Subprocess routing engine (numpy/scipy/scikit-image)
├── tests/
│   ├── test_connection.py        # Protocol-level MCP connection test
│   ├── test_connection.sh        # E2E connection test (install + launch + verify)
│   ├── create_hallbar.py         # Hall bar creation via execute_script
│   ├── create_hallbar_unrouted.py # Hall bar with pin markers, no traces
│   ├── evaluate_gds.py           # Hall bar structural evaluation (gdstk)
│   ├── evaluate_routing.py       # Routing structural validation (gdstk)
│   ├── test_hallbar.sh           # E2E Hall bar test (Claude + tmux)
│   └── test_autoroute.sh         # E2E autoroute test
├── docs/
│   ├── tools.md                  # MCP tool reference (5 tools)
│   ├── skills.md                 # Skills CLI reference (geometry, display, visual)
│   ├── ui-plugin.md              # UI plugin architecture + pya Qt pitfalls
│   └── plans/                    # Architecture design docs
│       ├── 2026-03-08-qtcpserver-mcp-design.md
│       ├── 2026-03-08-ui-plugin-design.md
│       ├── 2026-03-08-ui-plugin-impl.md
│       ├── 2026-03-08-autorouter-design.md
│       └── 2026-03-08-autorouter-impl.md
├── install.py                    # Copies plugins to ~/.klayout/pymacros/
├── mcp_config.json               # MCP client config for Claude Code
└── TODO.md                       # Task tracking
```

## MCP Tools (5 total)

| Tool | Description |
|------|-------------|
| `create_layout` | Create new layout + top cell |
| `execute_script` | Run arbitrary Python/pya code in KLayout |
| `save_layout` | Save layout as GDS2 or OASIS |
| `get_layout_info` | Layout summary info |
| `auto_route` | Autoroute pin pairs (subprocess, needs conda env) |

See `docs/tools.md` for full parameter schemas.

## Architecture
- `pya.QTcpServer` on Qt main thread — no Python threads, no GIL issues
- MCP server itself has no external dependencies — only stdlib + pya
- `auto_route` tool spawns a subprocess for heavy computation (numpy/scipy/scikit-image in conda env `instrMCPdev`)
- JSON-RPC 2.0 over HTTP (plain JSON, no SSE)
- All pya calls execute on the main thread directly
- See `docs/plans/` for design decisions and the GIL/threading problem

## Dev Notes
- `.lym` XML: escape `<` `>` `&` as `&lt;` `&gt;` `&amp;` in Python code
- Launch KLayout: `open /Applications/klayout.app` (standalone command, never chain with `&&`)
- After adding geometry via MCP, `_refresh_view()` updates GUI layer panel + zoom
- `cell.is_valid()` requires an Instance arg — use `cell is not None` instead
- **pya Qt property access**: use `mw.statusBar` NOT `mw.statusBar()` — pya exposes Qt getters as properties, calling them crashes with `'X_Native' object is not callable`
- Cross-macro shared state: use `sys.modules["_klayoutclaw"]` — pya module attributes set during autorun don't persist
- Install plugin: `python install.py` then restart KLayout
- MCP client config: `mcp_config.json` (type: http, url: `http://127.0.0.1:8765/mcp`)
- Test scripts use absolute paths for GDS output — KLayout's CWD is `/`, so relative paths fail
- `auto_route` subprocess needs `route_worker.py` — searched in `~/Documents/GitHub/KlayoutClaw/tools/` and `~/.klayout/pymacros/`
