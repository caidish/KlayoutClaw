#!/usr/bin/env python
"""Install KlayoutClaw MCP server plugin into KLayout's pymacros directory."""

import shutil
import sys
from pathlib import Path


def main():
    plugin_dir = Path(__file__).parent / "plugin"
    klayout_dir = Path.home() / ".klayout" / "pymacros"
    klayout_dir.mkdir(parents=True, exist_ok=True)

    for lym_file in ["klayoutclaw_server.lym", "klayoutclaw_ui.lym"]:
        src = plugin_dir / lym_file
        if not src.exists():
            print(f"ERROR: Plugin file not found: {src}")
            sys.exit(1)
        dst = klayout_dir / lym_file
        shutil.copy2(src, dst)
        print(f"Installed: {dst}")

    print("\nDone! No external Python dependencies needed (uses only stdlib + pya).")
    print("Restart KLayout to activate the MCP server.")
    print("The server will be available at http://127.0.0.1:8765/mcp")


if __name__ == "__main__":
    main()
