import json

import pytest

from klayoutclaw.config import ConfigError, resolve_mcp_config
from klayoutclaw.mcp_bridge import main


def write_config(path, **mcp):
    path.write_text(
        json.dumps({"schema": 1, "mcp": mcp}),
        encoding="utf-8",
    )


def test_defaults_when_implicit_config_is_absent(tmp_path):
    resolved = resolve_mcp_config(
        environ={}, default_config_path=tmp_path / "missing.json"
    )

    assert resolved.url == "http://127.0.0.1:8765/mcp"
    assert resolved.sources == {
        "bind": "default",
        "port": "default",
        "endpoint": "default",
        "tls": "default",
    }


def test_config_file_overrides_defaults(tmp_path):
    config = tmp_path / "config.json"
    write_config(
        config,
        bind="localhost",
        port=8766,
        endpoint="/from-file",
        tls=True,
    )

    resolved = resolve_mcp_config(
        environ={"KLAYOUT_MCP_CONFIG": str(config)}
    )

    assert resolved.url == "https://localhost:8766/from-file"
    assert set(resolved.sources.values()) == {f"config:{config}"}
    assert resolved.config_path_source == "env:KLAYOUT_MCP_CONFIG"


def test_field_environment_overrides_config_per_value(tmp_path):
    config = tmp_path / "config.json"
    write_config(
        config,
        bind="from-file",
        port=8765,
        endpoint="/from-file",
        tls=False,
    )

    resolved = resolve_mcp_config(
        environ={
            "KLAYOUT_MCP_CONFIG": str(config),
            "KLAYOUT_MCP_PORT": "8766",
            "KLAYOUT_MCP_TLS": "true",
        }
    )

    assert resolved.url == "https://from-file:8766/from-file"
    assert resolved.sources["bind"] == f"config:{config}"
    assert resolved.sources["port"] == "env:KLAYOUT_MCP_PORT"
    assert resolved.sources["tls"] == "env:KLAYOUT_MCP_TLS"


def test_complete_environment_url_is_authoritative_within_env_layer(tmp_path):
    config = tmp_path / "config.json"
    write_config(config, port=8765)

    resolved = resolve_mcp_config(
        environ={
            "KLAYOUT_MCP_CONFIG": str(config),
            "KLAYOUT_MCP_URL": "https://env.example:9443/env-mcp",
            "KLAYOUT_MCP_PORT": "9999",
        }
    )

    assert resolved.url == "https://env.example:9443/env-mcp"
    assert set(resolved.sources.values()) == {"env:KLAYOUT_MCP_URL"}


def test_cli_fields_override_environment_url(tmp_path):
    resolved = resolve_mcp_config(
        {"port": 8767, "endpoint": "/flag-mcp", "tls": False},
        environ={"KLAYOUT_MCP_URL": "https://env.example:9443/env-mcp"},
        default_config_path=tmp_path / "missing.json",
    )

    assert resolved.url == "http://env.example:8767/flag-mcp"
    assert resolved.sources == {
        "bind": "env:KLAYOUT_MCP_URL",
        "port": "flag:--port",
        "endpoint": "flag:--endpoint",
        "tls": "flag:--tls",
    }


def test_cli_url_can_be_refined_by_cli_component_flags(tmp_path):
    resolved = resolve_mcp_config(
        {"url": "https://flag.example/base", "port": 9443},
        environ={},
        default_config_path=tmp_path / "missing.json",
    )

    assert resolved.url == "https://flag.example:9443/base"
    assert resolved.sources["bind"] == "flag:--url"
    assert resolved.sources["port"] == "flag:--port"


def test_cli_config_path_overrides_environment_path(tmp_path):
    flag_config = tmp_path / "flag.json"
    env_config = tmp_path / "env.json"
    write_config(flag_config, port=8767)
    write_config(env_config, port=8766)

    resolved = resolve_mcp_config(
        {"config": str(flag_config)},
        environ={"KLAYOUT_MCP_CONFIG": str(env_config)},
    )

    assert resolved.port == 8767
    assert resolved.config_path == flag_config
    assert resolved.config_path_source == "flag:--config"


@pytest.mark.parametrize("source", ["flag", "environment"])
def test_explicit_missing_config_is_an_error(tmp_path, source):
    missing = tmp_path / "missing.json"
    cli = {"config": str(missing)} if source == "flag" else None
    env = (
        {}
        if source == "flag"
        else {"KLAYOUT_MCP_CONFIG": str(missing)}
    )

    with pytest.raises(ConfigError, match="does not exist"):
        resolve_mcp_config(cli, environ=env)


def test_legacy_unversioned_config_is_accepted(tmp_path):
    config = tmp_path / "legacy.json"
    config.write_text('{"mcp":{"port":8766}}', encoding="utf-8")

    assert resolve_mcp_config(
        environ={"KLAYOUT_MCP_CONFIG": str(config)}
    ).port == 8766


def test_unknown_config_schema_is_rejected(tmp_path):
    config = tmp_path / "future.json"
    config.write_text('{"schema":2,"mcp":{}}', encoding="utf-8")

    with pytest.raises(ConfigError, match="unsupported schema 2"):
        resolve_mcp_config(environ={"KLAYOUT_MCP_CONFIG": str(config)})


@pytest.mark.parametrize("schema", [True, 1.0, "1"])
def test_non_integer_config_schema_is_rejected(tmp_path, schema):
    config = tmp_path / "invalid-schema.json"
    config.write_text(json.dumps({"schema": schema, "mcp": {}}), encoding="utf-8")

    with pytest.raises(ConfigError, match="unsupported schema"):
        resolve_mcp_config(environ={"KLAYOUT_MCP_CONFIG": str(config)})


@pytest.mark.parametrize(
    "url",
    [
        "ftp://127.0.0.1/mcp",
        "http://user:password@127.0.0.1/mcp",
        "http://127.0.0.1/mcp?secret=value",
    ],
)
def test_unsafe_or_unsupported_urls_are_rejected(tmp_path, url):
    with pytest.raises(ConfigError):
        resolve_mcp_config(
            {"url": url},
            environ={},
            default_config_path=tmp_path / "missing.json",
        )


def test_print_config_is_non_secret_and_reports_provenance(
    tmp_path, monkeypatch, capsys
):
    config = tmp_path / "config.json"
    write_config(config, port=8766, certificate="secret-cert-path")
    monkeypatch.setenv("KLAYOUT_MCP_CONFIG", str(config))
    monkeypatch.setenv("KLAYOUT_MCP_ENDPOINT", "/from-env")

    assert main(["--port", "8767", "--print-config"]) == 0
    output = capsys.readouterr().out
    effective = json.loads(output)

    assert effective["url"] == "http://127.0.0.1:8767/from-env"
    assert effective["sources"]["port"] == "flag:--port"
    assert effective["sources"]["endpoint"] == "env:KLAYOUT_MCP_ENDPOINT"
    assert "secret-cert-path" not in output
