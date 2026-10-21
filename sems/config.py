from __future__ import annotations

import os
import shutil
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

MIN_ELO = 400
MAX_ELO = 3190  # Stockfish's UCI_Elo ceiling


class Style(str, Enum):
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"
    SOLID = "solid"
    TACTICAL = "tactical"
    GAMBIT = "gambit"
    # Joke styles: still Stockfish underneath, so harder to beat than they look.
    BONGCLOUD = "bongcloud"
    PAWN_STORM = "pawn_storm"
    KNIGHT_RIDER = "knight_rider"
    COWARD = "coward"
    NEVER_CASTLES = "never_castles"


class BotSettings(BaseModel):
    elo: int | None = Field(None, ge=MIN_ELO, le=MAX_ELO, description="None = full strength")
    style: Style = Style.BALANCED
    opening: str | None = Field(None, description="Opening id from data/openings.json")
    use_polyglot: bool = True
    movetime: float = Field(1.0, gt=0, le=60, description="Seconds per move when no clock is given")
    threads: int = Field(1, ge=1, le=64)
    hash_mb: int = Field(128, ge=16, le=8192)


def stockfish_path() -> Path:
    """STOCKFISH_PATH, else engines/stockfish(.exe) from get_stockfish.py, else `stockfish` on PATH."""
    env = os.environ.get("STOCKFISH_PATH")
    if env:
        if not Path(env).exists():
            raise FileNotFoundError(f"STOCKFISH_PATH points to {env}, which doesn't exist.")
        return Path(env)
    for name in ("stockfish.exe", "stockfish"):
        local = ROOT / "engines" / name
        if local.exists():
            return local
    on_path = shutil.which("stockfish")
    if on_path:
        return Path(on_path)
    raise FileNotFoundError(
        "Stockfish not found. Run `python scripts/get_stockfish.py`, install it (e.g. `brew install stockfish`, "
        "`sudo apt install stockfish`), or set STOCKFISH_PATH in .env."
    )


def polyglot_books() -> list[Path]:
    return sorted((ROOT / "books").glob("*.bin"))
