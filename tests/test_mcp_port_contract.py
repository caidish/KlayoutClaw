"""Regression tests for the MCP endpoint and KLayout macro startup contract."""

import ast
import importlib.util
import json
import os
import subprocess
import sys
import types
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER_MACRO = ROOT / "plugin" / "klayoutclaw_server.lym"
UI_MACRO = ROOT / "plugin" / "klayoutclaw_ui.lym"
DEFAULT_URL = "http://127.0.0.1:8765/mcp"


def _macro_tree(path: Path) -> ast.Module:
    """Parse the actual Python payload embedded in a KLayout macro."""
    code = ET.parse(path).getroot().findtext("text")
    assert code is not None
    return ast.parse(code, filename=str(path))


def _load_module(relative_path: str, name: str):
    """Load a first-party script so its real import-time config is exercised."""
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


def _load_server_contract(listen_ok=True):
    """Execute the server's real settings resolver and constructor with fake Qt."""
    tree = _macro_tree(SERVER_MACRO)
    selected = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in {
                "DEFAULT_MCP_BIND",
                "DEFAULT_MCP_CONFIG_PATH",
                "DEFAULT_MCP_ENDPOINT",
                "DEFAULT_MCP_PORT",
            }
            for target in node.targets
        ):
            selected.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in {
            "_load_mcp_server_config",
            "_normalize_mcp_endpoint",
            "_parse_bool_setting",
            "_resolve_mcp_port",
            "_resolve_mcp_settings",
            "KlayoutClawServer",
        }:
            selected.append(node)

    callback_state = types.SimpleNamespace(
        on_request=None,
        on_server_start=None,
        on_error=None,
        server_port=None,
        startup_error=None,
    )
    log = []

    class FakeTcpServer:
        def __init__(self, parent=None):
            self.parent = parent
            self.listen_calls = []
            self.connection_callback = None

        def listen(self, address, port):
            self.listen_calls.append((address, port))
            return listen_ok

        def newConnection(self, callback):
            self.connection_callback = callback

        def incomingConnection(self, socket_descriptor):
            self.plain_socket_descriptor = socket_descriptor

    class FakeSslSocket:
        instances = []

        def __init__(self, parent=None):
            self.parent = parent
            self.certificate = None
            self.private_key = None
            self.descriptor = None
            self.encryption_started = False
            self.__class__.instances.append(self)

        @staticmethod
        def supportsSsl():
            return True

        def setLocalCertificate(self, path, encoding):
            self.certificate = (path, encoding)

        def setPrivateKey(self, path, algorithm, encoding, passphrase):
            self.private_key = (path, algorithm, encoding, passphrase)

        def setSocketDescriptor(self, descriptor, state, mode):
            self.descriptor = (descriptor, state, mode)
            return True

        def startServerEncryption(self):
            self.encryption_started = True

        def waitForEncrypted(self, timeout):
            self.encryption_timeout = timeout
            return True

        def sslErrors(self):
            return []

        def disconnectFromHost(self):
            pass

        def deleteLater(self):
            pass

    fake_pya = types.SimpleNamespace(
        QTcpServer=FakeTcpServer,
        QHostAddress=lambda address: address,
        QSslSocket=FakeSslSocket,
        QSsl=types.SimpleNamespace(Pem="pem", Rsa="rsa", Ec="ec"),
        QAbstractSocket=types.SimpleNamespace(ConnectedState="connected"),
        QIODevice=types.SimpleNamespace(ReadWrite="read-write"),
        qt_signal=lambda value: value,
        qt_slot=lambda value: value,
        QObject=types.SimpleNamespace(connect=lambda *_args: None),
    )
    namespace = {
        "json": json,
        "os": os,
        "sys": types.SimpleNamespace(modules={"_klayoutclaw": callback_state}),
        "pya": fake_pya,
        "_log": log.append,
    }
    module = ast.Module(body=selected, type_ignores=[])
    exec(compile(module, str(SERVER_MACRO), "exec"), namespace)
    return namespace, callback_state, log


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, 8765),
        ("1", 1),
        ("8766", 8766),
        ("65535", 65535),
    ],
)
def test_actual_server_macro_resolves_valid_ports(raw, expected):
    namespace, _, _ = _load_server_contract()
    env = {} if raw is None else {"KLAYOUT_MCP_PORT": raw}
    assert namespace["_resolve_mcp_port"](env) == expected


@pytest.mark.parametrize("raw", ["", "abc", "0", "-1", "65536"])
def test_actual_server_macro_rejects_invalid_ports(raw):
    namespace, _, _ = _load_server_contract()
    with pytest.raises(ValueError, match="KLAYOUT_MCP_PORT"):
        namespace["_resolve_mcp_port"]({"KLAYOUT_MCP_PORT": raw})


def test_server_config_resolves_tls_port_and_endpoint(tmp_path):
    certificate = tmp_path / "server.crt"
    private_key = tmp_path / "server.key"
    certificate.write_text("certificate")
    private_key.write_text("key")
    config_path = tmp_path / "klayoutclaw.json"
    config_path.write_text(json.dumps({
        "mcp": {
            "tls": True,
            "port": 9443,
            "endpoint": "/custom-mcp",
            "certificate": str(certificate),
            "private_key": str(private_key),
        }
    }))
    namespace, _, _ = _load_server_contract()

    settings = namespace["_resolve_mcp_settings"]({
        "KLAYOUT_MCP_CONFIG": str(config_path),
    })

    assert settings == {
        "bind": "127.0.0.1",
        "port": 9443,
        "endpoint": "/custom-mcp",
        "tls": True,
        "certificate": str(certificate),
        "private_key": str(private_key),
        "key_algorithm": "rsa",
        "private_key_passphrase": "",
        "config_path": str(config_path),
    }


def test_server_environment_overrides_config_values(tmp_path):
    config_path = tmp_path / "klayoutclaw.json"
    config_path.write_text(json.dumps({
        "mcp": {"tls": False, "port": 8765, "endpoint": "/from-file"}
    }))
    namespace, _, _ = _load_server_contract()

    settings = namespace["_resolve_mcp_settings"]({
        "KLAYOUT_MCP_CONFIG": str(config_path),
        "KLAYOUT_MCP_BIND": "localhost",
        "KLAYOUT_MCP_PORT": "8766",
        "KLAYOUT_MCP_ENDPOINT": "/from-env",
        "KLAYOUT_MCP_TLS": "true",
        "KLAYOUT_MCP_TLS_CERT": "/tmp/env.crt",
        "KLAYOUT_MCP_TLS_KEY": "/tmp/env.key",
        "KLAYOUT_MCP_TLS_KEY_ALGORITHM": "ec",
    })

    assert settings["bind"] == "localhost"
    assert settings["port"] == 8766
    assert settings["endpoint"] == "/from-env"
    assert settings["tls"] is True
    assert settings["certificate"] == "/tmp/env.crt"
    assert settings["private_key"] == "/tmp/env.key"
    assert settings["key_algorithm"] == "ec"


@pytest.mark.parametrize(
    "raw", ["", "http://localhost/mcp", "/bad?query", "/bad#fragment"]
)
def test_server_rejects_invalid_endpoint_values(raw):
    namespace, _, _ = _load_server_contract()
    with pytest.raises(ValueError, match="KLAYOUT_MCP_ENDPOINT"):
        namespace["_resolve_mcp_settings"]({"KLAYOUT_MCP_ENDPOINT": raw})


def test_missing_server_config_is_optional(tmp_path):
    namespace, _, _ = _load_server_contract()
    settings = namespace["_resolve_mcp_settings"]({
        "KLAYOUT_MCP_CONFIG": str(tmp_path / "missing.json"),
    })
    assert settings["port"] == 8765
    assert settings["endpoint"] == "/mcp"
    assert settings["tls"] is False


def test_invalid_server_config_fails_loudly(tmp_path):
    config_path = tmp_path / "klayoutclaw.json"
    config_path.write_text("{not-json")
    namespace, _, _ = _load_server_contract()
    with pytest.raises(ValueError, match="Invalid JSON"):
        namespace["_resolve_mcp_settings"]({
            "KLAYOUT_MCP_CONFIG": str(config_path),
        })


def test_actual_server_macro_publishes_successful_port(monkeypatch):
    namespace, callback_state, log = _load_server_contract(listen_ok=True)
    monkeypatch.setenv("KLAYOUT_MCP_PORT", "8766")

    server = namespace["KlayoutClawServer"]()

    assert server.started is True
    assert server.port == 8766
    assert server.listen_calls == [("127.0.0.1", 8766)]
    assert callback_state.server_port == 8766
    assert callback_state.startup_error is None
    assert any("127.0.0.1:8766" in line for line in log)


def test_actual_server_macro_persists_bind_failure(monkeypatch):
    namespace, callback_state, log = _load_server_contract(listen_ok=False)
    monkeypatch.setenv("KLAYOUT_MCP_PORT", "8766")

    server = namespace["KlayoutClawServer"]()

    assert server.started is False
    assert server.port is None
    assert callback_state.server_port is None
    assert "127.0.0.1:8766" in callback_state.startup_error
    assert "port may be in use" in callback_state.startup_error
    callback_state.startup_error.encode("ascii")
    assert log[-1] == callback_state.startup_error


def test_actual_server_macro_initializes_tls_socket(monkeypatch, tmp_path):
    certificate = tmp_path / "server.crt"
    private_key = tmp_path / "server.key"
    certificate.write_text("certificate")
    private_key.write_text("key")
    config_path = tmp_path / "klayoutclaw.json"
    config_path.write_text(json.dumps({
        "mcp": {
            "tls": True,
            "port": 9443,
            "endpoint": "/secure-mcp",
            "certificate": str(certificate),
            "private_key": str(private_key),
        }
    }))
    monkeypatch.setenv("KLAYOUT_MCP_CONFIG", str(config_path))
    namespace, callback_state, log = _load_server_contract(listen_ok=True)
    server = namespace["KlayoutClawServer"]()
    handled = []
    server._handle_request = handled.append

    server.incomingConnection(42)

    socket = namespace["pya"].QSslSocket.instances[-1]
    assert server.started is True
    assert server.tls_enabled is True
    assert server.endpoint == "/secure-mcp"
    assert server.connection_callback is None
    assert socket.certificate == (str(certificate), "pem")
    assert socket.private_key[:3] == (str(private_key), "rsa", "pem")
    assert socket.descriptor == (42, "connected", "read-write")
    assert socket.encryption_started is True
    assert handled == [socket]
    assert callback_state.server_port == 9443
    assert any("https://127.0.0.1:9443/secure-mcp" in line for line in log)


def test_server_routes_only_the_configured_endpoint():
    code = ET.parse(SERVER_MACRO).getroot().findtext("text")
    assert code is not None
    assert 'method == "GET" and path == self.endpoint' in code
    assert 'method == "POST" and path == self.endpoint' in code


def test_server_flushes_buffered_response_before_disconnect():
    namespace, _, _ = _load_server_contract()
    server = namespace["KlayoutClawServer"]()

    class FakeConnection:
        def __init__(self):
            self.events = []

        def bytesToWrite(self):
            return 42

        def waitForBytesWritten(self, timeout):
            self.events.append(("flush", timeout))
            return True

        def disconnectFromHost(self):
            self.events.append(("disconnect", None))

    connection = FakeConnection()
    server._close_connection(connection)

    assert connection.events == [("flush", 1000), ("disconnect", None)]


def test_actual_server_macro_persists_invalid_port_failure(monkeypatch):
    namespace, callback_state, log = _load_server_contract(listen_ok=True)
    monkeypatch.setenv("KLAYOUT_MCP_PORT", "not-a-port")

    server = namespace["KlayoutClawServer"]()

    assert server.started is False
    assert server.listen_calls == []
    assert callback_state.server_port is None
    assert "KLAYOUT_MCP_PORT" in callback_state.startup_error
    assert log[-1] == callback_state.startup_error


def test_ui_macro_uses_callback_port_and_persisted_startup_error():
    code = ET.parse(UI_MACRO).getroot().findtext("text")
    assert code is not None
    assert 'getattr(cb, "startup_error", None)' in code

    tree = ast.parse(code, filename=str(UI_MACRO))
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in {
            "_set_status_running",
            "_on_server_start",
            "_on_error",
        }
    ]

    class FakeLabel:
        def __init__(self):
            self.text = None
            self.style = None

        def setText(self, value):
            self.text = value

        def setStyleSheet(self, value):
            self.style = value

    class FakeTimer:
        def __init__(self):
            self.started = []
            self.stops = 0

        def start(self, delay):
            self.started.append(delay)

        def stop(self):
            self.stops += 1

    callback_state = types.SimpleNamespace(server_port=None, startup_error="bind failed")
    label = FakeLabel()
    timer = FakeTimer()
    log = []
    namespace = {
        "sys": types.SimpleNamespace(modules={"_klayoutclaw": callback_state}),
        "datetime": __import__("datetime"),
        "_ui_status": label,
        "_ui_timer": timer,
        "_append_log": lambda text, success: log.append((text, success)),
    }
    exec(
        compile(ast.Module(body=selected, type_ignores=[]), str(UI_MACRO), "exec"),
        namespace,
    )

    namespace["_on_error"]("bind failed")
    assert label.text == "MCP: Error \u25cf"
    assert timer.started == []

    namespace["_on_server_start"](8766)
    assert label.text == "MCP: Running \u25cf :8766"
    assert callback_state.startup_error is None

    namespace["_on_error"]("transient request failure")
    assert timer.started == [5000]


def test_active_default_endpoint_contract():
    """First-party defaults stay on 8765 while runtime overrides remain possible."""
    direct = json.loads((ROOT / "mcp_config.json").read_text())
    assert direct["mcpServers"]["klayoutclaw"]["url"] == DEFAULT_URL

    proxy = json.loads((ROOT / ".mcp.json").read_text())
    plugin = proxy["mcpServers"]["klayoutclaw"]
    assert plugin["command"] == "/bin/sh"
    assert plugin["args"][0] == "-c"
    assert plugin["args"][2:] == ["klayoutclaw-mcp", DEFAULT_URL]
    shell_command = plugin["args"][1]
    assert 'config_path="$KLAYOUT_MCP_CONFIG"' in shell_command
    assert '$HOME/.klayout/klayoutclaw.json' in shell_command
    assert 'exec npx -y mcp-remote "$mcp_url" --allow-http' in shell_command
    assert "${" not in json.dumps(plugin)

    skills_dir = ROOT / "skills" / "scripts"
    sys.path.insert(0, str(skills_dir))
    try:
        import mcp_client

        assert mcp_client._DEFAULT_URL == DEFAULT_URL
    finally:
        sys.path.remove(str(skills_dir))

    config_source = (ROOT / "agent" / "src" / "config.ts").read_text()
    assert config_source.count(
        'process.env.KLAYOUT_MCP_URL ?? "http://127.0.0.1:8765/mcp"'
    ) == 2

    agent_source = (ROOT / "agent" / "src" / "agent.ts").read_text()
    assert "KLayout MCP ${config.klayout.url}" in agent_source
    assert 'connectedServers.push("klayout (KLayout MCP :8765)")' not in agent_source


@pytest.mark.parametrize(
    ("override", "server_config", "expected_url"),
    [
        (None, None, DEFAULT_URL),
        ("", None, DEFAULT_URL),
        ("http://127.0.0.1:8766/mcp", None, "http://127.0.0.1:8766/mcp"),
        (
            None,
            {"mcp": {"port": 8766, "endpoint": "/desktop-mcp", "tls": False}},
            "http://127.0.0.1:8766/desktop-mcp",
        ),
        (
            None,
            {
                "mcp": {
                    "bind": "localhost",
                    "port": 9443,
                    "endpoint": "/secure-mcp",
                    "tls": True,
                }
            },
            "https://localhost:9443/secure-mcp",
        ),
    ],
)
def test_actual_plugin_launcher_selects_endpoint(
    tmp_path, override, server_config, expected_url
):
    """Run the checked-in inline command and inspect its actual exec args."""
    args_file = tmp_path / "npx-args.txt"
    fake_npx = tmp_path / "npx"
    fake_npx.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$ARGS_FILE"\n'
    )
    fake_npx.chmod(0o755)
    (tmp_path / "python3").symlink_to(sys.executable)

    plugin = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"][
        "klayoutclaw"
    ]
    env = os.environ.copy()
    env["PATH"] = str(tmp_path)
    env["ARGS_FILE"] = str(args_file)
    config_path = tmp_path / "klayoutclaw.json"
    env["KLAYOUT_MCP_CONFIG"] = str(config_path)
    if server_config is not None:
        config_path.write_text(json.dumps(server_config))
    if override is None:
        env.pop("KLAYOUT_MCP_URL", None)
    else:
        env["KLAYOUT_MCP_URL"] = override

    result = subprocess.run(
        [plugin["command"], *plugin["args"]],
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert args_file.read_text().splitlines() == [
        "-y",
        "mcp-remote",
        expected_url,
        "--allow-http",
    ]


@pytest.mark.parametrize(
    ("relative_path", "attribute"),
    [
        ("tools/capture_demo.py", "MCP_URL"),
        ("tools/capture_ml08_demo.py", "MCP_URL"),
        ("skills/e2e_judge/scripts/harness.py", "KLAYOUT_MCP_URL"),
        ("skills/e2e_judge/scripts/verifier.py", "KLAYOUT_URL"),
    ],
)
def test_active_python_clients_honor_url_override(
    monkeypatch, relative_path, attribute
):
    override = "http://127.0.0.1:8766/mcp"
    monkeypatch.setenv("KLAYOUT_MCP_URL", override)

    module = _load_module(relative_path, "_port_contract_" + attribute)

    assert getattr(module, attribute) == override


def test_shared_client_reads_actual_plugin_config(monkeypatch):
    monkeypatch.delenv("KLAYOUT_MCP_URL", raising=False)
    client = _load_module(
        "skills/scripts/mcp_client.py", "_port_contract_mcp_client"
    )

    assert client.load_mcp_config(str(ROOT / ".mcp.json")) == DEFAULT_URL

    override = "http://127.0.0.1:8766/mcp"
    monkeypatch.setenv("KLAYOUT_MCP_URL", override)
    assert client.load_mcp_config(str(ROOT / ".mcp.json")) == override


def test_e2e_judge_passes_url_override_to_claude(monkeypatch):
    override = "http://127.0.0.1:8766/mcp"
    monkeypatch.setenv("KLAYOUT_MCP_URL", override)
    harness = _load_module(
        "skills/e2e_judge/scripts/harness.py", "_port_contract_harness"
    )
    observed = {}

    def fake_run(cmd, **kwargs):
        observed["cmd"] = cmd
        return types.SimpleNamespace(stdout="ok", stderr="", returncode=0)

    monkeypatch.setattr(harness.subprocess, "run", fake_run)
    result = harness.run_agent("probe", agent="claude")

    config_arg = observed["cmd"][observed["cmd"].index("--mcp-config") + 1]
    config = json.loads(config_arg)
    assert config["mcpServers"]["klayoutclaw"]["url"] == override
    assert result.success
