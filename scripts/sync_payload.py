"""Synchronize the wheel payload from the runtime source tree.

The root ``plugin/`` and selected ``tools/`` files remain canonical. Run this
script after changing any of them, then commit both the source and generated
payload. Packaging tests verify that the checked-in mirror has not drifted.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD_ROOT = ROOT / "src" / "klayoutclaw" / "payload"
PAYLOAD_VERSION = "0.6.0"

MACRO_FILES = tuple(
    path.relative_to(ROOT).as_posix()
    for path in sorted((ROOT / "plugin").glob("klayoutclaw_*.lym"))
)
WORKER_FILES = (
    "tools/evaluate_worker.py",
    "tools/ordered_loop.py",
    "tools/route_worker.py",
    "tools/two_level.py",
)
TOP_LEVEL_FILES = (*MACRO_FILES, *WORKER_FILES)
TREE_ROOT_FILES = ("tools/vc_mcp_handlers.py",)


def destination_for(source: str) -> str:
    """Return the path below KLayout's ``pymacros`` directory."""
    path = Path(source)
    if source in TOP_LEVEL_FILES:
        return path.name
    return path.as_posix()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_files() -> tuple[str, ...]:
    """Return the complete canonical payload source set.

    The VC package is recursive so adding a module or data file cannot leave a
    silently incomplete wheel; the drift test derives its expectation here.
    """
    vc_files = []
    vc_root = ROOT / "plugin" / "klayoutclaw_vc"
    for path in vc_root.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        if path.suffix in {".pyc", ".pyo"}:
            continue
        vc_files.append(path.relative_to(ROOT).as_posix())
    return (*TOP_LEVEL_FILES, *TREE_ROOT_FILES, *sorted(vc_files))


def main() -> None:
    files = []
    for source in source_files():
        source_path = ROOT / source
        if not source_path.is_file():
            raise SystemExit(f"required payload source is missing: {source}")
        data = source_path.read_bytes()
        resource = Path(source)
        target = PAYLOAD_ROOT / resource
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        files.append(
            {
                "source": source,
                "resource": resource.as_posix(),
                "destination": destination_for(source),
                "sha256": digest(data),
            }
        )

    expected = {Path(entry["resource"]) for entry in files}
    for candidate in PAYLOAD_ROOT.rglob("*"):
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(PAYLOAD_ROOT)
        if relative in expected or relative in {
            Path("__init__.py"),
            Path("manifest.json"),
        }:
            continue
        candidate.unlink()

    manifest = {
        "schema": 1,
        "version": PAYLOAD_VERSION,
        "files": files,
    }
    manifest_path = PAYLOAD_ROOT / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    for directory in sorted(PAYLOAD_ROOT.rglob("*"), reverse=True):
        if directory.is_dir() and not any(directory.iterdir()):
            shutil.rmtree(directory)

    print(f"Synchronized {len(files)} files into {PAYLOAD_ROOT}")


if __name__ == "__main__":
    main()
