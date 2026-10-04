"""The move pipeline: repertoire -> Polyglot book -> Stockfish -> style/strength selection."""
from __future__ import annotations

import random
from dataclasses import dataclass, field

import chess
import chess.engine

from .config import BotSettings, polyglot_books
from .engine import Candidate, EngineWrapper
from .openings import OpeningBook, polyglot_move
from .strength import StrengthPlan, plan_for
from .styles import pick

# Easy-move detection for fixed think time: probe with this share of the budget, and stop
# early if the best move beats the second best by at least this many centipawns.
EASY_PROBE_FRACTION = 0.25
EASY_GAP_CP = 150
MIN_PROBE_MOVETIME = 0.2  # below this the probe overhead isn't worth it


@dataclass
class MoveDecision:
    move: chess.Move
    source: str  # "repertoire" | "polyglot" | "engine" | "forced"
    score_cp: int | None = None  # side to move's POV
    mate: int | None = None
    pv: list[chess.Move] = field(default_factory=list)

    def white_pov(self, board: chess.Board) -> dict | None:
        """Eval for the position before the move, from White's POV (for eval bars)."""
        if self.score_cp is None:
            return None
        sign = 1 if board.turn == chess.WHITE else -1
        if self.mate is not None:
            return {"cp": None, "mate": sign * self.mate}
        return {"cp": sign * self.score_cp, "mate": None}


class SemsBot:
    def __init__(self, engine: EngineWrapper, book: OpeningBook | None = None, rng: random.Random | None = None):
        self.engine = engine
        self.book = book or OpeningBook.load()
        self.rng = rng or random.Random()
        self._polyglot = polyglot_books()

    def choose_move(
        self,
        board: chess.Board,
        settings: BotSettings,
        time_budget: float | None = None,
        clock: chess.engine.Limit | None = None,
    ) -> MoveDecision:
        """Pick a move. With `clock` (wtime/btime/inc), Stockfish's own time manager decides how
        long to think; otherwise think for `time_budget` (or settings.movetime) seconds, cut short
        when the easy-move probe finds one clearly best move."""
        legal = list(board.legal_moves)
        if not legal:
            raise ValueError("no legal moves: game is over")

        if settings.opening:
            move = self.book.next_move(board, settings.opening, self.rng)
            if move is not None:
                return MoveDecision(move, "repertoire")
        if settings.use_polyglot and self._polyglot:
            move = polyglot_move(board, self._polyglot, self.rng)
            if move is not None and move in board.legal_moves:
                return MoveDecision(move, "polyglot")

        if len(legal) == 1:
            return MoveDecision(legal[0], "forced")

        plan = plan_for(settings.elo, settings.style)
        self.engine.configure({"Threads": settings.threads, "Hash": settings.hash_mb, **plan.uci_options})
        if clock is not None:
            limit = clock
        else:
            movetime = max(0.05, (time_budget or settings.movetime) * plan.time_scale)
            limit = chess.engine.Limit(time=movetime)
            # Easy-move check only at full strength: weakened play must go through the limiter.
            if settings.elo is None and movetime >= MIN_PROBE_MOVETIME:
                easy = self._easy_move(board, plan, movetime * EASY_PROBE_FRACTION)
                if easy is not None:
                    return easy
                limit = chess.engine.Limit(time=movetime * (1 - EASY_PROBE_FRACTION))  # hash is warm

        if plan.native:
            move, cand = self.engine.play(board, limit)
            return self._decision(move, cand)

        cands = self.engine.analyse(board, limit, multipv=min(plan.multipv, len(legal)))
        if not cands:
            move, cand = self.engine.play(board, limit)
            return self._decision(move, cand)
        chosen = pick(board, cands, settings.style, plan.window_cp, plan.temperature_cp, self.rng)
        return self._decision(chosen.move, chosen)

    def _easy_move(self, board: chess.Board, plan: StrengthPlan, probe_time: float) -> MoveDecision | None:
        """Short multi-line search; if one move is far ahead of the rest, play it now."""
        cands = self.engine.analyse(board, chess.engine.Limit(time=probe_time), multipv=max(2, plan.multipv))
        if len(cands) < 2:
            return None
        best, second = cands[0], cands[1]
        if best.score_cp - second.score_cp < max(EASY_GAP_CP, plan.window_cp):
            return None
        return MoveDecision(best.move, "easy", best.score_cp, best.mate, best.pv)

    def analyse(self, board: chess.Board, seconds: float = 0.5, lines: int = 3) -> list[Candidate]:
        """Full-strength analysis for the GUI's analysis mode."""
        n = min(lines, board.legal_moves.count())
        if n == 0:
            return []
        self.engine.configure({"UCI_LimitStrength": False, "Skill Level": 20})
        return self.engine.analyse(board, chess.engine.Limit(time=seconds), multipv=n)

    @staticmethod
    def _decision(move: chess.Move, cand: Candidate | None) -> MoveDecision:
        if cand is None:
            return MoveDecision(move, "engine")
        return MoveDecision(move, "engine", cand.score_cp, cand.mate, cand.pv)
