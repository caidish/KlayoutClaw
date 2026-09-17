#!/usr/bin/env python3
"""Synchronize release versions across package, plugin, and server sources."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
ROOT = Path(__file__).resolve().parents[1]


class VersionError(RuntimeError):
    """Raised when release version sources are invalid or inconsistent."""


def _match_one(path: Path, pattern: str) -> str:
    matches = re.findall(pattern, path.read_text(encoding="utf-8"), re.MULTILINE)
    if len(matches) != 1:
        raise VersionError(
            f"expected exactly one version in {path}, found {len(matches)}"
        )
    return matches[0]


def source_versions(root: Path = ROOT) -> dict[str, str]:
    """Return every canonical release version keyed by its source."""
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    plugin = json.loads(
        (root / ".claude-plugin/plugin.json").read_text(encoding="utf-8")
    )
    marketplace = json.loads(
        (root / ".claude-plugin/marketplace.json").read_text(encoding="utf-8")
    )
    return {
        "pyproject.toml": pyproject["project"]["version"],
        "src/klayoutclaw/_version.py": _match_one(
            root / "src/klayoutclaw/_version.py",
            r'^__version__ = "([^"]+)"$',
        ),
        "scripts/sync_payload.py": _match_one(
            root / "scripts/sync_payload.py",
            r'^PAYLOAD_VERSION = "([^"]+)"$',
        ),
        ".claude-plugin/plugin.json": plugin["version"],
        ".claude-plugin/marketplace.json": marketplace["plugins"][0]["version"],
    }


def validate_sources(root: Path = ROOT, expected: str | None = None) -> str:
    """Validate canonical sources and return their shared semantic version."""
    versions = source_versions(root)
    unique = set(versions.values())
    if len(unique) != 1:
        details = ", ".join(f"{path}={version}" for path, version in versions.items())
        raise VersionError(f"release versions are inconsistent: {details}")
    version = unique.pop()
    if not SEMVER.fullmatch(version):
        raise VersionError(f"release version is not stable SemVer: {version!r}")
    if expected is not None and version != expected:
        raise VersionError(f"release version is {version}, expected {expected}")

    protocol_version = ".".join(version.split(".")[:2])
    server = (root / "plugin/klayoutclaw_server.lym").read_text(encoding="utf-8")
    contracts = {
        f"<version>{protocol_version}</version>": 1,
        f"v{protocol_version} QTcpServer": 1,
        f'"version": "{protocol_version}"': 2,
    }
    for text, count in contracts.items():
        actual = server.count(text)
        if actual != count:
            raise VersionError(
                f"server protocol version contract {text!r}: expected {count}, "
                f"found {actual}"
            )
    return version


def _replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise VersionError(f"expected one {old!r} in {path}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def set_version(version: str, root: Path = ROOT) -> None:
    """Update every canonical version source to ``version``."""
    if not SEMVER.fullmatch(version):
        raise VersionError(f"invalid stable SemVer: {version!r}")
    current = validate_sources(root)
    if current == version:
        return

    current_protocol = ".".join(current.split(".")[:2])
    next_protocol = ".".join(version.split(".")[:2])
    _replace_once(
        root / "pyproject.toml",
        f'version = "{current}"',
        f'version = "{version}"',
    )
    _replace_once(
        root / "src/klayoutclaw/_version.py",
        f'__version__ = "{current}"',
        f'__version__ = "{version}"',
    )
    _replace_once(
        root / "scripts/sync_payload.py",
        f'PAYLOAD_VERSION = "{current}"',
        f'PAYLOAD_VERSION = "{version}"',
    )

    for relative, update in (
        (".claude-plugin/plugin.json", lambda data: data.__setitem__("version", version)),
        (
            ".claude-plugin/marketplace.json",
            lambda data: data["plugins"][0].__setitem__("version", version),
        ),
    ):
        path = root / relative
        data = json.loads(path.read_text(encoding="utf-8"))
        update(data)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    server_path = root / "plugin/klayoutclaw_server.lym"
    server = server_path.read_text(encoding="utf-8")
    replacements = {
        f"<version>{current_protocol}</version>": f"<version>{next_protocol}</version>",
        f"v{current_protocol} QTcpServer": f"v{next_protocol} QTcpServer",
        f'"version": "{current_protocol}"': f'"version": "{next_protocol}"',
    }
    expected_counts = (1, 1, 2)
    for (old, new), expected_count in zip(replacements.items(), expected_counts):
        if server.count(old) != expected_count:
            raise VersionError(
                f"expected {expected_count} occurrences of {old!r} in {server_path}"
            )
        server = server.replace(old, new)
    server_path.write_text(server, encoding="utf-8")
    validate_sources(root, version)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("version", nargs="?", help="stable semantic version")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.check:
            version = validate_sources(ROOT, args.version)
            print(version)
            return 0
        if args.version is None:
            parser.error("version is required unless --check is used")
        set_version(args.version)
        print(args.version)
        return 0
    except VersionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
