#!/usr/bin/env python3
"""Check a running KlayoutClaw GUI with a disposable drawing and synthetic data."""

import argparse
import json
import platform
from pathlib import Path
import sys
import tempfile
from urllib.request import Request, urlopen
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "scripts"))
import mcp_client as mcp


def call(tool_name, **arguments):
    reply = mcp.mcp_call("tools/call", {"name": tool_name, "arguments": arguments})["result"]
    message = reply["content"][0]["text"]
    if reply.get("isError"):
        raise RuntimeError(message)
    return json.loads(message)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def in_test_view(name, code):
    guard = (
        "mw = pya.Application.instance().main_window()\n"
        "view = mw.current_view()\n"
        "cell = view.active_cellview().cell if view else None\n"
        f"if cell is None or cell.name != {name!r} or _top_cell.name != {name!r}:\n"
        "    raise RuntimeError('The test tab is no longer active')\n"
    )
    return call("execute_script", code=guard + code)


def check_workers(name, python_path, explicit_python):
    in_test_view(name, """
for y in (0, 100000):
    _top_cell.shapes(_layout.layer(100, 0)).insert(pya.Box(0, y, 2000, y + 2000))
    _top_cell.shapes(_layout.layer(101, 0)).insert(pya.Box(50000, y, 52000, y + 2000))
""")
    options = {"python_path": str(python_path)} if explicit_python else {}
    route = call(
        "auto_route", pin_layer_a="100/0", pin_layer_b="101/0", output_layer="102/0",
        path_width=2, map_resolution=2, pin_safe_distance_a=2, pin_safe_distance_b=2,
        obs_safe_distance=2, path_safe_distance=2, pin_pairs_override=[[0, 0], [1, 1]],
        **options,
    )
    result = in_test_view(name, """
layer = _layout.find_layer(102, 0)
result = {'route_count': _top_cell.shapes(layer).size() if layer is not None else 0}
""")
    require(route["routed_pairs"] == 2 and result["route_count"] == 2,
            f"Expected two committed routes, got {route}")
    print("PASS auto_route (two paths committed)", flush=True)

    evaluation = call(
        "evaluate_design", layer_map={"marker": [20, 9]},
        checks=[{"name": "component_overlap", "args": {"component": "marker", "region": "marker"}, "weight": 1}],
        **options,
    )
    require(evaluation["status"] == "ok" and evaluation["overall"] == 1.0,
            f"Unexpected evaluation: {evaluation}")
    print("PASS evaluate_design (expected score 1.0)", flush=True)


def check_gui(worker_python=None, workers_from_server_env=False):
    initialized = mcp.mcp_call("initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "check_klayout_gui", "version": "1"},
    }, timeout=8)["result"]
    server = initialized.get("serverInfo", {})
    require(server.get("name") == "KlayoutClaw", f"Unexpected MCP server: {server}")
    notification = Request(
        mcp.MCP_URL,
        data=b'{"jsonrpc":"2.0","method":"notifications/initialized"}',
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
    )
    with urlopen(notification, timeout=8) as response:
        require(response.status == 202 and not response.read(),
                "Expected an empty HTTP 202 for MCP initialization. Restart KLayout after installing.")
    tools = {tool["name"] for tool in mcp.mcp_call("tools/list", timeout=8)["result"]["tools"]}
    required = {"create_layout", "execute_script", "save_layout", "screenshot", "close_layout_view"}
    if worker_python or workers_from_server_env:
        required.update(("auto_route", "evaluate_design"))
    require(required <= tools, f"Missing tools: {sorted(required - tools)}")
    print(f"PASS MCP connection ({mcp.MCP_URL}, server {server['version']})", flush=True)

    folder = Path(tempfile.mkdtemp(prefix="KlayoutClaw 测试 "))
    name = "KLAYOUTCLAW_CHECK_" + uuid4().hex[:12]
    gds = folder / "样片 A.gds"
    png = folder / "样片 A.png"
    created = call("create_layout", name=name, dbu=0.001)
    require(created.get("top_cell") == name, f"Unexpected test cell: {created}")
    try:
        version = in_test_view(name, """
_top_cell.shapes(_layout.layer(20, 9)).insert(pya.Box(0, 0, 12000, 10000))
app = pya.Application.instance()
result = {'version': app.version(), 'data_path': app.application_data_path()}
""")
        print(f"{version['version']} | profile {version['data_path']}", flush=True)
        require(call("save_layout", filepath=str(gds)).get("status") == "ok", "GDS save failed")
        require(call("screenshot", filepath=str(png), width=320, height=240).get("status") == "ok",
                "PNG save failed")
        with png.open("rb") as image:
            require(image.read(8) == b"\x89PNG\r\n\x1a\n", "Screenshot is not a PNG")
        result = in_test_view(name, f"""
saved = pya.Layout()
saved.read({str(gds)!r})
top = saved.top_cell()
layer = saved.find_layer(20, 9)
shapes = list(top.shapes(layer).each())
box = shapes[0].bbox()
result = {{'name': top.name, 'dbu': saved.dbu, 'count': len(shapes),
           'bounds': [box.left, box.bottom, box.right, box.top]}}
""")
        expected = {"name": name, "dbu": 0.001, "count": 1, "bounds": [0, 0, 12000, 10000]}
        require(all(result.get(key) == value for key, value in expected.items()),
                f"Unexpected GDS readback: {result}")
        print("PASS geometry, GDS readback and PNG in a path containing spaces and Chinese", flush=True)
        if worker_python or workers_from_server_env:
            check_workers(name, worker_python, explicit_python=worker_python is not None)
    finally:
        in_test_view(name, "result = {}\n")
        closed = call("close_layout_view", mode="current")
        require(closed["closed"] == 1, f"Could not close the disposable tab: {closed}")
    print(f"PASS complete | {platform.platform()} | outputs {folder}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    worker = parser.add_mutually_exclusive_group()
    worker.add_argument("--worker-python", type=Path, help="Python executable with scientific dependencies")
    worker.add_argument("--workers-from-server-env", action="store_true",
                        help="Check workers using the running server's configured Python")
    args = parser.parse_args()
    if args.worker_python:
        args.worker_python = args.worker_python.expanduser().absolute()
        if not args.worker_python.is_file():
            parser.error("--worker-python must point to a Python executable")
    try:
        check_gui(args.worker_python, args.workers_from_server_env)
    except (OSError, RuntimeError, KeyError, ValueError) as exc:
        parser.exit(1, f"FAIL: {exc}\n")


if __name__ == "__main__":
    main()
