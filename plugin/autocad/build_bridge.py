#!/usr/bin/env python3
"""Build the Apple Silicon AutoCAD 2027 bridge from an Autodesk SDK download."""

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import platform
import plistlib
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
APP = Path("/Applications/Autodesk/AutoCAD 2027/AutoCAD 2027.app")


@contextmanager
def sdk_headers(source):
    if source.is_dir():
        yield source / "inc"
        return

    with tempfile.TemporaryDirectory(prefix="klayoutclaw-objectarx-") as directory:
        root = Path(directory)
        mount = root / "sdk"
        mount.mkdir()
        subprocess.run(
            ["hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", mount, source],
            check=True,
        )
        expanded = root / "expanded"
        try:
            packages = list(mount.glob("*.pkg"))
            if len(packages) != 1:
                raise SystemExit("Expected one ObjectARX installer in the SDK disk image")
            subprocess.run(["pkgutil", "--expand-full", packages[0], expanded], check=True)
        finally:
            subprocess.run(["hdiutil", "detach", mount], check=True)
        yield expanded / "objectarxsdk.pkg" / "Payload" / "inc"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", required=True, type=Path,
                        help="Autodesk ObjectARX 2027 Mac .dmg or extracted Payload directory")
    parser.add_argument("--app", default=APP, type=Path, help="AutoCAD 2027 .app path")
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        parser.error("Build the bridge on an Apple Silicon Mac using a native arm64 Python")
    sdk = args.sdk.expanduser().resolve()
    if not sdk.is_dir() and not (sdk.is_file() and sdk.suffix.lower() == ".dmg"):
        parser.error("--sdk must point to the downloaded .dmg or extracted SDK Payload")
    app = args.app.expanduser().resolve() / "Contents"
    plist = app / "Info.plist"
    if not plist.is_file() or not plistlib.loads(plist.read_bytes()).get(
        "CFBundleShortVersionString", ""
    ).startswith("26.0."):
        parser.error("--app must point to full AutoCAD 2027 for Mac")

    bundle = ROOT / "build" / "KlayoutClawAutoCAD.bundle"
    output = bundle / "Contents"
    executable = output / "MacOS" / "KlayoutClawAutoCAD"
    executable.parent.mkdir(parents=True, exist_ok=True)
    with sdk_headers(sdk) as headers:
        if not (headers / "acdocman.h").is_file():
            parser.error("Could not find inc/acdocman.h in the supplied SDK")
        subprocess.run([
            "xcrun", "clang++", "-std=c++17", "-arch", "arm64",
            "-mmacosx-version-min=14.0", "-bundle", "-fPIC", "-fobjc-arc", "-fblocks",
            "-D_ADESK_MAC_", "-DOSX_SYSTEM", "-D_NATIVE_WCHAR_T_DEFINED",
            "-DUNICODE", "-DACDB_EXT", "-DNDEBUG", "-Wno-nonportable-include-path",
            "-Wno-unused-value", "-Wno-format-security",
            "-I", headers, "-L", app / "Frameworks", ROOT / "native" / "bridge.mm",
            "-lacfirst", "-lwinapi", "-lacdb", "-laccore", "-lgelib", "-lAcPal",
            app / "PlugIns" / "AcGeomentObj.dbx" / "AcGeomentObj",
            "-framework", "Foundation", "-o", executable,
        ], check=True)
    (output / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleIdentifier": "org.klayoutclaw.autocad",
        "CFBundleExecutable": executable.name,
        "CFBundlePackageType": "BNDL",
        "CFBundleVersion": "0.1.0",
        "CFBundleName": executable.name,
    }))
    subprocess.run(["codesign", "--force", "--sign", "-", bundle], check=True)
    print("Load in the AutoCAD command line:")
    print(f"(arxload {json.dumps(str(bundle), ensure_ascii=False)})")


if __name__ == "__main__":
    main()
