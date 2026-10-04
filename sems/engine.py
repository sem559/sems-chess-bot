from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import chess
import chess.engine

from .config import stockfish_path

MATE_CP = 100_000


@dataclass
class Candidate:
    move: chess.Move
    score_cp: int  # from the side to move's point of view; mates mapped to +/-(MATE_CP - plies)
    mate: int | None = None
    pv: list[chess.Move] = field(default_factory=list)
    depth: int = 0


def _candidate(info: chess.engine.InfoDict) -> Candidate | None:
    pv = info.get("pv") or []
    if not pv or "score" not in info:
        return None
    pov = info["score"].relative
    return Candidate(
        move=pv[0],
        score_cp=pov.score(mate_score=MATE_CP),
        mate=pov.mate(),
        pv=list(pv),
        depth=info.get("depth", 0),
    )


class EngineWrapper:
    """One long-lived Stockfish process. Only re-sends UCI options that actually changed."""

    def __init__(self, path: Path | str | None = None, threads: int = 1, hash_mb: int = 128):
        self._engine = chess.engine.SimpleEngine.popen_uci(str(path or stockfish_path()))
        self._options: dict[str, object] = {}
        self.configure({"Threads": threads, "Hash": hash_mb})

    @property
    def name(self) -> str:
        return self._engine.id.get("name", "Stockfish")

    def configure(self, options: dict[str, object]) -> None:
        changed = {k: v for k, v in options.items() if self._options.get(k) != v}
        if changed:
            self._engine.configure(changed)
            self._options.update(changed)

    def analyse(self, board: chess.Board, limit: chess.engine.Limit, multipv: int = 1) -> list[Candidate]:
        infos = self._engine.analyse(board, limit, multipv=multipv)
        if isinstance(infos, dict):
            infos = [infos]
        cands = [c for c in (_candidate(i) for i in infos) if c is not None]
        cands.sort(key=lambda c: c.score_cp, reverse=True)
        return cands

    def play(self, board: chess.Board, limit: chess.engine.Limit) -> tuple[chess.Move, Candidate | None]:
        result = self._engine.play(board, limit, info=chess.engine.INFO_SCORE | chess.engine.INFO_PV)
        if result.move is None:
            raise RuntimeError("Engine returned no move")
        return result.move, _candidate(result.info)

    def close(self) -> None:
        try:
            self._engine.quit()
        except chess.engine.EngineTerminatedError:
            pass

    def __enter__(self) -> "EngineWrapper":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
