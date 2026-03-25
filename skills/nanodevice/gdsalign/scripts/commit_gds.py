#!/usr/bin/env python
"""Warp microscope image and material contours into GDS coordinates.

Transforms flakedetect traces and the full-stack image through the
image_um -> GDS_um affine warp computed by align_gds.py.

Usage:
    python commit_gds.py --warp gds_warp.npy --traces traces.json \
        --image full_stack_raw.jpg --pixel-size 0.087 \
        --gds Template.gds --output-dir output/ [--warp-only]

Outputs:
    traces_gds.json      - traces with contour_gds field added (GDS um coords)
    full_stack_gds.png   - microscope image warped into GDS coordinate frame
    image_placement.json - position/scale of warped image in GDS coordinates
"""
import argparse
import copy
import json
import os
import sys

import cv2
import numpy as np

# MCP client for KLayout communication (only imported when needed)
SKILL_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "..", "..", "scripts")

# Material → KLayout layer mapping
LAYER_MAP = {
    "top_hBN": (10, 0),
    "graphene": (11, 0),
    "bottom_hBN": (12, 0),
    "graphite": (13, 0),
}


def warp_contour(contour_um, M):
    """Transform Nx2 points through a 2x3 affine matrix.

    Parameters
    ----------
    contour_um : list of [x, y]
        Points in image-um coordinates.
    M : ndarray, shape (2, 3)
        Affine matrix mapping image_um -> GDS_um.

    Returns
    -------
    list of [x, y]
        Points in GDS-um coordinates.
    """
    pts = np.array(contour_um, dtype=np.float64)
    gds_pts = (M[:2, :2] @ pts.T).T + M[:2, 2]
    return gds_pts.tolist()


def compute_warped_corners(M, pixel_size, img_w, img_h):
    """Compute GDS-um positions of the four image corners.

    Parameters
    ----------
    M : ndarray (2, 3)
        image_um -> GDS_um affine.
    pixel_size : float
        um per pixel.
    img_w, img_h : int
        Image dimensions in pixels.

    Returns
    -------
    ndarray (4, 2)
        Corner positions in GDS-um.
    """
    corners_um = np.array([
        [0.0, 0.0],
        [img_w * pixel_size, 0.0],
        [img_w * pixel_size, img_h * pixel_size],
        [0.0, img_h * pixel_size],
    ])
    return warp_contour(corners_um, M)


def warp_image(img, M, pixel_size_in, pixel_size_out=None):
    """Warp an image from image-pixel space to GDS-um space.

    The full transform chain is:
        image_px -> image_um (multiply by pixel_size_in)
        image_um -> GDS_um   (apply M)
        GDS_um   -> output_px (subtract origin, divide by pixel_size_out)

    Parameters
    ----------
    img : ndarray
        Input image (H, W) or (H, W, C).
    M : ndarray (2, 3)
        image_um -> GDS_um affine.
    pixel_size_in : float
        Input image um/px.
    pixel_size_out : float or None
        Output image um/px. Defaults to pixel_size_in.

    Returns
    -------
    warped : ndarray
        Warped image.
    origin_um : tuple (x_min, y_min)
        GDS-um position of the output image origin (top-left pixel).
    out_pixel_size : float
        Output pixel size in um.
    """
    if pixel_size_out is None:
        pixel_size_out = pixel_size_in

    h, w = img.shape[:2]

    # Compute bounding box of warped corners in GDS-um
    corners_gds = np.array(compute_warped_corners(M, pixel_size_in, w, h))
    x_min = float(corners_gds[:, 0].min())
    y_min = float(corners_gds[:, 1].min())
    x_max = float(corners_gds[:, 0].max())
    y_max = float(corners_gds[:, 1].max())

    # Output canvas dimensions
    out_w = int(np.ceil((x_max - x_min) / pixel_size_out))
    out_h = int(np.ceil((y_max - y_min) / pixel_size_out))

    # Clamp to reasonable size (max 8000 px in either dimension)
    max_dim = 8000
    if out_w > max_dim or out_h > max_dim:
        scale_down = max_dim / max(out_w, out_h)
        pixel_size_out = pixel_size_out / scale_down
        out_w = int(np.ceil((x_max - x_min) / pixel_size_out))
        out_h = int(np.ceil((y_max - y_min) / pixel_size_out))

    # Build combined pixel-domain matrix:
    #   M_combined = S_out @ M_um @ S_in_inv
    # where:
    #   S_in: image_px -> image_um:  [[ps_in, 0, 0], [0, ps_in, 0]]
    #   S_out: GDS_um -> output_px:  [[1/ps_out, 0, -x_min/ps_out],
    #                                  [0, 1/ps_out, -y_min/ps_out]]

    # S_in_inv (um -> px): divide by ps_in, but we compose into M directly
    # S_in (px -> um): multiply by ps_in
    S_in = np.array([
        [pixel_size_in, 0, 0],
        [0, pixel_size_in, 0],
        [0, 0, 1],
    ])

    # M_um as 3x3
    M_3x3 = np.vstack([M, [0, 0, 1]])

    # S_out (GDS_um -> output_px)
    S_out = np.array([
        [1.0 / pixel_size_out, 0, -x_min / pixel_size_out],
        [0, 1.0 / pixel_size_out, -y_min / pixel_size_out],
        [0, 0, 1],
    ])

    M_combined_3x3 = S_out @ M_3x3 @ S_in
    M_combined = M_combined_3x3[:2, :]  # back to 2x3

    warped = cv2.warpAffine(
        img, M_combined, (out_w, out_h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )

    return warped, (x_min, y_min), pixel_size_out, (out_w, out_h)


def main():
    parser = argparse.ArgumentParser(
        description="Warp image + contours into GDS coordinates and commit")
    parser.add_argument("--warp", required=True,
                        help="Path to gds_warp.npy (2x3 affine)")
    parser.add_argument("--traces", required=True,
                        help="Path to traces.json from flakedetect")
    parser.add_argument("--image", required=True,
                        help="Path to full_stack image")
    parser.add_argument("--pixel-size", required=True, type=float,
                        help="Image pixel size in um/px")
    parser.add_argument("--gds", required=True,
                        help="Path to GDS template (for reference)")
    parser.add_argument("--output-dir", required=True,
                        help="Output directory")
    parser.add_argument("--warp-only", action="store_true",
                        help="Only produce warped outputs, skip KLayout commit")
    args = parser.parse_args()

    # --- Validate inputs ---
    for path, label in [(args.warp, "warp matrix"),
                        (args.traces, "traces"),
                        (args.image, "image"),
                        (args.gds, "GDS template")]:
        if not os.path.exists(path):
            print(f"ERROR: {label} not found: {path}", file=sys.stderr)
            sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)

    # --- Load inputs ---
    M = np.load(args.warp)
    if M.shape != (2, 3):
        print(f"ERROR: warp matrix has shape {M.shape}, expected (2, 3)",
              file=sys.stderr)
        sys.exit(1)

    with open(args.traces) as f:
        traces = json.load(f)

    img = cv2.imread(args.image)
    if img is None:
        print(f"ERROR: could not read image: {args.image}", file=sys.stderr)
        sys.exit(1)

    pixel_size = args.pixel_size

    # --- Step 1: Warp contours ---
    print("Warping material contours to GDS coordinates...")
    traces_gds = copy.deepcopy(traces)
    total_contours = 0

    for mat_name, mat_list in traces_gds["materials"].items():
        for entry in mat_list:
            if "contour_um" not in entry:
                print(f"  WARNING: {mat_name} id={entry.get('id', '?')} "
                      f"missing contour_um, skipping")
                continue
            gds_pts = warp_contour(entry["contour_um"], M)
            entry["contour_gds"] = [[round(x, 3), round(y, 3)]
                                    for x, y in gds_pts]
            total_contours += 1

    print(f"  Transformed {total_contours} contours")

    traces_gds_path = os.path.join(args.output_dir, "traces_gds.json")
    with open(traces_gds_path, "w") as f:
        json.dump(traces_gds, f, indent=2)
    print(f"Saved {traces_gds_path}")

    # --- Step 2: Warp image ---
    print("Warping image to GDS coordinate frame...")
    warped, origin_um, out_ps, (out_w, out_h) = warp_image(
        img, M, pixel_size)

    warped_path = os.path.join(args.output_dir, "full_stack_gds.png")

    # Horizontal flip to match KLayout coordinate convention (image X vs GDS X)
    warped = cv2.flip(warped, 1)

    # Use lossless PNG for quality
    cv2.imwrite(warped_path, warped)
    print(f"Saved {warped_path} ({out_w}x{out_h} px)")

    # Compute extent in GDS-um
    width_um = out_w * out_ps
    height_um = out_h * out_ps

    placement = {
        "image_file": os.path.basename(warped_path),
        "origin_um": [round(origin_um[0], 3), round(origin_um[1], 3)],
        "pixel_size_um": round(out_ps, 6),
        "width_um": round(width_um, 3),
        "height_um": round(height_um, 3),
        "canvas_px": [out_w, out_h],
    }

    placement_path = os.path.join(args.output_dir, "image_placement.json")
    with open(placement_path, "w") as f:
        json.dump(placement, f, indent=2)
    print(f"Saved {placement_path}")

    # --- Step 3: KLayout commit (if not --warp-only) ---
    if args.warp_only:
        print("--warp-only: skipping KLayout commit")
        print("Done.")
        sys.exit(0)

    # Import MCP client for KLayout communication
    sys.path.insert(0, SKILL_ROOT)
    from mcp_client import init_session, execute_script, tool_call

    print("Connecting to KLayout MCP server...")
    init_session()

    # Step 3a: Load Template.gds into KLayout
    gds_abs = os.path.abspath(args.gds)
    print(f"Loading GDS template: {gds_abs}")
    execute_script(f"""
import os
filepath = "{gds_abs}"
if not os.path.exists(filepath):
    raise FileNotFoundError(f"GDS not found: {{filepath}}")

mw = pya.Application.instance().main_window()

# Check if already loaded
view = mw.current_view()
already_loaded = False
if view is not None:
    for ci in range(view.cellviews()):
        cv = view.cellview(ci)
        if cv.is_valid() and cv.filename() == filepath:
            already_loaded = True
            break

if not already_loaded:
    mw.load_layout(filepath, 1)
    view = mw.current_view()

# Store references for subsequent calls
_layout = view.active_cellview().layout()
_top_cell = _layout.top_cell()
result = {{"status": "ok", "cell": _top_cell.name, "loaded": not already_loaded}}
""")
    print("  GDS template loaded")

    # Step 3b: Add warped image as background overlay
    warped_abs = os.path.abspath(warped_path)
    x_origin = placement["origin_um"][0]
    y_origin = placement["origin_um"][1]
    out_pixel_size = placement["pixel_size_um"]
    print(f"Adding warped image overlay at ({x_origin:.1f}, {y_origin:.1f}) um...")
    execute_script(f"""
import os
filepath = "{warped_abs}"
if not os.path.exists(filepath):
    raise FileNotFoundError(f"Image not found: {{filepath}}")

view, layout, cell = _get_or_create_view()
img = pya.Image(filepath)
img.visible = True

ps = {out_pixel_size}
# 180 deg rotation pivots around displacement point, so offset by image extent
# to keep the image in the same bounding box
x_off = {x_origin} + {placement["width_um"]}
y_off = {y_origin} + {placement["height_um"]}

img.trans = pya.DCplxTrans(ps, 180, False, pya.DVector(x_off, y_off))
view.insert_image(img)

result = {{"status": "ok", "id": img.id()}}
""")
    print("  Background image added")

    # Step 3c: Insert material contour polygons
    print("Inserting material polygons...")
    n_inserted = 0
    for mat_name, mat_list in traces_gds["materials"].items():
        layer_dt = LAYER_MAP.get(mat_name)
        if layer_dt is None:
            print(f"  WARNING: No layer mapping for '{mat_name}', skipping")
            continue
        layer, dt = layer_dt
        for entry in mat_list:
            if "contour_gds" not in entry:
                continue
            pts = entry["contour_gds"]
            if len(pts) < 3:
                continue
            # Build points list for pya — closed path (border only)
            closed_pts = pts + [pts[0]]  # close the contour
            pts_code = ", ".join(
                f"pya.Point(int({x}/dbu), int({y}/dbu))"
                for x, y in closed_pts
            )
            execute_script(f"""
view, layout, cell = _get_or_create_view()
dbu = layout.dbu
li = layout.layer({layer}, {dt})
pts = [{pts_code}]
cell.shapes(li).insert(pya.Path(pts, int(0.2/dbu)))
result = {{"status": "ok"}}
""")
            n_inserted += 1
            print(f"  {mat_name}: polygon with {len(pts)} points on L{layer}/{dt}")

    print(f"Inserted {n_inserted} polygons")

    # Step 3d: Refresh view and zoom to fit
    execute_script("""
view = pya.Application.instance().main_window().current_view()
if view is not None:
    view.zoom_fit()
    view.max_hier()
result = {"status": "ok"}
""")

    print("Done. KLayout updated with warped image + material polygons.")
    sys.exit(0)


if __name__ == "__main__":
    main()
