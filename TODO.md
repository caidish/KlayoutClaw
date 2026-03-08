# KlayoutClaw TODO

## v0.4: UI Plugin
- [x] Add UI callback slots to server macro (sys.modules shared state)
- [x] Create klayoutclaw_ui.lym — dock panel + status bar indicator
- [x] Update install.py to copy both .lym files
- [x] Fix pya property access (statusBar, verticalScrollBar — no parens in pya)
- [x] Add try/except safety wrappers for all UI callbacks
- [x] Verify: dock panel shows command history with ✔/✖ indicators
- [x] Verify: status bar shows "MCP: Running ● :8765" in green
- [x] Verify: error entries shown in red, no crashes

## v0.3: Slim MCP Server
- [x] Remove 7 redundant tools (create_cell, add_cell_instance, add_rectangle, add_polygon, add_path, list_cells, list_layers)
- [x] Add `execute_script` tool (arbitrary Python/pya code execution)
- [x] Update plugin version to 0.3
- [x] Update docs/tools.md
- [x] Update CLAUDE.md
- [x] Update tests/test_connection.py (expect 4 tools)
- [x] Rewrite tests/create_hallbar.py (use execute_script)
- [x] Verify: `python install.py` + `open /Applications/klayout.app`
- [x] Verify: `python tests/test_connection.py` — 4 tools listed
- [x] Verify: `python tests/create_hallbar.py && python tests/evaluate_gds.py test_hallbar.gds` — PASS

## v0.2 (completed)
- [x] Build KLayout plugin (`plugin/klayoutclaw_server.lym`) — v0.2 QTcpServer
- [x] Build plugin installer, MCP config, GDS-to-image tool
- [x] TEST 1: MCP connection — ALL PASS (10 tools)
- [x] TEST 2: Hall bar creation and evaluation — ALL PASS
