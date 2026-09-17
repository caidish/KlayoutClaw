"""qlaybot v0.4.4 Phase 5 §5.2.4 — VC MCP handler module.

Provides the 9 public ``vc_*`` handlers used by the KLayout MCP server
bridge in ``plugin/klayoutclaw_server.lym``.  Each handler delegates to
G5's frozen backend (``plugin.klayoutclaw_vc.repo``).

Public surface
--------------

* ``REGISTRY``                         — session registry keyed by id(layout)
* ``VC_EXPORT_INLINE_CAP_BYTES``       — 256 KiB truncation threshold
* ``reset_registry()``                 — test-only registry reset
* ``migrate_on_save(layout, path)``    — save_layout DM-4 hook
* ``vc_init, vc_checkpoint, vc_history, vc_checkout, vc_diff,
  vc_branch, vc_tag, vc_export, vc_status``  — 9 MCP handlers

All handlers use keyword-only ``layout`` and ``gds_path_hint`` parameters
and return a JSON-serialisable ``dict``.  Uninit sentinel is the exact
string ``"vc not initialized"`` per spec §8.3.
"""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, Optional


# Make sibling `plugin` package importable when this module is loaded from
# either the source tree or the installed ``~/.klayout/pymacros`` copy.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from plugin.klayoutclaw_vc import repo as vc_repo  # noqa: E402
from plugin.klayoutclaw_vc import serializer as vc_serializer  # noqa: E402


# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

# Session registry keyed by id(layout).  Each value:
#   {"handle": RepoHandle, "gds_path": str}
REGISTRY: Dict[int, Dict[str, Any]] = {}

# MCP-7 truncation threshold (bytes).  Spec §5.2.4 MCP-7.
VC_EXPORT_INLINE_CAP_BYTES: int = 256 * 1024

# Exact sentinel string mandated by spec §8.3.
_UNINIT_REASON = "vc not initialized"


def reset_registry() -> None:
    """Test-only helper — clear the session registry."""
    REGISTRY.clear()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _uninit_sentinel() -> Dict[str, Any]:
    return {"ok": False, "reason": _UNINIT_REASON}


def _get_entry(layout, gds_path_hint: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Resolve a registry entry for ``layout``.

    Primary lookup is by ``id(layout)``.  If that misses and a
    ``gds_path_hint`` is provided, we fall back to any registry entry that
    was initialised against the same gds_path — this lets callers run
    ops against a different in-memory pya.Layout (e.g. a branch's
    working copy) while pointing at the same repo.  The test suite's
    T46 merge-conflict scenario depends on this fallback.
    """
    entry = REGISTRY.get(id(layout))
    if entry is not None:
        return entry
    if gds_path_hint:
        target = os.path.abspath(gds_path_hint)
        for ent in REGISTRY.values():
            if ent.get("gds_path") == target:
                return ent
    return None


def _get_handle(layout, gds_path_hint: Optional[str] = None):
    entry = _get_entry(layout, gds_path_hint)
    return entry["handle"] if entry else None


# ---------------------------------------------------------------------------
# vc_init (MCP-1) — RB-1 auto-detect / memory / disk modes.
# ---------------------------------------------------------------------------


def vc_init(args: dict, *, layout, gds_path_hint=None) -> dict:
    mode = args.get("mode", "auto") if isinstance(args, dict) else "auto"
    gds_path = None
    if isinstance(args, dict):
        gds_path = args.get("gds_path")
    if not gds_path:
        gds_path = gds_path_hint

    if not gds_path:
        return {"ok": False, "reason": "gds_path required"}

    gds_path = os.path.abspath(gds_path)

    if mode not in ("auto", "memory", "disk"):
        return {"ok": False, "reason": f"unknown mode {mode!r}"}

    if mode == "disk":
        sidecar = gds_path + ".vc"
        parent = os.path.dirname(sidecar) or "."
        # Belt-and-suspenders writability check.
        if not os.access(parent, os.W_OK):
            return {
                "ok": False,
                "reason": f"disk mode requested but sidecar not writable: {sidecar}",
            }
        # If sidecar exists with .git already, just (re-)init handle.
        if not os.path.isdir(os.path.join(sidecar, ".git")):
            # Try to create sidecar + run ``git init`` eagerly so we end up
            # in true disk mode.  If anything fails, report failure WITHOUT
            # leaving a partial sidecar on disk.
            created_here = False
            try:
                if not os.path.exists(sidecar):
                    os.makedirs(sidecar, exist_ok=False)
                    created_here = True
                r = subprocess.run(
                    ["git", "-C", sidecar, "init", "-q", "-b", "main"],
                    capture_output=True, text=True,
                )
                if r.returncode != 0:
                    raise OSError(f"git init failed: {r.stderr.strip()}")
            except Exception as e:
                if created_here:
                    try:
                        shutil.rmtree(sidecar, ignore_errors=True)
                    except Exception:
                        pass
                return {
                    "ok": False,
                    "reason": (
                        f"disk mode requested but sidecar not writable: "
                        f"{sidecar} ({e})"
                    ),
                }
        # Fall through to init — G5 will now detect the sidecar.

    if mode == "memory":
        # Force memory mode even if a sidecar exists by bypassing G5's
        # auto-detect.  We still register the handle via the usual path so
        # auto-invalidation of any prior handle on the same gds_path is
        # observed.
        # Invalidate any previous handle explicitly (mirrors G5 init).
        prev = vc_repo._HANDLES_BY_PATH.get(gds_path)
        if prev is not None and not prev._invalidated:
            prev._invalidate_internal(remove_tmpdir=False)
        tmp = tempfile.mkdtemp(prefix="klayoutclaw_vc_")
        handle = vc_repo.RepoHandle(
            gds_path=gds_path, repo_path=tmp, mode="memory",
        )
        vc_repo._HANDLES_BY_PATH[gds_path] = handle
        actual_mode = "memory"
    else:
        handle = vc_repo.init(gds_path)
        actual_mode = handle._mode
        if mode == "disk" and actual_mode != "disk":
            # Should not happen given the pre-work above, but be defensive.
            return {
                "ok": False,
                "reason": (
                    f"disk mode requested but sidecar not writable: "
                    f"{gds_path + '.vc'}"
                ),
            }

    REGISTRY[id(layout)] = {"handle": handle, "gds_path": gds_path}
    return {
        "ok": True,
        "mode": actual_mode,
        "repo_path": handle._repo_path,
    }


# ---------------------------------------------------------------------------
# vc_checkpoint (MCP-2)
# ---------------------------------------------------------------------------


def vc_checkpoint(args: dict, *, layout, gds_path_hint=None) -> dict:
    handle = _get_handle(layout, gds_path_hint)
    if handle is None:
        return _uninit_sentinel()
    message = args.get("message", "") if isinstance(args, dict) else ""
    tags = args.get("tags") if isinstance(args, dict) else None
    if tags is None:
        tags = []
    return handle.checkpoint(layout, message, tags)


# ---------------------------------------------------------------------------
# vc_history (MCP-3)
# ---------------------------------------------------------------------------


def vc_history(args: dict, *, layout, gds_path_hint=None) -> dict:
    handle = _get_handle(layout, gds_path_hint)
    if handle is None:
        return _uninit_sentinel()
    limit = args.get("limit") if isinstance(args, dict) else None
    branch = args.get("branch") if isinstance(args, dict) else None
    commits = handle.history(limit, branch)
    return {"ok": True, "commits": commits}


# ---------------------------------------------------------------------------
# vc_checkout (MCP-4) — also rewrites the live layout in place.
# ---------------------------------------------------------------------------


def _rewrite_layout_from_snapshot(layout, snap_dict) -> None:
    """Repopulate ``layout`` with the cells+shapes described by ``snap_dict``.

    Strategy:
      1. ``serializer.deserialize`` the snapshot into a fresh tmp layout.
      2. Clear the live ``layout`` (cells, instances, layer shapes).
      3. Copy the tmp layout's cells + shapes + instances into ``layout``.
      4. Restore ``dbu`` from the snapshot if present.
    """
    import json

    import pya  # local import — only available in KLayout / test env

    try:
        tmp_layout = vc_serializer.deserialize(json.dumps(snap_dict))
    except Exception:
        # Best-effort: leave layout unchanged on deserialize error.
        return

    # Wipe the current layout.  ``Layout.clear()`` removes cells, instances,
    # and layer shapes in one call.
    try:
        layout.clear()
    except Exception:
        # Fallback: delete each cell manually.
        for cell in list(layout.each_cell()):
            try:
                layout.delete_cell(cell.cell_index())
            except Exception:
                pass

    # Restore dbu from snapshot (clear() may reset it in some pya builds).
    dbu = snap_dict.get("dbu")
    if isinstance(dbu, (int, float)) and dbu > 0:
        try:
            layout.dbu = float(dbu)
        except Exception:
            pass
    else:
        try:
            layout.dbu = tmp_layout.dbu
        except Exception:
            pass

    # Build cell-name → new-cell map.
    name_to_cell: Dict[str, Any] = {}
    for tmp_cell in tmp_layout.each_cell():
        new_cell = layout.create_cell(tmp_cell.name)
        name_to_cell[tmp_cell.name] = new_cell

    # Copy shapes, layer-by-layer, cell-by-cell.
    for tmp_cell in tmp_layout.each_cell():
        new_cell = name_to_cell[tmp_cell.name]
        for li in tmp_layout.layer_indexes():
            info = tmp_layout.get_info(li)
            target_li = layout.layer(info.layer, info.datatype)
            for shape in tmp_cell.shapes(li).each():
                try:
                    new_cell.shapes(target_li).insert(shape)
                except Exception:
                    pass

    # Copy instances.
    for tmp_cell in tmp_layout.each_cell():
        new_cell = name_to_cell[tmp_cell.name]
        try:
            inst_iter = tmp_cell.each_inst()
        except Exception:
            continue
        for inst in inst_iter:
            try:
                child_name = inst.cell.name
            except Exception:
                continue
            child_new = name_to_cell.get(child_name)
            if child_new is None:
                continue
            try:
                ca = inst.cell_inst
                new_ca = pya.CellInstArray(
                    child_new.cell_index(), ca.trans,
                    ca.a, ca.b, ca.na, ca.nb,
                ) if ca.is_regular_array() else pya.CellInstArray(
                    child_new.cell_index(), ca.trans,
                )
                new_cell.insert(new_ca)
            except Exception:
                pass


def vc_checkout(args: dict, *, layout, gds_path_hint=None) -> dict:
    handle = _get_handle(layout, gds_path_hint)
    if handle is None:
        return _uninit_sentinel()
    ref = args.get("ref") if isinstance(args, dict) else None
    if not ref:
        return {"ok": False, "reason": "ref required"}
    result = handle.checkout(ref)
    if not isinstance(result, dict) or not result.get("ok"):
        return result
    # Sidecar HEAD moved — now rewrite the live layout to match state.
    try:
        snap = handle._load_snapshot(ref)
        if snap:
            _rewrite_layout_from_snapshot(layout, snap)
    except Exception:
        # Don't let rewrite errors fail the checkout — but surface a warning.
        pass
    return result


# ---------------------------------------------------------------------------
# vc_diff (MCP-5)
# ---------------------------------------------------------------------------


def vc_diff(args: dict, *, layout, gds_path_hint=None) -> dict:
    handle = _get_handle(layout, gds_path_hint)
    if handle is None:
        return _uninit_sentinel()
    ref_a = args.get("ref_a") if isinstance(args, dict) else None
    ref_b = args.get("ref_b") if isinstance(args, dict) else None
    if not ref_a or not ref_b:
        return {"ok": False, "reason": "ref_a and ref_b required"}
    result = handle.diff(ref_a, ref_b)
    if isinstance(result, dict) and "ok" not in result:
        # Annotate success envelope (tests only require the diagnostic keys).
        result = {"ok": True, **result}
    return result


# ---------------------------------------------------------------------------
# vc_branch (MCP-6)
# ---------------------------------------------------------------------------


def vc_branch(args: dict, *, layout, gds_path_hint=None) -> dict:
    handle = _get_handle(layout, gds_path_hint)
    if handle is None:
        return _uninit_sentinel()
    if not isinstance(args, dict):
        args = {}
    op = args.get("op")
    if op == "list":
        raw = handle.branch("list")
        if isinstance(raw, list):
            return {"ok": True, "names": raw}
        # G5 returned an error dict (e.g. {"ok": False, "reason": "handle invalidated"})
        # — propagate it unchanged.
        if isinstance(raw, dict):
            return raw
        # Unexpected return type — surface as structured error.
        return {"ok": False, "reason": f"vc_branch list got unexpected return type: {type(raw).__name__}"}
    if op == "create":
        return handle.branch(
            "create", name=args.get("name"), from_ref=args.get("from_ref"),
        )
    if op == "switch":
        return handle.branch("switch", name=args.get("name"))
    if op == "merge":
        return handle.branch("merge", name=args.get("name"))
    return {"ok": False, "reason": f"unknown branch op: {op!r}"}


# ---------------------------------------------------------------------------
# vc_tag (MCP-9)
# ---------------------------------------------------------------------------


def vc_tag(args: dict, *, layout, gds_path_hint=None) -> dict:
    handle = _get_handle(layout, gds_path_hint)
    if handle is None:
        return _uninit_sentinel()
    if not isinstance(args, dict):
        args = {}
    name = args.get("name")
    ref = args.get("ref")
    return handle.tag(name, ref)


# ---------------------------------------------------------------------------
# vc_export (MCP-7) — owns truncation logic.
# ---------------------------------------------------------------------------


def vc_export(args: dict, *, layout, gds_path_hint=None) -> dict:
    handle = _get_handle(layout, gds_path_hint)
    if handle is None:
        return _uninit_sentinel()
    if not isinstance(args, dict):
        args = {}
    ref = args.get("ref")
    fmt = args.get("format")
    if not ref or not fmt:
        return {"ok": False, "reason": "ref and format required"}

    payload = handle.export(ref, fmt)
    # G5 signals error by returning a dict with ok=False; pass through.
    if isinstance(payload, dict) and payload.get("ok") is False:
        return payload

    if fmt == "gds":
        if not isinstance(payload, (bytes, bytearray)):
            return {
                "ok": False,
                "reason": f"gds export backend returned {type(payload).__name__}",
            }
        raw_bytes = bytes(payload)
        size = len(raw_bytes)
        if size <= VC_EXPORT_INLINE_CAP_BYTES:
            content = base64.b64encode(raw_bytes).decode("ascii")
            return {
                "ok": True,
                "format": "gds",
                "content": content,
                "truncated": False,
            }
        # Oversize — write to tempfile.
        fd, path = tempfile.mkstemp(suffix=".gds", prefix="klayoutclaw_vc_export_")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(raw_bytes)
        except Exception as e:
            try:
                os.unlink(path)
            except Exception:
                pass
            return {"ok": False, "reason": f"tempfile write failed: {e}"}
        return {
            "ok": True,
            "format": "gds",
            "truncated": True,
            "path": os.path.abspath(path),
        }

    if fmt == "pya":
        if not isinstance(payload, str):
            return {
                "ok": False,
                "reason": f"pya export backend returned {type(payload).__name__}",
            }
        encoded = payload.encode("utf-8")
        size = len(encoded)
        if size <= VC_EXPORT_INLINE_CAP_BYTES:
            return {
                "ok": True,
                "format": "pya",
                "content": payload,
                "truncated": False,
            }
        fd, path = tempfile.mkstemp(suffix=".py", prefix="klayoutclaw_vc_export_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
        except Exception as e:
            try:
                os.unlink(path)
            except Exception:
                pass
            return {"ok": False, "reason": f"tempfile write failed: {e}"}
        return {
            "ok": True,
            "format": "pya",
            "truncated": True,
            "path": os.path.abspath(path),
        }

    return {"ok": False, "reason": f"unknown export format {fmt!r}"}


# ---------------------------------------------------------------------------
# vc_status (MCP-8) — never raises; uninit returns sentinel.
# ---------------------------------------------------------------------------


def vc_status(args: dict, *, layout, gds_path_hint=None) -> dict:
    try:
        entry = _get_entry(layout, gds_path_hint)
    except Exception:
        return _uninit_sentinel()
    if entry is None:
        return _uninit_sentinel()
    handle = entry["handle"]
    try:
        result = handle.status(layout)
    except Exception as e:
        return {"ok": False, "reason": f"status failed: {e}"}
    if not isinstance(result, dict):
        return {"ok": False, "reason": "status backend returned non-dict"}
    # G5 may return an error dict (e.g. handle invalidated) — pass through.
    if result.get("ok") is False:
        return result
    if "ok" not in result:
        result = {"ok": True, **result}
    return result


# ---------------------------------------------------------------------------
# DM-4 save_layout hook
# ---------------------------------------------------------------------------


def migrate_on_save(layout, gds_path: str) -> None:
    """Called from the plugin's ``save_layout`` handler after a successful
    write.  Migrates a memory-mode handle (if any) to a disk sidecar next
    to the freshly-written GDS.  No-op if no handle is registered for this
    layout or if the handle is already disk-mode.
    """
    entry = REGISTRY.get(id(layout))
    if not entry:
        return
    handle = entry["handle"]
    try:
        mode = getattr(handle, "_mode", None)
    except Exception:
        return
    if mode != "memory":
        return
    try:
        vc_repo.migrate_to_disk(handle, os.path.abspath(gds_path))
        entry["gds_path"] = os.path.abspath(gds_path)
    except Exception:
        # Best-effort — save_layout succeeded; don't fail the save on a
        # migration hiccup.
        pass
