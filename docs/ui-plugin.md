# KlayoutClaw UI Plugin

The UI plugin (`klayoutclaw_ui.lym`) adds a status indicator and command history panel to KLayout's main window. It requires no modifications to KLayout's source code — it's a standard autorun macro.

## Components

### Status Bar Indicator (bottom right)

Shows the MCP server state:

| State | Display | Color |
|-------|---------|-------|
| Running | `MCP: Running ● :8765` | Green |
| Error | `MCP: Error ●` | Red (reverts to green after 5s) |
| Stopped | `MCP: Stopped ●` | Gray |

### Command History Dock Panel (bottom)

A dockable panel titled "KlayoutClaw" showing every MCP request:

```
Server started on port 8765
[14:32:05] initialize  ✔
[14:32:05] tools/list  ✔
[14:32:06] tools/call:execute_script  ✔
[14:32:07] tools/call:save_layout  ✖ Missing required parameter: filepath
```

- Successful requests shown in green with ✔
- Failed requests shown in red with ✖ and the error message
- Panel is dockable, resizable, and closable

## Installation

```bash
python install.py
```

This copies both `klayoutclaw_server.lym` and `klayoutclaw_ui.lym` to `~/.klayout/pymacros/`. Restart KLayout to activate.

## Architecture

### File Layout

| File | Purpose |
|------|---------|
| `plugin/klayoutclaw_server.lym` | MCP server + callback hooks |
| `plugin/klayoutclaw_ui.lym` | UI widgets + callback handlers |

### Cross-Macro Communication

The server and UI macros communicate via a shared module in `sys.modules["_klayoutclaw"]`:

```
Server macro (loads first, alphabetically "s" < "u")
  └── Creates sys.modules["_klayoutclaw"] with callback slots:
      ├── on_request = None
      ├── on_server_start = None
      ├── on_error = None
      └── server_port = None

UI macro (loads second)
  └── Registers handler functions into the callback slots
      and checks if server already started
```

The server calls callbacks at these points:
- After `initialize`, `tools/list` → `on_request(method, timestamp, True, None)`
- After successful `tools/call` → `on_request("tools/call:<name>", timestamp, True, None)`
- After failed `tools/call` → `on_request("tools/call:<name>", timestamp, False, error_msg)`
- After server starts → `on_server_start(port)`
- On any ERROR log message → `on_error(msg)`

### Why sys.modules?

Each `.lym` macro runs in its own `exec()` namespace — Python globals are not shared. The `pya` module object is shared, but attributes set on it during autorun don't persist (KLayout may reinitialize it). `sys.modules` is a process-wide dict that reliably persists across all execution contexts.

## pya Qt Pitfalls

These were discovered during development and apply to all KLayout Python macros:

| Pattern | Correct | Wrong (crashes KLayout) |
|---------|---------|------------------------|
| Status bar | `mw.statusBar` | `mw.statusBar()` |
| Scroll bar | `widget.verticalScrollBar` | `widget.verticalScrollBar()` |
| Maximum value | `scrollbar.maximum` | `scrollbar.maximum()` |

In pya, Qt getter methods are exposed as **properties** (no parentheses). Calling them as methods triggers `'X_Native' object is not callable` which crashes KLayout at the C++ level (SIGABRT, unrecoverable).

All UI callback handlers are wrapped in `try/except` to prevent widget errors from crashing the MCP server.

## Customization

The dock panel can be:
- **Moved** — drag the title bar to dock at top/bottom/left/right
- **Floated** — drag it out of the main window
- **Closed** — click the X button
- **Resized** — drag the border

If `addDockWidget` fails (e.g., on a KLayout build without full Qt bindings), the panel automatically falls back to a floating widget.
