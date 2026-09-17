"""qlaybot v0.4.4 Phase 6 §5.2.3 — KLayout VC UI.

The module stays importable in plain Python, but the concrete Qt widgets are
only usable inside KLayout's embedded Python where ``pya`` exposes Qt.
"""
from __future__ import annotations

import atexit
import json
import datetime
import os
import sys
import types
from collections import OrderedDict
from typing import Any, Optional

try:  # KLayout runtime
    import pya  # type: ignore
except ImportError:  # pragma: no cover - plain Python import path
    import klayout.db as pya  # type: ignore


_STATE_MODULE = "_klayoutclaw"
_DIRTY_TEXT = "⬤dirty"
_BULLET = "•"
_THUMBNAIL_CACHE_LIMIT = 50
_THUMBNAIL_CACHE: "OrderedDict[str, Any]" = OrderedDict()
_ATEXIT_REGISTERED = False


def _require_qt() -> None:
    if not hasattr(pya, "Application") or not hasattr(pya, "QLabel"):
        raise RuntimeError("plugin.klayoutclaw_vc.ui requires KLayout's Qt-enabled pya")


def _session_state():
    state = sys.modules.get(_STATE_MODULE)
    if state is None:
        state = types.ModuleType(_STATE_MODULE)
        sys.modules[_STATE_MODULE] = state
    settings = getattr(state, "settings", None)
    if not isinstance(settings, dict):
        state.settings = {"autoCheckpointOnSave": True}
    elif "autoCheckpointOnSave" not in settings:
        settings["autoCheckpointOnSave"] = True
    return state


def _callable_or_value(obj, attr: str):
    value = getattr(obj, attr)
    return value() if callable(value) else value


def _current_view(main_window):
    try:
        return main_window.current_view()
    except Exception:
        return None


def _active_cellview(view):
    try:
        return view.active_cellview()
    except Exception:
        return None


def _layout_for_view(view):
    cellview = _active_cellview(view)
    if cellview is None:
        return None
    try:
        return cellview.layout()
    except Exception:
        return None


def _gds_path_for_view(view) -> Optional[str]:
    cellview = _active_cellview(view)
    if cellview is None:
        return None
    try:
        filename = _callable_or_value(cellview, "filename")
    except Exception:
        return None
    if not filename:
        return None
    return os.path.abspath(str(filename))


def _resolve_top_cell(layout):
    if layout is None:
        return None
    try:
        top = layout.top_cell()
        if top is not None:
            return top
    except Exception:
        pass
    try:
        for cell in layout.each_top_cell():
            if hasattr(cell, "cell_index"):
                return cell
            return layout.cell(int(cell))
    except Exception:
        return None
    return None


def _ensure_vc_handle_for_view(view):
    layout = _layout_for_view(view)
    gds_path = _gds_path_for_view(view)
    if layout is None or not gds_path:
        return None

    from plugin.klayoutclaw_vc import repo as vc_repo
    import tools.vc_mcp_handlers as vc_handlers

    entry = vc_handlers.REGISTRY.get(id(layout))
    if entry is not None and entry.get("gds_path") == gds_path:
        handle = entry.get("handle")
        if handle is not None and not getattr(handle, "_invalidated", False):
            return entry

    handle = vc_repo.init(gds_path)
    entry = {"handle": handle, "gds_path": gds_path}
    vc_handlers.REGISTRY[id(layout)] = entry
    return entry


def _minutes_ago(ts_text: Optional[str]) -> Optional[int]:
    if not ts_text:
        return None
    try:
        when = datetime.datetime.fromisoformat(ts_text)
    except Exception:
        return None
    if when.tzinfo is None:
        now = datetime.datetime.now()
    else:
        now = datetime.datetime.now(when.tzinfo)
    delta = now - when
    minutes = int(max(0, delta.total_seconds()) // 60)
    return minutes


def _format_status(status: dict) -> tuple[str, str]:
    mode = status.get("mode", "unknown")
    branch = status.get("branch")
    minutes = _minutes_ago(status.get("last_checkpoint_ts"))
    if branch and minutes is not None:
        cleanliness = _DIRTY_TEXT if status.get("dirty") else "clean"
        return (
            f"{branch} {_BULLET} {cleanliness} {_BULLET} last: {minutes} min ago",
            f"mode: {mode}",
        )
    return (f"VC: mode: {mode}", f"mode: {mode}")


def _relative_time_label(ts_text: Optional[str]) -> str:
    minutes = _minutes_ago(ts_text)
    if minutes is None:
        return "unknown"
    return f"{minutes} min ago"


def _truncate_message(message: str, limit: int = 50) -> str:
    message = str(message)
    if len(message) <= limit:
        return message
    return message[: limit - 1].rstrip() + "…"


def _snapshot_layout_thumbnail(snapshot: dict):
    from plugin.klayoutclaw_vc import serializer

    layout = serializer.deserialize(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    )
    return _render_layout_thumbnail(layout)


def _render_layout_thumbnail(layout, *, size: int = 128):
    image = pya.QImage(size, size, pya.QImage.Format_ARGB32)
    image.fill(pya.QColor("#f8fafc"))

    boxes = []
    bounds = None
    for top in layout.each_top_cell():
        if not hasattr(top, "begin_shapes_rec"):
            try:
                top = layout.cell(int(top))
            except Exception:
                continue
        for li in layout.layer_indexes():
            iterator = top.begin_shapes_rec(li)
            while not iterator.at_end():
                try:
                    box = iterator.shape().bbox()
                except Exception:
                    iterator.next()
                    continue
                iterator.next()
                if box.empty():
                    continue
                rect = (int(box.left), int(box.bottom), int(box.right), int(box.top))
                boxes.append(rect)
                if bounds is None:
                    bounds = list(rect)
                else:
                    bounds[0] = min(bounds[0], rect[0])
                    bounds[1] = min(bounds[1], rect[1])
                    bounds[2] = max(bounds[2], rect[2])
                    bounds[3] = max(bounds[3], rect[3])

    painter = pya.QPainter(image)
    try:
        painter.setRenderHint(pya.QPainter.Antialiasing, True)
    except Exception:
        pass
    pen = pya.QPen(pya.QColor("#0f172a"))
    try:
        pen.setWidth(2)
    except Exception:
        pass
    painter.setPen(pen)
    painter.setBrush(pya.QBrush(pya.QColor("#93c5fd")))

    if bounds is not None:
        min_x, min_y, max_x, max_y = bounds
        width = max(1, max_x - min_x)
        height = max(1, max_y - min_y)
        pad = 8.0
        scale = min((size - 2 * pad) / float(width), (size - 2 * pad) / float(height))
        for left, bottom, right, top in boxes:
            x1 = pad + (left - min_x) * scale
            x2 = pad + (right - min_x) * scale
            y1 = size - pad - (top - min_y) * scale
            y2 = size - pad - (bottom - min_y) * scale
            painter.drawRect(
                int(round(x1)),
                int(round(y1)),
                max(1, int(round(x2 - x1))),
                max(1, int(round(y2 - y1))),
            )
    painter.end()
    return image


def _cached_thumbnail_for_commit(handle, sha: str):
    cached = _THUMBNAIL_CACHE.get(sha)
    if cached is not None:
        _THUMBNAIL_CACHE.move_to_end(sha)
        return cached

    snapshot = handle._load_snapshot(sha)
    if not snapshot:
        return None
    pixmap = _snapshot_layout_thumbnail(snapshot)
    _THUMBNAIL_CACHE[sha] = pixmap
    while len(_THUMBNAIL_CACHE) > _THUMBNAIL_CACHE_LIMIT:
        _THUMBNAIL_CACHE.popitem(last=False)
    return pixmap


def _safe_delete_qobject(obj) -> None:
    if obj is None:
        return
    for name in ("hide", "close", "deleteLater"):
        method = getattr(obj, name, None)
        if callable(method):
            try:
                method()
            except Exception:
                pass


class VCStatusChip:
    """Status-bar label that reflects the active layout's VC status."""

    def __init__(self, main_window, *, poll_interval_ms: int = 5000):
        _require_qt()
        self._main_window = main_window
        self._label = pya.QLabel("VC: not initialized")
        self._label.setObjectName("klayoutclaw-vc-status-chip")
        self._label.setStyleSheet(
            "QLabel#klayoutclaw-vc-status-chip { padding: 0 8px; color: #555555; }"
        )
        self._label.setToolTip("mode: unknown")
        self._poll_timer = pya.QTimer()
        self._poll_timer.timeout(self.refresh)
        self._poll_timer.start(max(50, int(poll_interval_ms)))
        self._attach_to_status_bar()

    @property
    def widget(self):
        return self._label

    def text(self) -> str:
        return str(_callable_or_value(self._label, "text"))

    def refresh(self) -> dict:
        from tools import vc_mcp_handlers as vc_handlers

        view = _current_view(self._main_window)
        if view is None:
            self._show_not_initialized()
            return {"ok": False, "reason": "no current view"}

        entry = _ensure_vc_handle_for_view(view)
        if entry is None:
            self._show_not_initialized()
            return {"ok": False, "reason": "vc not initialized"}

        layout = _layout_for_view(view)
        gds_path = _gds_path_for_view(view)
        status = vc_handlers.vc_status({}, layout=layout, gds_path_hint=gds_path)
        if not isinstance(status, dict) or status.get("ok") is False:
            self._show_not_initialized()
            return status if isinstance(status, dict) else {"ok": False}

        text, tooltip = _format_status(status)
        self._label.setText(text)
        self._label.setToolTip(tooltip)
        color = "#a61d24" if status.get("dirty") else "#1d6b36"
        self._label.setStyleSheet(
            "QLabel#klayoutclaw-vc-status-chip { padding: 0 8px; color: "
            + color
            + "; }"
        )
        return status

    def _attach_to_status_bar(self) -> None:
        status_bar = self._main_window.statusBar
        try:
            status_bar.addPermanentWidget(self._label)
        except Exception:
            status_bar.addWidget(self._label)

    def _show_not_initialized(self) -> None:
        self._label.setText("VC: not initialized")
        self._label.setToolTip("mode: unavailable")
        self._label.setStyleSheet(
            "QLabel#klayoutclaw-vc-status-chip { padding: 0 8px; color: #555555; }"
        )


class CheckpointDialog:
    """Modal checkpoint dialog with required message and optional tags."""

    def __init__(self, controller):
        self.controller = controller
        self._dialog = pya.QDialog(controller.main_window)
        self._dialog.setWindowTitle("Create VC Checkpoint")
        self._dialog.setModal(True)
        self._dialog.resize(420, 150)

        root = pya.QVBoxLayout(self._dialog)
        form = pya.QFormLayout()
        self.message_edit = pya.QLineEdit()
        self.tags_edit = pya.QLineEdit()
        form.addRow("Message", self.message_edit)
        form.addRow("Tags", self.tags_edit)
        root.addLayout(form)

        self._error_label = pya.QLabel("")
        self._error_label.setObjectName("klayoutclaw-vc-checkpoint-error")
        self._error_label.setStyleSheet(
            "QLabel#klayoutclaw-vc-checkpoint-error { color: #a61d24; padding-top: 4px; }"
        )
        self._error_label.hide()
        root.addWidget(self._error_label)

        button_row = pya.QHBoxLayout()
        button_row.addStretch(1)
        self.cancel_button = pya.QPushButton("Cancel")
        self.submit_button = pya.QPushButton("Checkpoint")
        button_row.addWidget(self.cancel_button)
        button_row.addWidget(self.submit_button)
        root.addLayout(button_row)

        self.cancel_button.clicked(self._dialog.reject)
        self.submit_button.clicked(self.submit)
        try:
            self.message_edit.returnPressed(self.submit)
        except Exception:
            pass

    @property
    def dialog(self):
        return self._dialog

    def open(self) -> None:
        self._clear_error()
        self._dialog.show()
        self._dialog.raise_()
        self._dialog.activateWindow()
        self.message_edit.setFocus()

    def is_open(self) -> bool:
        return bool(self._dialog.isVisible())

    def error_text(self) -> str:
        return str(_callable_or_value(self._error_label, "text"))

    @staticmethod
    def parse_tags(text: str) -> list[str]:
        return [part.strip() for part in str(text).split(",") if part.strip()]

    def submit(self) -> dict:
        message = str(_callable_or_value(self.message_edit, "text")).strip()
        tags_text = str(_callable_or_value(self.tags_edit, "text"))
        tags = self.parse_tags(tags_text)

        if not message:
            self._show_error("Checkpoint message is required.")
            return {"ok": False, "reason": "message required"}

        view = _current_view(self.controller.main_window)
        if view is None:
            self._show_error("No active layout view.")
            return {"ok": False, "reason": "no active view"}

        _ensure_vc_handle_for_view(view)
        layout = _layout_for_view(view)
        gds_path = _gds_path_for_view(view)

        from tools import vc_mcp_handlers as vc_handlers

        result = vc_handlers.vc_checkpoint(
            {"message": message, "tags": tags},
            layout=layout,
            gds_path_hint=gds_path,
        )
        if not isinstance(result, dict) or result.get("ok") is False:
            reason = (
                result.get("reason", "checkpoint failed")
                if isinstance(result, dict) else
                "checkpoint failed"
            )
            self._show_error(str(reason))
            return result if isinstance(result, dict) else {"ok": False, "reason": reason}

        self._clear_error()
        self._dialog.accept()
        self.controller.refresh()
        try:
            self.controller.history_dock.refresh()
        except Exception:
            pass
        return result

    def _clear_error(self) -> None:
        self._error_label.setText("")
        self._error_label.hide()

    def _show_error(self, text: str) -> None:
        self._error_label.setText(str(text))
        self._error_label.show()

    def shutdown(self) -> None:
        _safe_delete_qobject(self._dialog)


class HistoryDockPanel:
    """Dockable VC history browser with lazy thumbnail loading."""

    def __init__(self, controller):
        self.controller = controller
        self._dock = pya.QDockWidget("VC History", controller.main_window)
        self._dock.setObjectName("klayoutclaw-vc-history-dock")
        self._tree = pya.QTreeWidget()
        self._tree.setColumnCount(4)
        self._tree.setHeaderLabels(["When", "Message", "Tags", "Preview"])
        self._tree.setRootIsDecorated(False)
        self._tree.setUniformRowHeights(True)
        self._tree.setAlternatingRowColors(True)
        self._tree.setColumnWidth(0, 90)
        self._tree.setColumnWidth(1, 240)
        self._tree.setColumnWidth(2, 120)
        self._tree.setColumnWidth(3, 96)
        self._dock.setWidget(self._tree)
        self._rows: list[dict] = []
        self._visible = False
        try:
            controller.main_window.addDockWidget(
                pya.Qt.RightDockWidgetArea, self._dock,
            )
        except Exception:
            self._dock.setFloating(True)
        self._dock.hide()

    @property
    def dock(self):
        return self._dock

    @property
    def tree(self):
        return self._tree

    def refresh(self) -> None:
        from tools import vc_mcp_handlers as vc_handlers

        view = _current_view(self.controller.main_window)
        if view is None:
            self._tree.clear()
            self._rows = []
            return None

        _ensure_vc_handle_for_view(view)
        layout = _layout_for_view(view)
        gds_path = _gds_path_for_view(view)
        result = vc_handlers.vc_history({}, layout=layout, gds_path_hint=gds_path)
        commits = result.get("commits", []) if isinstance(result, dict) else []

        self._tree.clear()
        self._rows = []
        for commit in commits:
            tags = list(commit.get("tags") or [])
            item = pya.QTreeWidgetItem([
                _relative_time_label(commit.get("ts")),
                _truncate_message(commit.get("message", "")),
                ", ".join(tags),
                "",
            ])
            item.setToolTip(1, str(commit.get("message", "")))
            item.setToolTip(2, ", ".join(tags))
            self._tree.addTopLevelItem(item)
            self._rows.append({
                "item": item,
                "commit": commit,
                "thumbnail_loaded": False,
            })

        if self.is_visible():
            self.load_visible_thumbnails()
        return None

    def toggle(self) -> None:
        if self.is_visible():
            self._visible = False
            self._dock.hide()
            return
        self.refresh()
        self._visible = True
        self._dock.show()
        self._dock.raise_()
        self.load_visible_thumbnails()

    def is_visible(self) -> bool:
        return bool(self._visible)

    def row_count(self) -> int:
        return len(self._rows)

    def row_data(self, index: int) -> dict:
        row = self._rows[index]
        commit = row["commit"]
        return {
            "sha": commit.get("sha"),
            "message": commit.get("message"),
            "tags": list(commit.get("tags") or []),
            "timestamp": _relative_time_label(commit.get("ts")),
            "thumbnail_loaded": bool(row["thumbnail_loaded"]),
        }

    def load_visible_thumbnails(self) -> None:
        if self._rows:
            self.ensure_thumbnail_for_row(0)

    def ensure_thumbnail_for_row(self, index: int) -> None:
        from tools import vc_mcp_handlers as vc_handlers

        if index < 0 or index >= len(self._rows):
            return
        row = self._rows[index]
        if row["thumbnail_loaded"]:
            return

        view = _current_view(self.controller.main_window)
        if view is None:
            return
        layout = _layout_for_view(view)
        gds_path = _gds_path_for_view(view)
        handle = vc_handlers._get_handle(layout, gds_path)
        if handle is None:
            return

        image = _cached_thumbnail_for_commit(handle, row["commit"]["sha"])
        if image is not None:
            row["item"].setIcon(3, pya.QIcon(pya.QPixmap.fromImage(image)))
        row["thumbnail_loaded"] = True

    def shutdown(self) -> None:
        self._rows = []
        _safe_delete_qobject(self._dock)


class ContextMenus:
    """Background and history-row context menus."""

    def __init__(self, controller):
        self.controller = controller
        self._connected_widgets: set[int] = set()
        try:
            tree = self.controller.history_dock.tree
            tree.setContextMenuPolicy(pya.Qt.CustomContextMenu)
            tree.customContextMenuRequested(self._show_history_menu_at)
        except Exception:
            pass

    def attach_view(self, view) -> None:
        try:
            widget = view.widget()
        except Exception:
            return
        widget_id = id(widget)
        if widget_id in self._connected_widgets:
            return
        self._connected_widgets.add(widget_id)
        try:
            widget.setContextMenuPolicy(pya.Qt.CustomContextMenu)
            widget.customContextMenuRequested(
                lambda pos, _widget=widget: self._show_background_menu(_widget, pos)
            )
        except Exception:
            pass

    def menu_labels(self, menu) -> list[str]:
        labels = []
        for action in menu.actions():
            try:
                if action.isSeparator():
                    continue
            except Exception:
                pass
            labels.append(str(_callable_or_value(action, "text")))
        return labels

    def background_menu(self):
        menu = pya.QMenu(self.controller.main_window)
        action = pya.QAction("Checkpoint", menu)
        action.triggered(self.trigger_background_checkpoint)
        menu.addAction(action)
        return menu

    def trigger_background_checkpoint(self):
        self.controller.show_checkpoint_dialog()

    def history_row_menu_for_row(self, index: int):
        row = self.controller.history_dock._rows[index]
        ref = row["commit"]["sha"]
        menu = pya.QMenu(self.controller.main_window)

        checkout = pya.QAction("Checkout", menu)
        checkout.triggered(lambda _checked=False, _ref=ref: self.checkout_ref(_ref))
        menu.addAction(checkout)

        branch = pya.QAction("Branch from here", menu)
        branch.triggered(lambda _checked=False, _ref=ref: self._prompt_branch(_ref))
        menu.addAction(branch)

        tag = pya.QAction("Tag", menu)
        tag.triggered(lambda _checked=False, _ref=ref: self._prompt_tag(_ref))
        menu.addAction(tag)
        return menu

    def checkout_ref(self, ref: str) -> dict:
        from tools import vc_mcp_handlers as vc_handlers

        view = _current_view(self.controller.main_window)
        layout, gds_path = self._active_layout_and_path()
        result = vc_handlers.vc_checkout(
            {"ref": ref},
            layout=layout,
            gds_path_hint=gds_path,
        )
        if isinstance(result, dict) and result.get("ok"):
            top = _resolve_top_cell(layout)
            cellview = _active_cellview(view)
            if cellview is not None and top is not None:
                try:
                    cellview.cell = top
                except Exception:
                    try:
                        cellview.set_cell(top.cell_index())
                    except Exception:
                        pass
        self.controller.refresh()
        self.controller.history_dock.refresh()
        return result

    def branch_from_ref(self, ref: str, name: str) -> dict:
        from tools import vc_mcp_handlers as vc_handlers

        layout, gds_path = self._active_layout_and_path()
        result = vc_handlers.vc_branch(
            {"op": "create", "name": name, "from_ref": ref},
            layout=layout,
            gds_path_hint=gds_path,
        )
        self.controller.history_dock.refresh()
        return result

    def tag_ref(self, ref: str, name: str) -> dict:
        from tools import vc_mcp_handlers as vc_handlers

        layout, gds_path = self._active_layout_and_path()
        result = vc_handlers.vc_tag(
            {"name": name, "ref": ref},
            layout=layout,
            gds_path_hint=gds_path,
        )
        self.controller.history_dock.refresh()
        return result

    def _prompt_branch(self, ref: str) -> dict:
        name = self._prompt_text("Create Branch", "Branch name")
        if not name:
            return {"ok": False, "reason": "branch name required"}
        return self.branch_from_ref(ref, name)

    def _prompt_tag(self, ref: str) -> dict:
        name = self._prompt_text("Create Tag", "Tag name")
        if not name:
            return {"ok": False, "reason": "tag name required"}
        return self.tag_ref(ref, name)

    def _prompt_text(self, title: str, label: str) -> Optional[str]:
        try:
            value, ok = pya.QInputDialog.getText(
                self.controller.main_window, title, label,
            )
        except Exception:
            return None
        if not ok:
            return None
        text = str(value).strip()
        return text or None

    def _active_layout_and_path(self):
        view = _current_view(self.controller.main_window)
        return _layout_for_view(view), _gds_path_for_view(view)

    def _show_background_menu(self, widget, pos) -> None:
        menu = self.background_menu()
        try:
            menu.exec_(widget.mapToGlobal(pos))
        except Exception:
            menu.popup(widget.mapToGlobal(pos))

    def _show_history_menu_at(self, pos) -> None:
        try:
            item = self.controller.history_dock.tree.itemAt(pos)
        except Exception:
            item = None
        if item is None:
            return
        for index, row in enumerate(self.controller.history_dock._rows):
            if row["item"] is item:
                menu = self.history_row_menu_for_row(index)
                try:
                    tree = self.controller.history_dock.tree
                    menu.exec_(tree.viewport().mapToGlobal(pos))
                except Exception:
                    pass
                return


class SaveIntegration:
    """Post-save VC integration for manual and native save flows."""

    def __init__(self, controller):
        self.controller = controller
        self._connect_native_actions()

    def save_layout(self, filepath: Optional[str] = None, *, format: str = "GDS2") -> dict:
        view = _current_view(self.controller.main_window)
        layout = _layout_for_view(view)
        if layout is None:
            return {"ok": False, "reason": "no active layout"}

        target = filepath or _gds_path_for_view(view)
        if not target:
            return {"ok": False, "reason": "filepath required"}
        abs_path = os.path.abspath(str(target))

        save_opts = pya.SaveLayoutOptions()
        save_opts.format = "OASIS" if str(format).upper() == "OASIS" else "GDS2"
        layout.write(abs_path, save_opts)
        return self._post_save(abs_path, layout=layout)

    def _connect_native_actions(self) -> None:
        try:
            menu = self.controller.main_window.menu()
        except Exception:
            return
        for path in ("file_menu.save", "file_menu.save_as"):
            try:
                action = menu.action(path)
            except Exception:
                action = None
            if action is not None:
                try:
                    action.on_triggered(self._schedule_post_save)
                except Exception:
                    pass

    def _schedule_post_save(self) -> None:
        try:
            pya.QTimer.singleShot(0, self._post_save_from_current_view)
        except Exception:
            self._post_save_from_current_view()

    def _post_save_from_current_view(self) -> None:
        view = _current_view(self.controller.main_window)
        path = _gds_path_for_view(view)
        if path:
            self._post_save(path)

    def _post_save(self, filepath: str, *, layout=None) -> dict:
        from plugin.klayoutclaw_vc import repo as vc_repo
        from tools import vc_mcp_handlers as vc_handlers

        if layout is None:
            view = _current_view(self.controller.main_window)
            layout = _layout_for_view(view)
        if layout is None:
            return {"ok": False, "reason": "no active layout"}

        abs_path = os.path.abspath(filepath)
        entry = vc_handlers.REGISTRY.get(id(layout))
        if entry is None:
            handle = vc_repo.init(abs_path)
            vc_handlers.REGISTRY[id(layout)] = {
                "handle": handle,
                "gds_path": abs_path,
            }
        vc_handlers.migrate_on_save(layout, abs_path)

        settings = _session_state().settings
        auto_checkpoint = bool(settings.get("autoCheckpointOnSave", True))
        checkpoint_result = None
        if auto_checkpoint:
            checkpoint_result = vc_handlers.vc_checkpoint(
                {"message": "auto-checkpoint on save"},
                layout=layout,
                gds_path_hint=abs_path,
            )

        self.controller.refresh()
        try:
            self.controller.history_dock.refresh()
        except Exception:
            pass
        return {
            "ok": True,
            "filepath": abs_path,
            "auto_checkpoint": auto_checkpoint,
            "checkpoint": checkpoint_result,
        }


class VCUIController:
    """Composes the Phase 6 VC UI pieces for a KLayout main window."""

    def __init__(self, main_window, *, poll_interval_ms: int = 5000):
        _require_qt()
        self.main_window = main_window
        self.status_chip = VCStatusChip(
            main_window, poll_interval_ms=poll_interval_ms,
        )
        self.checkpoint_dialog = CheckpointDialog(self)
        self.history_dock = HistoryDockPanel(self)
        self.context_menus = ContextMenus(self)
        self.save_integration = SaveIntegration(self)
        self.checkpoint_shortcut = pya.QShortcut(
            pya.QKeySequence("Ctrl+Shift+K"), main_window,
        )
        self.checkpoint_shortcut.activated(self.show_checkpoint_dialog)
        self.history_shortcut = pya.QShortcut(
            pya.QKeySequence("Ctrl+Shift+H"), main_window,
        )
        self.history_shortcut.activated(self.toggle_history_dock)
        self._connected_views: set[int] = set()
        self._connect_main_window()
        self._attach_current_view()
        self.refresh()

    def refresh(self):
        return self.status_chip.refresh()

    def show_checkpoint_dialog(self) -> None:
        self.checkpoint_dialog.open()

    def toggle_history_dock(self) -> None:
        self.history_dock.toggle()

    def shutdown(self) -> None:
        _THUMBNAIL_CACHE.clear()
        try:
            self.status_chip._poll_timer.stop()
        except Exception:
            pass
        self._connected_views.clear()
        self.checkpoint_dialog.shutdown()
        self.history_dock.shutdown()
        _safe_delete_qobject(self.checkpoint_shortcut)
        _safe_delete_qobject(self.history_shortcut)
        _safe_delete_qobject(self.status_chip.widget)

    def _connect_main_window(self) -> None:
        try:
            self.main_window.on_view_created(self._on_view_created)
        except Exception:
            pass
        try:
            self.main_window.on_current_view_changed(self._on_current_view_changed)
        except Exception:
            pass

    def _attach_current_view(self) -> None:
        view = _current_view(self.main_window)
        if view is None:
            return
        view_id = id(view)
        if view_id in self._connected_views:
            return
        self._connected_views.add(view_id)
        try:
            view.on_file_open(lambda: self._handle_view_event(view))
        except Exception:
            pass
        try:
            view.on_cellview_changed(lambda: self._handle_view_event(view))
        except Exception:
            pass
        try:
            view.on_active_cellview_changed(lambda: self._handle_view_event(view))
        except Exception:
            pass
        try:
            self.context_menus.attach_view(view)
        except Exception:
            pass
        self._handle_view_event(view)

    def _handle_view_event(self, view) -> None:
        _ensure_vc_handle_for_view(view)
        self.refresh()

    def _on_view_created(self) -> None:
        self._attach_current_view()

    def _on_current_view_changed(self) -> None:
        self._attach_current_view()
        self.refresh()


def _shutdown_installed_controller() -> None:
    state = sys.modules.get(_STATE_MODULE)
    controller = getattr(state, "vc_ui_controller", None) if state is not None else None
    if isinstance(controller, VCUIController):
        try:
            controller.shutdown()
        except Exception:
            pass


def install_ui(*, main_window=None, poll_interval_ms: int = 5000) -> VCUIController:
    """Install or return the singleton VC UI controller for the session."""
    _require_qt()
    state = _session_state()
    if main_window is None:
        app = pya.Application.instance()
        if app is None:
            raise RuntimeError("KLayout application instance unavailable")
        main_window = app.main_window()
    if main_window is None:
        raise RuntimeError("KLayout main window unavailable")

    controller = getattr(state, "vc_ui_controller", None)
    if isinstance(controller, VCUIController) and controller.main_window is main_window:
        controller.refresh()
        return controller

    controller = VCUIController(main_window, poll_interval_ms=poll_interval_ms)
    state.vc_ui_controller = controller
    global _ATEXIT_REGISTERED
    if not _ATEXIT_REGISTERED:
        atexit.register(_shutdown_installed_controller)
        _ATEXIT_REGISTERED = True
    return controller
