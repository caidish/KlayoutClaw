#!/usr/bin/env python3
"""Run against a real, idle AutoCAD instance with this checkout's bundle loaded."""

import base64
import json
from pathlib import Path
import select
import subprocess
import sys
import tempfile

import gdstk

ADAPTER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADAPTER))
from qlay_autocad.bridge import Bridge

PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAAI0lEQVR4nGN0KDjAQApgIkk1w6gG4gAT"
    "kergYFQDMYDkUAIAl+wBkLfhgjsAAAAASUVORK5CYII="
)


def check_export(path):
    layout = gdstk.read_gds(path)
    assert layout.unit == 1e-6 and layout.precision == 1e-9
    cell, = layout.top_level()
    assert cell.name == "PR2_SMOKE"
    grouped = {}
    for shape in cell.polygons:
        grouped.setdefault((shape.layer, shape.datatype), []).append(shape)
    assert set(grouped) == {(20, 0), (20, 9)}
    rectangle = gdstk.rectangle((30, 0), (34, 5))
    ring = gdstk.boolean(
        gdstk.rectangle((0, 0), (12, 10)), gdstk.rectangle((3, 3), (9, 7)), "not", precision=0.001
    )
    assert not gdstk.boolean(grouped[20, 0], rectangle, "xor", precision=0.001)
    assert not gdstk.boolean(grouped[20, 9], ring, "xor", precision=0.001)


def exercise(root, bridge):
    before = bridge.documents()
    previous = next((doc["id"] for doc in before if doc["active"]), None)
    image = root / "reference image.png"
    image.write_bytes(base64.b64decode(PNG))
    dwg = root / "task.dwg"
    output = root / "mask.gds"
    owned = set()
    request_id = 0

    with subprocess.Popen(
        [ADAPTER / "qlay-autocad", "serve"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    ) as server:
        def rpc(method, params):
            nonlocal request_id
            request_id += 1
            payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
            server.stdin.write(json.dumps(payload) + "\n")
            server.stdin.flush()
            assert select.select([server.stdout], [], [], 60)[0], f"Timeout calling {method}"
            result = json.loads(server.stdout.readline())
            assert result["id"] == request_id and "error" not in result, result
            return result["result"]

        def call(tool, expect_error=None, **arguments):
            result = rpc("tools/call", {"name": tool, "arguments": arguments})
            content = result["structuredContent"]
            if expect_error:
                assert result["isError"] and expect_error in content["error"], result
            else:
                assert not result["isError"], content
            return content

        try:
            initialized = rpc("initialize", {
                "protocolVersion": "2025-03-26", "capabilities": {},
                "clientInfo": {"name": "autocad-native-check", "version": "1"},
            })
            assert initialized["serverInfo"]["name"] == "KlayoutClaw AutoCAD"
            assert initialized["protocolVersion"] == "2025-03-26"
            server.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
            server.stdin.flush()
            names = {tool["name"] for tool in rpc("tools/list", {})["tools"]}
            assert {"ping", "create_layout", "save_layout", "close_layout_view"} <= names
            ping = call("ping")
            assert ping["transport"] == "unix-socket" and ping["ui_automation"] is False

            # Keep a saved disposable second drawing to check the active-document fence.
            other = call("create_layout", name="PR2_FENCE")["document_id"]
            owned.add(other)
            call("save_layout", path=str(root / "fence.dwg"))
            drawing = call("create_layout", name="PR2_SMOKE")
            did = drawing["document_id"]
            owned.add(did)
            assert drawing["units"] == "um"
            call("add_polygon", layer="20/9", points=[[0, 0], [12, 0], [12, 10], [0, 10]],
                 holes=[[[3, 3], [3, 7], [9, 7], [9, 3]]])
            call("add_rect", layer="20/0", x1=30, y1=0, x2=34, y2=5)
            call("add_image", path=str(image), pixel_size=0.1, origin=[0, 15])
            call("add_image", path=str(image), pixel_size=0.1, origin=[10, 15])
            images = call("list_images")["images"]
            assert len(images) == 2 and sorted(i["origin"][:2] for i in images) == [[0, 15], [10, 15]]
            info = call("get_layout_info")
            assert info["units"] == 13 and {"L020_D000", "L020_D009"} <= set(info["layers"])
            call("save_layout", expect_error="different open drawing", path=str(root / "fence.dwg"))

            bridge.activate_document(other)
            call("add_rect", expect_error="Active document mismatch", layer="50/0",
                 x1=0, y1=0, x2=1, y2=1)
            call("activate_document", document_id=other)
            assert call("get_layout_info")["entity_count"] == 0
            call("close_layout_view", document_id=other)
            owned.remove(other)

            call("activate_document", document_id=did)
            call("save_layout", path=str(dwg))
            assert dwg.read_bytes()[:4] == b"AC10"
            call("save_layout", path=str(output), layers=["20/0", "20/9"])
            check_export(output)
            call("close_layout_view", document_id=did)
            owned.remove(did)

            original = dwg.read_bytes()
            copied = call("import_layout", path=str(dwg))
            working = Path(copied["working_path"])
            assert working.parent == dwg.resolve().parent and not working.samefile(dwg)
            reopened = copied["document_id"]
            owned.add(reopened)
            assert len(call("list_images")["images"]) == 2
            call("add_rect", layer="50/0", x1=0, y1=0, x2=1, y2=1)
            call("save_layout", path=str(dwg), expect_error="source DWG is protected")
            call("save_layout", path=str(working))
            assert dwg.read_bytes() == original
            call("save_layout", path=str(output), layers=["20/0", "20/9"], name="PR2_SMOKE")
            check_export(output)
            call("close_layout_view", document_id=reopened)
            owned.remove(reopened)

            imported = call("import_layout", path=str(output))["document_id"]
            owned.add(imported)
            roundtrip = root / "roundtrip.gds"
            call("save_layout", path=str(roundtrip), layers=["20/0", "20/9"])
            check_export(roundtrip)
            call("save_layout", path=str(root / "imported.dwg"))
            call("close_layout_view", document_id=imported)
            owned.remove(imported)
        finally:
            try:
                for did in owned:
                    bridge.activate_document(did)
                    bridge.save_document(root / ("cleanup-" + did.replace(":", "-") + ".dwg"), did)
                    bridge.close_document(did)
                # AutoCAD may discard its initial untouched Drawing1 when opening a real file.
                remaining = {doc["id"] for doc in bridge.documents()}
                assert {doc["id"] for doc in before if Path(doc["path"]).is_file()} <= remaining
                if previous in remaining:
                    bridge.activate_document(previous)
            finally:
                server.stdin.close()
                server.wait(timeout=10)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="klayoutclaw AutoCAD check ") as directory:
        exercise(Path(directory), Bridge())
    print("Passed: stdio MCP, native geometry and raster placement, drawing fence, DWG reopen and GDS readback")
