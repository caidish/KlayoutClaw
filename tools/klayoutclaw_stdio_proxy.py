#!/usr/bin/env python3
"""
Stdio-to-HTTP proxy for KlayoutClaw MCP server.

Bridges Claude Code's stdio MCP transport to the KlayoutClaw HTTP server
running at http://127.0.0.1:8765/mcp. This avoids OAuth discovery issues
that occur with the HTTP transport.

Usage (Claude Code):
  claude mcp add klayoutclaw -- python3 /path/to/klayoutclaw_stdio_proxy.py
"""

import sys
import json
import urllib.request
import urllib.error

SERVER_URL = "http://127.0.0.1:8765/mcp"


def post_json(data: dict) -> dict | None:
    """POST JSON-RPC to the KlayoutClaw server, return parsed response."""
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        SERVER_URL,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
            if not raw.strip():
                return None
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        return {
            "jsonrpc": "2.0",
            "id": data.get("id"),
            "error": {"code": -32000, "message": f"HTTP {e.code}: {error_body}"},
        }
    except Exception as e:
        return {
            "jsonrpc": "2.0",
            "id": data.get("id"),
            "error": {"code": -32000, "message": str(e)},
        }


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        resp = post_json(msg)

        # Notifications (no "id") get no response
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
