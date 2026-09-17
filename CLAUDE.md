# KlayoutClaw

MCP server plugin for KLayout GUI — enables AI tools to control KLayout via MCP protocol over HTTP on `127.0.0.1:8765`. macOS only for now.

## Directory Structure

```
KlayoutClaw/
├── plugin/
│   ├── klayoutclaw_server.lym    # KLayout autorun macro (MCP server, v0.6)
│   └── klayoutclaw_ui.lym        # KLayout autorun macro (UI panel + status bar)
├── tools/
│   ├── gds_to_image.py           # GDS → PNG converter (gdstk + matplotlib)
│   ├── route_worker.py           # Subprocess ordered-loop routing engine (numpy/scikit-image/klayout)
│   └── evaluate_worker.py        # Configurable device design evaluation (gdstk)
├── skills/
│   ├── scripts/
│   │   └── mcp_client.py            # Shared MCP client for all skills
│   ├── geometry/
│   │   ├── SKILL.md
│   │   └── scripts/
│   │       ├── add_rect.py
│   │       ├── add_polygon.py
│   │       ├── add_path.py
│   │       ├── create_cell.py
│   │       └── add_instance.py
│   ├── display/
│   │   ├── SKILL.md
│   │   └── scripts/
│   │       ├── toggle_layer.py
│   │       └── show_only.py
│   ├── image/
│   │   ├── SKILL.md
│   │   └── scripts/
│   │       ├── add_image.py
│   │       ├── list_images.py
│   │       └── remove_image.py
│   ├── visual/
│   │   ├── SKILL.md
│   │   └── scripts/
│   │       └── capture.py
│   ├── nanodevice_flakedetect/
│   │   ├── SKILL.md              # Orchestrator (dispatches subagents)
│   │   └── scripts/
│   │       └── core.py           # Shared CV utilities (morph, contour, Chamfer)
│   ├── nanodevice_flakedetect_align/
│   │   ├── SKILL.md              # SIFT + Chamfer cross-substrate alignment
│   │   └── scripts/              # sift_align, source_contour, footprint, sweep, refine
│   ├── nanodevice_flakedetect_detect/
│   │   ├── SKILL.md              # Per-material segmentation (4 scripts)
│   │   └── scripts/              # graphite, graphene, bottom_hbn, top_hbn
│   ├── nanodevice_flakedetect_combine/
│   │   ├── SKILL.md              # Coordinate transforms + overlay
│   │   └── scripts/              # ecc_register, transform, overlay
│   ├── nanodevice_flakedetect_commit/
│   │   └── SKILL.md              # Insert polygons into KLayout
│   ├── nanodevice_flakedetect_review/
│   │   └── SKILL.md              # Visual validation protocol
│   ├── nanodevice_gdsalign/
│   │   ├── SKILL.md              # GDS template alignment orchestrator
│   │   └── scripts/
│   │       ├── extract_markers.py # Parse GDS L5/0 marker pairs
│   │       ├── detect_markers.py  # Template-match markers in image
│   │       ├── align_gds.py       # Compute similarity transform
│   │       └── commit_gds.py      # Warp image + contours, commit to KLayout
│   ├── nanodevice_e2e_design/
│   │   └── SKILL.md              # E2E device design methodology (device-agnostic)
│   ├── nanodevice_routing/
│   │   ├── SKILL.md
│   │   └── scripts/
│   │       ├── place_pads.py
│   │       ├── route_multiwindow.py
│   │       └── clear_routes.py
│   └── e2e_judge/
│       ├── SKILL.md              # Agentic E2E test harness with LLM judge
│       └── scripts/              # conftest, harness, judge, verifier, run_tests
├── agent/                          # qlaybot v0.4.4 — Pi-Agent SDK wrapper
│   ├── src/                        # TypeScript source (see agent/CLAUDE.md)
│   ├── tests/                      # 697 tests: unit / integration / e2e
│   ├── workspace/                  # Domain knowledge (SOUL, TOOLS, RULES)
│   ├── package.json
│   └── CLAUDE.md                   # Agent dev instructions
├── tests/
│   ├── test_connection.py        # Protocol-level MCP connection test
│   ├── test_connection.sh        # E2E connection test (install + launch + verify)
│   ├── test_phase0_func.py       # Phase 0: connection + geometry functional tests
│   ├── test_phase0_mcp.py        # Phase 0: MCP protocol tests
│   ├── test_phase1_mcp.py        # Phase 1: LYM server MCP tests
│   ├── test_phase1_worker.py     # Phase 1: route/evaluate worker tests
│   ├── test_phase2_phase3_func.py # Phase 2-3: skills + flakedetect functional tests
│   ├── test_phase4_docs_integration.py # Phase 4: docs integration tests
│   ├── test_phase4_mcp.py        # Phase 4: GDS alignment + routing MCP tests
│   ├── test_phase4_skills_func.ts # Phase 4: skills functional tests (TS)
│   ├── create_hallbar.py         # Hall bar creation via execute_script
│   ├── create_hallbar_unrouted.py # Hall bar with pin markers, no traces
│   ├── evaluate_gds.py           # Hall bar structural evaluation (gdstk)
│   ├── evaluate_routing.py       # Routing structural validation (gdstk)
│   ├── test_gdsalign.py          # GDS alignment pipeline tests (12 tests)
│   ├── test_hallbar.sh           # E2E Hall bar test (Claude + tmux)
│   └── test_autoroute.sh         # E2E autoroute test
├── docs/
│   ├── tools.md                  # MCP tool reference (10 tools)
│   ├── skills.md                 # Skills CLI reference (geometry, display, image, visual, nanodevice_flakedetect, nanodevice_gdsalign, nanodevice_routing, nanodevice_e2e_design)
│   ├── ui-plugin.md              # UI plugin architecture + pya Qt pitfalls
│   ├── plans/                    # Architecture design docs
│   │   ├── 2026-03-08-qtcpserver-mcp-design.md
│   │   ├── 2026-03-08-ui-plugin-design.md
│   │   ├── 2026-03-08-ui-plugin-impl.md
│   │   ├── 2026-03-08-autorouter-design.md
│   │   └── 2026-03-08-autorouter-impl.md
│   ├── 2026-04-17-benchmark-review-update.md  # 2026-04-14/15 benchmark-review fix report
│   └── superpowers/plans/2026-04-17-benchmark-review-fixes.md  # Fix plan (TRD)
├── .claude-plugin/
│   ├── plugin.json               # Claude Code plugin manifest
│   └── marketplace.json          # Claude Code marketplace catalog
├── install.py                    # Copies plugins to ~/.klayout/pymacros/
├── mcp_config.json               # MCP client config for Claude Code
└── TODO.md                       # Task tracking
```

## MCP Tools (19 total)

| Tool | Description |
|------|-------------|
| `create_layout` | Create new layout + top cell |
| `execute_script` | Run arbitrary Python/pya code in KLayout |
| `save_layout` | Save layout as GDS2 or OASIS |
| `get_layout_info` | Layout summary info |
| `screenshot` | Capture viewport as PNG (what the user sees) |
| `auto_route` | Autoroute pin pairs with ordered-loop assignment + sequential pathfinding (subprocess, needs conda env); supports dry_run preview, per_pair_obstacle_layers, auto_map_resolution |
| `route_inspect` | Per-route metadata (contact/pad assignment, length, crossings) on a given layer; requires `route_layer`, `contact_layers`, `pad_layer` (no defaults). `route_id` aligns with `evaluate_design.contact_isolation.crossing_pairs`. |
| `evaluate_design` | Nanodevice DRC + metric evaluation via configurable check primitives (subprocess); includes `bulk_containment` + `arm_material_class` + `material_overlap_report` + `next_step_suggestion` |
| `validate_pixel_size` | Validate pixel_size against known objectives |
| `close_layout_view` | Close one or more layout tabs to keep the server healthy (modes: current/others/all, or by index) |
| `vc_init` | Initialise version control for current layout (mode: auto/memory/disk). Wraps G5 `RepoHandle`. |
| `vc_checkpoint` | Create a checkpoint (git commit) of the current layout with message + optional tags. |
| `vc_history` | List checkpoint history `{commits:[{sha, message, ts, tags, branch, stats}, ...]}`. |
| `vc_checkout` | Restore layout + sidecar HEAD to a prior checkpoint (agent-first, no dirty dialog). |
| `vc_diff` | Structured polygon-level diff between two checkpoints. |
| `vc_branch` | Branch ops: list / create / switch / merge. Merge conflicts return `{ok:false, conflicts:[...]}` atomically. |
| `vc_tag` | Create an annotated tag at a ref. Duplicate names rejected. |
| `vc_export` | Export a checkpoint as GDS bytes (base64) or pya code. Payloads over 256 KiB truncated to tempfile with `path`. |
| `vc_status` | Report VC status `{branch, dirty, last_checkpoint_ts, pending, mode}`. Uninit returns `{ok:false, reason:"vc not initialized"}`. |

See `docs/tools.md` for full parameter schemas.

## Architecture
- `pya.QTcpServer` on Qt main thread — no Python threads, no GIL issues
- MCP server itself has no external dependencies — only stdlib + pya
- `auto_route` tool spawns a subprocess for heavy computation (numpy/scikit-image/klayout in conda env `instrMCPdev`)
- `evaluate_design` tool spawns a subprocess in the same `instrMCPdev` env (gdstk + shapely + numpy)
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
- MCP client config defaults to `http://127.0.0.1:8765/mcp`; active clients honor `KLAYOUT_MCP_URL`
- Test scripts use absolute paths for GDS output — KLayout's CWD is `/`, so relative paths fail
- `auto_route` subprocess needs `route_worker.py` — searched in `~/Documents/GitHub/KlayoutClaw/tools/` and `~/.klayout/pymacros/`
- **`mw.create_layout(mode)` mode argument** — empirically determined (pya docs are misleading):
  - `mode=0` — **replaces** the current view's layout in place (wipes cells, **keeps image annotations** on the view)
  - `mode=1` — **creates a new tab** with a fresh empty layout, auto-switches `current_view()` to it, leaves existing tabs untouched
  - `mode=2` — operates on the current view without creating a new tab
  - `_tool_create_layout` uses mode 1 so each MCP `create_layout` call adds a blank tab (non-destructive)
- **`Layout.read()` cell-name collisions** — reading a GDS into a layout that already contains cells with the same names (e.g. the default empty `TOP` from `mw.create_layout()` vs. a template whose top cell is also `TOP`) leaves **orphaned cell index slots**: `layout.cells()` reports N but `layout.cell(N-1)` raises "Not a valid cell index". Always iterate cells via `layout.each_cell()`, and when loading templates into a populated layout call `layout.clear()` first to avoid the collision.
- **Dangling `cv.cell` after `Layout.read()`** — when the collision above triggers, the `CellView.cell` pointer can be left referencing a cell that was destroyed. Explicitly rebind `cv.cell = <resolved_top_cell>` after any `layout.read()` into a non-empty layout.
- **`pya.Image` placement semantics** — `DCplxTrans(ps, rot, mirror, disp)` applied to a `pya.Image` factors the scale into `img.pixel_width`/`pixel_height` (so `trans.mag` reads back as 1.0 but `img.box()` is the real GDS bbox). With identity rotation, the image occupies `(disp.x, disp.y)` to `(disp.x + W*ps, disp.y + H*ps)` — i.e. it extends in **+X and +Y** from `disp`. PNG file row 0 is rendered at the **high-Y edge** (north) of the bbox, so to display a PNG whose row 0 represents GDS-south you must `cv2.flip(img, 0)` before placement.
- **Image annotations persist across layout changes** — `pya.Image` is stored on the `LayoutView`, not the `Layout`. Replacing a layout (`mode=0`) leaves old images attached; use `view.clear_images()` if you need to wipe them without closing the tab.
