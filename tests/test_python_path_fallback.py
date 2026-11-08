"""Exercise the platform helpers that the installed macro actually runs."""

import ast
from importlib.util import module_from_spec, spec_from_file_location
import ntpath
import os
from pathlib import Path
import posixpath
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[1]
install_spec = spec_from_file_location("klayoutclaw_install", ROOT / "install.py")
install = module_from_spec(install_spec)
install_spec.loader.exec_module(install)


@pytest.fixture
def macro_helpers():
    source = ET.parse(ROOT / "plugin" / "klayoutclaw_server.lym").findtext("text")
    tree = ast.parse(source)
    wanted = {"_worker_command", "_temp_output_path", "_payload_roots", "_find_worker"}
    helpers = ast.Module(
        body=[node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted],
        type_ignores=[],
    )
    scope = {
        "os": SimpleNamespace(environ={}, path=ntpath),
        "sys": SimpleNamespace(platform="win32"),
        "tempfile": SimpleNamespace(gettempdir=lambda: r"C:\Users\User Name\AppData\Local\Temp"),
    }
    exec(compile(helpers, "klayoutclaw_server.lym", "exec"), scope)
    return scope


def test_windows_explicit_python_path_overrides_env_without_shell(macro_helpers):
    macro_helpers["os"].environ["PYTHON_PATH"] = r"C:\Fallback\python.exe"
    python = r"C:\Worker 环境\Scripts\python.exe"
    worker = r"C:\KLayout Home\pymacros\route_worker.py"
    config = r"C:\Temp 测试\config.json"
    assert macro_helpers["_worker_command"](python, "instrMCPdev", worker, config) == [
        python, "-X", "utf8", worker, config,
    ]


def test_windows_python_path_env_used_when_argument_omitted(macro_helpers):
    python = r"C:\My Python\python.exe"
    macro_helpers["os"].environ["PYTHON_PATH"] = python
    assert macro_helpers["_worker_command"](None, "instrMCPdev", "worker.py", "config.json")[0] == python


def test_windows_missing_worker_python_gives_actionable_error(macro_helpers):
    with pytest.raises(ValueError, match="python.exe"):
        macro_helpers["_worker_command"](None, "instrMCPdev", "worker.py", "config.json")


def test_unix_conda_fallback_quotes_each_argument(macro_helpers):
    conda_sh = "/Users/Test User/miniforge3/etc/profile.d/conda.sh"
    macro_helpers["sys"].platform = "darwin"
    macro_helpers["os"].path = SimpleNamespace(
        expanduser=lambda value: value.replace("~", "/Users/Test User", 1),
        isfile=lambda value: value == conda_sh,
    )
    cmd = macro_helpers["_worker_command"](None, "instrMCPdev", "/my worker.py", "/my config.json")
    assert cmd[:2] == ["bash", "-c"]
    assert cmd[4:] == [conda_sh, "instrMCPdev", "/my worker.py", "/my config.json"]
    assert 'source "$1"' in cmd[2] and 'python -X utf8 "$3" "$4"' in cmd[2]


def test_windows_uses_system_temp_directory(macro_helpers):
    output = macro_helpers["_temp_output_path"]("KLayout_Execute")
    assert output == r"C:\Users\User Name\AppData\Local\Temp\KLayout_Execute"


def test_unix_preserves_existing_temp_directory(macro_helpers):
    macro_helpers["sys"].platform = "darwin"
    macro_helpers["os"].path = posixpath
    assert macro_helpers["_temp_output_path"]("KLayout_Execute") == "/tmp/KLayout_Execute"


def test_worker_is_found_in_actual_klayout_profile(macro_helpers, tmp_path):
    macro_helpers["os"].path = os.path
    macro_helpers["_app_data_path"] = str(tmp_path)
    pymacros = tmp_path / "pymacros"
    pymacros.mkdir()
    worker = pymacros / "route_worker.py"
    worker.touch()
    assert macro_helpers["_find_worker"]("route_worker.py") == str(worker)


def test_windows_installer_matches_klayout_profile_lookup(monkeypatch, tmp_path):
    monkeypatch.setattr(install, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "User Profile")
    monkeypatch.delenv("KLAYOUT_HOME", raising=False)
    monkeypatch.delenv("HOMEDRIVE", raising=False)
    monkeypatch.delenv("HOMESHARE", raising=False)
    monkeypatch.delenv("HOMEPATH", raising=False)
    assert install.klayout_home() == tmp_path / "User Profile" / "KLayout"
    monkeypatch.setenv("HOMEDRIVE", "H:")
    monkeypatch.setenv("HOMEPATH", r"\Redirected User")
    assert install.klayout_home() == Path(r"H:\Redirected User") / "KLayout"


def test_installer_honors_custom_home(monkeypatch, tmp_path):
    custom = tmp_path / "KLayout Profile"
    monkeypatch.setenv("KLAYOUT_HOME", str(custom))
    assert install.klayout_home() == custom


def test_macos_installer_preserves_legacy_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(install, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("KLAYOUT_HOME", raising=False)
    assert install.klayout_home() == tmp_path / ".klayout"


def test_empty_custom_home_does_not_install_in_current_directory(monkeypatch):
    monkeypatch.setenv("KLAYOUT_HOME", "")
    with pytest.raises(ValueError, match="KLAYOUT_HOME is empty"):
        install.klayout_home()
