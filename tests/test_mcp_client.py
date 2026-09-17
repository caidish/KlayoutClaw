#!/usr/bin/env python
"""Unit tests for mcp_client._extract_mcp_url.

Regression tests for the qlaybot commit_gds.py bug: the shared MCP client
used to hardcode the lookup ``cfg["mcpServers"]["klayoutclaw"]["url"]``,
which raised ``KeyError`` whenever the config file labeled the server
anything else (qlaybot canonically uses ``"klayout"``).

These tests pin the contract of ``_extract_mcp_url``: it must accept any
of the known label names, fall back to a single-entry config, handle
qlaybot's flat ``klayout.json`` shape, and raise a useful ``KeyError``
when no entry can be resolved. No network access, no KLayout needed.
"""
import json
import os
import sys

import pytest

_SKILLS_SCRIPTS = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "skills", "scripts")
)
if _SKILLS_SCRIPTS not in sys.path:
    sys.path.insert(0, _SKILLS_SCRIPTS)

from mcp_client import (  # noqa: E402, I001
    _entry_url,
    _extract_mcp_url,
    _url_from_server_config,
    load_mcp_config,
)


# ---------------------------------------------------------------------------
# Known label resolution
# ---------------------------------------------------------------------------

def test_resolves_legacy_klayoutclaw_label():
    """The plugin's own mcp_config.json shape must still work."""
    cfg = {"mcpServers": {"klayoutclaw": {"url": "http://127.0.0.1:8765/mcp"}}}
    assert _extract_mcp_url(cfg, "legacy.json") == "http://127.0.0.1:8765/mcp"


def test_resolves_claude_env_default_argument(monkeypatch):
    entry = {
        "command": "npx",
        "args": [
            "mcp-remote",
            "${KLAYOUT_MCP_URL:-http://127.0.0.1:8765/mcp}",
            "--allow-http",
        ],
    }

    monkeypatch.delenv("KLAYOUT_MCP_URL", raising=False)
    assert _entry_url(entry) == "http://127.0.0.1:8765/mcp"

    monkeypatch.setenv("KLAYOUT_MCP_URL", "http://127.0.0.1:8766/mcp")
    assert _entry_url(entry) == "http://127.0.0.1:8766/mcp"


def test_resolves_packaged_uvx_bridge(monkeypatch, tmp_path):
    entry = {
        "command": "uvx",
        "args": ["--from", "klayoutclaw", "klayoutclaw-mcp"],
    }
    config = tmp_path / "klayoutclaw.json"
    config.write_text('{"mcp":{"port":8766}}', encoding="utf-8")
    monkeypatch.delenv("KLAYOUT_MCP_URL", raising=False)
    monkeypatch.setenv("KLAYOUT_MCP_CONFIG", str(config))

    assert _entry_url(entry) == "http://127.0.0.1:8766/mcp"

    monkeypatch.setenv("KLAYOUT_MCP_URL", "http://127.0.0.1:8767/custom")
    assert _entry_url(entry) == "http://127.0.0.1:8767/custom"


def test_resolves_qlaybot_klayout_label():
    """Regression: qlaybot writes its server under the 'klayout' key."""
    cfg = {"mcpServers": {"klayout": {"url": "http://127.0.0.1:8765/mcp"}}}
    assert _extract_mcp_url(cfg, "qlaybot.json") == "http://127.0.0.1:8765/mcp"


def test_resolves_klayout_mcp_label():
    """Migration path from qlaybot's defaultConfig().mcp.klayout_mcp."""
    cfg = {"mcpServers": {"klayout_mcp": {"url": "http://127.0.0.1:8765/mcp"}}}
    assert _extract_mcp_url(cfg, "migration.json") == "http://127.0.0.1:8765/mcp"


def test_klayoutclaw_wins_over_klayout_when_both_present():
    """Priority order: the preferred label resolves first."""
    cfg = {
        "mcpServers": {
            "klayout": {"url": "http://OTHER:8765/mcp"},
            "klayoutclaw": {"url": "http://WIN:8765/mcp"},
        }
    }
    assert _extract_mcp_url(cfg, "both.json") == "http://WIN:8765/mcp"


# ---------------------------------------------------------------------------
# Single-entry fallback
# ---------------------------------------------------------------------------

def test_single_unknown_labeled_entry_is_used():
    """A user's personal .mcp.json with a custom label still works."""
    cfg = {"mcpServers": {"my-dev-klayout": {"url": "http://127.0.0.1:8765/mcp"}}}
    assert _extract_mcp_url(cfg, "custom.json") == "http://127.0.0.1:8765/mcp"


# ---------------------------------------------------------------------------
# Flat qlaybot klayout.json shape
# ---------------------------------------------------------------------------

def test_flat_klayout_config_shape():
    """qlaybot's ~/.qlaybot/config/klayout.json has no 'mcpServers' wrapper."""
    cfg = {"url": "http://127.0.0.1:8765/mcp", "required": True}
    assert _extract_mcp_url(cfg, "klayout.json") == "http://127.0.0.1:8765/mcp"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

def test_multi_entry_with_no_known_label_and_no_url_hint_raises():
    """Ambiguous config with nothing resembling klayout should fail loudly."""
    cfg = {
        "mcpServers": {
            "some-other-mcp": {"url": "http://example.com/mcp"},
            "another-mcp": {"url": "https://api.example.org/v1"},
        }
    }
    with pytest.raises(KeyError) as excinfo:
        _extract_mcp_url(cfg, "ambiguous.json")
    # Error message should list present keys so operators can fix it fast.
    msg = str(excinfo.value)
    assert "some-other-mcp" in msg
    assert "another-mcp" in msg


def test_empty_mcp_servers_with_no_top_level_url_raises():
    """Empty config can't silently resolve to anything."""
    cfg = {"mcpServers": {}}
    with pytest.raises(KeyError):
        _extract_mcp_url(cfg, "empty.json")


def test_label_heuristic_resolves_custom_klayout_server():
    """Multi-entry config resolves a custom label rather than a port hint."""
    cfg = {
        "mcpServers": {
            "other-mcp": {"url": "http://example.com/api"},
            "my-dev-klayout": {"url": "http://127.0.0.1:8766/mcp"},
        }
    }
    assert _extract_mcp_url(cfg, "heur.json") == "http://127.0.0.1:8766/mcp"


def test_default_port_does_not_identify_klayout_in_ambiguous_config():
    """Port 8765 alone is not proof of identity because AnkiConnect uses it."""
    cfg = {
        "mcpServers": {
            "anki": {"url": "http://127.0.0.1:8765/mcp"},
            "other": {"url": "http://example.com/mcp"},
        }
    }
    with pytest.raises(KeyError):
        _extract_mcp_url(cfg, "ambiguous.json")


# ---------------------------------------------------------------------------
# End-to-end: load_mcp_config through a qlaybot-shape file
# ---------------------------------------------------------------------------

def test_load_mcp_config_accepts_qlaybot_klayout_label(tmp_path, monkeypatch):
    """Regression for commit_gds.py: qlaybot writes "klayout" as the label."""
    # Pre-condition: clear KLAYOUT_MCP_URL so file lookup is actually used.
    monkeypatch.delenv("KLAYOUT_MCP_URL", raising=False)

    cfg_path = tmp_path / "qlaybot_style.json"
    cfg_path.write_text(json.dumps({
        "mcpServers": {
            "klayout": {"type": "http", "url": "http://127.0.0.1:8765/mcp"}
        }
    }))

    url = load_mcp_config(str(cfg_path))
    assert url == "http://127.0.0.1:8765/mcp"


def test_server_config_builds_http_url():
    cfg = {"mcp": {"tls": False, "port": 8766, "endpoint": "/custom-mcp"}}
    assert _url_from_server_config(cfg, "server.json") == (
        "http://127.0.0.1:8766/custom-mcp"
    )


def test_server_config_builds_https_url():
    cfg = {
        "mcp": {
            "tls": True,
            "bind": "localhost",
            "port": 9443,
            "endpoint": "/secure-mcp",
        }
    }
    assert _url_from_server_config(cfg, "server.json") == (
        "https://localhost:9443/secure-mcp"
    )


def test_server_config_uses_loopback_for_wildcard_bind():
    cfg = {"mcp": {"bind": "0.0.0.0", "port": 8766, "endpoint": "/mcp"}}
    assert _url_from_server_config(cfg, "server.json") == (
        "http://127.0.0.1:8766/mcp"
    )


def test_local_server_config_precedes_repository_config(tmp_path, monkeypatch):
    server_config = tmp_path / "klayoutclaw.json"
    server_config.write_text(json.dumps({
        "mcp": {"tls": False, "port": 8766, "endpoint": "/desktop-mcp"}
    }))
    (tmp_path / ".mcp.json").write_text(json.dumps({
        "mcpServers": {
            "klayoutclaw": {"url": "http://127.0.0.1:8765/mcp"}
        }
    }))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KLAYOUT_MCP_URL", raising=False)
    monkeypatch.setenv("KLAYOUT_MCP_CONFIG", str(server_config))

    assert load_mcp_config() == "http://127.0.0.1:8766/desktop-mcp"


@pytest.mark.parametrize(
    "config",
    [
        {"mcp": {"tls": "sometimes"}},
        {"mcp": {"port": 0}},
        {"mcp": {"port": 65536}},
        {"mcp": {"endpoint": ""}},
        {"mcp": {"endpoint": "/mcp?query=1"}},
    ],
)
def test_server_config_url_rejects_invalid_values(config):
    with pytest.raises(ValueError):
        _url_from_server_config(config, "server.json")
