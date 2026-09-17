"""Bridge MCP messages on stdio to KlayoutClaw's HTTP endpoint.

The KLayout plugin speaks JSON-RPC over a small HTTP/1.0 server. Claude
Desktop launches local MCP integrations over stdio, so this module adapts the
transport without requiring Node.js or ``mcp-remote``.
"""

from __future__ import annotations

import ipaddress
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlsplit

DEFAULT_MCP_URL = "http://127.0.0.1:8765/mcp"
DEFAULT_CONFIG_PATH = Path("~/.klayout/klayoutclaw.json")


class BridgeError(RuntimeError):
    """Raised when the stdio-to-HTTP bridge cannot complete a request."""


def _as_bool(value: Any, *, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise BridgeError(f"{name} must be a boolean")


def _url_from_settings(settings: Mapping[str, Any]) -> str:
    try:
        port = int(settings.get("port", 8765))
    except (TypeError, ValueError) as exc:
        raise BridgeError("mcp.port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise BridgeError("mcp.port must be between 1 and 65535")

    host = str(settings.get("bind", "127.0.0.1")).strip()
    wildcard_addresses = {
        str(ipaddress.IPv4Address(0)),
        str(ipaddress.IPv6Address(0)),
    }
    if host == "*" or host in wildcard_addresses:
        host = "127.0.0.1"
    if not host:
        raise BridgeError("mcp.bind must not be empty")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"

    endpoint = str(settings.get("endpoint", "/mcp")).strip()
    if not endpoint.startswith("/"):
        raise BridgeError("mcp.endpoint must start with '/'")
    tls = _as_bool(settings.get("tls", False), name="mcp.tls")
    scheme = "https" if tls else "http"
    return f"{scheme}://{host}:{port}{endpoint}"


def _validate_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise BridgeError("KLAYOUT_MCP_URL must be an http(s) URL with a host")
    try:
        port = parsed.port
    except ValueError as exc:
        raise BridgeError(f"invalid MCP URL: {exc}") from exc
    if port is not None and not 1 <= port <= 65535:
        raise BridgeError("MCP URL port must be between 1 and 65535")
    return url


def resolve_mcp_url(environ: Mapping[str, str] | None = None) -> str:
    """Resolve the endpoint using the existing URL/config/default contract."""
    env = os.environ if environ is None else environ
    explicit_url = env.get("KLAYOUT_MCP_URL", "").strip()
    if explicit_url:
        return _validate_url(explicit_url)

    configured_path = env.get("KLAYOUT_MCP_CONFIG", "").strip()
    path = Path(configured_path or DEFAULT_CONFIG_PATH).expanduser()
    if not path.exists():
        return DEFAULT_MCP_URL
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BridgeError(f"cannot read MCP config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise BridgeError(f"MCP config {path} must contain a JSON object")
    settings = data.get("mcp", data)
    if not isinstance(settings, dict):
        raise BridgeError(f"MCP config {path} field 'mcp' must be a JSON object")
    return _url_from_settings(settings)


class HttpBridge:
    """Forward JSON-RPC values to one KlayoutClaw HTTP endpoint."""

    def __init__(
        self,
        url: str,
        *,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self.url = _validate_url(url)
        self._opener = opener
        self._session_id: str | None = None

    def exchange(self, message: Any) -> Any | None:
        """POST one JSON-RPC value and return its response, if any."""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        request = urllib.request.Request(
            self.url,
            data=json.dumps(message, separators=(",", ":")).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with self._opener(request) as response:
                session_id = response.headers.get("Mcp-Session-Id")
                if session_id:
                    self._session_id = session_id
                body = response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read()
            if not body.strip():
                raise BridgeError(f"KLayout MCP returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise BridgeError(f"cannot reach KLayout MCP at {self.url}: {exc}") from exc

        if not body.strip():
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BridgeError("KLayout MCP returned an invalid JSON response") from exc


def _request_id(message: Any) -> Any | None:
    return message.get("id") if isinstance(message, dict) else None


def _write_message(stream: TextIO, message: Any) -> None:
    stream.write(json.dumps(message, separators=(",", ":")) + "\n")
    stream.flush()


def serve(
    bridge: HttpBridge,
    *,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """Serve newline-delimited MCP messages until stdin reaches EOF."""
    for line in stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            _write_message(
                stdout,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {exc.msg}"},
                },
            )
            continue

        try:
            response = bridge.exchange(message)
        except BridgeError as exc:
            request_id = _request_id(message)
            if request_id is None:
                print(f"klayoutclaw-mcp: {exc}", file=stderr, flush=True)
                continue
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": str(exc)},
            }
        if response is not None:
            _write_message(stdout, response)
    return 0


def main() -> int:
    try:
        url = resolve_mcp_url()
    except BridgeError as exc:
        print(f"klayoutclaw-mcp: {exc}", file=sys.stderr)
        return 2
    return serve(HttpBridge(url))


if __name__ == "__main__":
    raise SystemExit(main())
