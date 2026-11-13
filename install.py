#!/usr/bin/env python
"""Install KlayoutClaw MCP server plugin into KLayout's pymacros directory."""

import os
import shutil
import sys
from pathlib import Path


def klayout_home():
    configured = os.environ.get("KLAYOUT_HOME")
    if configured is not None:
        if not configured:
            raise ValueError("KLAYOUT_HOME is empty. Set it to a directory or unset it before installing.")
        return Path(configured).expanduser().resolve()
    if sys.platform == "win32":
        # Match KLayout's Windows home lookup, including redirected profiles.
        home_path = os.environ.get("HOMEPATH")
        if home_path is not None:
            for key in ("HOMEDRIVE", "HOMESHARE"):
                prefix = os.environ.get(key)
                if prefix is not None:
                    return Path(prefix + home_path) / "KLayout"
        return Path.home() / "KLayout"
    return Path.home() / ".klayout"


def main():
    plugin_dir = Path(__file__).parent / "plugin"
    try:
        klayout_dir = klayout_home() / "pymacros"
    except ValueError as exc:
        sys.exit(str(exc))
    klayout_dir.mkdir(parents=True, exist_ok=True)

    for lym_file in ["klayoutclaw_server.lym", "klayoutclaw_ui.lym"]:
        src = plugin_dir / lym_file
        if not src.exists():
            print(f"ERROR: Plugin file not found: {src}")
            sys.exit(1)
        dst = klayout_dir / lym_file
        shutil.copy2(src, dst)
        print(f"Installed: {dst}")

    tools_dir = Path(__file__).parent / "tools"
    # ordered_loop.py + two_level.py are imported by route_worker.py
    # (assignment_engine="ordered_loop" / adaptive two-level routing).
    for worker in ["route_worker.py", "evaluate_worker.py", "ordered_loop.py", "two_level.py"]:
        src = tools_dir / worker
        if not src.exists():
            print(f"ERROR: Worker script not found: {src}")
            sys.exit(1)
        dst = klayout_dir / worker
        shutil.copy2(src, dst)
        print(f"Installed: {dst}")

    # Phase 5: install tools/vc_mcp_handlers.py as pymacros/tools/vc_mcp_handlers.py
    # so the lym server can import ``tools.vc_mcp_handlers``.
    dst_tools_dir = klayout_dir / "tools"
    dst_tools_dir.mkdir(parents=True, exist_ok=True)
    # Make ``tools`` an importable package.
    init_py = dst_tools_dir / "__init__.py"
    if not init_py.exists():
        init_py.write_text("")
    vc_src = tools_dir / "vc_mcp_handlers.py"
    if vc_src.exists():
        shutil.copy2(vc_src, dst_tools_dir / "vc_mcp_handlers.py")
        print(f"Installed: {dst_tools_dir / 'vc_mcp_handlers.py'}")

    # Phase 4: install the klayoutclaw_vc package (G5 backend) next to the
    # lym files so ``from plugin.klayoutclaw_vc import repo`` resolves via
    # the sys.path bootstrap in tools/vc_mcp_handlers.py.
    plugin_pkg_src = plugin_dir / "klayoutclaw_vc"
    if plugin_pkg_src.is_dir():
        # Mirror the `plugin/` parent so the import path matches source.
        dst_plugin_parent = klayout_dir / "plugin"
        dst_plugin_parent.mkdir(parents=True, exist_ok=True)
        plugin_init = dst_plugin_parent / "__init__.py"
        if not plugin_init.exists():
            plugin_init.write_text("")
        dst_pkg = dst_plugin_parent / "klayoutclaw_vc"
        if dst_pkg.exists():
            shutil.rmtree(dst_pkg)
        shutil.copytree(plugin_pkg_src, dst_pkg)
        print(f"Installed: {dst_pkg}")


    print("\nDone! No external Python dependencies needed (uses only stdlib + pya).")
    print("Restart KLayout to activate the MCP server.")
    print("The server will be available at http://127.0.0.1:8765/mcp")


if __name__ == "__main__":
    main()
