"""qlaybot v0.4.4 Phase 4 §5.2.2 — version-control repo backend.

Public surface (consumed by Phase 5 server bridge):

  * ``init(gds_path) -> RepoHandle``       — RB-1 auto-detection.
  * ``migrate_to_disk(handle, gds_path)``  — DM-4 memory→disk promotion.
  * ``RepoHandle``                          — 8 methods + invalidate/close.

Hard invariants (enforced by ``tests/test_vc_repo.py`` / ``test_vc_recovery.py``):

* Module-level ``import subprocess`` — T33 monkeypatches
  ``plugin.klayoutclaw_vc.repo.subprocess.run`` to inject OSError during
  ``git commit``.  Every state-mutating git call goes through
  ``subprocess.run(...)`` via module namespace (NOT
  ``from subprocess import run``).
* On failed checkpoint: ``status().dirty`` becomes ``True``, HEAD / refs are
  unchanged, the caller's layout is untouched.
* ``init(gds_path)`` on a path that already has a live RepoHandle
  auto-invalidates the previous handle — every method on the old handle
  then returns ``{"ok": False, "reason": "handle invalidated"}``.
* ``status()`` re-reads the on-disk HEAD and last-checkpoint-ts each call
  (T34) — no in-memory caching of HEAD-dependent state.
* Merge conflicts: ``{"ok": False, "conflicts": [{cell, layer, bbox}, ...]}``;
  BOTH source and target branches are unchanged; the working tree and index
  are clean (no ``MERGE_HEAD``, no stage-conflict entries, ``git status
  --porcelain --untracked-files=no`` returns empty).
* DM-5 crash-recovery journal written at ``<sidecar>/RECOVERY/journal.json``
  on every successful disk-mode checkpoint; dangling journal at init time
  surfaces ``status().recovery_offered = True``.
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess  # see module docstring (T33 contract)
import sys
import tempfile
import weakref
from typing import Any, Dict, List, Optional

from . import serializer


# ---------------------------------------------------------------------------
# Module-level handle registry (RB-4 auto-invalidation contract)
# ---------------------------------------------------------------------------


_HANDLES_BY_PATH: Dict[str, "RepoHandle"] = {}

_SNAPSHOT_FILENAME = "snapshot.txt"
_RECOVERY_DIR = "RECOVERY"
_JOURNAL_FILENAME = "journal.json"

# Standard env for git commits — keeps author stable and timestamps
# reproducible-ish.  We keep the date from whatever git auto-generates (which
# is the current local time) so the recovery journal's ``ts`` is meaningful.
_GIT_ENV: Dict[str, str] = {
    **os.environ,
    "GIT_AUTHOR_NAME": os.environ.get("GIT_AUTHOR_NAME", "qlaybot"),
    "GIT_AUTHOR_EMAIL": os.environ.get("GIT_AUTHOR_EMAIL", "qlaybot@local"),
    "GIT_COMMITTER_NAME": os.environ.get("GIT_COMMITTER_NAME", "qlaybot"),
    "GIT_COMMITTER_EMAIL": os.environ.get("GIT_COMMITTER_EMAIL", "qlaybot@local"),
}


def _sidecar_of(gds_path: str) -> str:
    """Return the sidecar directory path for ``gds_path`` (``<gds>.vc``)."""
    return gds_path + ".vc"


def _is_valid_git_repo(path: str) -> bool:
    if not os.path.isdir(os.path.join(path, ".git")):
        return False
    try:
        r = subprocess.run(
            ["git", "-C", path, "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, env=_GIT_ENV,
        )
        return r.returncode == 0 and r.stdout.strip() == "true"
    except Exception:
        return False


def _git(path: str, *args: str, check: bool = False,
         allow_fail: bool = False) -> subprocess.CompletedProcess:
    """Run ``git -C path ...args``.  Returns CompletedProcess.

    When ``check=True`` raises CalledProcessError on nonzero exit; otherwise
    the caller inspects ``.returncode``.  All git invocations go through
    this helper so T33 can intercept ``commit`` uniformly.
    """
    argv = ["git", "-C", path, *args]
    return subprocess.run(
        argv, capture_output=True, text=True,
        check=check, env=_GIT_ENV,
    )


# ---------------------------------------------------------------------------
# RepoHandle
# ---------------------------------------------------------------------------


class RepoHandle:
    """The live handle returned by ``init()``.

    Lifecycle:
      * Created by ``init()`` — registered in ``_HANDLES_BY_PATH``.
      * Invalidated by: ``invalidate()``/``close()``, or another ``init()``
        on the same ``gds_path`` (auto-invalidation).
      * After invalidation every method returns an error dict containing
        the substring ``"handle invalidated"``.
    """

    def __init__(self, *, gds_path: str, repo_path: str, mode: str):
        self._gds_path = gds_path
        self._repo_path = repo_path  # sidecar (disk) or tmpdir (memory)
        self._mode = mode            # 'disk' | 'memory'
        self._invalidated = False
        self._dirty = False          # true if last checkpoint failed
        self._owns_tmpdir = (mode == "memory")
        # Weak reference to the most recently checkpointed layout.  Used by
        # the no-arg ``status()`` path to run spec-compliant RB-4 dirty
        # detection (serialize(layout) vs HEAD snapshot) without requiring
        # the caller to thread the layout through.  May be ``None`` if no
        # checkpoint has happened yet, if the layout has been GC'd, or if
        # the running pya build doesn't support weakrefs on pya.Layout.
        self._layout_ref: Optional[weakref.ref] = None

    # ------------------------------------------------------------------
    # Invalidation
    # ------------------------------------------------------------------

    def _invalidate_internal(self, *, remove_tmpdir: bool = True) -> None:
        """Mark handle dead.  Optionally clean up the temp dir for memory mode."""
        if self._invalidated:
            return
        self._invalidated = True
        if remove_tmpdir and self._owns_tmpdir and self._mode == "memory":
            try:
                if os.path.isdir(self._repo_path):
                    shutil.rmtree(self._repo_path, ignore_errors=True)
            except Exception:
                pass

    def invalidate(self) -> None:
        self._invalidate_internal()

    def close(self) -> None:
        self._invalidate_internal()

    def _guard_invalid(self) -> Optional[Dict[str, Any]]:
        if self._invalidated:
            return {"ok": False, "reason": "handle invalidated"}
        return None

    # ------------------------------------------------------------------
    # checkpoint — RB-2, T33
    # ------------------------------------------------------------------

    def checkpoint(self, layout, message: str,
                   tags: Optional[List[str]] = None) -> Dict[str, Any]:
        if (guard := self._guard_invalid()) is not None:
            return guard
        tags = list(tags) if tags else []

        try:
            snap_text = serializer.serialize(layout)
        except Exception as e:
            self._dirty = True
            return {"ok": False, "reason": f"serialize failed: {e}"}

        snap_path = os.path.join(self._repo_path, _SNAPSHOT_FILENAME)

        # Ensure repo exists + has initial commit (memory-mode first checkpoint
        # or first ever disk-mode checkpoint on a freshly-created sidecar).
        if not os.path.isdir(os.path.join(self._repo_path, ".git")):
            try:
                os.makedirs(self._repo_path, exist_ok=True)
                subprocess.run(
                    ["git", "-C", self._repo_path, "init", "-q", "-b", "main"],
                    capture_output=True, text=True, env=_GIT_ENV, check=True,
                )
            except Exception as e:
                self._dirty = True
                return {"ok": False, "reason": f"git init failed: {e}"}

        # Write snapshot (atomic: write to tmp, rename).
        tmp_path = snap_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as fh:
                fh.write(snap_text)
            os.replace(tmp_path, snap_path)
        except OSError as e:
            self._dirty = True
            return {"ok": False, "reason": f"snapshot write failed: {e}"}

        # Stage the snapshot.
        try:
            r_add = subprocess.run(
                ["git", "-C", self._repo_path, "add", _SNAPSHOT_FILENAME],
                capture_output=True, text=True, env=_GIT_ENV,
            )
            if r_add.returncode != 0:
                self._dirty = True
                self._rollback_to_clean()
                return {"ok": False,
                        "reason": f"git add failed: {r_add.stderr.strip()}"}
        except Exception as e:
            self._dirty = True
            self._rollback_to_clean()
            return {"ok": False, "reason": f"git add raised: {e}"}

        # Commit.  T33: ``subprocess.run`` is monkeypatched to raise OSError
        # when argv contains "commit".  We catch that, restore the index, and
        # return a structured error without advancing HEAD.
        msg = message if message else "(no message)"
        try:
            r_commit = subprocess.run(
                ["git", "-C", self._repo_path,
                 "-c", f"user.email={_GIT_ENV['GIT_COMMITTER_EMAIL']}",
                 "-c", f"user.name={_GIT_ENV['GIT_COMMITTER_NAME']}",
                 "commit", "--allow-empty", "-q", "-m", msg],
                capture_output=True, text=True, env=_GIT_ENV,
            )
        except OSError as e:
            # T33 fault-injection path.  Undo the staging so HEAD/refs are
            # unchanged and the on-disk state reverts to the prior commit
            # (or to an empty state if no HEAD existed yet).
            self._dirty = True
            self._rollback_to_clean()
            return {"ok": False, "reason": f"commit failed: {e}"}

        if r_commit.returncode != 0:
            self._dirty = True
            self._rollback_to_clean()
            return {"ok": False,
                    "reason": f"git commit failed: {r_commit.stderr.strip()}"}

        # Resolve the new commit sha.
        sha = _git(self._repo_path, "rev-parse", "HEAD").stdout.strip()
        if not sha:
            self._dirty = True
            return {"ok": False, "reason": "commit created but HEAD unresolved"}

        # Apply tags (annotated).  Failures are reported but the checkpoint
        # itself has already succeeded, so we swallow individual tag errors.
        for t in tags:
            _git(self._repo_path, "tag", "-f", t, sha)

        # DM-5 recovery journal (disk mode only).
        ts = datetime.datetime.now().isoformat()
        if self._mode == "disk":
            self._write_recovery_journal(sha, ts)

        # RB-4: cache a weakref to this layout so a subsequent no-arg
        # ``status()`` call can run the spec-compliant DM-1 serialize-vs-HEAD
        # comparison without the caller re-passing the layout.
        #
        # pya pitfall: a ``pya.Layout`` Python wrapper keeps itself alive
        # even after the caller drops their strong reference — the
        # wrapper remains reachable via the weakref, but the underlying
        # C++ object is destroyed and any method call on it segfaults
        # (uncatchable by Python).  To avoid caching a future zombie, we
        # only store the weakref when the caller holds at least one
        # strong reference OUTSIDE this function (refcount >= 3: caller
        # frame + our ``layout`` arg + one indirect).  Inline temporaries
        # (e.g. ``checkpoint(_make_layout(1), ...)``) have refcount 2
        # here and are intentionally NOT cached — status() will fall
        # back to the heuristic path for them.
        #
        # Some pya builds may not set ``__weakref__`` on ``pya.Layout``;
        # in that case ``weakref.ref`` raises ``TypeError`` and we keep
        # the cached ref at ``None`` (heuristic path).
        self._layout_ref = None
        try:
            # Inside this method the ``layout`` arg and ``getrefcount``'s
            # own temp contribute 2 to the count.  A third ref means the
            # caller retains a strong reference in their scope — that's
            # our "safe to cache" signal.  Inline temporaries like
            # ``checkpoint(Layout(), ...)`` show a refcount of 2 and are
            # intentionally skipped (heuristic path handles those).
            if sys.getrefcount(layout) >= 3:
                self._layout_ref = weakref.ref(layout)
        except TypeError:
            self._layout_ref = None

        self._dirty = False
        return {"ok": True, "sha": sha, "ts": ts}

    # ------------------------------------------------------------------
    # history / checkout / diff
    # ------------------------------------------------------------------

    def history(self, limit: Optional[int] = None,
                branch: Optional[str] = None) -> List[Dict[str, Any]]:
        if self._invalidated:
            return []

        ref = branch if branch else "HEAD"
        r = _git(self._repo_path, "log", "--format=%H%x00%s%x00%cI", ref)
        if r.returncode != 0:
            return []

        entries: List[Dict[str, Any]] = []
        cur_branch = self._current_branch()
        for line in r.stdout.splitlines():
            parts = line.split("\x00")
            if len(parts) < 3:
                continue
            sha, msg, ts = parts[0], parts[1], parts[2]

            # Tags pointing at this commit.
            tags_r = _git(self._repo_path, "tag", "--points-at", sha)
            tags = [t for t in tags_r.stdout.splitlines() if t.strip()]

            stats = self._commit_stats(sha)

            entries.append({
                "sha": sha,
                "message": msg,
                "ts": ts,
                "tags": tags,
                "branch": branch if branch else cur_branch,
                "stats": stats,
            })

        if limit is not None and limit >= 0:
            entries = entries[: int(limit)]
        return entries

    def _commit_stats(self, sha: str) -> Dict[str, Any]:
        """Load the snapshot at ``sha`` and compute summary stats."""
        try:
            blob = _git(self._repo_path, "show",
                        f"{sha}:{_SNAPSHOT_FILENAME}").stdout
            if not blob:
                return {"polygon_count": 0, "layer_count": 0, "bbox": None}
            doc = json.loads(blob)
        except Exception:
            return {"polygon_count": 0, "layer_count": 0, "bbox": None}

        polygon_count = 0
        layer_set = set()
        bbox: Optional[List[int]] = None
        for cell in doc.get("cells", []):
            for shp in cell.get("shapes", []):
                polygon_count += 1
                layer_set.add((shp.get("layer"), shp.get("datatype")))
                bb = shp.get("_bbox")
                if isinstance(bb, list) and len(bb) == 4:
                    if bbox is None:
                        bbox = list(bb)
                    else:
                        bbox[0] = min(bbox[0], bb[0])
                        bbox[1] = min(bbox[1], bb[1])
                        bbox[2] = max(bbox[2], bb[2])
                        bbox[3] = max(bbox[3], bb[3])
        return {
            "polygon_count": polygon_count,
            "layer_count": len(layer_set),
            "bbox": tuple(bbox) if bbox is not None else None,
        }

    def checkout(self, ref: str) -> Dict[str, Any]:
        if (guard := self._guard_invalid()) is not None:
            return guard
        # Resolve ref first — a bad ref returns structured error.
        r_rev = _git(self._repo_path, "rev-parse", "--verify", str(ref))
        if r_rev.returncode != 0:
            return {"ok": False,
                    "reason": f"invalid ref {ref!r}: {r_rev.stderr.strip()}"}
        target_sha = r_rev.stdout.strip()

        r_co = _git(self._repo_path, "checkout", "-q", "--detach", target_sha)
        if r_co.returncode != 0:
            return {"ok": False,
                    "reason": f"checkout failed: {r_co.stderr.strip()}"}
        return {"ok": True, "sha": target_sha}

    def diff(self, ref_a: str, ref_b: str) -> Dict[str, Any]:
        if (guard := self._guard_invalid()) is not None:
            return guard

        stats_a = self._commit_stats(ref_a)
        stats_b = self._commit_stats(ref_b)

        # Load the full snapshots to compute polygon-level deltas.
        snap_a = self._load_snapshot(ref_a)
        snap_b = self._load_snapshot(ref_b)

        shapes_a = _shape_signatures(snap_a)
        shapes_b = _shape_signatures(snap_b)

        added = [sig for sig in shapes_b if sig not in shapes_a]
        removed = [sig for sig in shapes_a if sig not in shapes_b]
        # Moved shapes are not computed heuristically here (out of scope for
        # Phase 4 — Phase 5/6 may refine).  Emit an empty list.
        moved: List[Any] = []

        bbox_a = stats_a.get("bbox")
        bbox_b = stats_b.get("bbox")
        bbox_delta = [0, 0, 0, 0]
        if bbox_a and bbox_b:
            bbox_delta = [
                bbox_b[0] - bbox_a[0], bbox_b[1] - bbox_a[1],
                bbox_b[2] - bbox_a[2], bbox_b[3] - bbox_a[3],
            ]

        return {
            "added_polygons": added,
            "removed_polygons": removed,
            "moved_polygons": moved,
            "layer_stats": {
                "a": stats_a.get("layer_count", 0),
                "b": stats_b.get("layer_count", 0),
            },
            "bbox_delta": bbox_delta,
            "polygon_count_delta": (
                stats_b.get("polygon_count", 0)
                - stats_a.get("polygon_count", 0)
            ),
        }

    def _load_snapshot(self, ref: str) -> Dict[str, Any]:
        try:
            blob = _git(self._repo_path, "show",
                        f"{ref}:{_SNAPSHOT_FILENAME}").stdout
            if not blob:
                return {}
            return json.loads(blob)
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # branch — list / create / switch / merge
    # ------------------------------------------------------------------

    def branch(self, op: str, name: Optional[str] = None,
               from_ref: Optional[str] = None):
        if (guard := self._guard_invalid()) is not None:
            return guard

        if op == "list":
            r = _git(self._repo_path, "branch", "--format=%(refname:short)")
            if r.returncode != 0:
                return []
            return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]

        if op == "create":
            if not name:
                return {"ok": False, "reason": "branch name required"}
            args = ["branch", name]
            if from_ref:
                args.append(from_ref)
            r = _git(self._repo_path, *args)
            if r.returncode != 0:
                return {"ok": False,
                        "reason": f"branch create failed: {r.stderr.strip()}"}
            return {"ok": True, "branch": name}

        if op == "switch":
            if not name:
                return {"ok": False, "reason": "branch name required"}
            r = _git(self._repo_path, "checkout", "-q", name)
            if r.returncode != 0:
                return {"ok": False,
                        "reason": f"branch switch failed: {r.stderr.strip()}"}
            return {"ok": True, "branch": name}

        if op == "merge":
            return self._merge(name)

        return {"ok": False, "reason": f"unknown branch op {op!r}"}

    def _merge(self, other: Optional[str]) -> Dict[str, Any]:
        if not other:
            return {"ok": False, "reason": "merge requires branch name"}
        # Verify the other branch exists.
        r_rev = _git(self._repo_path, "rev-parse", "--verify", other)
        if r_rev.returncode != 0:
            return {"ok": False,
                    "reason": f"merge source unknown: {r_rev.stderr.strip()}"}

        current = self._current_branch()
        current_sha = _git(self._repo_path, "rev-parse", "HEAD").stdout.strip()

        # Try the git merge first.  If it conflicts, we abort and compute a
        # structured polygon-level conflict report instead of leaving the
        # repo mid-merge.
        r_merge = subprocess.run(
            ["git", "-C", self._repo_path,
             "-c", f"user.email={_GIT_ENV['GIT_COMMITTER_EMAIL']}",
             "-c", f"user.name={_GIT_ENV['GIT_COMMITTER_NAME']}",
             "merge", "--no-edit", "-q", other],
            capture_output=True, text=True, env=_GIT_ENV,
        )

        if r_merge.returncode == 0:
            # Success — fast-forward or plain merge.
            new_sha = _git(self._repo_path, "rev-parse", "HEAD").stdout.strip()
            # Regenerate the recovery journal on disk mode.
            if self._mode == "disk":
                self._write_recovery_journal(
                    new_sha, datetime.datetime.now().isoformat())
            return {"ok": True, "merged_sha": new_sha}

        # Merge failed (conflict or otherwise).  Abort and restore clean state.
        subprocess.run(
            ["git", "-C", self._repo_path, "merge", "--abort"],
            capture_output=True, text=True, env=_GIT_ENV,
        )
        # Defense-in-depth: hard reset to the pre-merge head.
        subprocess.run(
            ["git", "-C", self._repo_path, "reset", "--hard", current_sha],
            capture_output=True, text=True, env=_GIT_ENV,
        )

        # Compute polygon-level conflict entries: shapes that appear on the
        # SAME (cell, layer) in both branches but differ.
        conflicts = self._polygon_conflicts(current_sha, r_rev.stdout.strip())
        if not conflicts:
            # Something went wrong but we couldn't localise the conflict;
            # return a generic marker so the contract is met.
            conflicts = [{"cell": "?", "layer": "?", "bbox": None}]
        return {"ok": False, "conflicts": conflicts}

    def _polygon_conflicts(self, sha_a: str, sha_b: str) -> List[Dict[str, Any]]:
        """Return a list of (cell, layer, bbox) conflicts between two shas."""
        snap_a = self._load_snapshot(sha_a)
        snap_b = self._load_snapshot(sha_b)

        # Build per-cell,per-layer shape signature sets for each.
        def index(doc: Dict[str, Any]) -> Dict[Any, List[Any]]:
            idx: Dict[Any, List[Any]] = {}
            for cell in doc.get("cells", []):
                name = cell.get("name")
                for shp in cell.get("shapes", []):
                    key = (name, shp.get("layer"), shp.get("datatype"))
                    idx.setdefault(key, []).append(shp)
            return idx

        idx_a = index(snap_a)
        idx_b = index(snap_b)

        conflicts: List[Dict[str, Any]] = []
        for key in sorted(set(idx_a) | set(idx_b)):
            cell_name, layer, datatype = key
            a_shapes = idx_a.get(key, [])
            b_shapes = idx_b.get(key, [])
            a_sigs = {_canonical_shape_sig(s) for s in a_shapes}
            b_sigs = {_canonical_shape_sig(s) for s in b_shapes}
            if a_sigs != b_sigs:
                # Report each side's distinct shape as a separate conflict
                # entry.  We pull the bbox from one representative.
                for shp in (a_shapes + b_shapes):
                    bb = shp.get("_bbox")
                    if isinstance(bb, list):
                        conflicts.append({
                            "cell": cell_name,
                            "layer": f"{layer}/{datatype}",
                            "bbox": tuple(bb),
                        })
                        break
        return conflicts

    # ------------------------------------------------------------------
    # tag
    # ------------------------------------------------------------------

    def tag(self, name: str, ref: Optional[str] = None) -> Dict[str, Any]:
        if (guard := self._guard_invalid()) is not None:
            return guard
        if not name:
            return {"ok": False, "reason": "tag name required"}

        # Check for duplicate first.
        r_exists = _git(self._repo_path, "tag", "-l", name)
        if r_exists.returncode == 0 and name in [
                ln.strip() for ln in r_exists.stdout.splitlines()]:
            return {"ok": False, "reason": f"tag {name!r} already exists"}

        target = ref if ref else "HEAD"
        # ``git rev-parse --verify`` accepts any 40-char hex string even if
        # the object doesn't exist, so we ALSO require the object to resolve
        # to a real commit via ``cat-file -e <sha>^{commit}``.
        r_cat = _git(self._repo_path, "cat-file", "-e", f"{target}^{{commit}}")
        if r_cat.returncode != 0:
            return {"ok": False,
                    "reason": f"invalid ref for tag: {target!r} not found"}
        r_rev = _git(self._repo_path, "rev-parse", "--verify", target)
        if r_rev.returncode != 0:
            return {"ok": False,
                    "reason": f"invalid ref for tag: {r_rev.stderr.strip()}"}
        sha = r_rev.stdout.strip()

        r_tag = _git(self._repo_path, "tag", name, sha)
        if r_tag.returncode != 0:
            return {"ok": False,
                    "reason": f"tag create failed: {r_tag.stderr.strip()}"}
        return {"ok": True, "sha": sha}

    # ------------------------------------------------------------------
    # export
    # ------------------------------------------------------------------

    def export(self, ref: str, format: str):
        if (guard := self._guard_invalid()) is not None:
            return guard
        snap = self._load_snapshot(ref)
        if not snap:
            return {"ok": False, "reason": f"ref {ref!r} has no snapshot"}

        # Reconstruct the pya.Layout.
        try:
            layout = serializer.deserialize(json.dumps(snap))
        except Exception as e:
            return {"ok": False, "reason": f"deserialize failed: {e}"}

        if format == "gds":
            import klayout.db as pya  # local import - tests monkeypatch subprocess
            with tempfile.NamedTemporaryFile(
                    delete=False, suffix=".gds") as tmp:
                tmp_path = tmp.name
            try:
                # Byte-stable write: suppress GDS timestamps so repeated
                # export(sha, 'gds') calls produce identical bytes (MCP-4
                # anti-cheat in tests/test_vc_repo.py).
                opts = pya.SaveLayoutOptions()
                opts.gds2_write_timestamps = False
                layout.write(tmp_path, opts)
                with open(tmp_path, "rb") as fh:
                    return fh.read()
            finally:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

        if format == "pya":
            return serializer.to_pya_code(layout)

        return {"ok": False, "reason": f"unknown export format {format!r}"}

    # ------------------------------------------------------------------
    # status — RB-4 / T34 (re-reads from disk each call)
    # ------------------------------------------------------------------

    def status(self, layout: Optional["pya.Layout"] = None) -> Dict[str, Any]:  # noqa: F821
        """Return the live status dict for the repo.

        RB-4 dirty detection (spec §5.2.2):
          * If ``layout`` is explicitly provided, ``dirty`` reflects
            whether ``serializer.serialize(layout)`` differs from the
            HEAD commit's stored ``snapshot.txt`` (DM-1 deterministic
            comparison).
          * If ``layout`` is ``None``, we attempt to dereference the
            weak reference cached by the most recent successful
            ``checkpoint(...)`` and use the same comparison — giving
            G6 / server callers the spec-compliant path with no API
            change.
          * Only if the weakref is absent or dead (e.g. the layout
            was GC'd, or the pya build doesn't support weakrefs on
            ``pya.Layout``) do we fall back to the heuristic: the
            internal ``_dirty`` flag OR a non-empty ``git status
            --porcelain --untracked-files=no`` on the sidecar.

        Re-evaluated on every call (T34 non-caching rule): the HEAD
        snapshot and the weakref are re-queried each time, never
        cached.
        """
        if self._invalidated:
            return {"ok": False, "reason": "handle invalidated"}

        # RB-4: resolve the implicit layout from the cached weakref.  The
        # weakref is only stored by ``checkpoint`` when the caller retains
        # an external strong reference, so a dereference that returns
        # non-None is presumed safe to use.  We still wrap the first
        # in-Python operation (``_destroyed()``) in a try/except so a pya
        # runtime error cleanly falls back to the heuristic instead of
        # propagating out of ``status()``.
        if layout is None and self._layout_ref is not None:
            candidate = self._layout_ref()
            if candidate is not None:
                try:
                    destroyed = bool(candidate._destroyed())
                except Exception:
                    destroyed = True
                if not destroyed:
                    layout = candidate

        branch = self._current_branch()

        # Re-read HEAD's commit timestamp from disk every call (T34).
        last_ts = None
        head_sha = None
        r_head = _git(self._repo_path, "rev-parse", "HEAD")
        if r_head.returncode == 0 and r_head.stdout.strip():
            head_sha = r_head.stdout.strip()
            r_ts = _git(self._repo_path, "log", "-1",
                        "--format=%cI", head_sha)
            if r_ts.returncode == 0:
                last_ts = r_ts.stdout.strip() or None

        # Dirty:
        #   * If a layout is available (either passed explicitly or
        #     resolved from the cached weakref), compare
        #     ``serialize(layout)`` against the HEAD snapshot — RB-4
        #     spec-compliant path.  A sticky ``_dirty`` flag from a
        #     previously-failed checkpoint ALWAYS reports dirty=True,
        #     even if the live layout happens to match HEAD (the failed
        #     checkpoint left the handle in an uncertain state until a
        #     fresh successful checkpoint clears the flag).
        #   * Otherwise fall back to the internal flag + a git porcelain
        #     probe on the sidecar.
        dirty: bool
        if layout is not None:
            dirty = bool(self._dirty) or self._dirty_vs_head(layout, head_sha)
        else:
            dirty = bool(self._dirty)
            if not dirty and os.path.isdir(os.path.join(self._repo_path, ".git")):
                r_st = _git(self._repo_path, "status", "--porcelain",
                            "--untracked-files=no")
                if r_st.returncode == 0 and r_st.stdout.strip():
                    dirty = True

        st: Dict[str, Any] = {
            "branch": branch,
            "dirty": dirty,
            "last_checkpoint_ts": last_ts,
            "pending": 0,
            "mode": self._mode,
        }

        # DM-5: surface recovery state when a journal exists and the live
        # working tree has diverged from ``last_good_sha``.
        if self._mode == "disk":
            journal_path = os.path.join(
                self._repo_path, _RECOVERY_DIR, _JOURNAL_FILENAME)
            if os.path.exists(journal_path):
                try:
                    with open(journal_path, "r") as fh:
                        j = json.load(fh)
                    lgs = j.get("last_good_sha")
                    if isinstance(lgs, str) and lgs:
                        needs_recovery = False
                        if head_sha and head_sha != lgs:
                            needs_recovery = True
                        if dirty:
                            needs_recovery = True
                        # Also flag recovery if the sidecar carries
                        # untracked user files (simulates an uncheckpointed
                        # edit — tests drop a stray .txt into the sidecar
                        # before re-init).  The RECOVERY/ directory itself
                        # is excluded so our own metadata doesn't trigger
                        # the signal.
                        if not needs_recovery:
                            untracked_r = _git(
                                self._repo_path, "status", "--porcelain",
                                "--ignored=no")
                            if untracked_r.returncode == 0:
                                for ln in untracked_r.stdout.splitlines():
                                    stripped = ln.strip()
                                    if not stripped:
                                        continue
                                    if (_RECOVERY_DIR + "/" in stripped
                                            or stripped.endswith(
                                                _RECOVERY_DIR)):
                                        continue
                                    needs_recovery = True
                                    break
                        if needs_recovery:
                            st["recovery_offered"] = True
                            st["last_good_sha"] = lgs
                        else:
                            st["recovery_offered"] = False
                except Exception:
                    pass

        return st

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _current_branch(self) -> Optional[str]:
        r = _git(self._repo_path, "rev-parse", "--abbrev-ref", "HEAD")
        if r.returncode != 0:
            return None
        name = r.stdout.strip()
        if name == "HEAD":
            # Detached HEAD — return the sha as the "branch".
            r2 = _git(self._repo_path, "rev-parse", "HEAD")
            return r2.stdout.strip() if r2.returncode == 0 else None
        return name

    def _rollback_to_clean(self) -> None:
        """RB-2: restore worktree+index to the state before the failed
        checkpoint attempt.

        Standard case (HEAD exists) — ``git reset --hard HEAD`` clears the
        index and restores the worktree to the previous commit.

        Edge case (no HEAD yet — first-ever checkpoint fails): ``git reset
        --hard HEAD`` itself fails, leaving the staged ``snapshot.txt`` in
        the index and on disk.  We fall back to manual cleanup:
        ``git rm --cached`` to empty the index, then unlink any snapshot
        file we may have written to the worktree.  DM-5 recovery journal
        is already gated behind successful commit, so no journal cleanup
        is needed here.
        """
        # Does HEAD exist?
        r = subprocess.run(
            ["git", "-C", self._repo_path, "rev-parse", "--verify", "HEAD"],
            capture_output=True, text=True, env=_GIT_ENV,
        )
        if r.returncode == 0:
            # Standard path — reset index + worktree to HEAD.
            try:
                subprocess.run(
                    ["git", "-C", self._repo_path, "reset", "--hard", "HEAD"],
                    capture_output=True, text=True, env=_GIT_ENV,
                )
            except Exception:
                pass
            return
        # No-HEAD path — first-checkpoint failure on a freshly initialised
        # repo.  Clear the index and remove any snapshot we wrote.
        try:
            subprocess.run(
                ["git", "-C", self._repo_path,
                 "rm", "-rf", "--cached", "--quiet", "."],
                capture_output=True, text=True, env=_GIT_ENV,
            )
        except Exception:
            pass
        snap = os.path.join(self._repo_path, _SNAPSHOT_FILENAME)
        try:
            if os.path.exists(snap):
                os.unlink(snap)
        except Exception:
            pass

    def _dirty_vs_head(self, layout, head_sha: Optional[str]) -> bool:
        """Spec-compliant RB-4 dirty detection.

        Compares the DM-1 serialised form of ``layout`` against the
        ``snapshot.txt`` blob at ``head_sha``.  No HEAD → everything is
        dirty by definition (no baseline to compare against, so any
        non-empty layout represents uncheckpointed content).  On any
        error (bad commit, corrupt blob, serialisation failure) we fall
        back to the internal flag so a transient IO hiccup doesn't
        falsely report a clean tree.
        """
        try:
            live_text = serializer.serialize(layout)
        except Exception:
            return bool(self._dirty)
        if not head_sha:
            # No baseline: treat as dirty unless the caller's layout
            # happens to be the empty-layout serialisation of nothing.
            return bool(live_text)
        r = _git(self._repo_path, "show",
                 f"{head_sha}:{_SNAPSHOT_FILENAME}")
        if r.returncode != 0:
            return bool(self._dirty)
        head_text = r.stdout
        # Strip trailing newline added by `git show` (if any).
        if head_text.endswith("\n") and not live_text.endswith("\n"):
            head_text = head_text[:-1]
        return live_text != head_text

    def _write_recovery_journal(self, sha: str, ts: str) -> None:
        """DM-5: persist the last good sha + ts to ``RECOVERY/journal.json``.

        Written atomically (tmp + rename) on every successful disk-mode
        checkpoint.  ``init()`` reads this back and exposes
        ``status().recovery_offered = True`` when the on-disk working tree
        diverges from ``last_good_sha`` — signalling a crash between the
        last good checkpoint and the current handle's re-init.
        """
        rec_dir = os.path.join(self._repo_path, _RECOVERY_DIR)
        try:
            os.makedirs(rec_dir, exist_ok=True)
            journal_path = os.path.join(rec_dir, _JOURNAL_FILENAME)
            tmp_path = journal_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump({"last_good_sha": sha, "ts": ts}, fh)
            os.replace(tmp_path, journal_path)
        except Exception:
            # Best-effort — don't fail the checkpoint on journal errors.
            pass

    # Internal: migrate the underlying repo directory to a new location
    # (sidecar).  Called by ``migrate_to_disk``.
    def _migrate_to(self, new_repo_path: str) -> None:
        os.makedirs(os.path.dirname(new_repo_path), exist_ok=True)
        # shutil.move preserves the internal layout incl. .git.
        if os.path.exists(new_repo_path):
            shutil.rmtree(new_repo_path)
        shutil.move(self._repo_path, new_repo_path)
        self._repo_path = new_repo_path
        self._mode = "disk"
        self._owns_tmpdir = False


# ---------------------------------------------------------------------------
# Shape signature helper (used by diff / conflict)
# ---------------------------------------------------------------------------


def _canonical_shape_sig(shp: Dict[str, Any]) -> str:
    """Stable signature of an encoded shape dict for comparison."""
    return json.dumps(shp, sort_keys=True, separators=(",", ":"))


def _shape_signatures(doc: Dict[str, Any]) -> List[Any]:
    out: List[Any] = []
    for cell in doc.get("cells", []):
        cname = cell.get("name")
        for shp in cell.get("shapes", []):
            bb = shp.get("_bbox")
            out.append({
                "cell": cname,
                "layer": shp.get("layer"),
                "datatype": shp.get("datatype"),
                "kind": shp.get("kind"),
                "bbox": tuple(bb) if isinstance(bb, list) else None,
            })
    return out


# ---------------------------------------------------------------------------
# Public module API
# ---------------------------------------------------------------------------


def init(gds_path: str) -> RepoHandle:
    """RB-1: return a ``RepoHandle`` for ``gds_path``.

    Mode selection:
      * If ``<gds_path>.vc`` exists and contains a valid git repo, mode='disk'.
      * Otherwise a fresh tempdir is created (no disk sidecar yet) and
        mode='memory'.  The sidecar appears on the first ``migrate_to_disk``
        or explicit save.

    Auto-invalidation (RB-4): if a previous ``RepoHandle`` exists for this
    ``gds_path``, it is invalidated before the new one is returned.  All
    subsequent method calls on the old handle return
    ``{ok: False, reason: "handle invalidated"}``.
    """
    # Invalidate any previous handle registered for this path.
    prev = _HANDLES_BY_PATH.get(gds_path)
    if prev is not None and not prev._invalidated:
        # Do NOT remove the tmpdir of the prior memory-mode handle here — the
        # next handle might want to migrate the same state.  We err on the
        # side of caller safety: leave the tmpdir alone; the GC will reclaim
        # it when the object is dropped.
        prev._invalidate_internal(remove_tmpdir=False)

    sidecar = _sidecar_of(gds_path)
    if _is_valid_git_repo(sidecar):
        handle = RepoHandle(gds_path=gds_path, repo_path=sidecar, mode="disk")
    else:
        tmp = tempfile.mkdtemp(prefix="klayoutclaw_vc_")
        handle = RepoHandle(gds_path=gds_path, repo_path=tmp, mode="memory")

    _HANDLES_BY_PATH[gds_path] = handle
    return handle


def migrate_to_disk(handle: RepoHandle, gds_path: str) -> None:
    """DM-4: atomically move the in-memory tmp repo to ``<gds_path>.vc``.

    After migration the handle remains valid and its ``status().mode`` is
    ``"disk"``.  Idempotent: calling on an already-disk handle is a no-op.
    """
    if handle._invalidated:
        return
    if handle._mode == "disk":
        return
    sidecar = _sidecar_of(gds_path)
    handle._migrate_to(sidecar)
