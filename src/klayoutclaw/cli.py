"""Command-line interface for the KlayoutClaw KLayout payload."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from ._version import __version__
from .lifecycle import LifecycleError, doctor, install, status, uninstall


def _target_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--target-dir",
        help="KLayout pymacros directory (default: $KLAYOUT_HOME/pymacros or ~/.klayout/pymacros)",
    )


def _write_json(value: dict) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _print_status(result: dict) -> None:
    print(f"KlayoutClaw {result['status']}: {result['target']}")
    installed = result.get("installed_version")
    if installed and installed != result.get("version"):
        print(f"Installed version {installed}; available version {result['version']}")
    for entry in result.get("files", []):
        if entry["status"] != "current":
            print(f"  {entry['status']}: {entry['path']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="klayoutclaw", description="Install and manage KlayoutClaw for KLayout"
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    install_parser = subparsers.add_parser(
        "install", help="install or update the KLayout payload"
    )
    _target_argument(install_parser)
    install_parser.add_argument("--force", action="store_true")
    install_parser.add_argument("--dry-run", action="store_true")

    status_parser = subparsers.add_parser(
        "status", help="inspect the installed payload"
    )
    _target_argument(status_parser)
    status_parser.add_argument("--json", action="store_true", dest="as_json")

    uninstall_parser = subparsers.add_parser(
        "uninstall", help="remove the installed payload"
    )
    _target_argument(uninstall_parser)
    uninstall_parser.add_argument("--force", action="store_true")
    uninstall_parser.add_argument("--dry-run", action="store_true")

    doctor_parser = subparsers.add_parser(
        "doctor", help="diagnose core and worker installations"
    )
    _target_argument(doctor_parser)
    doctor_parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            result = install(args.target_dir, force=args.force, dry_run=args.dry_run)
            action = "Would install" if args.dry_run else "Installed"
            if result["status"] == "current":
                action = "Already current"
            print(f"{action} KlayoutClaw {__version__} in {result['target']}")
            for path in result["written"]:
                print(f"  write: {path}")
            for path in result["removed"]:
                print(f"  remove stale: {path}")
            for path in result.get("compatibility_created", []):
                print(f"  create shared compatibility marker: {path}")
            if not args.dry_run:
                print("Restart KLayout to load the installed macros.")
            return 0
        if args.command == "status":
            result = status(args.target_dir)
            _write_json(result) if args.as_json else _print_status(result)
            return 0 if result["status"] == "current" else 1
        if args.command == "uninstall":
            result = uninstall(args.target_dir, force=args.force, dry_run=args.dry_run)
            print(f"KlayoutClaw {result['status']}: {result['target']}")
            for path in result["removed"]:
                print(f"  remove: {path}")
            if result["changed"] and not args.dry_run:
                print("Restart KLayout to unload the removed macros.")
            return 0
        result = doctor(args.target_dir)
        if args.as_json:
            _write_json(result)
        else:
            _print_status(result["installation"])
            workers = result["workers"]
            print(
                f"Workers: {workers['status']} ({workers.get('python') or 'not configured'})"
            )
            for name, available in workers["imports"].items():
                print(f"  {'ok' if available else 'missing'}: {name}")
        return 0 if result["status"] == "ok" else 1
    except LifecycleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
