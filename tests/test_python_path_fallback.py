import os
from unittest.mock import patch

def _resolve_python_cmd(python_path, conda_env, worker_path, config_json):
    """Mirror of the patched plugin logic; testable without pya."""
    python_path = python_path or os.environ.get("PYTHON_PATH")
    if python_path:
        return "{} {} {}".format(python_path, worker_path, config_json)
    # conda fallback (omitted in test — assert it returns None / raises)
    return None

def test_python_path_env_var_used_when_arg_omitted():
    with patch.dict(os.environ, {"PYTHON_PATH": "/opt/venv/bin/python"}):
        cmd = _resolve_python_cmd(None, "instrMCPdev", "/wp.py", "/cfg.json")
    assert cmd == "/opt/venv/bin/python /wp.py /cfg.json"

def test_explicit_python_path_arg_overrides_env():
    with patch.dict(os.environ, {"PYTHON_PATH": "/opt/venv/bin/python"}):
        cmd = _resolve_python_cmd("/usr/bin/python3", None, "/wp.py", "/cfg.json")
    assert cmd == "/usr/bin/python3 /wp.py /cfg.json"

def test_no_python_path_falls_through():
    with patch.dict(os.environ, {}, clear=True):
        cmd = _resolve_python_cmd(None, "instrMCPdev", "/wp.py", "/cfg.json")
    assert cmd is None  # falls through to conda branch
