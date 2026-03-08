#!/usr/bin/env python
"""Remove background image(s) from the current KLayout view.

Usage:
    python remove_image.py <image_id | all>
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
from mcp_client import init_session, execute_script


def main():
    if len(sys.argv) != 2:
        print(f"Usage: python {sys.argv[0]} <image_id | all>")
        sys.exit(1)

    target = sys.argv[1]

    init_session()

    if target == "all":
        result = execute_script("""
view, layout, cell = _get_or_create_view()
images = list(view.each_image())
count = len(images)
for img in images:
    view.erase_image(img.id())
result = {"status": "ok", "removed": count}
""")
        count = result.get("removed", 0)
        print(f"OK: removed {count} image(s)")
    else:
        try:
            img_id = int(target)
        except ValueError:
            print(f"ERROR: invalid image id '{target}' (use a number or 'all')", file=sys.stderr)
            sys.exit(1)

        result = execute_script(f"""
view, layout, cell = _get_or_create_view()
try:
    view.erase_image({img_id})
    result = {{"status": "ok", "removed_id": {img_id}}}
except Exception as e:
    result = {{"status": "error", "message": str(e)}}
""")
        if result.get("status") == "ok":
            print(f"OK: removed image id={img_id}")
        else:
            print(f"ERROR: {result.get('message', 'unknown error')}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
