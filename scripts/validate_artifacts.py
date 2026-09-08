"""Validate the exact contents of KlayoutClaw wheel and sdist artifacts."""

from __future__ import annotations

import json
import re
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

FORBIDDEN_ROOTS = {
    ".claude",
    "agent",
    "docs",
    "packaging_tests",
    "tests",
    "tests_resources",
}
SDIST_ROOTS = {
    "LICENSE",
    "PKG-INFO",
    "PYPI.md",
    "install.py",
    "plugin",
    "pyproject.toml",
    "pyproject.toml.orig",
    "scripts",
    "src",
    "tools",
}
EXPECTED_WORKER_DESTINATIONS = {
    "evaluate_worker.py",
    "ordered_loop.py",
    "route_worker.py",
    "two_level.py",
}


def _payload_manifest(root: Path) -> dict:
    return json.loads(
        (root / "src" / "klayoutclaw" / "payload" / "manifest.json").read_text()
    )


def _payload_resources(root: Path) -> set[str]:
    return {entry["resource"] for entry in _payload_manifest(root)["files"]}


def _validate_worker_contract(root: Path) -> None:
    """Check the worker set independently of the payload sync implementation."""
    destinations = {entry["destination"] for entry in _payload_manifest(root)["files"]}
    missing = EXPECTED_WORKER_DESTINATIONS - destinations
    if missing:
        raise SystemExit(f"payload manifest is missing workers: {sorted(missing)}")


def _validate_long_description(root: Path) -> None:
    readme = (root / "PYPI.md").read_text(encoding="utf-8")
    local_markdown_images = re.findall(r"!\[[^]]*\]\((?!https?://)[^)]+\)", readme)
    local_html_images = re.findall(
        r"<img\b[^>]*\bsrc=[\"'](?!https?://)[^\"']+[\"']", readme, re.IGNORECASE
    )
    if local_markdown_images or local_html_images:
        raise SystemExit(
            "PYPI.md references local image assets excluded from artifacts"
        )


def validate_wheel(path: Path, root: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
    expected = {
        f"klayoutclaw/payload/{resource}" for resource in _payload_resources(root)
    }
    expected.update(
        {
            "klayoutclaw/__init__.py",
            "klayoutclaw/_version.py",
            "klayoutclaw/cli.py",
            "klayoutclaw/lifecycle.py",
            "klayoutclaw/payload/__init__.py",
            "klayoutclaw/payload/manifest.json",
        }
    )
    missing = expected - names
    if missing:
        raise SystemExit(f"wheel is missing files: {sorted(missing)}")
    forbidden = [
        name for name in names if PurePosixPath(name).parts[0] in FORBIDDEN_ROOTS
    ]
    if forbidden:
        raise SystemExit(f"wheel contains forbidden files: {forbidden}")
    unexpected_roots = {
        PurePosixPath(name).parts[0]
        for name in names
        if PurePosixPath(name).parts
        and PurePosixPath(name).parts[0] != "klayoutclaw"
        and PurePosixPath(name).parts[0]
        != f"klayoutclaw-{_payload_manifest(root)['version']}.dist-info"
    }
    if unexpected_roots:
        raise SystemExit(f"wheel contains unexpected roots: {sorted(unexpected_roots)}")
    unexpected_files = {
        name
        for name in names
        if not name.endswith("/")
        and name not in expected
        and not PurePosixPath(name).parts[0].endswith(".dist-info")
    }
    if unexpected_files:
        raise SystemExit(f"wheel contains unexpected files: {sorted(unexpected_files)}")


def validate_sdist(path: Path, root: Path) -> None:
    with tarfile.open(path, "r:gz") as archive:
        names = archive.getnames()
    relative_names = {
        PurePosixPath(*PurePosixPath(name).parts[1:]).as_posix() for name in names
    }
    required = {
        "PYPI.md",
        "install.py",
        "pyproject.toml",
        "scripts/sync_payload.py",
        "src/klayoutclaw/lifecycle.py",
    }
    manifest = _payload_manifest(root)
    required.update(entry["source"] for entry in manifest["files"])
    missing = required - relative_names
    if missing:
        raise SystemExit(f"sdist is missing files: {sorted(missing)}")
    forbidden = [
        name
        for name in relative_names
        if PurePosixPath(name).parts and PurePosixPath(name).parts[0] in FORBIDDEN_ROOTS
    ]
    if forbidden:
        raise SystemExit(f"sdist contains forbidden files: {forbidden[:20]}")
    unexpected_roots = {
        PurePosixPath(name).parts[0]
        for name in relative_names
        if PurePosixPath(name).parts and PurePosixPath(name).parts[0] not in SDIST_ROOTS
    }
    if unexpected_roots:
        raise SystemExit(f"sdist contains unexpected roots: {sorted(unexpected_roots)}")

    expected_source_files = {entry["source"] for entry in manifest["files"]}
    unexpected_runtime_sources = {
        name
        for name in relative_names
        if PurePosixPath(name).parts
        and PurePosixPath(name).parts[0] in {"plugin", "tools"}
        and Path(name).suffix
        and name not in expected_source_files
    }
    if unexpected_runtime_sources:
        raise SystemExit(
            "sdist contains runtime sources outside the payload manifest: "
            f"{sorted(unexpected_runtime_sources)}"
        )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        raise SystemExit("usage: validate_artifacts.py DIST_DIR")
    dist = Path(args[0])
    root = Path(__file__).resolve().parents[1]
    wheels = list(dist.glob("klayoutclaw-*.whl"))
    sdists = list(dist.glob("klayoutclaw-*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise SystemExit("expected exactly one KlayoutClaw wheel and one sdist")
    _validate_worker_contract(root)
    _validate_long_description(root)
    validate_wheel(wheels[0], root)
    validate_sdist(sdists[0], root)
    print(f"Validated {wheels[0].name} and {sdists[0].name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
