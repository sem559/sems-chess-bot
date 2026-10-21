"""Download the latest official Stockfish build for this computer into engines/.

Source: https://github.com/official-stockfish/Stockfish/releases (official project only).
Works on Windows, macOS and Linux (x86-64 and arm64).

Usage: python scripts/get_stockfish.py [--asset NAME]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import platform
import stat
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

API = "https://api.github.com/repos/official-stockfish/Stockfish/releases/latest"
ROOT = Path(__file__).resolve().parent.parent
ENGINES = ROOT / "engines"


def wanted_assets() -> list[str]:
    """Release asset names to try for this OS/CPU, best first."""
    system = platform.system()
    arm = platform.machine().lower() in ("arm64", "aarch64")
    if system == "Windows":
        cpu = "arm64" if arm else "x86-64"
        return [f"stockfish-windows-{cpu}-universal.zip", "stockfish-windows-x86-64-avx2.zip"]
    if system == "Darwin":
        return ["stockfish-macos-universal.tar.gz", f"stockfish-macos-{'m1-apple-silicon' if arm else 'x86-64-avx2'}.tar"]
    cpu = "arm64" if arm else "x86-64"
    return [f"stockfish-linux-{cpu}-universal.tar.gz", f"stockfish-ubuntu-{cpu}-avx2.tar"]


def extract_binary(name: str, data: bytes) -> bytes:
    """The engine is by far the largest file in the archive (the rest is source and docs)."""
    if name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            info = max((i for i in zf.infolist() if not i.is_dir()), key=lambda i: i.file_size)
            return zf.read(info)
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        member = max((m for m in tf.getmembers() if m.isfile()), key=lambda m: m.size)
        return tf.extractfile(member).read()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", help="exact release asset name to download (overrides auto-detection)")
    args = parser.parse_args()

    request = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json"})
    if os.environ.get("GITHUB_TOKEN"):  # CI: avoids the shared-IP rate limit
        request.add_header("Authorization", f"Bearer {os.environ['GITHUB_TOKEN']}")
    with urllib.request.urlopen(request, timeout=30) as resp:
        release = json.load(resp)
    by_name = {a["name"]: a for a in release["assets"]}
    candidates = [args.asset] if args.asset else wanted_assets()
    asset = next((by_name[n] for n in candidates if n in by_name), None)
    if asset is None:
        print(f"No matching build in {release['tag_name']} for {platform.system()} {platform.machine()}.\n"
              f"Available: {', '.join(by_name)}\nRe-run with --asset NAME, or install Stockfish yourself "
              "and set STOCKFISH_PATH in .env.", file=sys.stderr)
        return 1

    print(f"Downloading {asset['name']} ({asset['size'] / 1e6:.0f} MB) from {release['tag_name']}...")
    with urllib.request.urlopen(asset["browser_download_url"], timeout=600) as resp:
        data = resp.read()

    ENGINES.mkdir(exist_ok=True)
    target = ENGINES / ("stockfish.exe" if platform.system() == "Windows" else "stockfish")
    target.write_bytes(extract_binary(asset["name"], data))
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"Installed {target} ({release['tag_name']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
