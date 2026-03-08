#!/usr/bin/env python
"""Capture current KLayout layout as a PNG image.

Saves the layout to a temp GDS file, converts to PNG using gds_to_image.py,
and prints the file paths.

Usage:
    python capture.py [--output path.png] [--gds path.gds] [--dpi 200]
"""

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
from mcp_client import init_session, tool_call

# GDS-to-image converter — resolve relative to this script (skills/visual/scripts/ → repo root)
_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
GDS_TO_IMAGE = os.path.join(_REPO_ROOT, "tools", "gds_to_image.py")


def main():
    parser = argparse.ArgumentParser(description="Capture KLayout layout as PNG")
    parser.add_argument("--output", default="/tmp/klayoutclaw_capture.png",
                        help="PNG output path")
    parser.add_argument("--gds", default="/tmp/klayoutclaw_capture.gds",
                        help="Temp GDS output path")
    parser.add_argument("--dpi", type=int, default=200,
                        help="Image DPI")
    args = parser.parse_args()

    # Ensure absolute paths
    gds_path = os.path.abspath(args.gds)
    png_path = os.path.abspath(args.output)

    # Step 1: Save layout to GDS via MCP
    init_session()
    result = tool_call("save_layout", filepath=gds_path)
    print(f"GDS saved: {result.get('filepath', gds_path)}")

    # Step 2: Convert GDS to PNG
    if not os.path.exists(GDS_TO_IMAGE):
        print(f"ERROR: gds_to_image.py not found at {GDS_TO_IMAGE}", file=sys.stderr)
        sys.exit(1)

    cmd = [sys.executable, GDS_TO_IMAGE, gds_path, png_path, str(args.dpi)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"ERROR: gds_to_image.py failed:\n{proc.stderr}", file=sys.stderr)
        sys.exit(1)

    print(f"PNG saved: {png_path}")


if __name__ == "__main__":
    main()
