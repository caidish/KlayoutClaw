"""Shared identity probe for live KlayoutClaw tests."""

import json
import urllib.error
import urllib.request


def identity_error(data):
    """Return a diagnostic unless *data* is KlayoutClaw's GET identity."""
    if not isinstance(data, dict):
        return "GET /mcp did not return a JSON object"
    if data.get("status") != "ok" or data.get("server") != "KlayoutClaw":
        return (
            "GET /mcp answered, but it is not KlayoutClaw "
            f"(status={data.get('status')!r}, server={data.get('server')!r})"
        )
    return None


def is_klayoutclaw(url, timeout=2):
    """Return true only when GET *url* authenticates the server identity."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        return identity_error(data) is None
    except (urllib.error.URLError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
