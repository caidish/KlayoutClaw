#!/usr/bin/env python
"""Capture step-by-step KLayout demo screenshots and combine into GIF.

Steps:
1. Create layout + cell
2. Draw Hall bar mesa + probes
3. Draw bonding pads + pin markers
4. Run autorouter
5. Final view with layer names

Usage:
    python tools/capture_demo.py [output.gif]
"""

import sys
import json
import time
import urllib.request
from pathlib import Path

MCP_URL = "http://127.0.0.1:8765/mcp"
_req_id = 0
_session_id = None
FRAME_DIR = Path("/tmp/demo_frames")
FRAME_DIR.mkdir(exist_ok=True)


def mcp_call(method, params=None, timeout=30):
    global _req_id, _session_id
    _req_id += 1
    payload = {"jsonrpc": "2.0", "id": _req_id, "method": method}
    if params:
        payload["params"] = params
    headers = {"Content-Type": "application/json"}
    if _session_id:
        headers["Mcp-Session-Id"] = _session_id
    req = urllib.request.Request(
        MCP_URL, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    r = urllib.request.urlopen(req, timeout=timeout)
    _session_id = r.headers.get("Mcp-Session-Id", _session_id)
    data = json.loads(r.read().decode())
    if "error" in data:
        raise RuntimeError(f"MCP error: {data['error']}")
    return data


def tool_call(tool_name, timeout=30, **kwargs):
    result = mcp_call("tools/call", {"name": tool_name, "arguments": kwargs}, timeout=timeout)
    text = result["result"]["content"][0]["text"]
    return json.loads(text)


def screenshot(name):
    """Take a screenshot of the full KLayout window (including layer panel)."""
    path = str(FRAME_DIR / f"{name}.png")
    tool_call("execute_script", code=f"""
import time
time.sleep(0.5)
mw = pya.Application.instance().main_window()
pixmap = mw.grab()
pixmap.save("{path}", "PNG")
result = {{"saved": "{path}"}}
""")
    print(f"  Screenshot: {path}")
    return path


def set_layer_names():
    """Set readable layer names in the layer panel."""
    tool_call("execute_script", code="""
layer_names = {
    (1, 0): "Mesa",
    (3, 0): "Pads",
    (10, 0): "Routes",
    (102, 0): "Pin_A",
    (111, 0): "Pin_B",
}
lp_iter = _layout_view.begin_layers()
while not lp_iter.at_end():
    lp = lp_iter.current()
    key = (lp.source_layer, lp.source_datatype)
    if key in layer_names:
        lp.name = layer_names[key]
        _layout_view.set_layer_properties(lp_iter, lp)
    lp_iter.next()
result = {"status": "ok", "names_set": list(layer_names.values())}
""")


def main():
    output_gif = sys.argv[1] if len(sys.argv) > 1 else "docs/demo.gif"
    frames = []

    # Initialize MCP
    mcp_call("initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "capture_demo", "version": "1.0"},
    })

    # =====================================================================
    # Frame 1: Create layout + cell
    # =====================================================================
    print("\n=== Frame 1: Create Layout ===")
    tool_call("create_layout", name="HALLBAR_DEMO", dbu=0.001)
    tool_call("execute_script", code="""
# Add a title annotation
result = {"status": "ok"}
""")
    frames.append(screenshot("01_create_layout"))

    # =====================================================================
    # Frame 2: Draw Hall bar mesa + probes
    # =====================================================================
    print("\n=== Frame 2: Draw Mesa ===")
    tool_call("execute_script", code="""
dbu = _layout.dbu
cell = _top_cell

def rect(layer, dt, x1, y1, x2, y2):
    li = _layout.layer(layer, dt)
    cell.shapes(li).insert(pya.Box(int(x1/dbu), int(y1/dbu), int(x2/dbu), int(y2/dbu)))

# Main channel: 100um x 25um
rect(1, 0, -50, -12.5, 50, 12.5)

# 6 side probes
probe_xs = [-30, 0, 30]
for px in probe_xs:
    rect(1, 0, px - 5, 12.5, px + 5, 32.5)
    rect(1, 0, px - 5, -32.5, px + 5, -12.5)

result = {"status": "ok", "step": "mesa drawn"}
""")
    set_layer_names()
    frames.append(screenshot("02_mesa"))

    # =====================================================================
    # Frame 3: Draw bonding pads + pin markers
    # =====================================================================
    print("\n=== Frame 3: Draw Pads + Pin Markers ===")
    tool_call("execute_script", code="""
dbu = _layout.dbu
cell = _top_cell

def rect(layer, dt, x1, y1, x2, y2):
    li = _layout.layer(layer, dt)
    cell.shapes(li).insert(pya.Box(int(x1/dbu), int(y1/dbu), int(x2/dbu), int(y2/dbu)))

def pin(layer, dt, cx, cy, size=5):
    hs = size / 2.0
    rect(layer, dt, cx - hs, cy - hs, cx + hs, cy + hs)

# 300x300um bonding pads on layer 3/0
rect(3, 0, -1050, -150, -750, 150)
rect(3, 0,  750,  -150, 1050, 150)
rect(3, 0, -750, 700, -450, 1000)
rect(3, 0, -150, 700,  150, 1000)
rect(3, 0,  450, 700,  750, 1000)
rect(3, 0, -750, -1000, -450, -700)
rect(3, 0, -150, -1000,  150, -700)
rect(3, 0,  450, -1000,  750, -700)

# Pin A markers (probe tips + channel ends) on 102/0
pin(102, 0, -30, 32.5)
pin(102, 0,   0, 32.5)
pin(102, 0,  30, 32.5)
pin(102, 0, -30, -32.5)
pin(102, 0,   0, -32.5)
pin(102, 0,  30, -32.5)
pin(102, 0, -50, 0)
pin(102, 0,  50, 0)

# Pin B markers (pad centers) on 111/0
pin(111, 0, -900, 0)
pin(111, 0,  900, 0)
pin(111, 0, -600, 850)
pin(111, 0,    0, 850)
pin(111, 0,  600, 850)
pin(111, 0, -600, -850)
pin(111, 0,    0, -850)
pin(111, 0,  600, -850)

result = {"status": "ok", "step": "pads + pins drawn"}
""")
    set_layer_names()
    frames.append(screenshot("03_pads_pins"))

    # =====================================================================
    # Frame 4: Run autorouter
    # =====================================================================
    print("\n=== Frame 4: Auto-Route ===")
    result = tool_call(
        "auto_route",
        timeout=180,
        pin_layer_a="102/0",
        pin_layer_b="111/0",
        obstacle_layers=["1/0", "3/0"],
        output_layer="10/0",
        path_width=10.0,
        obs_safe_distance=15.0,
        path_safe_distance=10.0,
        map_resolution=5.0,
    )
    print(f"  Route result: {json.dumps(result)}")
    set_layer_names()
    frames.append(screenshot("04_routed"))

    # =====================================================================
    # Combine into GIF
    # =====================================================================
    print(f"\n=== Combining {len(frames)} frames into GIF ===")
    from PIL import Image

    images = []
    for f in frames:
        img = Image.open(f)
        # Convert to RGB if RGBA
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg
        images.append(img)

    # Save GIF: 2 seconds per frame, loop forever
    images[0].save(
        output_gif,
        save_all=True,
        append_images=images[1:],
        duration=2000,
        loop=0,
        optimize=True,
    )
    print(f"\nGIF saved: {output_gif}")
    print(f"Frames: {len(images)}, Duration: {len(images) * 2}s")


if __name__ == "__main__":
    main()
