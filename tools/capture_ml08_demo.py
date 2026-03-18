#!/usr/bin/env python
"""Capture step-by-step ML08 Hall bar demo screenshots and combine into GIF.

Loads Template.gds, overlays flake detection results, generates a
self-adjusting Hall bar from the graphene-graphite overlap, routes
11 pins to bonding pads, and captures each step as a screenshot.

Usage:
    python tools/capture_ml08_demo.py [output.gif]
"""

import sys
import json
import tempfile
import os
import urllib.request
from pathlib import Path

MCP_URL = "http://127.0.0.1:8765/mcp"
_req_id = 0
_session_id = None
FRAME_DIR = Path("/tmp/ml08_demo_frames")
FRAME_DIR.mkdir(exist_ok=True)

# Resolve data paths relative to this script
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent
RESOURCES = REPO_DIR / "tests_resources" / "ml08"
PRECOMPUTED = RESOURCES / "precomputed"
TEMPLATE_GDS = RESOURCES / "Template.gds"
TRACES_GDS = PRECOMPUTED / "traces_gds.json"
IMAGE_PLACEMENT = PRECOMPUTED / "image_placement.json"
FULL_STACK_PNG = PRECOMPUTED / "full_stack_gds.png"


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
    content = result["result"]["content"]
    text = content[0]["text"] if content else ""
    if not text:
        print(f"  WARNING: {tool_name} returned empty text. Full content: {content}")
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        print(f"  WARNING: {tool_name} returned non-JSON: {text[:500]}")
        return {"_raw": text}


def screenshot(name, zoom_box=None):
    """Capture the full KLayout window.

    Args:
        name: Screenshot filename (without extension).
        zoom_box: Optional (x1, y1, x2, y2) in um. If provided, zoom + grab
                  happen in one execute_script call (lesson 2: _refresh_view
                  resets zoom via zoom_fit after every execute_script).
    """
    path = str(FRAME_DIR / f"{name}.png")
    if zoom_box:
        x1, y1, x2, y2 = zoom_box
        tool_call("execute_script", code=f"""
import time
_layout_view, _layout, _top_cell = _get_or_create_view()
_layout_view.zoom_box(pya.DBox({x1}, {y1}, {x2}, {y2}))
time.sleep(0.3)
mw = pya.Application.instance().main_window()
pixmap = mw.grab()
pixmap.save("{path}", "PNG")
result = {{"saved": "{path}"}}
""")
    else:
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


def main():
    output_gif = sys.argv[1] if len(sys.argv) > 1 else "docs/ml08_demo.gif"
    frames = []

    # Validate data files exist
    for p in [TEMPLATE_GDS, TRACES_GDS, IMAGE_PLACEMENT, FULL_STACK_PNG]:
        if not p.exists():
            print(f"ERROR: Missing {p}", file=sys.stderr)
            sys.exit(1)

    # Load pre-computed data
    with open(TRACES_GDS) as f:
        traces = json.load(f)
    with open(IMAGE_PLACEMENT) as f:
        placement = json.load(f)

    # Initialize MCP session
    mcp_call("initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "capture_ml08_demo", "version": "1.0"},
    })

    # =================================================================
    # Frame 1: Load Template.gds
    # =================================================================
    print("\n=== Frame 1: Load Template.gds ===")
    gds_path = str(TEMPLATE_GDS)
    tool_call("execute_script", code=f"""
import os
mw = pya.Application.instance().main_window()
mw.load_layout("{gds_path}", 1)
# Lesson 1: must re-sync globals after load_layout (new view created)
# MUST assign back to _layout_view, _layout, _top_cell so the server
# post-execution sync picks up the new references (not local names).
_layout_view, _layout, _top_cell = _get_or_create_view()
result = {{"cell": _top_cell.name, "dbu": _layout.dbu}}
""")
    frames.append(screenshot("01_template"))

    # =================================================================
    # Frame 2: Load background image
    # =================================================================
    print("\n=== Frame 2: Load Background Image ===")
    img_path = str(FULL_STACK_PNG)
    ox, oy = placement["origin_um"]
    ps = placement["pixel_size_um"]
    h_um = placement["height_um"]
    # Lesson 3: mirror=True, displacement at top edge (oy + h_um)
    top_y = oy + h_um
    tool_call("execute_script", code=f"""
_layout_view, _layout, _top_cell = _get_or_create_view()
img = pya.Image("{img_path}")
img.visible = True
# DCplxTrans(mag, angle, mirror, displacement)
img.trans = pya.DCplxTrans({ps}, 0, True, pya.DVector({ox}, {top_y}))
_layout_view.insert_image(img)
result = {{"status": "ok"}}
""")
    # Lesson 2: zoom + screenshot in one call
    frames.append(screenshot("02_background", zoom_box=(650, 690, 850, 870)))

    # =================================================================
    # Frame 3: Commit flake polygons
    # =================================================================
    print("\n=== Frame 3: Commit Flake Polygons ===")
    poly_data = {}
    for mat_name in traces["stack"]:
        layer_spec = traces["layer_map"][mat_name]
        entries = traces["materials"][mat_name]
        contours = [e["contour_gds"] for e in entries]
        poly_data[mat_name] = {"layer": layer_spec, "contours": contours}
    poly_tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(poly_data, poly_tmp)
    poly_tmp.close()
    tool_call("execute_script", code=f"""
import json
with open("{poly_tmp.name}") as f:
    poly_data = json.load(f)
dbu = _layout.dbu
for mat_name, info in poly_data.items():
    layer_spec = info["layer"]
    ln, dt = int(layer_spec.split("/")[0]), int(layer_spec.split("/")[1])
    li = _layout.layer(ln, dt)
    for contour in info["contours"]:
        pts = [pya.Point(int(round(x / dbu)), int(round(y / dbu))) for x, y in contour]
        _top_cell.shapes(li).insert(pya.Polygon(pts))
result = {{"status": "ok", "materials": len(poly_data)}}
""")
    os.unlink(poly_tmp.name)
    frames.append(screenshot("03_polygons", zoom_box=(650, 690, 850, 870)))

    # =================================================================
    # Frame 4: Set layer names + flake layers to outline-only
    # =================================================================
    print("\n=== Frame 4: Set Layer Names ===")
    tool_call("execute_script", code="""
layer_names = {
    (0, 0): "Origin",
    (2, 0): "Pads",
    (3, 0): "Routes",
    (5, 0): "Markers",
    (10, 0): "top_hBN",
    (11, 0): "graphene",
    (12, 0): "bottom_hBN",
    (13, 0): "graphite",
    (20, 0): "Mesa",
    (21, 0): "Contact",
    (22, 0): "TopGate",
    (100, 0): "Pin_Contact",
    (101, 0): "Pin_Pad",
}
flake_layers = {10, 11, 12, 13}
# Pre-create new layers so they appear in the panel
for ln, dt in [(3, 0), (20, 0), (21, 0), (22, 0), (100, 0), (101, 0)]:
    _layout.layer(ln, dt)
lp_iter = _layout_view.begin_layers()
while not lp_iter.at_end():
    lp = lp_iter.current()
    key = (lp.source_layer, lp.source_datatype)
    if key in layer_names:
        lp.name = layer_names[key]
    # Lesson 4: flake contour layers outline-only for device visibility
    if lp.source_layer in flake_layers:
        lp.transparent = True
        lp.width = 3
        lp.dither_pattern = 1  # hollow (no fill)
    _layout_view.set_layer_properties(lp_iter, lp)
    lp_iter.next()
result = {"status": "ok"}
""")
    frames.append(screenshot("04_layer_names", zoom_box=(650, 690, 850, 870)))

    # =================================================================
    # Frame 5: Compute overlap + generate Hall bar Mesa
    # =================================================================
    print("\n=== Frame 5: Hall Bar Mesa Generation ===")
    tool_call("execute_script", code="""
import math, json

dbu = _layout.dbu

# --- Step 1: Read graphene and graphite polygons ---
li_graphene = _layout.find_layer(11, 0)
li_graphite = _layout.find_layer(13, 0)
reg_graphene = pya.Region(_top_cell.shapes(li_graphene))
reg_graphite = pya.Region(_top_cell.shapes(li_graphite))

# --- Step 2: Boolean intersection ---
overlap = reg_graphene & reg_graphite
overlap.merge()
if overlap.is_empty():
    raise RuntimeError("Graphene-graphite overlap is empty")
overlap_poly = max(list(overlap.each()), key=lambda p: p.area())

# --- Step 3: Min-area bounding rectangle ---
hull = overlap_poly.to_simple_polygon()
hull_pts = [hull.point(i) for i in range(hull.num_points())]

best_area = float('inf')
best_angle = 0
best_bbox = None

for i in range(len(hull_pts)):
    j = (i + 1) % len(hull_pts)
    dx = hull_pts[j].x - hull_pts[i].x
    dy = hull_pts[j].y - hull_pts[i].y
    angle = math.atan2(dy, dx)
    cos_a = math.cos(-angle)
    sin_a = math.sin(-angle)
    xs = [pt.x * cos_a - pt.y * sin_a for pt in hull_pts]
    ys = [pt.x * sin_a + pt.y * cos_a for pt in hull_pts]
    w = max(xs) - min(xs)
    h = max(ys) - min(ys)
    area = w * h
    if area < best_area:
        best_area = area
        best_angle = angle
        best_bbox = (min(xs), min(ys), max(xs), max(ys), w, h)

# Ensure long axis is along X -- recompute bbox if swapping
angle = best_angle
mnx, mny, mxx, mxy, w, h = best_bbox
if h > w:
    angle = best_angle + math.pi / 2
    cos_a2 = math.cos(-angle)
    sin_a2 = math.sin(-angle)
    xs2 = [pt.x * cos_a2 - pt.y * sin_a2 for pt in hull_pts]
    ys2 = [pt.x * sin_a2 + pt.y * cos_a2 for pt in hull_pts]
    mnx, mny, mxx, mxy = min(xs2), min(ys2), max(xs2), max(ys2)
    w, h = mxx - mnx, mxy - mny

# --- Step 4: Hall bar geometry in rotated frame ---
chan_width = int(h * 0.25)
chan_length = int(w * 0.50)
cx_r = (mnx + mxx) / 2.0
cy_r = (mny + mxy) / 2.0

# Thin lead width for voltage arms AND current leads (~1 um)
arm_width = max(int(chan_width * 0.2), int(round(1.0 / dbu)))
n_arms_per_side = 3
arm_spacing = chan_length // (n_arms_per_side + 1)

cos_a = math.cos(angle)
sin_a = math.sin(angle)

def to_layout(rx, ry):
    return int(rx * cos_a - ry * sin_a), int(rx * sin_a + ry * cos_a)

def rotate_rect(rx, ry, rw, rh):
    corners = [
        (rx - rw / 2, ry - rh / 2), (rx + rw / 2, ry - rh / 2),
        (rx + rw / 2, ry + rh / 2), (rx - rw / 2, ry + rh / 2),
    ]
    return pya.Polygon([pya.Point(*to_layout(lx, ly)) for lx, ly in corners])

mesa_region = pya.Region()
# Track explicit tip positions in rotated frame
tip_positions_r = []  # (rx, ry) in rotated frame

# Main channel
mesa_region.insert(rotate_rect(cx_r, cy_r, chan_length, chan_width))

# Side arms (thin voltage probes, extending perpendicular)
arm_len = int(h * 0.60)
for i in range(n_arms_per_side):
    arm_x = cx_r - chan_length / 2 + arm_spacing * (i + 1)
    # Top arm
    arm_cy_top = cy_r + chan_width / 2 + arm_len / 2
    mesa_region.insert(rotate_rect(arm_x, arm_cy_top, arm_width, arm_len))
    tip_positions_r.append((arm_x, cy_r + chan_width / 2 + arm_len))
    # Bottom arm
    arm_cy_bot = cy_r - chan_width / 2 - arm_len / 2
    mesa_region.insert(rotate_rect(arm_x, arm_cy_bot, arm_width, arm_len))
    tip_positions_r.append((arm_x, cy_r - chan_width / 2 - arm_len))

# L-shaped current leads (THIN, same width as voltage arms)
# Short along-axis stub from channel end + perpendicular bend into graphene-only
# Bend in OPPOSITE directions (one +y, one -y) for symmetric pin distribution
along_len = int(round(2.0 / dbu))
perp_len = int(h * 0.75)  # 75% of overlap short dim to exit overlap
for side, perp_dir in [(-1, +1), (+1, -1)]:  # left bends up, right bends down
    # Along-axis stub at channel end (thin)
    stub_cx = cx_r + side * (chan_length / 2 + along_len / 2)
    mesa_region.insert(rotate_rect(stub_cx, cy_r, along_len, arm_width))
    # Perpendicular bend (thin)
    bend_cy = cy_r + perp_dir * perp_len / 2
    bend_cx = cx_r + side * (chan_length / 2 + along_len)
    mesa_region.insert(rotate_rect(bend_cx, bend_cy, arm_width, perp_len))
    # Current lead tip at end of perpendicular bend
    tip_positions_r.append((bend_cx, cy_r + perp_dir * perp_len))

# Merge and clip to graphene boundary
mesa_region.merge()
mesa_region = mesa_region & reg_graphene

# Insert on Mesa layer (L20/0)
li_mesa = _layout.layer(20, 0)
for poly in mesa_region.each():
    _top_cell.shapes(li_mesa).insert(poly)

# Convert tip positions to layout coordinates, then snap to nearest Mesa vertex
mesa_merged = list(mesa_region.each_merged())
mcx = sum(p.bbox().center().x for p in mesa_merged) // len(mesa_merged)
mcy = sum(p.bbox().center().y for p in mesa_merged) // len(mesa_merged)

# Collect all Mesa vertices for snapping
all_mesa_pts = []
for poly in mesa_merged:
    sp = poly.to_simple_polygon()
    for i in range(sp.num_points()):
        pt = sp.point(i)
        all_mesa_pts.append((pt.x, pt.y))

mesa_tips = []
# Radius for collecting nearby vertices to compute centroid of arm end
tip_radius = int(round(3.0 / dbu))
for rx, ry in tip_positions_r:
    # Transform to layout space
    lx, ly = to_layout(rx, ry)
    # Collect all Mesa vertices within tip_radius and average them
    nearby = [(mx, my) for mx, my in all_mesa_pts
              if abs(mx - lx) < tip_radius and abs(my - ly) < tip_radius]
    if nearby:
        avg_x = sum(x for x, y in nearby) // len(nearby)
        avg_y = sum(y for x, y in nearby) // len(nearby)
        mesa_tips.append([avg_x, avg_y])
    else:
        # Fallback: nearest vertex
        best_d2 = float('inf')
        best_pt = [lx, ly]
        for mx, my in all_mesa_pts:
            d2 = (mx - lx)**2 + (my - ly)**2
            if d2 < best_d2:
                best_d2 = d2
                best_pt = [mx, my]
        mesa_tips.append(best_pt)

# Backgate contacts in graphite-only region
graphite_only = reg_graphite - reg_graphene
bg_tips = []
if not graphite_only.is_empty():
    go_bbox = graphite_only.bbox()
    go_cx = (go_bbox.left + go_bbox.right) // 2
    margin = int(round(5.0 / dbu))
    bg_tips.append([go_cx, go_bbox.top - margin])
    bg_tips.append([go_cx, go_bbox.bottom + margin])

with open("/tmp/ml08_contact_tips.json", "w") as f:
    json.dump({"mesa_tips": mesa_tips, "backgate_tips": bg_tips,
               "device_center": [mcx, mcy],
               "channel": {"cx_r": cx_r, "cy_r": cy_r,
                            "length": chan_length, "width": chan_width,
                            "angle": angle}}, f)

_refresh_view()
result = {
    "status": "ok",
    "angle_deg": round(math.degrees(angle), 1),
    "overlap_area_um2": round(overlap_poly.area() * dbu * dbu, 1),
    "channel_width_um": round(chan_width * dbu, 1),
    "mesa_tips": len(mesa_tips),
    "backgate_tips": len(bg_tips),
}
""")
    frames.append(screenshot("05_mesa", zoom_box=(650, 690, 850, 870)))

    # =================================================================
    # Frame 6: Place Contact pads
    # =================================================================
    print("\n=== Frame 6: Place Contact Pads ===")
    tool_call("execute_script", code="""
import json
dbu = _layout.dbu
contact_size = int(round(3.0 / dbu))
hs = contact_size // 2
li_contact = _layout.layer(21, 0)

with open("/tmp/ml08_contact_tips.json") as f:
    tips = json.load(f)

for cx, cy in tips["mesa_tips"]:
    _top_cell.shapes(li_contact).insert(
        pya.Box(int(cx) - hs, int(cy) - hs, int(cx) + hs, int(cy) + hs))
for cx, cy in tips["backgate_tips"]:
    _top_cell.shapes(li_contact).insert(
        pya.Box(int(cx) - hs, int(cy) - hs, int(cx) + hs, int(cy) + hs))

result = {"status": "ok", "contacts": len(tips["mesa_tips"]) + len(tips["backgate_tips"])}
""")
    frames.append(screenshot("06_contacts", zoom_box=(650, 690, 850, 870)))

    # =================================================================
    # Frame 7: Generate TopGate + isolated lead
    # =================================================================
    print("\n=== Frame 7: Generate TopGate ===")
    tool_call("execute_script", code="""
import json, math
dbu = _layout.dbu
li_topgate = _layout.layer(22, 0)
li_contact = _layout.layer(21, 0)

with open("/tmp/ml08_contact_tips.json") as f:
    tips = json.load(f)
dcx, dcy = tips["device_center"]
ch = tips["channel"]
ch_cx, ch_cy = ch["cx_r"], ch["cy_r"]
ch_len, ch_w = ch["length"], ch["width"]
ch_angle = ch["angle"]

# TopGate covers just the channel + 1 um margin on each side
margin = int(round(1.0 / dbu))
gate_w = ch_len + 2 * margin
gate_h = ch_w + 2 * margin

cos_a = math.cos(ch_angle)
sin_a = math.sin(ch_angle)

def to_layout(rx, ry):
    return int(rx * cos_a - ry * sin_a), int(rx * sin_a + ry * cos_a)

def rotate_rect(rx, ry, rw, rh):
    corners = [
        (rx - rw / 2, ry - rh / 2), (rx + rw / 2, ry - rh / 2),
        (rx + rw / 2, ry + rh / 2), (rx - rw / 2, ry + rh / 2),
    ]
    return pya.Polygon([pya.Point(*to_layout(lx, ly)) for lx, ly in corners])

gate_poly = rotate_rect(ch_cx, ch_cy, gate_w, gate_h)
_top_cell.shapes(li_topgate).insert(gate_poly)

# Topgate lead along channel axis for isolation from L-bends
lead_width = int(round(2.0 / dbu))
lead_len = int(round(15.0 / dbu))
# Extend from one end of the gate along the channel axis
lead_cx = ch_cx + (gate_w / 2 + lead_len / 2)
lead_poly = rotate_rect(lead_cx, ch_cy, lead_len, lead_width)
_top_cell.shapes(li_topgate).insert(lead_poly)

# Topgate contact at lead tip
contact_size = int(round(3.0 / dbu))
hs = contact_size // 2
tip_rx = ch_cx + gate_w / 2 + lead_len
tg_cx, tg_cy = to_layout(tip_rx, ch_cy)
_top_cell.shapes(li_contact).insert(
    pya.Box(tg_cx - hs, tg_cy - hs, tg_cx + hs, tg_cy + hs))

# Save topgate contact position for pin placement
tips["topgate_tip"] = [tg_cx, tg_cy]
with open("/tmp/ml08_contact_tips.json", "w") as f:
    json.dump(tips, f)

_refresh_view()
result = {"status": "ok"}
""")
    frames.append(screenshot("07_topgate", zoom_box=(650, 690, 850, 870)))

    # =================================================================
    # Frame 8: Place pin markers (angular pad assignment)
    # =================================================================
    print("\n=== Frame 8: Place Pin Markers ===")
    tool_call("execute_script", code="""
import json, math

dbu = _layout.dbu
pin_size = int(round(5.0 / dbu))
hs = pin_size // 2

li_contact = _layout.find_layer(21, 0)
li_pads = _layout.find_layer(2, 0)
li_pin_contact = _layout.layer(100, 0)
li_pin_pad = _layout.layer(101, 0)

# Read all contact positions (mesa + backgate + topgate)
with open("/tmp/ml08_contact_tips.json") as f:
    tips = json.load(f)

contact_centers = []  # (x, y, type)
for cx, cy in tips["mesa_tips"]:
    contact_centers.append((int(cx), int(cy), "graphene"))
for cx, cy in tips["backgate_tips"]:
    contact_centers.append((int(cx), int(cy), "graphite"))
if "topgate_tip" in tips:
    cx, cy = tips["topgate_tip"]
    contact_centers.append((int(cx), int(cy), "graphene"))

# Place contact pins on L100/0
for cx, cy, ctype in contact_centers:
    _top_cell.shapes(li_pin_contact).insert(
        pya.Box(cx - hs, cy - hs, cx + hs, cy + hs))

n_pins = len(contact_centers)

# Lesson 8: RecursiveShapeIterator for pads (child cells!)
min_pad_dim = int(round(80.0 / dbu))
pad_centers = []
reg_pads = pya.Region()
ri = pya.RecursiveShapeIterator(_layout, _top_cell, li_pads)
while not ri.at_end():
    reg_pads.insert(ri.shape().polygon.transformed(ri.itrans()))
    ri.next()
reg_pads.merge()
for poly in reg_pads.each():
    bbox = poly.bbox()
    if bbox.width() >= min_pad_dim or bbox.height() >= min_pad_dim:
        pad_centers.append(((bbox.left + bbox.right) // 2,
                            (bbox.bottom + bbox.top) // 2))

# Device centroid
dcx, dcy = tips["device_center"]

# Angular pin-to-pad assignment
def angle_from_center(x, y):
    return math.atan2(y - dcy, x - dcx)

contact_angles = [(angle_from_center(cx, cy), i, cx, cy, ctype)
                   for i, (cx, cy, ctype) in enumerate(contact_centers)]
pad_angles = [(angle_from_center(px, py), px, py) for px, py in pad_centers]
contact_angles.sort()
pad_angles.sort()

# Build matched pairs
used_pads = set()
pin_pairs = []  # [(contact_x, contact_y, pad_x, pad_y, angle, type), ...]
for _, ci, ccx, ccy, ctype in contact_angles:
    c_angle = angle_from_center(ccx, ccy)
    best_idx = None
    best_diff = float('inf')
    for j, (pa, px, py) in enumerate(pad_angles):
        if j in used_pads:
            continue
        diff = abs(pa - c_angle)
        if diff > math.pi:
            diff = 2 * math.pi - diff
        if diff < best_diff:
            best_diff = diff
            best_idx = j
    if best_idx is not None:
        used_pads.add(best_idx)
        _, px, py = pad_angles[best_idx]
        pin_pairs.append((ccx, ccy, px, py, c_angle, ctype))

# Sort clockwise (decreasing angle)
pin_pairs.sort(key=lambda p: p[4], reverse=True)

# Place all pins for the screenshot
for ccx, ccy, px, py, _, _ in pin_pairs:
    _top_cell.shapes(li_pin_contact).insert(
        pya.Box(ccx - hs, ccy - hs, ccx + hs, ccy + hs))
    _top_cell.shapes(li_pin_pad).insert(
        pya.Box(px - hs, py - hs, px + hs, py + hs))

# Save pairs for one-at-a-time routing
with open("/tmp/ml08_pin_pairs.json", "w") as f:
    json.dump([{"contact": [cx, cy], "pad": [px, py], "type": ct}
               for cx, cy, px, py, _, ct in pin_pairs], f)

_refresh_view()
result = {"status": "ok", "contact_pins": len(pin_pairs), "pad_pins": len(pin_pairs)}
""")
    frames.append(screenshot("08_pins"))

    # =================================================================
    # Frames 9-19: Multi-window routing, one pair at a time, clockwise
    #   Inner window (200 um around device): fine res 0.5 um, thin lines
    #   Outer window (full template): coarse res 2.0 um, wider lines
    # =================================================================
    with open("/tmp/ml08_pin_pairs.json") as f:
        pin_pairs = json.load(f)

    # Read device center for inner window placement
    with open("/tmp/ml08_contact_tips.json") as f:
        tips_data = json.load(f)
    dev_cx, dev_cy = tips_data["device_center"]

    import math as _math
    inner_half = 100.0  # 200 um inner window, half-size in um
    inner_half_dbu = int(round(inner_half / 0.001))  # in dbu

    for i, pair in enumerate(pin_pairs):
        frame_num = 9 + i
        print(f"\n=== Frame {frame_num}: Route {i + 1}/{len(pin_pairs)} ===")

        cx, cy = pair["contact"]
        px, py = pair["pad"]
        ctype = pair.get("type", "graphene")

        # Compute boundary intersection: ray from device center through
        # contact pin, hitting the inner window edge
        dx = cx - dev_cx
        dy = cy - dev_cy
        if abs(dx) < 1 and abs(dy) < 1:
            bx, by = cx, cy  # contact is at center, skip boundary
        else:
            sx = inner_half_dbu / abs(dx) if abs(dx) > 0 else 1e9
            sy = inner_half_dbu / abs(dy) if abs(dy) > 0 else 1e9
            s = min(sx, sy)
            bx = int(dev_cx + dx * s)
            by = int(dev_cy + dy * s)

        # Place pins: contact pin + boundary pin for inner route
        tool_call("execute_script", code=f"""
dbu = _layout.dbu
pin_size = int(round(5.0 / dbu))
hs = pin_size // 2
li_pin_c = _layout.layer(100, 0)
li_pin_p = _layout.layer(101, 0)
_top_cell.shapes(li_pin_c).clear()
_top_cell.shapes(li_pin_p).clear()
# Contact pin
_top_cell.shapes(li_pin_c).insert(pya.Box({cx} - hs, {cy} - hs, {cx} + hs, {cy} + hs))
# Boundary pin (target for inner route)
_top_cell.shapes(li_pin_p).insert(pya.Box({bx} - hs, {by} - hs, {bx} + hs, {by} + hs))
result = {{"status": "ok"}}
""")

        # Inner route: contact → boundary (fine resolution)
        base_inner = ["3/0", "4/0", "13/0", "20/0", "21/0", "22/0"]
        if ctype == "graphene":
            inner_obs = [o for o in base_inner]  # graphene removed
        else:
            inner_obs = [o for o in base_inner if o != "13/0"]
        route_result = tool_call("auto_route", timeout=120,
            pin_layer_a="100/0",
            pin_layer_b="101/0",
            obstacle_layers=inner_obs,
            output_layer="99/0",
            path_width=0.5,
            obs_safe_distance=3.0,
            path_safe_distance=2.0,
            map_resolution=0.5,
            python_path="/Users/andrewwayne/anaconda3/bin/python3",
        )
        inner_ok = route_result.get("routed_pairs", 0)

        # Move inner path to L3/0
        tool_call("execute_script", code="""
li_temp = _layout.find_layer(99, 0)
li_routes = _layout.layer(3, 0)
if li_temp is not None:
    for shape in _top_cell.shapes(li_temp).each():
        if shape.is_path():
            _top_cell.shapes(li_routes).insert(shape.path)
    _top_cell.shapes(li_temp).clear()
result = {"status": "ok"}
""")

        # Place connection patch at boundary point
        tool_call("execute_script", code=f"""
dbu = _layout.dbu
patch_size = int(round(1.5 / dbu))
phs = patch_size // 2
li_patch = _layout.layer(5, 0)
_top_cell.shapes(li_patch).insert(
    pya.Box({bx} - phs, {by} - phs, {bx} + phs, {by} + phs))
result = {{"status": "ok"}}
""")

        # Outer route: boundary → pad (coarse resolution)
        tool_call("execute_script", code=f"""
dbu = _layout.dbu
pin_size = int(round(5.0 / dbu))
hs = pin_size // 2
li_pin_c = _layout.layer(100, 0)
li_pin_p = _layout.layer(101, 0)
_top_cell.shapes(li_pin_c).clear()
_top_cell.shapes(li_pin_p).clear()
# Boundary pin (start of outer route)
_top_cell.shapes(li_pin_c).insert(pya.Box({bx} - hs, {by} - hs, {bx} + hs, {by} + hs))
# Pad pin
_top_cell.shapes(li_pin_p).insert(pya.Box({px} - hs, {py} - hs, {px} + hs, {py} + hs))
result = {{"status": "ok"}}
""")

        outer_obs = ["2/0", "3/0", "4/0", "5/0", "11/0", "13/0", "20/0", "21/0", "22/0"]
        route_result = tool_call("auto_route", timeout=120,
            pin_layer_a="100/0",
            pin_layer_b="101/0",
            obstacle_layers=outer_obs,
            output_layer="99/0",
            path_width=1.0,
            obs_safe_distance=5.0,
            path_safe_distance=3.0,
            map_resolution=2.0,
            python_path="/Users/andrewwayne/anaconda3/bin/python3",
        )
        outer_ok = route_result.get("routed_pairs", 0)

        # Move outer path to L4/0
        tool_call("execute_script", code="""
li_temp = _layout.find_layer(99, 0)
li_routes = _layout.layer(4, 0)
if li_temp is not None:
    for shape in _top_cell.shapes(li_temp).each():
        if shape.is_path():
            _top_cell.shapes(li_routes).insert(shape.path)
    _top_cell.shapes(li_temp).clear()
result = {"status": "ok"}
""")
        print(f"  Inner: {inner_ok}, Outer: {outer_ok}")
        frames.append(screenshot(f"route_{i + 1:02d}"))

    # =================================================================
    # Final Frame: Final overview
    # =================================================================
    final_frame_num = 9 + len(pin_pairs)
    print(f"\n=== Frame {final_frame_num}: Final Overview ===")
    tool_call("execute_script", code="""
li_pin_c = _layout.find_layer(100, 0)
li_pin_p = _layout.find_layer(101, 0)
if li_pin_c is not None:
    _top_cell.shapes(li_pin_c).clear()
if li_pin_p is not None:
    _top_cell.shapes(li_pin_p).clear()
result = {"status": "ok"}
""")
    # zoom_fit happens automatically via _refresh_view
    frames.append(screenshot("final_overview"))

    # =================================================================
    # Final Frame +1: Zoomed-in review of device area
    # =================================================================
    print(f"\n=== Frame {final_frame_num + 1}: Zoomed-In Review ===")
    frames.append(screenshot("final_zoomed", zoom_box=(650, 690, 850, 870)))

    # =================================================================
    # Combine into GIF
    # =================================================================
    print(f"\n=== Combining {len(frames)} frames into GIF ===")
    from PIL import Image

    images = []
    for f in frames:
        img = Image.open(f)
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg
        images.append(img)

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
