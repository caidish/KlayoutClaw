import io
import json
import os
import subprocess
import sys
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from klayoutclaw.mcp_bridge import (
    BridgeError,
    HttpBridge,
    resolve_mcp_url,
    serve,
)


class FakeResponse:
    def __init__(self, body=b"", headers=None):
        self._body = body
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._body


def test_resolve_url_override_wins(tmp_path):
    config = tmp_path / "config.json"
    config.write_text('{"mcp":{"port":8766}}', encoding="utf-8")

    assert resolve_mcp_url(
        {
            "KLAYOUT_MCP_CONFIG": str(config),
            "KLAYOUT_MCP_URL": "https://layout.example:9443/custom",
        }
    ) == "https://layout.example:9443/custom"


def test_resolve_url_from_user_config(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "mcp": {
                    "bind": "0.0.0.0",
                    "port": 8766,
                    "endpoint": "/custom-mcp",
                    "tls": False,
                }
            }
        ),
        encoding="utf-8",
    )

    assert resolve_mcp_url({"KLAYOUT_MCP_CONFIG": str(config)}) == (
        "http://127.0.0.1:8766/custom-mcp"
    )


def test_resolve_url_defaults_when_config_is_absent(tmp_path):
    assert resolve_mcp_url(
        {"KLAYOUT_MCP_CONFIG": str(tmp_path / "missing.json")}
    ) == "http://127.0.0.1:8765/mcp"


@pytest.mark.parametrize(
    "settings, message",
    [
        ({"port": 0}, "mcp.port must be between 1 and 65535"),
        ({"port": "not-a-port"}, "mcp.port must be an integer"),
        ({"endpoint": "mcp"}, "mcp.endpoint must start with '/'"),
        ({"tls": "sometimes"}, "mcp.tls must be a boolean"),
    ],
)
def test_invalid_config_is_rejected(tmp_path, settings, message):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"mcp": settings}), encoding="utf-8")

    with pytest.raises(BridgeError, match=message):
        resolve_mcp_url({"KLAYOUT_MCP_CONFIG": str(config)})


def test_bridge_forwards_json_and_reuses_session_header():
    requests = []
    responses = iter(
        [
            FakeResponse(
                b'{"jsonrpc":"2.0","id":1,"result":{}}',
                {"Mcp-Session-Id": "session-1"},
            ),
            FakeResponse(b'{"jsonrpc":"2.0","id":2,"result":{"tools":[]}}'),
        ]
    )

    def opener(request):
        requests.append(request)
        return next(responses)

    bridge = HttpBridge("http://127.0.0.1:8766/mcp", opener=opener)
    first = bridge.exchange({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    second = bridge.exchange({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

    assert first["id"] == 1
    assert second["result"] == {"tools": []}
    assert requests[0].get_header("Mcp-session-id") is None
    assert requests[1].get_header("Mcp-session-id") == "session-1"
    assert json.loads(requests[0].data) == {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
    }


def test_bridge_suppresses_empty_notification_response():
    bridge = HttpBridge(
        "http://127.0.0.1:8765/mcp",
        opener=lambda _request: FakeResponse(),
    )

    assert bridge.exchange(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}
    ) is None


def test_serve_emits_transport_error_for_request():
    def refused(_request):
        raise urllib.error.URLError("connection refused")

    stdin = io.StringIO('{"jsonrpc":"2.0","id":7,"method":"ping"}\n')
    stdout = io.StringIO()
    bridge = HttpBridge("http://127.0.0.1:8765/mcp", opener=refused)

    assert serve(bridge, stdin=stdin, stdout=stdout, stderr=io.StringIO()) == 0
    response = json.loads(stdout.getvalue())
    assert response["id"] == 7
    assert response["error"]["code"] == -32000
    assert "connection refused" in response["error"]["message"]


def test_serve_keeps_stdout_protocol_clean_for_notification_errors():
    def refused(_request):
        raise urllib.error.URLError("connection refused")

    stdin = io.StringIO(
        '{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
    )
    stdout = io.StringIO()
    stderr = io.StringIO()
    bridge = HttpBridge("http://127.0.0.1:8765/mcp", opener=refused)

    serve(bridge, stdin=stdin, stdout=stdout, stderr=stderr)

    assert stdout.getvalue() == ""
    assert "connection refused" in stderr.getvalue()


def test_module_bridges_stdio_to_real_http_server():
    observed = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            request = json.loads(body)
            observed.append((request, self.headers.get("Mcp-Session-Id")))
            if "id" not in request:
                response_body = b""
            else:
                response_body = json.dumps(
                    {"jsonrpc": "2.0", "id": request["id"], "result": {}}
                ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.send_header("Mcp-Session-Id", "integration-session")
            self.end_headers()
            self.wfile.write(response_body)

        def log_message(self, _format, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = os.environ.copy()
        env["KLAYOUT_MCP_URL"] = (
            f"http://127.0.0.1:{server.server_address[1]}/mcp"
        )
        payload = (
            '{"jsonrpc":"2.0","id":1,"method":"initialize"}\n'
            '{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
        )
        result = subprocess.run(
            [sys.executable, "-m", "klayoutclaw.mcp_bridge"],
            input=payload,
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
            env=env,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert observed == [
        ({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, None),
        (
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            "integration-session",
        ),
    ]
