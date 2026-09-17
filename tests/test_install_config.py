import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "klayoutclaw_install", ROOT / "install.py"
)
assert SPEC is not None and SPEC.loader is not None
INSTALL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALL)
ensure_user_config = INSTALL.ensure_user_config


def test_installer_creates_default_user_config(tmp_path):
    config_path, created = ensure_user_config(tmp_path)

    assert created is True
    assert config_path == tmp_path / "klayoutclaw.json"
    assert json.loads(config_path.read_text()) == {
        "mcp": {
            "bind": "127.0.0.1",
            "port": 8765,
            "endpoint": "/mcp",
            "tls": False,
            "certificate": "",
            "private_key": "",
            "key_algorithm": "rsa",
        }
    }


def test_installer_preserves_existing_user_config(tmp_path):
    config_path = tmp_path / "klayoutclaw.json"
    original = '{"mcp":{"port":8766,"endpoint":"/custom"}}\n'
    config_path.write_text(original)

    returned_path, created = ensure_user_config(tmp_path)

    assert created is False
    assert returned_path == config_path
    assert config_path.read_text() == original
