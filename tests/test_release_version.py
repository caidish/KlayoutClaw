from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "set_version", ROOT / "scripts/set_version.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _copy_version_sources(tmp_path: Path) -> Path:
    for relative in (
        "pyproject.toml",
        "src/klayoutclaw/_version.py",
        "scripts/sync_payload.py",
        ".claude-plugin/plugin.json",
        ".claude-plugin/marketplace.json",
        "plugin/klayoutclaw_server.lym",
    ):
        source = ROOT / relative
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return tmp_path


def test_checked_in_release_versions_are_synchronized() -> None:
    module = _load_module()
    version = module.validate_sources(ROOT)
    assert module.SEMVER.fullmatch(version)


def test_set_version_updates_package_plugin_and_protocol(tmp_path: Path) -> None:
    module = _load_module()
    root = _copy_version_sources(tmp_path)

    module.set_version("0.7.1", root)

    assert module.validate_sources(root, "0.7.1") == "0.7.1"
    assert set(module.source_versions(root).values()) == {"0.7.1"}
    server = (root / "plugin/klayoutclaw_server.lym").read_text(encoding="utf-8")
    assert server.count('"version": "0.7"') == 2


@pytest.mark.parametrize("version", ["v1.2.3", "1.2", "1.2.3rc1", "01.2.3"])
def test_set_version_rejects_non_stable_semver(tmp_path: Path, version: str) -> None:
    module = _load_module()
    root = _copy_version_sources(tmp_path)
    with pytest.raises(module.VersionError, match="invalid stable SemVer"):
        module.set_version(version, root)
