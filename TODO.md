# KlayoutClaw TODO

## Backlog: Autorouter Improvements
- [ ] Fix route overlapping — routes cross/overlap when many pins fan out from a small cluster
- [ ] Improve path-to-path collision avoidance in dense routing scenarios (48-pin fan-out)
- [ ] Consider sequential routing with progressive obstacle updates (route one, add to obstacles, route next)

## v0.6.1: Nanodevice Routing Skill
- [x] Create `skills/nanodevice/routing/` skill — multi-window EBL routing
- [x] `place_pads.py` — bonding pads around field perimeter with pin markers
- [x] `route_multiwindow.py` — two-pass routing (inner fine + outer coarse + boundary patches)
- [x] `clear_routes.py` — clean up routing layers
- [x] Fix `mcp_client.py` timeout for long-running tool calls (auto_route)
- [x] Test all 3 scripts against live KLayout (48/48 inner + 48/48 outer routed)
- [x] Update docs (CLAUDE.md, TODO.md)

## v0.6: Screenshot Tool
- [x] Add `screenshot` MCP tool — captures viewport as PNG via `pya.LayoutView.save_image()`
- [x] Register tool in TOOLS list and _TOOL_DISPATCH
- [x] Update docs (tools.md, CLAUDE.md, TODO.md)
- [ ] Verify: `python install.py` + restart KLayout
- [ ] Verify: screenshot tool returns correct PNG

## v0.5.3: Image Skill
- [x] Create `skills/image/` skill — load reference images as background overlays
- [x] `add_image.py` — load image with pixel-size, position, center options
- [x] `list_images.py` — list all background images in view
- [x] `remove_image.py` — remove image by ID or all
- [x] Test all 3 scripts against live KLayout instance
- [x] Update docs (skills.md, CLAUDE.md, TODO.md)

## v0.5.2: Skills Integration + Plugin Marketplace
- [x] Move skills from external `my-agent-prompt` repo into `skills/` directory
- [x] Fix import paths in all 8 scripts (mcp_client.py resolution)
- [x] Fix `capture.py` to use relative path for `gds_to_image.py`
- [x] Add `.claude-plugin/plugin.json` manifest
- [x] Add `.claude-plugin/marketplace.json` catalog
- [x] Validate plugin structure (`claude plugin validate .` — PASS)
- [x] Update docs (skills.md, CLAUDE.md, README.md, TODO.md)

## v0.5.1: Error Handling &amp; Parallel Call Guard
- [x] Return tool errors as MCP results with `isError: true` (not JSON-RPC errors)
- [x] Add busy guard to reject parallel tool calls with clear retry message
- [x] Improve `execute_script` errors: show exception type, message, and user code line numbers
- [x] Improve `auto_route` errors: timeout hint, last error line extraction, actionable messages
- [x] Improve `tool not found` error: list available tools

## v0.5: Autorouter
- [x] Install routing deps (scikit-image, klayout standalone) in conda env
- [x] Create `tools/route_worker.py` — subprocess routing engine (numpy/scipy/scikit-image)
- [x] Create `tests/create_hallbar_unrouted.py` — Hall bar with pin markers, no traces
- [x] Add `auto_route` MCP tool to server plugin (subprocess-based)
- [x] Create `tests/evaluate_routing.py` — structural validation of routed GDS
- [x] Create `tests/test_autoroute.sh` — E2E autoroute test script
- [x] Fix field name mismatches between MCP handler and route_worker.py
- [x] Update docs (tools.md, CLAUDE.md, TODO.md)
- [x] Verify: `python install.py` + restart KLayout
- [x] Verify: `bash tests/test_autoroute.sh` — PASS (8/8 pairs routed, structural eval PASS)
- [x] Verify: visual check of routed PNG screenshot — routes connect all probes to 300x300um pads

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
