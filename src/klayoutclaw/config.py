"""Central configuration model for KlayoutClaw MCP clients.

Resolution is deterministic and per-field:

    command-line flags > environment > config file > defaults

The KLayout GUI server has no command-line surface, so it uses the available
subset: environment > config file > defaults.
"""

from __future__ import annotations

import ipaddress
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

CONFIG_SCHEMA = 1
DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_ENDPOINT = "/mcp"
DEFAULT_TLS = False
DEFAULT_CONFIG_PATH = Path("~/.klayout/klayoutclaw.json")
DEFAULT_MCP_URL = "http://127.0.0.1:8765/mcp"

FIELD_ENV_VARS = {
    "bind": "KLAYOUT_MCP_BIND",
    "port": "KLAYOUT_MCP_PORT",
    "endpoint": "KLAYOUT_MCP_ENDPOINT",
    "tls": "KLAYOUT_MCP_TLS",
}


class ConfigError(ValueError):
    """Raised when endpoint configuration is invalid or inconsistent."""


@dataclass(frozen=True)
class ResolvedMcpConfig:
    """Validated effective client endpoint plus provenance for each value."""

    bind: str
    port: int
    endpoint: str
    tls: bool
    config_path: Path
    config_path_source: str
    sources: dict[str, str]

    @property
    def url(self) -> str:
        host = self.bind
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        scheme = "https" if self.tls else "http"
        return f"{scheme}://{host}:{self.port}{self.endpoint}"

    def as_dict(self) -> dict[str, Any]:
        """Return non-secret effective configuration suitable for diagnostics."""
        return {
            "schema": CONFIG_SCHEMA,
            "url": self.url,
            "mcp": {
                "bind": self.bind,
                "port": self.port,
                "endpoint": self.endpoint,
                "tls": self.tls,
            },
            "config_path": str(self.config_path),
            "sources": {
                **self.sources,
                "config_path": self.config_path_source,
            },
        }


def _parse_bool(value: Any, *, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ConfigError(f"{name} must be a boolean")


def _parse_port(value: Any, *, name: str) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{name} must be an integer")
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ConfigError(f"{name} must be between 1 and 65535")
    return port


def _normalize_bind(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} must be a non-empty hostname or address")
    host = value.strip()
    wildcard_addresses = {
        str(ipaddress.IPv4Address(0)),
        str(ipaddress.IPv6Address(0)),
    }
    if host == "*" or host in wildcard_addresses:
        return DEFAULT_BIND
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host


def _normalize_endpoint(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value.startswith("/"):
        raise ConfigError(f"{name} must start with '/'")
    if any(char.isspace() for char in value) or "?" in value or "#" in value:
        raise ConfigError(f"{name} must be a path without whitespace, query, or fragment")
    return value


def _settings_from_url(url: str, *, name: str) -> dict[str, Any]:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ConfigError(f"{name} is invalid: {exc}") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigError(f"{name} must be an http(s) URL with a host")
    if parsed.username is not None or parsed.password is not None:
        raise ConfigError(f"{name} must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ConfigError(f"{name} must not contain a query or fragment")
    return {
        "bind": _normalize_bind(parsed.hostname, name=name),
        "port": port or (443 if parsed.scheme == "https" else 80),
        "endpoint": _normalize_endpoint(parsed.path or DEFAULT_ENDPOINT, name=name),
        "tls": parsed.scheme == "https",
    }


def normalize_mcp_url(url: str, *, name: str = "MCP URL") -> str:
    """Validate and canonicalize a complete MCP endpoint URL."""
    settings = _settings_from_url(url, name=name)
    return _url_from_values(settings)


def _url_from_values(values: Mapping[str, Any]) -> str:
    host = str(values["bind"])
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    scheme = "https" if values["tls"] else "http"
    return f"{scheme}://{host}:{values['port']}{values['endpoint']}"


def _read_config(path: Path, *, required: bool) -> Mapping[str, Any]:
    if not path.exists():
        if required:
            raise ConfigError(f"configured MCP config does not exist: {path}")
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read MCP config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"MCP config {path} must contain a JSON object")
    schema = data.get("schema", CONFIG_SCHEMA)
    if (
        isinstance(schema, bool)
        or not isinstance(schema, int)
        or schema != CONFIG_SCHEMA
    ):
        raise ConfigError(
            f"MCP config {path} uses unsupported schema {schema!r}; "
            f"expected {CONFIG_SCHEMA}"
        )
    settings = data.get("mcp", data)
    if not isinstance(settings, dict):
        raise ConfigError(f"MCP config {path} field 'mcp' must be a JSON object")
    return settings


def _apply_url(
    values: dict[str, Any],
    sources: dict[str, str],
    url: str,
    *,
    source: str,
    name: str,
) -> None:
    parsed = _settings_from_url(url, name=name)
    values.update(parsed)
    sources.update({field: source for field in parsed})


def _apply_fields(
    values: dict[str, Any],
    sources: dict[str, str],
    layer: Mapping[str, Any],
    *,
    source: str | Mapping[str, str],
    prefix: str,
) -> None:
    def field_source(field: str) -> str:
        return source[field] if isinstance(source, Mapping) else source

    if "bind" in layer and layer["bind"] is not None:
        values["bind"] = _normalize_bind(layer["bind"], name=f"{prefix}.bind")
        sources["bind"] = field_source("bind")
    if "port" in layer and layer["port"] is not None:
        values["port"] = _parse_port(layer["port"], name=f"{prefix}.port")
        sources["port"] = field_source("port")
    if "endpoint" in layer and layer["endpoint"] is not None:
        values["endpoint"] = _normalize_endpoint(
            layer["endpoint"], name=f"{prefix}.endpoint"
        )
        sources["endpoint"] = field_source("endpoint")
    if "tls" in layer and layer["tls"] is not None:
        values["tls"] = _parse_bool(layer["tls"], name=f"{prefix}.tls")
        sources["tls"] = field_source("tls")


def resolve_mcp_config(
    cli: Mapping[str, Any] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    default_config_path: str | os.PathLike[str] | None = None,
) -> ResolvedMcpConfig:
    """Resolve endpoint config with flags > environment > file > defaults."""
    flags = {} if cli is None else cli
    env = os.environ if environ is None else environ

    flag_path = str(flags.get("config") or "").strip()
    env_path = env.get("KLAYOUT_MCP_CONFIG", "").strip()
    if flag_path:
        config_path = Path(flag_path).expanduser()
        config_path_source = "flag:--config"
        config_required = True
    elif env_path:
        config_path = Path(env_path).expanduser()
        config_path_source = "env:KLAYOUT_MCP_CONFIG"
        config_required = True
    else:
        configured_default = default_config_path or DEFAULT_CONFIG_PATH
        config_path = Path(configured_default).expanduser()
        config_path_source = "default"
        config_required = False

    values: dict[str, Any] = {
        "bind": DEFAULT_BIND,
        "port": DEFAULT_PORT,
        "endpoint": DEFAULT_ENDPOINT,
        "tls": DEFAULT_TLS,
    }
    sources = {field: "default" for field in values}

    file_settings = _read_config(config_path, required=config_required)
    if file_settings:
        file_source = f"config:{config_path}"
        file_url = file_settings.get("url")
        if file_url:
            _apply_url(
                values,
                sources,
                str(file_url),
                source=f"{file_source}:url",
                name="mcp.url",
            )
        _apply_fields(
            values,
            sources,
            file_settings,
            source=file_source,
            prefix="mcp",
        )

    env_url = env.get("KLAYOUT_MCP_URL", "").strip()
    if env_url:
        _apply_url(
            values,
            sources,
            env_url,
            source="env:KLAYOUT_MCP_URL",
            name="KLAYOUT_MCP_URL",
        )
    else:
        env_fields = {
            field: env.get(variable)
            for field, variable in FIELD_ENV_VARS.items()
            if env.get(variable) not in {None, ""}
        }
        _apply_fields(
            values,
            sources,
            env_fields,
            source={
                field: f"env:{FIELD_ENV_VARS[field]}" for field in env_fields
            },
            prefix="environment",
        )

    flag_url = str(flags.get("url") or "").strip()
    if flag_url:
        _apply_url(
            values,
            sources,
            flag_url,
            source="flag:--url",
            name="--url",
        )
    _apply_fields(
        values,
        sources,
        flags,
        source={
            field: f"flag:--{field.replace('_', '-')}"
            for field in ("bind", "port", "endpoint", "tls")
        },
        prefix="flags",
    )

    return ResolvedMcpConfig(
        bind=values["bind"],
        port=values["port"],
        endpoint=values["endpoint"],
        tls=values["tls"],
        config_path=config_path,
        config_path_source=config_path_source,
        sources=sources,
    )
