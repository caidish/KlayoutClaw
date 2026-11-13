"""Local CLI and stdio launcher."""

import argparse
import json
import sys

from . import __version__
from .bridge import BridgeError
from .server import serve
from .tools import definitions, invoke


def main():
    parser = argparse.ArgumentParser(description="KlayoutClaw's native AutoCAD adapter")
    parser.add_argument("command", choices=["serve", "doctor", "tools"])
    args = parser.parse_args()
    if args.command == "serve":
        serve()
        return
    if args.command == "tools":
        result = definitions()
    else:
        try:
            result = invoke("ping")
            if result["bridge"] != "KlayoutClaw AutoCAD" or result["version"] != __version__:
                raise ValueError("Bridge version mismatch. Rebuild and load this checkout's bundle.")
            result.update(invoke("documents"))
            if not any(doc["active"] for doc in result["documents"]):
                raise ValueError("Open a drawing in AutoCAD before using the adapter.")
        except (BridgeError, OSError, ValueError) as exc:
            print(f"AutoCAD doctor: {exc}", file=sys.stderr)
            if isinstance(exc, BridgeError) and exc.data.get("sockets"):
                print("\n".join(exc.data["sockets"]), file=sys.stderr)
            raise SystemExit(1) from exc
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
