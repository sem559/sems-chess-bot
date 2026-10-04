from __future__ import annotations

import os
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


class BotSettings(BaseModel):
    elo: int | None = Field(None, ge=MIN_ELO, le=MAX_ELO, description="None = full strength")
    style: Style = Style.BALANCED
    opening: str | None = Field(None, description="Opening id from data/openings.json")
    use_polyglot: bool = True
    movetime: float = Field(1.0, gt=0, le=60, description="Seconds per move when no clock is given")
    threads: int = Field(1, ge=1, le=64)
    hash_mb: int = Field(128, ge=16, le=8192)


def stockfish_path() -> Path:
    env = os.environ.get("STOCKFISH_PATH")
    path = Path(env) if env else ROOT / "engines" / "stockfish.exe"
    if not path.exists():
        raise FileNotFoundError(
            f"Stockfish not found at {path}. Run `python scripts/get_stockfish.py` or set STOCKFISH_PATH."
        )
    return path


def polyglot_books() -> list[Path]:
    return sorted((ROOT / "books").glob("*.bin"))
