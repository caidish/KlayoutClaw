from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

from klayoutclaw import __version__

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = ROOT / "src" / "klayoutclaw" / "payload"
EXPECTED_WORKER_DESTINATIONS = {
    "evaluate_worker.py",
    "ordered_loop.py",
    "route_worker.py",
    "two_level.py",
}


def _sync_module():
    spec = importlib.util.spec_from_file_location(
        "sync_payload", ROOT / "scripts" / "sync_payload.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_payload_matches_all_canonical_sources() -> None:
    sync = _sync_module()
    manifest = json.loads((PAYLOAD / "manifest.json").read_text(encoding="utf-8"))
    entries = {entry["source"]: entry for entry in manifest["files"]}

    # source_files() recursively discovers the entire VC package. This set
    # equality makes the checked-in manifest authoritative for additions and
    # removals, not merely for edits to an existing hard-coded list.
    assert set(entries) == set(sync.source_files())
    assert manifest["version"] == __version__

    expected_resources = set()
    for source, entry in entries.items():
        source_bytes = (ROOT / source).read_bytes()
        resource = Path(entry["resource"])
        expected_resources.add(resource)
        assert (PAYLOAD / resource).read_bytes() == source_bytes
        assert entry["sha256"] == hashlib.sha256(source_bytes).hexdigest()
        assert entry["destination"] == sync.destination_for(source)

    packaged_resources = {
        path.relative_to(PAYLOAD)
        for path in PAYLOAD.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
        and path.relative_to(PAYLOAD)
        not in {Path("__init__.py"), Path("manifest.json")}
    }
    assert packaged_resources == expected_resources


def test_distribution_version_is_synchronized() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    server = (ROOT / "plugin" / "klayoutclaw_server.lym").read_text()
    protocol_version = ".".join(__version__.split(".")[:2])

    assert f'version = "{__version__}"' in pyproject
    assert f"<version>{protocol_version}</version>" in server
    assert (
        f'"serverInfo": {{"name": "KlayoutClaw", "version": "{protocol_version}"}}'
        in server
    )
    assert f'"server": "KlayoutClaw", "version": "{protocol_version}"' in server


def test_worker_payload_contract_is_complete_independently_of_sync_code() -> None:
    manifest = json.loads((PAYLOAD / "manifest.json").read_text(encoding="utf-8"))
    destinations = {entry["destination"] for entry in manifest["files"]}
    actual_workers = {
        destination
        for destination in destinations
        if destination.endswith("_worker.py")
        or destination in {"ordered_loop.py", "two_level.py"}
    }
    assert actual_workers == EXPECTED_WORKER_DESTINATIONS
