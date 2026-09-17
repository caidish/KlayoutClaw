"""Unit tests for the protocol-level connection check (no KLayout required)."""

import pytest

from test_connection import (
    identity_error,
    initialize_error,
    run_connection_test,
    tools_error,
)


@pytest.mark.parametrize(
    "body",
    [
        None,
        {},
        {"status": "ok"},
        {"status": "ok", "server": "AnkiConnect"},
        {"jsonrpc": "2.0", "result": None},
    ],
)
def test_get_identity_rejects_unrelated_responses(body):
    assert identity_error(body) is not None


def test_get_identity_accepts_klayoutclaw():
    assert identity_error({"status": "ok", "server": "KlayoutClaw"}) is None


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"error": {"code": -1, "message": "nope"}},
        {"result": None},
        {"result": []},
        {"result": {}},
        {"result": {"serverInfo": {"name": "AnkiConnect"}}},
        {
            "result": {
                "serverInfo": {"name": "KlayoutClaw"},
                "protocolVersion": "unsupported",
            }
        },
    ],
)
def test_initialize_rejects_malformed_or_wrong_server(body):
    assert initialize_error(body) is not None


def test_initialize_accepts_klayoutclaw():
    body = {
        "result": {
            "serverInfo": {"name": "KlayoutClaw", "version": "0.6"},
            "protocolVersion": "2025-03-26",
        }
    }
    assert initialize_error(body) is None


def test_tools_check_uses_stable_required_subset():
    tools = [
        {"name": "create_layout"},
        {"name": "execute_script"},
        {"name": "save_layout"},
        {"name": "get_layout_info"},
        {"name": "future_tool"},
    ]
    assert tools_error({"result": {"tools": tools}}) is None


def test_tools_check_reports_missing_required_tool():
    assert "get_layout_info" in tools_error(
        {"result": {"tools": [{"name": "create_layout"}]}}
    )


def test_full_check_rejects_another_service_without_traceback(monkeypatch, capsys):
    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"jsonrpc":"2.0","result":null}'

    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: FakeResponse())

    assert run_connection_test("http://127.0.0.1:8765/mcp", wait_seconds=0.1) == 1
    output = capsys.readouterr().out
    assert "not KlayoutClaw" in output
    assert "lsof -nP -iTCP:8765" in output
    assert "Traceback" not in output
