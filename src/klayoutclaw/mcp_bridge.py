"""Bridge MCP messages on stdio to KlayoutClaw's HTTP endpoint.

The KLayout plugin speaks JSON-RPC over a small HTTP/1.0 server. Claude
Desktop launches local MCP integrations over stdio, so this module adapts the
transport without requiring Node.js or ``mcp-remote``.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import Any, TextIO

from .config import ConfigError, normalize_mcp_url, resolve_mcp_config


class BridgeError(RuntimeError):
    """Raised when the stdio-to-HTTP bridge cannot complete a request."""


def resolve_mcp_url(environ: Mapping[str, str] | None = None) -> str:
    """Backward-compatible URL-only wrapper around the central resolver."""
    try:
        return resolve_mcp_config(environ=environ).url
    except ConfigError as exc:
        raise BridgeError(str(exc)) from exc


class HttpBridge:
    """Forward JSON-RPC values to one KlayoutClaw HTTP endpoint."""

    def __init__(
        self,
        url: str,
        *,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        try:
            self.url = normalize_mcp_url(url)
        except ConfigError as exc:
            raise BridgeError(str(exc)) from exc
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="klayoutclaw-mcp",
        description="Bridge MCP on stdio to the KlayoutClaw HTTP endpoint",
    )
    parser.add_argument("--url", help="complete MCP URL")
    parser.add_argument(
        "--config",
        help="JSON config path (default: KLAYOUT_MCP_CONFIG or ~/.klayout/klayoutclaw.json)",
    )
    parser.add_argument("--bind", help="MCP hostname or address")
    parser.add_argument("--port", type=int, help="MCP TCP port")
    parser.add_argument("--endpoint", help="MCP HTTP path")
    parser.add_argument(
        "--tls",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="use HTTPS (or --no-tls for HTTP)",
    )
    parser.add_argument(
        "--print-config",
        action="store_true",
        help="print effective non-secret config and provenance, then exit",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        resolved = resolve_mcp_config(vars(args))
    except ConfigError as exc:
        print(f"klayoutclaw-mcp: {exc}", file=sys.stderr)
        return 2
    if args.print_config:
        print(json.dumps(resolved.as_dict(), indent=2, sort_keys=True))
        return 0
    return serve(HttpBridge(resolved.url))


if __name__ == "__main__":
    raise SystemExit(main())
