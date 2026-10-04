from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

import chess
import chess.polyglot

DATA = Path(__file__).resolve().parent / "data" / "openings.json"


@dataclass(frozen=True)
class Opening:
    id: str
    name: str
    eco: str
    side: str  # "white" or "black": whose opening this is
    lines: tuple[tuple[str, ...], ...]

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "eco": self.eco, "side": self.side}


class OpeningBook:
    def __init__(self, openings: list[Opening]):
        self.openings = {o.id: o for o in openings}

    @classmethod
    def load(cls, path: Path = DATA) -> "OpeningBook":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls([
            Opening(o["id"], o["name"], o["eco"], o["side"], tuple(tuple(l.split()) for l in o["lines"]))
            for o in raw
        ])

    def get(self, opening_id: str) -> Opening:
        return self.openings[opening_id]

    def next_move(self, board: chess.Board, opening_id: str, rng: random.Random | None = None) -> chess.Move | None:
        """Next repertoire move if the game so far is a prefix of one of the opening's lines."""
        opening = self.openings.get(opening_id)
        if opening is None or board.root() != chess.Board():
            return None
        played = tuple(m.uci() for m in board.move_stack)
        options = {
            line[len(played)]
            for line in opening.lines
            if len(line) > len(played) and line[: len(played)] == played
        }
        legal = [chess.Move.from_uci(u) for u in sorted(options) if chess.Move.from_uci(u) in board.legal_moves]
        if not legal:
            return None
        return (rng or random).choice(legal)


def polyglot_move(board: chess.Board, books: list[Path], rng: random.Random | None = None) -> chess.Move | None:
    for path in books:
        try:
            with chess.polyglot.open_reader(path) as reader:
                return reader.weighted_choice(board, random=rng).move
        except IndexError:
            continue
    return None
