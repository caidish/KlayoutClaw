#!/usr/bin/env python
"""Create a graphene Hall bar device via MCP execute_script.

This script calls the KlayoutClaw MCP server to create a Hall bar with
proper scale (~2000um extent) and no overlapping geometry:

- Layer 1/0 (Mesa): Graphene channel (W=25um, L=100um) with 6 side probes
- Layer 2/0 (Metal): Manhattan routing from device to bonding pads
- Layer 3/0 (Bonding pads): 100x100um pads at the periphery

Usage:
    python tests/create_hallbar.py [output.gds]
"""

import sys
import json
import urllib.request

MCP_URL = "http://127.0.0.1:8765/mcp"
_req_id = 0
_session_id = None


def mcp_call(method, params=None):
    global _req_id, _session_id
    _req_id += 1
    payload = {"jsonrpc": "2.0", "id": _req_id, "method": method}
    if params:
        payload["params"] = params
    headers = {"Content-Type": "application/json"}
    if _session_id:
        headers["Mcp-Session-Id"] = _session_id
    req = urllib.request.Request(MCP_URL, data=json.dumps(payload).encode(), headers=headers, method="POST")
    r = urllib.request.urlopen(req, timeout=30)
    _session_id = r.headers.get("Mcp-Session-Id", _session_id)
    data = json.loads(r.read().decode())
    if "error" in data:
        raise RuntimeError(f"MCP error: {data['error']}")
    return data


def tool_call(tool_name, **kwargs):
    result = mcp_call("tools/call", {"name": tool_name, "arguments": kwargs})
    text = result["result"]["content"][0]["text"]
    return json.loads(text)


def main():
    gds_path = sys.argv[1] if len(sys.argv) > 1 else "test_hallbar.gds"

    # Initialize MCP
    mcp_call("initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "create_hallbar", "version": "0.3"},
    })

    # Create layout
    print("Creating layout...")
    tool_call("create_layout", name="HALLBAR", dbu=0.001)

    # Build entire Hall bar geometry in one execute_script call
    print("Drawing Hall bar geometry...")
    tool_call("execute_script", code="""
dbu = _layout.dbu
cell = _top_cell

def rect(layer, dt, x1, y1, x2, y2):
    li = _layout.layer(layer, dt)
    cell.shapes(li).insert(pya.Box(int(x1/dbu), int(y1/dbu), int(x2/dbu), int(y2/dbu)))

def path(layer, dt, points, width):
    li = _layout.layer(layer, dt)
    pts = [pya.Point(int(x/dbu), int(y/dbu)) for x, y in points]
    cell.shapes(li).insert(pya.Path(pts, int(width/dbu)))

# =========================================================================
# Layer 1/0: Mesa (graphene channel + probes) — small device at center
# =========================================================================
# Main channel: 100um x 25um, centered at origin
rect(1, 0, -50, -12.5, 50, 12.5)

# 6 side probes (3 per side), 10um wide, 20um long
probe_xs = [-30, 0, 30]
for px in probe_xs:
    rect(1, 0, px - 5, 12.5, px + 5, 32.5)    # top probes
    rect(1, 0, px - 5, -32.5, px + 5, -12.5)   # bottom probes

# =========================================================================
# Layer 3/0: Bonding pads — 100x100um pads at the periphery
# =========================================================================
# Current pads (left and right)
rect(3, 0, -950, -50, -850, 50)    # left current pad
rect(3, 0,  850, -50,  950, 50)    # right current pad

# Voltage pads — top row (3 pads at y = 800..900)
rect(3, 0, -650, 800, -550, 900)   # top-left voltage
rect(3, 0,  -50, 800,   50, 900)   # top-center voltage
rect(3, 0,  550, 800,  650, 900)   # top-right voltage

# Voltage pads — bottom row (3 pads at y = -900..-800)
rect(3, 0, -650, -900, -550, -800)  # bottom-left voltage
rect(3, 0,  -50, -900,   50, -800)  # bottom-center voltage
rect(3, 0,  550, -900,  650, -800)  # bottom-right voltage

# =========================================================================
# Layer 2/0: Metal routing — Manhattan traces from device to pads
# =========================================================================
tw = 10  # trace width in microns

# Current routing: horizontal from channel ends to current pads
path(2, 0, [[-50, 0], [-850, 0]], tw)     # left
path(2, 0, [[ 50, 0], [ 850, 0]], tw)     # right

# Top voltage routing (each probe routes at a different y-level to avoid overlap)
# Probe x=-30: up to y=200, left to x=-600, up to y=800
path(2, 0, [[-30, 32.5], [-30, 200], [-600, 200], [-600, 800]], tw)
# Probe x=0: straight up to y=800
path(2, 0, [[0, 32.5], [0, 800]], tw)
# Probe x=30: up to y=300, right to x=600, up to y=800
path(2, 0, [[30, 32.5], [30, 300], [600, 300], [600, 800]], tw)

# Bottom voltage routing (mirror of top)
# Probe x=-30: down to y=-200, left to x=-600, down to y=-800
path(2, 0, [[-30, -32.5], [-30, -200], [-600, -200], [-600, -800]], tw)
# Probe x=0: straight down to y=-800
path(2, 0, [[0, -32.5], [0, -800]], tw)
# Probe x=30: down to y=-300, right to x=600, down to y=-800
path(2, 0, [[30, -32.5], [30, -300], [600, -300], [600, -800]], tw)

result = {"status": "ok", "message": "Hall bar geometry created (~1900x1800um)"}
""")

    # Save
    print(f"Saving to {gds_path}...")
    tool_call("save_layout", filepath=gds_path)

    # Verify
    info = tool_call("get_layout_info")
    print(f"Layout info: {json.dumps(info, indent=2)}")
    print(f"\nDone! GDS saved to {gds_path}")


if __name__ == "__main__":
    main()
