#!/usr/bin/env python
"""Shared MCP client for KlayoutClaw skills.

Provides helpers to call the KlayoutClaw MCP server at 127.0.0.1:8765.

Contract:
    - ``load_mcp_config()`` honors the ``KLAYOUT_MCP_URL`` environment variable
      before any config file lookup. This is the preferred injection point for
      containerized deployments (e.g. Docker: ``host.docker.internal:8765``).
    - ``mcp_call()`` raises ``RuntimeError`` on connection failure instead of
      calling ``sys.exit(1)``. Callers may catch this to fall back to a local
      mode; by default, an uncaught ``RuntimeError`` will terminate the script
      with a traceback, which matches the previous hard-fail behavior for
      scripts that do not install a handler.
"""

import json
import os
import urllib.error
import urllib.request

_DEFAULT_URL = "http://127.0.0.1:8765/mcp"
_DEFAULT_SERVER_CONFIG_PATH = os.path.expanduser("~/.klayout/klayoutclaw.json")


def _parse_server_config_bool(value, source):
    """Parse a JSON/environment boolean used by the local server config."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"{source}: 'mcp.tls' must be a boolean")


def _normalize_server_endpoint(value, source):
    """Validate the HTTP path served by KlayoutClaw."""
    if not isinstance(value, str) or not value.startswith("/"):
        raise ValueError(f"{source}: 'mcp.endpoint' must start with '/'")
    if (
        not value
        or any(char.isspace() for char in value)
        or "?" in value
        or "#" in value
    ):
        raise ValueError(
            f"{source}: 'mcp.endpoint' must be a path without whitespace, "
            "query, or fragment"
        )
    return value


def _url_from_server_config(cfg, source):
    """Build a client URL from ``~/.klayout/klayoutclaw.json``."""
    if not isinstance(cfg, dict):
        raise ValueError(f"{source}: top-level value must be an object")
    mcp = cfg.get("mcp", cfg)
    if not isinstance(mcp, dict):
        raise ValueError(f"{source}: 'mcp' must be an object")

    tls = _parse_server_config_bool(mcp.get("tls", False), source)
    raw_port = mcp.get("port", 8765)
    if isinstance(raw_port, bool):
        raise ValueError(
            f"{source}: 'mcp.port' must be an integer from 1 to 65535"
        )
    try:
        port = int(raw_port)
    except (TypeError, ValueError) as ex:
        raise ValueError(
            f"{source}: 'mcp.port' must be an integer from 1 to 65535"
        ) from ex
    if port < 1 or port > 65535:
        raise ValueError(
            f"{source}: 'mcp.port' must be an integer from 1 to 65535"
        )

    endpoint = _normalize_server_endpoint(mcp.get("endpoint", "/mcp"), source)
    host = mcp.get("bind", "127.0.0.1")
    if not isinstance(host, str) or not host.strip():
        raise ValueError(
            f"{source}: 'mcp.bind' must be a non-empty hostname or address"
        )
    host = host.strip()
    if host in {"0.0.0.0", "::", "*"}:
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    scheme = "https" if tls else "http"
    return f"{scheme}://{host}:{port}{endpoint}"


def _local_server_config_url(environ=None):
    """Return the local server URL when its per-user config exists."""
    if environ is None:
        environ = os.environ
    path = os.path.expanduser(
        environ.get("KLAYOUT_MCP_CONFIG", _DEFAULT_SERVER_CONFIG_PATH)
    )
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as config_file:
            cfg = json.load(config_file)
    except json.JSONDecodeError as ex:
        raise ValueError(f"{path}: invalid JSON: {ex}") from ex
    return _url_from_server_config(cfg, path)


# Seed MCP_URL from the KLAYOUT_MCP_URL env var at import time so every skill
# script that imports this module picks up the Docker-friendly override even
# if the caller never explicitly calls load_mcp_config(). The per-user server
# config is the next fallback so server and bundled skills share one endpoint.
MCP_URL = (
    os.environ.get("KLAYOUT_MCP_URL")
    or _local_server_config_url()
    or _DEFAULT_URL
)

# Default HTTP timeout for tool-invoking calls (execute_script, auto_route,
# evaluate_design). 300s matches the evaluate_design subprocess cap in the
# plugin. The OC ml14 benchmark showed the previous 30s default was too
# short for geometry queries over layouts with 200+ shapes — the server
# kept working but the client gave up early.
# Initialize/list calls pass a shorter override; see _SHORT_TIMEOUT below.
DEFAULT_TIMEOUT_SECONDS = 300
_SHORT_TIMEOUT = 30  # for initialize / tools_list / healthcheck-style calls
_req_id = 0
_session_id = None

# Server labels accepted in a Claude-Code-style mcpServers block. The plugin's
# own mcp_config.json labels the server "klayoutclaw", but qlaybot uses
# "klayout" (agent/src/mcp/manager.ts KLAYOUT_KEY) or "klayout_mcp"
# (agent/src/config.ts defaultConfig().mcp). The client only needs the URL
# string, not the label, so we resolve generically rather than pinning a name.
_KLAYOUT_KEY_HINTS = ("klayoutclaw", "klayout", "klayout_mcp")
_KLAYOUT_ENV_DEFAULT_PREFIX = "${KLAYOUT_MCP_URL:-"


def _entry_url(entry):
    """Extract the MCP server URL from a server entry dict.

    Handles two shapes:
    - Direct URL: ``{"url": "http://..."}``
    - stdio via mcp-remote: ``{"command": "npx", "args": ["mcp-remote", "http://...", ...]}``
      including Claude's ``${KLAYOUT_MCP_URL:-http://...}`` default expansion.

    Returns the URL string, or None if not extractable.
    """
    if not isinstance(entry, dict):
        return None
    # Direct "url" field (HTTP/SSE transport).
    if "url" in entry:
        return entry["url"]
    # stdio transport via mcp-remote: URL is the first http arg.
    args = entry.get("args") or []
    for arg in args:
        if isinstance(arg, str) and arg.startswith("http"):
            return arg
        if (
            isinstance(arg, str)
            and arg.startswith(_KLAYOUT_ENV_DEFAULT_PREFIX)
            and arg.endswith("}")
        ):
            default_url = arg[len(_KLAYOUT_ENV_DEFAULT_PREFIX):-1]
            if default_url.startswith("http"):
                return os.environ.get("KLAYOUT_MCP_URL") or default_url
    return None


def _extract_mcp_url(cfg, source):
    """Resolve the KlayoutClaw server URL from a parsed config dict.

    Handles three shapes seen in the wild:

    1. Claude-Code ``mcpServers`` block keyed by a known label (the plugin's
       own ``klayoutclaw``, or qlaybot's ``klayout`` / ``klayout_mcp``).
    2. Single-entry ``mcpServers`` block with an unknown label — trusted.
    3. Flat qlaybot ``klayout.json``: ``{"url": "...", ...}``.

    Also falls back to a server-label heuristic when multiple entries exist
    with no exact known label.

    Parameters
    ----------
    cfg : dict
        Parsed config JSON.
    source : str
        Human-readable origin (path) used only in error messages.

    Returns
    -------
    str
        The resolved MCP server URL.

    Raises
    ------
    KeyError
        Nothing usable found. Message includes the labels actually present.
    """
    # Flat shape — qlaybot's klayout.json.
    if isinstance(cfg, dict) and "mcpServers" not in cfg:
        if "url" in cfg:
            return cfg["url"]
        raise KeyError(
            f"{source}: no 'mcpServers' block and no top-level 'url' field"
        )

    servers = cfg.get("mcpServers") or {}

    # Known labels in priority order.
    for key in _KLAYOUT_KEY_HINTS:
        entry = servers.get(key)
        url = _entry_url(entry)
        if url:
            return url

    # Single-entry config with an unrecognized label.
    if len(servers) == 1:
        only = next(iter(servers.values()))
        url = _entry_url(only)
        if url:
            return url

    # Multi-entry heuristic: choose a server whose label identifies KLayout.
    # Do not infer identity from the default port: custom ports are supported,
    # and 8765 is also AnkiConnect's conventional port.
    for key, entry in servers.items():
        url = _entry_url(entry)
        if url and "klayout" in key.lower():
            return url

    raise KeyError(
        f"{source}: no KlayoutClaw server entry found under 'mcpServers'. "
        f"Tried keys {list(_KLAYOUT_KEY_HINTS)}; "
        f"present keys: {list(servers.keys())}"
    )


def _script_dir():
    """Return the directory containing this script."""
    return os.path.dirname(os.path.abspath(__file__))


def load_mcp_config(config_path=None):
    """Load MCP server URL from env var or a config file.

    Fallback order:
      0. ``KLAYOUT_MCP_URL`` environment variable (highest priority — used in
         Docker to point at ``http://host.docker.internal:8765/mcp``). If set,
         this bypasses the entire config-file chain.
      1. ``config_path`` (explicit ``--mcp-config`` flag)
      2. ``~/.klayout/klayoutclaw.json`` (or ``KLAYOUT_MCP_CONFIG``)
      3. ``.mcp.json`` in current working directory
      4. ``mcp_config.json`` in the KlayoutClaw project root
      5. ``klayout.json`` in the workspace or ``~/.qlaybot/config/``
      6. Default ``http://127.0.0.1:8765/mcp``

    Returns the resolved URL and sets the module-level ``MCP_URL``.
    """
    global MCP_URL

    # Fallback 0: environment variable (highest priority, for Docker etc.)
    env_url = os.environ.get("KLAYOUT_MCP_URL")
    if env_url:
        MCP_URL = env_url
        return env_url

    if config_path is not None:
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"MCP config not found: {config_path}")
        with open(config_path) as f:
            cfg = json.load(f)
        url = _extract_mcp_url(cfg, config_path)
        MCP_URL = url
        return url

    # Fallback 2: per-user server config shared with the KLayout macro.
    local_url = _local_server_config_url()
    if local_url:
        MCP_URL = local_url
        return local_url

    # Fallback 3: .mcp.json in current working directory
    cwd_cfg = os.path.join(os.getcwd(), ".mcp.json")
    if os.path.exists(cwd_cfg):
        with open(cwd_cfg) as f:
            cfg = json.load(f)
        url = _extract_mcp_url(cfg, cwd_cfg)
        MCP_URL = url
        return url

    # Fallback 4: mcp_config.json in project root (two levels up from skills/scripts/)
    project_root = os.path.dirname(os.path.dirname(_script_dir()))
    root_cfg = os.path.join(project_root, "mcp_config.json")
    if os.path.exists(root_cfg):
        with open(root_cfg) as f:
            cfg = json.load(f)
        url = _extract_mcp_url(cfg, root_cfg)
        MCP_URL = url
        return url

    # Fallback 5: klayout.json in the workspace or ~/.qlaybot/config
    workspace_cfg = os.path.join(os.getcwd(), "klayout.json")
    qlaybot_root_cfg = os.path.expanduser(
        os.path.join("~/.qlaybot/config", "klayout.json")
    )
    if os.path.exists(workspace_cfg):
        with open(workspace_cfg) as f:
            cfg = json.load(f)
        url = cfg["url"]
        MCP_URL = url
        return url
    if os.path.exists(qlaybot_root_cfg):
        with open(qlaybot_root_cfg) as f:
            cfg = json.load(f)
        url = cfg["url"]
        MCP_URL = url
        return url
    # Default
    MCP_URL = _DEFAULT_URL
    return _DEFAULT_URL


def mcp_call(method, params=None, timeout=DEFAULT_TIMEOUT_SECONDS):
    """Send a JSON-RPC 2.0 request to the MCP server.

    Default timeout is :data:`DEFAULT_TIMEOUT_SECONDS` (300s). Short-running
    calls (initialize, tools/list) should pass ``timeout=_SHORT_TIMEOUT`` so
    connection failures surface quickly instead of hanging for 5 minutes.
    """
    global _req_id, _session_id
    _req_id += 1
    payload = {"jsonrpc": "2.0", "id": _req_id, "method": method}
    if params:
        payload["params"] = params
    headers = {"Content-Type": "application/json"}
    if _session_id:
        headers["Mcp-Session-Id"] = _session_id
    req = urllib.request.Request(
        MCP_URL, data=json.dumps(payload).encode(),
        headers=headers, method="POST",
    )
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.URLError as e:
        # Raise instead of sys.exit so callers can catch this and decide
        # whether to fall back to a local mode. See module docstring.
        raise RuntimeError(
            f"Cannot connect to KLayout MCP at {MCP_URL}: {e}. "
            f"Make sure KLayout is running with the KlayoutClaw plugin, "
            f"or set KLAYOUT_MCP_URL to override the server URL."
        ) from e
    _session_id = r.headers.get("Mcp-Session-Id", _session_id)
    data = json.loads(r.read().decode())
    if "error" in data:
        raise RuntimeError(f"MCP error: {data['error']}")
    return data


def tool_call(tool_name, timeout=DEFAULT_TIMEOUT_SECONDS, **kwargs):
    """Call an MCP tool and return parsed JSON result."""
    result = mcp_call("tools/call", {"name": tool_name, "arguments": kwargs}, timeout=timeout)
    text = result["result"]["content"][0]["text"]
    return json.loads(text)


def execute_script(code, timeout=DEFAULT_TIMEOUT_SECONDS):
    """Execute Python/pya code in KLayout via execute_script tool.

    ``timeout`` is the HTTP read deadline in seconds; defaults to
    :data:`DEFAULT_TIMEOUT_SECONDS` (300). Raise for long-running geometry
    scripts (e.g. iterating across hundreds of shapes).
    """
    return tool_call("execute_script", timeout=timeout, code=code)


def init_session():
    """Initialize MCP session (call once at start).

    Uses the short timeout — if the server isn't reachable, we want to fail
    fast rather than wait 5 minutes for the connection error to surface.
    """
    mcp_call("initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "klayoutclaw-skill", "version": "0.3"},
    }, timeout=_SHORT_TIMEOUT)
