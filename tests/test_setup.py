"""Engine download helpers (no network)."""
import io
import sys
import tarfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from get_stockfish import extract_binary, wanted_assets  # noqa: E402


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _tar(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_extracts_largest_file_as_engine():
    files = {"stockfish/README.md": b"x" * 10, "stockfish/src/search.cpp": b"y" * 100,
             "stockfish/stockfish-bin": b"ENGINE" * 1000}
    assert extract_binary("a.zip", _zip(files)) == files["stockfish/stockfish-bin"]
    assert extract_binary("a.tar.gz", _tar(files)) == files["stockfish/stockfish-bin"]


def test_wanted_assets_match_this_platform():
    names = wanted_assets()
    assert names and all(n.startswith("stockfish-") for n in names)
    key = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    assert key in names[0]
