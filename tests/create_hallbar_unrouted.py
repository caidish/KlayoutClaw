#!/usr/bin/env python
"""Create a Hall bar with pin markers but NO metal routing.

This script calls the KlayoutClaw MCP server to create a Hall bar layout
with probe and pad pin markers for autorouter testing:

- Layer 1/0 (Mesa): Graphene channel (W=25um, L=100um) with 6 side probes
- Layer 3/0 (Pads): 100x100um bonding pads at the periphery
- Layer 102/0 (Pin_A): 5x5um pin markers at probe tips and channel ends
- Layer 111/0 (Pin_B): 5x5um pin markers at pad centers

NO layer 2/0 (Metal) — the autorouter creates this.

Usage:
    python tests/create_hallbar_unrouted.py [output.gds]
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
    gds_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/test_hallbar_unrouted.gds"

    # Initialize MCP
    mcp_call("initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "create_hallbar_unrouted", "version": "0.1"},
    })

    # Create layout
    print("Creating layout...")
    tool_call("create_layout", name="HALLBAR_UNROUTED", dbu=0.001)

    # Build Hall bar geometry with pin markers (no metal routing)
    print("Drawing Hall bar geometry with pin markers...")
    tool_call("execute_script", code="""
dbu = _layout.dbu
cell = _top_cell

def rect(layer, dt, x1, y1, x2, y2):
    li = _layout.layer(layer, dt)
    cell.shapes(li).insert(pya.Box(int(x1/dbu), int(y1/dbu), int(x2/dbu), int(y2/dbu)))

def pin_marker(layer, dt, cx, cy):
    \"\"\"Create a 5x5um pin marker centered at (cx, cy).\"\"\"
    li = _layout.layer(layer, dt)
    cell.shapes(li).insert(pya.Box(
        int((cx - 2.5)/dbu), int((cy - 2.5)/dbu),
        int((cx + 2.5)/dbu), int((cy + 2.5)/dbu)
    ))

# =========================================================================
# Layer 1/0: Mesa (graphene channel + probes) - obstacles
# =========================================================================
# Main channel: 100um x 25um, centered at origin
rect(1, 0, -50, -12.5, 50, 12.5)

# 6 side probes (3 per side), 10um wide, 20um long
probe_xs = [-30, 0, 30]
for px in probe_xs:
    rect(1, 0, px - 5, 12.5, px + 5, 32.5)    # top probes
    rect(1, 0, px - 5, -32.5, px + 5, -12.5)   # bottom probes

# =========================================================================
# Layer 3/0: Bonding pads - obstacles
# =========================================================================
# Current pads (left and right)
rect(3, 0, -950, -50, -850, 50)    # left current pad
rect(3, 0,  850, -50,  950, 50)    # right current pad

# Voltage pads - top row (3 pads at y = 800..900)
rect(3, 0, -650, 800, -550, 900)   # top-left voltage
rect(3, 0,  -50, 800,   50, 900)   # top-center voltage
rect(3, 0,  550, 800,  650, 900)   # top-right voltage

# Voltage pads - bottom row (3 pads at y = -900..-800)
rect(3, 0, -650, -900, -550, -800)  # bottom-left voltage
rect(3, 0,  -50, -900,   50, -800)  # bottom-center voltage
rect(3, 0,  550, -900,  650, -800)  # bottom-right voltage

# =========================================================================
# Layer 102/0: Pin_A - pin markers at probe tips and channel ends
# =========================================================================
# Probe tips (top)
pin_marker(102, 0, -30, 32.5)
pin_marker(102, 0,   0, 32.5)
pin_marker(102, 0,  30, 32.5)

# Probe tips (bottom)
pin_marker(102, 0, -30, -32.5)
pin_marker(102, 0,   0, -32.5)
pin_marker(102, 0,  30, -32.5)

# Channel ends
pin_marker(102, 0, -50, 0)
pin_marker(102, 0,  50, 0)

# =========================================================================
# Layer 111/0: Pin_B - pin markers at pad centers
# =========================================================================
# Current pad centers
pin_marker(111, 0, -900, 0)
pin_marker(111, 0,  900, 0)

# Top voltage pad centers
pin_marker(111, 0, -600, 850)
pin_marker(111, 0,    0, 850)
pin_marker(111, 0,  600, 850)

# Bottom voltage pad centers
pin_marker(111, 0, -600, -850)
pin_marker(111, 0,    0, -850)
pin_marker(111, 0,  600, -850)

result = {"status": "ok", "message": "Unrouted Hall bar with pin markers created (~1900x1800um)"}
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
