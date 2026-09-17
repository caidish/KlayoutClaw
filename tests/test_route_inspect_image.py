"""Issue #34 — route_inspect labeled-crossings image.

Two tiers of test:

1. **Static** (no KLayout required) — parses the .lym source and asserts
   that the ``image_path`` arg is wired through the schema, the
   helper ``_render_route_inspect_crossings_image`` exists, and the
   tool returns ``image_path`` in its JSON envelope.
2. **Snapshot** (requires running KLayout MCP server) — synthesises a
   small layout with two intentionally crossing routes, calls
   ``route_inspect`` via MCP, and asserts the returned ``image_path``
   resolves to a non-empty PNG. Skips when MCP is not reachable.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from mcp_identity import is_klayoutclaw


REPO_ROOT = Path(__file__).resolve().parents[1]
LYM_PATH = REPO_ROOT / "plugin" / "klayoutclaw_server.lym"
MCP_URL = os.environ.get("KLAYOUT_MCP_URL", "http://127.0.0.1:8765/mcp")


def _server_source() -> str:
    text = ET.parse(LYM_PATH).getroot().find("text").text
    assert text, "klayoutclaw_server.lym <text> empty"
    return text


# ---------------------------------------------------------------------------
# Tier 1: static structure tests
# ---------------------------------------------------------------------------

def test_route_inspect_schema_advertises_image_path():
    src = _server_source()
    # Find the route_inspect TOOLS entry and check image_path appears in it.
    m = re.search(
        r'"name":\s*"route_inspect".*?"required":\s*\[[^\]]+\]\s*\}\s*\}',
        src, re.S,
    )
    assert m, "Could not locate route_inspect TOOLS entry"
    block = m.group(0)
    assert '"image_path"' in block, (
        "route_inspect schema is missing the 'image_path' property "
        "(issue #34). Block snippet:\n" + block[:600]
    )


def test_render_helper_defined():
    src = _server_source()
    assert "def _render_route_inspect_crossings_image(" in src, (
        "Expected helper _render_route_inspect_crossings_image() to be "
        "defined in klayoutclaw_server.lym (issue #34)."
    )


def test_route_inspect_returns_image_path_field():
    src = _server_source()
    # The return-dict in _tool_route_inspect must include the new key.
    m = re.search(r"def _tool_route_inspect\(args\):(.+?)\ndef ", src, re.S)
    assert m, "Could not locate _tool_route_inspect body"
    body = m.group(1)
    assert '"image_path": image_path_out' in body, (
        "_tool_route_inspect must include 'image_path' in its JSON return "
        "(issue #34). Got body tail:\n" + body[-800:]
    )


def test_no_crossings_yields_null_image_path():
    """Ensure the rendering call is gated on `if crossings:`."""
    src = _server_source()
    m = re.search(
        r"image_path_out = None\s*\n\s*if crossings:",
        src,
    )
    assert m, (
        "Expected `image_path_out = None` followed by `if crossings:` "
        "guard so the empty-crossings case returns image_path=null."
    )


# ---------------------------------------------------------------------------
# Tier 2: live MCP snapshot test (skips when server is down)
# ---------------------------------------------------------------------------

def _mcp_available() -> bool:
    return is_klayoutclaw(MCP_URL)


def _plugin_supports_image_path() -> bool:
    """The running KLayout plugin must be the issue-#34 build."""
    if not _mcp_available():
        return False
    try:
        payload = json.dumps({
            "jsonrpc": "2.0", "id": 0, "method": "tools/list",
        }).encode()
        req = urllib.request.Request(
            MCP_URL, data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        r = urllib.request.urlopen(req, timeout=2)
        data = json.loads(r.read().decode())
        for t in data.get("result", {}).get("tools", []):
            if t.get("name") == "route_inspect":
                props = (t.get("inputSchema") or {}).get("properties", {})
                return "image_path" in props
        return False
    except Exception:
        return False


_session_id = None
_req_id = 0


def _mcp_call(method, params=None, timeout=30):
    global _req_id, _session_id
    _req_id += 1
    body = {"jsonrpc": "2.0", "id": _req_id, "method": method}
    if params is not None:
        body["params"] = params
    headers = {"Content-Type": "application/json"}
    if _session_id:
        headers["Mcp-Session-Id"] = _session_id
    req = urllib.request.Request(
        MCP_URL, data=json.dumps(body).encode(),
        headers=headers, method="POST",
    )
    r = urllib.request.urlopen(req, timeout=timeout)
    _session_id = r.headers.get("Mcp-Session-Id", _session_id)
    data = json.loads(r.read().decode())
    if "error" in data:
        raise RuntimeError(f"MCP error: {data['error']}")
    return data


@pytest.mark.mcp
@pytest.mark.skipif(
    not _plugin_supports_image_path(),
    reason="Running KLayout plugin lacks issue-#34 image_path schema; reinstall plugin",
)
def test_route_inspect_image_snapshot(tmp_path):
    """Live snapshot: synthesise crossing routes, call route_inspect, verify
    the returned image_path resolves to a non-empty PNG."""
    # Initialise + create a fresh layout.
    _mcp_call("initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "test_route_inspect_image", "version": "0.1"},
    })
    _mcp_call("tools/call", {
        "name": "create_layout",
        "arguments": {"name": "TOP_RI", "dbu": 0.001},
    })

    # Build a minimal scene via execute_script:
    # - two routes on layer 3/0 that overlap (crossing)
    # - one contact polygon on 1/0 near each route start
    # - one pad polygon on 2/0 near each route end
    out_png = str(tmp_path / "labeled.png")
    setup_script = (
        "from pya import DPath, DPolygon, DPoint, DBox\n"
        "L_route, _ = _layout, _top_cell\n"
        "li_r = _layout.layer(3, 0)\n"
        "li_c = _layout.layer(1, 0)\n"
        "li_p = _layout.layer(2, 0)\n"
        "# Route A: horizontal\n"
        "_top_cell.shapes(li_r).insert(DPath([DPoint(0, 50), DPoint(200, 50)], 4.0))\n"
        "# Route B: vertical, crosses A\n"
        "_top_cell.shapes(li_r).insert(DPath([DPoint(100, -50), DPoint(100, 150)], 4.0))\n"
        "# Contacts (layer 1) at the start of each route\n"
        "_top_cell.shapes(li_c).insert(DBox(-6, 44, 4, 56))\n"
        "_top_cell.shapes(li_c).insert(DBox(94, -56, 106, -44))\n"
        "# Pads (layer 2) at the end of each route\n"
        "_top_cell.shapes(li_p).insert(DBox(196, 44, 206, 56))\n"
        "_top_cell.shapes(li_p).insert(DBox(94, 144, 106, 156))\n"
        "result = {'ok': True}\n"
    )
    _mcp_call("tools/call", {
        "name": "execute_script",
        "arguments": {"code": setup_script},
    })

    resp = _mcp_call("tools/call", {
        "name": "route_inspect",
        "arguments": {
            "route_layer": "3/0",
            "contact_layers": ["1/0"],
            "pad_layer": "2/0",
            "tolerance_um": 10.0,
            "image_path": out_png,
        },
    })
    text = resp["result"]["content"][0]["text"]
    payload = json.loads(text)
    assert payload["status"] == "ok", payload
    assert payload["crossings"], (
        "Expected at least one crossing in the synthesised scene; got "
        f"crossings={payload['crossings']!r}"
    )
    img = payload["image_path"]
    assert img is not None, "image_path must be set when crossings exist"
    assert os.path.isfile(img), f"image_path does not exist on disk: {img}"
    assert os.path.getsize(img) > 1024, (
        f"labeled crossings PNG is suspiciously small ({os.path.getsize(img)} bytes)"
    )


@pytest.mark.mcp
@pytest.mark.skipif(
    not _plugin_supports_image_path(),
    reason="Running KLayout plugin lacks issue-#34 image_path schema; reinstall plugin",
)
def test_route_inspect_no_crossings_yields_null_image(tmp_path):
    """Live snapshot: layout with no crossings should return image_path=null."""
    _mcp_call("initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "test_route_inspect_image", "version": "0.1"},
    })
    _mcp_call("tools/call", {
        "name": "create_layout",
        "arguments": {"name": "TOP_RI_NX", "dbu": 0.001},
    })
    setup_script = (
        "from pya import DPath, DPoint, DBox\n"
        "li_r = _layout.layer(3, 0)\n"
        "li_c = _layout.layer(1, 0)\n"
        "li_p = _layout.layer(2, 0)\n"
        "# Two parallel non-crossing routes\n"
        "_top_cell.shapes(li_r).insert(DPath([DPoint(0, 50), DPoint(200, 50)], 4.0))\n"
        "_top_cell.shapes(li_r).insert(DPath([DPoint(0, 100), DPoint(200, 100)], 4.0))\n"
        "_top_cell.shapes(li_c).insert(DBox(-6, 44, 4, 56))\n"
        "_top_cell.shapes(li_c).insert(DBox(-6, 94, 4, 106))\n"
        "_top_cell.shapes(li_p).insert(DBox(196, 44, 206, 56))\n"
        "_top_cell.shapes(li_p).insert(DBox(196, 94, 206, 106))\n"
        "result = {'ok': True}\n"
    )
    _mcp_call("tools/call", {
        "name": "execute_script",
        "arguments": {"code": setup_script},
    })
    resp = _mcp_call("tools/call", {
        "name": "route_inspect",
        "arguments": {
            "route_layer": "3/0",
            "contact_layers": ["1/0"],
            "pad_layer": "2/0",
            "tolerance_um": 10.0,
        },
    })
    text = resp["result"]["content"][0]["text"]
    payload = json.loads(text)
    assert payload["status"] == "ok"
    assert payload["crossings"] == [], (
        f"Expected no crossings, got {payload['crossings']!r}"
    )
    assert payload["image_path"] is None, (
        f"Expected image_path=null when no crossings; got {payload['image_path']!r}"
    )
