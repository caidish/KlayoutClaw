"""Compatibility wrapper for the packaged ``klayoutclaw install`` command."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    source_root = Path(__file__).resolve().parent / "src"
    if source_root.is_dir():
        sys.path.insert(0, str(source_root))
    from klayoutclaw.cli import main as cli_main

    print(
        "warning: install.py is deprecated; use `klayoutclaw install` instead",
        file=sys.stderr,
    )
    return cli_main(["install", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
