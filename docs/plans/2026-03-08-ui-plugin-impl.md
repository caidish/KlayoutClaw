# KlayoutClaw UI Plugin Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a dock panel and status bar indicator to KLayout showing MCP server status, errors, and command history — as a separate plugin with no KLayout source modifications.

**Architecture:** Two `.lym` autorun macros. The server macro exposes 3 global callback slots. A new UI macro registers handlers into those slots and creates a QDockWidget (bottom dock) + QLabel (status bar). Communication is via global function variables.

**Tech Stack:** Python, pya (KLayout's Qt bindings), QDockWidget, QTextEdit, QLabel, QTimer

---

### Task 1: Add global callback slots to the server macro

**Files:**
- Modify: `plugin/klayoutclaw_server.lym:60-68` (after logging, before layout state)

**Step 1: Add the 3 global callback variables**

After the logging section (line 58) and before the layout state section (line 62), add:

```python
# ---------------------------------------------------------------------------
# UI callback slots (set by klayoutclaw_ui.lym)
# ---------------------------------------------------------------------------

_klayoutclaw_on_request = None      # fn(method, timestamp, success, error_msg)
_klayoutclaw_on_server_start = None # fn(port)
_klayoutclaw_on_error = None        # fn(error_msg)
```

Note: In the `.lym` XML, `<` `>` `&` must be escaped as `&lt;` `&gt;` `&amp;`.

**Step 2: Add callback call in `_handle_jsonrpc` after successful method dispatch**

In `_handle_jsonrpc()`, after the successful `tools/call` return (line 327), add a callback invocation. Also add one after error cases (lines 329, 331). The modified `tools/call` block should be:

```python
    if method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})
        handler = _TOOL_DISPATCH.get(tool_name)
        if handler is None:
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Tool not found: {}".format(tool_name)}}, 200
        try:
            result_str = handler(tool_args)
            if _klayoutclaw_on_request:
                _klayoutclaw_on_request(method + ":" + tool_name, datetime.datetime.now().strftime("%H:%M:%S"), True, None)
            return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": result_str}]}}, 200
        except ValueError as e:
            if _klayoutclaw_on_request:
                _klayoutclaw_on_request(method + ":" + tool_name, datetime.datetime.now().strftime("%H:%M:%S"), False, str(e))
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": str(e)}}, 200
        except Exception as e:
            _log("Tool error: {} — {}".format(tool_name, e))
            if _klayoutclaw_on_request:
                _klayoutclaw_on_request(method + ":" + tool_name, datetime.datetime.now().strftime("%H:%M:%S"), False, str(e))
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": "Internal error: " + str(e)}}, 200
```

Also add a callback for non-tool methods (initialize, tools/list) — after each return in `_handle_jsonrpc`, add:

```python
        if _klayoutclaw_on_request:
            _klayoutclaw_on_request(method, datetime.datetime.now().strftime("%H:%M:%S"), True, None)
```

**Step 3: Add callback in server startup**

In `KlayoutClawServer.__init__` (line 372), after the `_log("MCP server listening...")` line, add:

```python
        if _klayoutclaw_on_server_start:
            _klayoutclaw_on_server_start(8765)
```

**Step 4: Add callback in error logging**

In the `_log` function (line 50), add error detection:

```python
def _log(msg):
    ts = datetime.datetime.now().isoformat()
    line = "[{}] {}".format(ts, msg)
    try:
        with open(_log_path, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print("[KlayoutClaw] " + msg)
    if msg.startswith("ERROR") and _klayoutclaw_on_error:
        _klayoutclaw_on_error(msg)
```

**Step 5: Commit**

```bash
git add plugin/klayoutclaw_server.lym
git commit -m "feat: add UI callback slots to MCP server for status/history reporting"
```

---

### Task 2: Create the UI macro with dock widget and status indicator

**Files:**
- Create: `plugin/klayoutclaw_ui.lym`

**Step 1: Create the UI macro file**

Create `plugin/klayoutclaw_ui.lym` — a KLayout autorun macro. The file is XML with escaped Python code inside `<text>` tags.

The Python code inside should:

1. Import `pya`, `datetime`
2. Get the main window: `mw = pya.Application.instance().main_window()`
3. Create a `QLabel` for the status bar with initial gray "MCP: Stopped" text
4. Add it to `mw.statusBar()`
5. Create a `QDockWidget` titled "KlayoutClaw"
6. Inside the dock: a read-only `QTextEdit` with monospace font
7. Add the dock to bottom area via `mw.addDockWidget(pya.Qt.BottomDockWidgetArea, dock)`
8. If `addDockWidget` raises, fall back to showing dock as a floating widget
9. Define callback handlers that update the QTextEdit and QLabel
10. Register callbacks into the server's globals (e.g., set the global `_klayoutclaw_on_request` in the server module's namespace)

Key implementation details:

- **Accessing server globals**: Since both macros run in the same Python interpreter, use the `__main__` module or check `globals()`. The simplest approach: the server's globals are in the pymacro's execution namespace. Since KLayout runs all pymacros in the same namespace, the UI macro can directly access and set `_klayoutclaw_on_request` etc.
- **XML escaping**: All `<`, `>`, `&` in the Python code must be escaped as `&lt;`, `&gt;`, `&amp;`
- **Widget references**: Store all Qt objects as globals to prevent garbage collection
- **QTimer for error reset**: Use `pya.QTimer.singleShot(5000, lambda: _reset_status())` to revert error state

Here is the complete Python code (before XML escaping):

```python
import pya
import datetime

# ---------------------------------------------------------------------------
# UI Widgets (stored as globals to prevent GC)
# ---------------------------------------------------------------------------

_ui_dock = None
_ui_log = None
_ui_status = None
_ui_timer = None

def _setup_ui():
    global _ui_dock, _ui_log, _ui_status, _ui_timer

    mw = pya.Application.instance().main_window()
    if mw is None:
        return

    # --- Status bar label ---
    _ui_status = pya.QLabel("MCP: Stopped \u25cf")
    _ui_status.setStyleSheet("QLabel { color: gray; padding: 0 8px; }")
    mw.statusBar().addPermanentWidget(_ui_status)

    # --- Command history dock ---
    _ui_dock = pya.QDockWidget("KlayoutClaw", mw)
    _ui_log = pya.QTextEdit()
    _ui_log.setReadOnly(True)
    _ui_log.setFontFamily("monospace")
    _ui_log.setMaximumHeight(150)
    _ui_dock.setWidget(_ui_log)

    try:
        mw.addDockWidget(pya.Qt.BottomDockWidgetArea, _ui_dock)
    except Exception:
        # Fallback: show as floating widget
        _ui_dock.setFloating(True)
        _ui_dock.resize(500, 150)
        _ui_dock.show()

    # --- Timer for error state reset ---
    _ui_timer = pya.QTimer()
    _ui_timer.timeout(lambda: _set_status_running())

    # --- Register callbacks ---
    import __main__
    __main__._klayoutclaw_on_request = _on_request
    __main__._klayoutclaw_on_server_start = _on_server_start
    __main__._klayoutclaw_on_error = _on_error

    # Check if server already started
    if hasattr(__main__, '_server') and __main__._server is not None:
        _on_server_start(8765)


def _set_status_running():
    global _ui_status
    if _ui_status:
        _ui_status.setText("MCP: Running \u25cf :8765")
        _ui_status.setStyleSheet("QLabel { color: green; padding: 0 8px; }")


def _on_server_start(port):
    _set_status_running()
    _append_log("Server started on port {}".format(port), True)


def _on_request(method, timestamp, success, error_msg):
    if success:
        line = "[{}] {:<30s} \u2714".format(timestamp, method)
    else:
        line = "[{}] {:<30s} \u2716 {}".format(timestamp, method, error_msg or "")
    _append_log(line, success)


def _on_error(error_msg):
    global _ui_status, _ui_timer
    if _ui_status:
        _ui_status.setText("MCP: Error \u25cf")
        _ui_status.setStyleSheet("QLabel { color: red; padding: 0 8px; }")
        _ui_timer.stop()
        _ui_timer.start(5000)
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    _append_log("[{}] ERROR: {}".format(ts, error_msg), False)


def _append_log(text, success):
    global _ui_log
    if _ui_log is None:
        return
    if success:
        color = "#228B22"
    else:
        color = "#CC0000"
    _ui_log.append('<span style="color:{};">{}</span>'.format(color, text))
    # Auto-scroll to bottom
    sb = _ui_log.verticalScrollBar()
    sb.setValue(sb.maximum)


# --- Initialize ---
try:
    _setup_ui()
except Exception as e:
    print("[KlayoutClaw UI] Failed to initialize: {}".format(e))
```

**Step 2: Write the `.lym` XML wrapper**

Wrap the above Python in the `.lym` XML format, escaping `<`, `>`, `&` in the code.

**Step 3: Commit**

```bash
git add plugin/klayoutclaw_ui.lym
git commit -m "feat: add UI plugin with dock panel and status bar indicator"
```

---

### Task 3: Update install.py to copy both plugin files

**Files:**
- Modify: `install.py:9-22`

**Step 1: Update install.py**

Change `install.py` to copy both `.lym` files:

```python
def main():
    plugin_dir = Path(__file__).parent / "plugin"
    klayout_dir = Path.home() / ".klayout" / "pymacros"
    klayout_dir.mkdir(parents=True, exist_ok=True)

    for lym_file in ["klayoutclaw_server.lym", "klayoutclaw_ui.lym"]:
        src = plugin_dir / lym_file
        if not src.exists():
            print(f"ERROR: Plugin file not found: {src}")
            sys.exit(1)
        dst = klayout_dir / lym_file
        shutil.copy2(src, dst)
        print(f"Installed: {dst}")

    print("\nDone! No external Python dependencies needed (uses only stdlib + pya).")
    print("Restart KLayout to activate the MCP server.")
    print("The server will be available at http://127.0.0.1:8765/mcp")
```

**Step 2: Commit**

```bash
git add install.py
git commit -m "feat: install.py copies both server and UI plugin files"
```

---

### Task 4: Install and test in KLayout

**Step 1: Run install.py**

```bash
source ~/miniforge3/etc/profile.d/conda.sh && conda activate instrMCPdev && python install.py
```

Expected: Both files copied to `~/.klayout/pymacros/`

**Step 2: Launch KLayout**

```bash
open /Applications/klayout.app
```

**Step 3: Verify visually**

- Check bottom dock: "KlayoutClaw" panel should appear with empty log
- Check status bar: should show "MCP: Running ● :8765" in green (or "Stopped" in gray if timing issue)
- Check KLayout log (`~/.klayout/klayoutclaw.log`) for any errors

**Step 4: Test with a MCP request**

```bash
curl -X POST http://127.0.0.1:8765/mcp -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
```

Expected: The dock panel should show a new log entry like `[HH:MM:SS] initialize ✔`

**Step 5: Commit any fixes**

If any adjustments were needed, commit them.
