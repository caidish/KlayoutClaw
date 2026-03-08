# KlayoutClaw TODO

## v0.5: Autorouter
- [x] Install routing deps (scikit-image, klayout standalone) in conda env
- [x] Create `tools/route_worker.py` — subprocess routing engine (numpy/scipy/scikit-image)
- [x] Create `tests/create_hallbar_unrouted.py` — Hall bar with pin markers, no traces
- [x] Add `auto_route` MCP tool to server plugin (subprocess-based)
- [x] Create `tests/evaluate_routing.py` — structural validation of routed GDS
- [x] Create `tests/test_autoroute.sh` — E2E autoroute test script
- [x] Fix field name mismatches between MCP handler and route_worker.py
- [x] Update docs (tools.md, CLAUDE.md, TODO.md)
- [ ] Verify: `python install.py` + restart KLayout
- [ ] Verify: `bash tests/test_autoroute.sh` — PASS
- [ ] Verify: visual check of routed PNG screenshot

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
