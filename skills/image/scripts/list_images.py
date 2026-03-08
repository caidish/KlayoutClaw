#!/usr/bin/env python
"""List all background images in the current KLayout view.

Usage:
    python list_images.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
from mcp_client import init_session, execute_script


def main():
    init_session()
    result = execute_script("""
view, layout, cell = _get_or_create_view()
images = list(view.each_image())
img_list = []
for img in images:
    s = img.to_s()
    # Extract file path from to_s() output
    fname = ""
    if "file='" in s:
        fname = s.split("file='")[-1].rstrip("'")
    # Extract visibility from to_s()
    vis = "is_visible=true" in s
    img_list.append({
        "id": img.id(),
        "file": fname,
        "visible": vis,
        "trans": str(img.trans),
    })
result = {"count": len(img_list), "images": img_list}
""")

    images = result.get("images", [])
    if not images:
        print("No background images loaded.")
        return

    print(f"Background images ({len(images)}):")
    print(f"  {'ID':<6} {'Visible':<9} {'Transform':<30} {'File'}")
    print(f"  {'─'*6} {'─'*9} {'─'*30} {'─'*30}")
    for img in images:
        fname = os.path.basename(img["file"]) if img["file"] else "(no file)"
        vis = "yes" if img["visible"] else "no"
        print(f"  {img['id']:<6} {vis:<9} {img['trans']:<30} {fname}")


if __name__ == "__main__":
    main()
