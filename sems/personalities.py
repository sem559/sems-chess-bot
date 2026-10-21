"""Named bot characters: an ELO/style/opening preset plus chat lines for game events."""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

import chess

from .config import BotSettings, Style

DATA = Path(__file__).resolve().parent / "data" / "personalities.json"
EVENTS = ("greeting", "check", "capture", "blunder", "winning", "losing", "win", "loss", "draw")


@dataclass(frozen=True)
class Personality:
    id: str
    name: str
    avatar: str
    tagline: str
    elo: int | None
    style: Style
    openings: dict[str, str]  # bot color ("white"/"black") -> opening id
    lines: dict[str, tuple[str, ...]]

    def settings(self, bot_color: chess.Color, **overrides) -> BotSettings:
        opening = self.openings.get("white" if bot_color == chess.WHITE else "black")
        return BotSettings(elo=self.elo, style=self.style, opening=opening, **overrides)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "avatar": self.avatar, "tagline": self.tagline,
                "elo": self.elo, "style": self.style.value, "openings": self.openings}


def load_personalities(path: Path = DATA) -> dict[str, Personality]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        p["id"]: Personality(
            p["id"], p["name"], p["avatar"], p["tagline"], p["elo"], Style(p["style"]),
            dict(p["openings"]), {k: tuple(v) for k, v in p["lines"].items()},
        )
        for p in raw
    }


class Chatter:
    """Decides when a personality says something. Big moments always get a line;
    checks and captures only sometimes, with a cooldown so it isn't spammy."""

    BLUNDER_JUMP_CP = 250  # bot eval rising this much between its moves = opponent blundered
    WINNING_CP = 500
    LOSING_CP = -300
    COOLDOWN_MOVES = 3

    def __init__(self, personality: Personality, rng: random.Random | None = None):
        self.p = personality
        self.rng = rng or random.Random()
        self.prev_eval: int | None = None
        self.since_last = self.COOLDOWN_MOVES
        self.said: set[str] = set()

    def line(self, event: str) -> str | None:
        options = self.p.lines.get(event)
        return self.rng.choice(options) if options else None

    def greet(self) -> str | None:
        return self.line("greeting")

    def after_bot_move(self, board_before: chess.Board, move: chess.Move, bot_eval_cp: int | None) -> str | None:
        """`bot_eval_cp` is the engine score from the bot's point of view (None for book moves)."""
        self.since_last += 1
        event = None
        if bot_eval_cp is not None:
            ev = max(-2000, min(2000, bot_eval_cp))
            if self.prev_eval is not None and ev - self.prev_eval >= self.BLUNDER_JUMP_CP and ev >= 150:
                event = "blunder"
            elif ev >= self.WINNING_CP and "winning" not in self.said:
                event = "winning"
            elif ev <= self.LOSING_CP and "losing" not in self.said:
                event = "losing"
            self.prev_eval = ev
        if event is None and self.since_last > self.COOLDOWN_MOVES:
            if board_before.gives_check(move) and self.rng.random() < 0.5:
                event = "check"
            elif board_before.is_capture(move) and self.rng.random() < 0.35:
                event = "capture"
        if event is None:
            return None
        self.said.add(event)
        self.since_last = 0
        return self.line(event)

    def game_over(self, result: str, bot_color: chess.Color) -> str | None:
        if result == "1/2-1/2":
            return self.line("draw")
        bot_won = (result == "1-0") == (bot_color == chess.WHITE)
        return self.line("win" if bot_won else "loss")
