# KlayoutClaw UI Plugin Design

**Date**: 2026-03-08
**Status**: Approved

## Goal

Add a UI overlay to KLayout showing MCP server status, error indicators, and command history — without modifying KLayout's source code. Implemented as a separate `.lym` autorun macro.

## Architecture

Two `.lym` files, both autorun:

```
klayoutclaw_server.lym  (existing, modified slightly)
  └── Adds 3 global callback slots:
      _klayoutclaw_on_request(method, timestamp, success, error_msg)
      _klayoutclaw_on_server_start(port)
      _klayoutclaw_on_error(error_msg)

klayoutclaw_ui.lym  (new, ~120 lines)
  └── Creates:
      ├── QDockWidget "KlayoutClaw" (bottom dock area)
      │     └── QTextEdit (read-only, monospace, scrolling command log)
      ├── QLabel in status bar ("MCP: Running ●" / "MCP: Stopped ●")
      └── Registers callbacks into the server's global slots
```

## Communication: Global Callbacks

The server macro defines three global callback variables (initially `None`). The UI macro sets them to its handler functions. When the server processes requests, it calls whatever function is in the global — if `None`, the call is a no-op.

Server side (3 globals added):
```python
_klayoutclaw_on_request = None    # fn(method, timestamp, success, error_msg)
_klayoutclaw_on_server_start = None  # fn(port)
_klayoutclaw_on_error = None      # fn(error_msg)
```

Call sites:
- After `self.listen()` succeeds → `_klayoutclaw_on_server_start(8765)`
- After successful tool/method dispatch → `_klayoutclaw_on_request(method, ts, True, None)`
- After tool error → `_klayoutclaw_on_request(method, ts, False, str(error))`
- On any ERROR log → `_klayoutclaw_on_error(msg)`

## UI Components

### Status Bar Label

A `QLabel` added to `mw.statusBar()`:
- **Running**: green text `"MCP: Running ● :8765"`
- **Error**: red text `"MCP: Error ●"` (reverts after 5 seconds via QTimer)
- **Stopped**: gray text `"MCP: Stopped ●"` (initial state before server starts)

### Command History Dock Widget

A `QDockWidget` titled "KlayoutClaw" added to the bottom dock area via `mw.addDockWidget(Qt.BottomDockWidgetArea, dock)`.

Contains a read-only `QTextEdit` with monospace font showing:
```
[14:32:05] initialize                ✔
[14:32:05] tools/list                ✔
[14:32:06] execute_script            ✔
[14:32:07] save_layout               ✖ Missing required parameter: filepath
```

### Fallback

If `addDockWidget` fails at runtime (unlikely but possible), fall back to a floating `QDialog` with the same QTextEdit content.

## Load Order

KLayout loads pymacros alphabetically. `klayoutclaw_server.lym` (s) loads before `klayoutclaw_ui.lym` (u), ensuring the server globals exist when the UI registers callbacks.

## Changes Summary

| File | Change |
|------|--------|
| `plugin/klayoutclaw_server.lym` | Add 3 global callback vars + 4 call sites (~15 lines) |
| `plugin/klayoutclaw_ui.lym` | New file: dock widget, status label, callback handlers (~120 lines) |
| `install.py` | Copy both `.lym` files to `~/.klayout/pymacros/` |

## Key APIs Used

- `pya.QDockWidget` — dockable panel
- `pya.QTextEdit` — read-only scrolling log
- `pya.QLabel` — status bar indicator
- `pya.QMainWindow.addDockWidget()` — inherited by `MainWindow`
- `pya.QMainWindow.statusBar()` — access status bar
- `pya.QTimer.singleShot()` — delayed status reset after errors
