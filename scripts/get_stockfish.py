"""Download the latest official Stockfish Windows build into engines/.

Source: https://github.com/official-stockfish/Stockfish/releases (official project only).
Usage: python scripts/get_stockfish.py [--variant universal|avx2|bmi2|...]
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

API = "https://api.github.com/repos/official-stockfish/Stockfish/releases/latest"
ROOT = Path(__file__).resolve().parent.parent
ENGINES = ROOT / "engines"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", default="universal")
    args = parser.parse_args()

    with urllib.request.urlopen(API, timeout=30) as resp:
        release = json.load(resp)

    # Newer releases (sf_19+) ship a single "universal" build that picks the CPU path at runtime.
    candidates = [f"stockfish-windows-x86-64-{args.variant}.zip", "stockfish-windows-x86-64-universal.zip"]
    by_name = {a["name"]: a for a in release["assets"]}
    asset = next((by_name[n] for n in candidates if n in by_name), None)
    if asset is None:
        names = ", ".join(a["name"] for a in release["assets"] if "windows" in a["name"])
        print(f"No matching asset in {release['tag_name']}. Available: {names}", file=sys.stderr)
        return 1

    print(f"Downloading {asset['name']} ({asset['size'] / 1e6:.1f} MB) from {release['tag_name']}")
    with urllib.request.urlopen(asset["browser_download_url"], timeout=300) as resp:
        data = resp.read()

    ENGINES.mkdir(exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        exe = next(n for n in zf.namelist() if n.endswith(".exe"))
        with zf.open(exe) as src, open(ENGINES / "stockfish.exe", "wb") as dst:
            shutil.copyfileobj(src, dst)

    print(f"Installed {ENGINES / 'stockfish.exe'} ({release['tag_name']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
