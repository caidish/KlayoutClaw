from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from klayoutclaw import __version__, lifecycle

ROOT = Path(__file__).resolve().parents[1]


def _snapshot(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_install_status_idempotency_and_uninstall(tmp_path: Path) -> None:
    target = tmp_path / "KLayout home ü" / "pymacros"
    foreign = target / "foreign" / "keep.txt"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("mine", encoding="utf-8")

    installed = lifecycle.install(target)
    assert installed["status"] == "installed"
    config_path = target.parent / lifecycle.USER_CONFIG
    assert installed["config_path"] == str(config_path)
    assert installed["config_created"] is True
    assert json.loads(config_path.read_text(encoding="utf-8"))["mcp"]["port"] == 8765
    assert lifecycle.status(target)["status"] == "current"
    runtime = json.loads((target / lifecycle.RUNTIME_CONFIG).read_text())
    assert runtime["plugin_version"] == __version__
    assert runtime["worker_python"] == sys.executable

    before = _snapshot(target)
    repeated = lifecycle.install(target)
    assert repeated["status"] == "current"
    assert repeated["changed"] is False
    assert repeated["config_created"] is False
    assert _snapshot(target) == before

    removed = lifecycle.uninstall(target)
    assert removed["status"] == "uninstalled"
    assert config_path.exists()
    assert foreign.read_text(encoding="utf-8") == "mine"
    for relative in lifecycle.COMPATIBILITY_MARKERS:
        assert (target / relative).read_bytes() == b""
    assert lifecycle.status(target)["status"] == "not-installed"


def test_dry_run_never_creates_target(tmp_path: Path) -> None:
    target = tmp_path / "missing"
    result = lifecycle.install(target, dry_run=True)
    assert result["status"] == "would-install"
    assert result["config_would_create"] is True
    assert not target.exists()
    assert not (target.parent / lifecycle.USER_CONFIG).exists()


def test_install_preserves_existing_user_config(tmp_path: Path) -> None:
    target = tmp_path / "pymacros"
    config_path = tmp_path / lifecycle.USER_CONFIG
    config_path.write_text('{"mcp":{"port":8766}}\n', encoding="utf-8")

    result = lifecycle.install(target)

    assert result["config_created"] is False
    assert config_path.read_text(encoding="utf-8") == '{"mcp":{"port":8766}}\n'


def test_uninstall_dry_run_writes_nothing(tmp_path: Path) -> None:
    target = tmp_path / "pymacros"
    lifecycle.install(target)
    before = _snapshot(target)
    result = lifecycle.uninstall(target, dry_run=True)
    assert result["status"] == "would-uninstall"
    assert _snapshot(target) == before


def test_unowned_collision_requires_force(tmp_path: Path) -> None:
    target = tmp_path / "pymacros"
    target.mkdir()
    collision = target / "route_worker.py"
    collision.write_text("user owned", encoding="utf-8")

    with pytest.raises(lifecycle.LifecycleError, match="unowned files"):
        lifecycle.install(target)
    assert collision.read_text(encoding="utf-8") == "user owned"

    lifecycle.install(target, force=True)
    assert collision.read_bytes() == lifecycle.payload_files()["route_worker.py"]


def test_matching_legacy_install_is_adopted_without_force(tmp_path: Path) -> None:
    target = tmp_path / "legacy" / "pymacros"
    for relative, data in lifecycle.payload_files().items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    result = lifecycle.install(target)
    assert result["status"] == "installed"
    assert result["written"] == [lifecycle.RUNTIME_CONFIG]
    assert lifecycle.status(target)["status"] == "current"


def test_directory_collision_is_never_silently_replaced(tmp_path: Path) -> None:
    target = tmp_path / "pymacros"
    (target / "route_worker.py").mkdir(parents=True)
    with pytest.raises(lifecycle.LifecycleError, match="unowned files"):
        lifecycle.install(target)


def test_symlinked_payload_parent_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "pymacros"
    outside = tmp_path / "outside"
    target.mkdir()
    outside.mkdir()
    (target / "plugin").symlink_to(outside, target_is_directory=True)
    with pytest.raises(lifecycle.LifecycleError, match="symlinked"):
        lifecycle.install(target)


def test_modified_owned_file_requires_force_for_install_and_uninstall(
    tmp_path: Path,
) -> None:
    target = tmp_path / "pymacros"
    lifecycle.install(target)
    worker = target / "route_worker.py"
    worker.write_text("local patch", encoding="utf-8")
    assert lifecycle.status(target)["status"] == "modified"

    with pytest.raises(lifecycle.LifecycleError, match="locally modified"):
        lifecycle.install(target)
    with pytest.raises(lifecycle.LifecycleError, match="locally modified"):
        lifecycle.uninstall(target)
    assert worker.read_text(encoding="utf-8") == "local patch"

    lifecycle.uninstall(target, force=True)
    assert not worker.exists()


def test_missing_stale_and_version_mismatched_statuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "pymacros"
    lifecycle.install(target)

    (target / "ordered_loop.py").unlink()
    assert lifecycle.status(target)["status"] == "missing"
    lifecycle.install(target)

    original_payload = lifecycle.payload_files()
    changed_payload = dict(original_payload)
    changed_payload["ordered_loop.py"] += b"\n# packaged update\n"
    monkeypatch.setattr(lifecycle, "payload_files", lambda: changed_payload)
    assert lifecycle.status(target)["status"] == "stale"
    lifecycle.install(target)
    assert (target / "ordered_loop.py").read_bytes() == changed_payload[
        "ordered_loop.py"
    ]

    monkeypatch.setattr(lifecycle, "payload_files", lambda: original_payload)
    lifecycle.install(target)
    manifest_path = target / lifecycle.INSTALL_MANIFEST
    manifest = json.loads(manifest_path.read_text())
    manifest["version"] = "0.0.0"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert lifecycle.status(target)["status"] == "version-mismatched"


def test_target_resolution_honors_klayout_home(tmp_path: Path) -> None:
    assert lifecycle.default_target_dir({"KLAYOUT_HOME": str(tmp_path)}) == (
        tmp_path / "pymacros"
    )


def test_doctor_reports_missing_recorded_interpreter(tmp_path: Path) -> None:
    target = tmp_path / "pymacros"
    lifecycle.install(target)
    runtime_path = target / lifecycle.RUNTIME_CONFIG
    runtime = json.loads(runtime_path.read_text())
    runtime["worker_python"] = str(tmp_path / "deleted-python")
    runtime_path.write_text(json.dumps(runtime), encoding="utf-8")

    report = lifecycle.doctor(target)
    assert report["workers"]["status"] == "interpreter-missing"
    assert all(
        item["status"] == "interpreter-missing"
        for item in report["workers"]["dependency_diagnostics"].values()
    )


def test_legacy_namespace_markers_are_conditional_and_unowned(tmp_path: Path) -> None:
    target = tmp_path / "pymacros"
    plugin_marker = target / "plugin" / "__init__.py"
    tools_marker = target / "tools" / "__init__.py"
    plugin_marker.parent.mkdir(parents=True)
    plugin_marker.write_text("# shared plugin marker\n", encoding="utf-8")

    installed = lifecycle.install(target)
    assert installed["compatibility_created"] == ["tools/__init__.py"]
    assert plugin_marker.read_text(encoding="utf-8") == "# shared plugin marker\n"
    assert tools_marker.read_bytes() == b""

    manifest = json.loads((target / lifecycle.INSTALL_MANIFEST).read_text())
    owned = {entry["path"] for entry in manifest["files"]}
    assert not owned.intersection(lifecycle.COMPATIBILITY_MARKERS)

    lifecycle.uninstall(target)
    assert plugin_marker.read_text(encoding="utf-8") == "# shared plugin marker\n"
    assert tools_marker.read_bytes() == b""


def test_interrupted_install_is_resumable(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "pymacros"
    real_atomic_write = lifecycle._atomic_write
    calls = 0

    def fail_third_write(path: Path, data: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("injected write failure")
        real_atomic_write(path, data)

    with monkeypatch.context() as context:
        context.setattr(lifecycle, "_atomic_write", fail_third_write)
        with pytest.raises(OSError, match="injected write failure"):
            lifecycle.install(target)

    assert not (target / lifecycle.INSTALL_MANIFEST).exists()
    assert any(target.rglob("*.py"))
    resumed = lifecycle.install(target)
    assert resumed["status"] == "installed"
    assert lifecycle.status(target)["status"] == "current"


@pytest.mark.parametrize(
    "error", [OSError("cannot execute"), PermissionError("denied")]
)
def test_doctor_structures_probe_launch_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: OSError
) -> None:
    target = tmp_path / "pymacros"
    lifecycle.install(target)

    def fail_probe(*args, **kwargs):
        raise error

    monkeypatch.setattr(lifecycle.subprocess, "run", fail_probe)
    report = lifecycle.doctor(target)
    assert report["workers"]["status"] == "probe-error"
    assert all(
        item["status"] == "launch-error"
        for item in report["workers"]["dependency_diagnostics"].values()
    )


def test_doctor_structures_timeout_and_malformed_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "pymacros"
    lifecycle.install(target)

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=20)

    monkeypatch.setattr(lifecycle.subprocess, "run", timeout)
    timed_out = lifecycle.doctor(target)
    assert all(
        item["status"] == "timeout"
        for item in timed_out["workers"]["dependency_diagnostics"].values()
    )

    monkeypatch.setattr(
        lifecycle.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout="not a probe result\n", stderr=""
        ),
    )
    malformed = lifecycle.doctor(target)
    assert all(
        item["status"] == "malformed-output"
        for item in malformed["workers"]["dependency_diagnostics"].values()
    )


def test_compatibility_wrapper_and_json_status(tmp_path: Path) -> None:
    target = tmp_path / "wrapper target"
    installed = subprocess.run(
        [sys.executable, str(ROOT / "install.py"), "--target-dir", str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert installed.returncode == 0, installed.stderr
    assert "deprecated" in installed.stderr

    checked = subprocess.run(
        [
            sys.executable,
            "-m",
            "klayoutclaw.cli",
            "status",
            "--target-dir",
            str(target),
            "--json",
        ],
        env={"PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert checked.returncode == 0, checked.stderr
    assert json.loads(checked.stdout)["status"] == "current"
