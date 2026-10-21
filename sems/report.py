"""Post-game report: eval graph, per-move accuracy and ?!/?/?? marks.

Uses Lichess's published formulas: win% from centipawns, move accuracy from the drop in
win%, and mistake thresholds of 10/20/30 points of "winning chances" (5/10/15 win%).
"""
from __future__ import annotations

import math
from typing import Callable

import chess

MATE_EVAL_CP = 10_000  # mates count as this many centipawns for win% maths

# Drop in the mover's win% (0-100) that earns each mark.
INACCURACY, MISTAKE, BLUNDER = 5.0, 10.0, 15.0
SYMBOLS = {"inaccuracy": "?!", "mistake": "?", "blunder": "??"}


def win_pct(cp: float) -> float:
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def move_accuracy(win_drop: float) -> float:
    return max(0.0, min(100.0, 103.1668 * math.exp(-0.04354 * win_drop) - 3.1669))


def eval_cp(ev: dict) -> int:
    """White-POV eval dict {"cp", "mate"} -> centipawns for the maths."""
    if ev.get("mate") is not None:
        return MATE_EVAL_CP if ev["mate"] > 0 else -MATE_EVAL_CP
    return int(ev.get("cp") or 0)


def classify(win_drop: float) -> str | None:
    if win_drop >= BLUNDER:
        return "blunder"
    if win_drop >= MISTAKE:
        return "mistake"
    if win_drop >= INACCURACY:
        return "inaccuracy"
    return None


def terminal_eval(board: chess.Board) -> dict | None:
    """Exact eval for a finished position, None if the game isn't over."""
    outcome = board.outcome()  # no claimable draws: mid-game repetitions still get analysed
    if outcome is None:
        return None
    if outcome.winner is None:
        return {"cp": 0, "mate": None}
    return {"cp": MATE_EVAL_CP if outcome.winner == chess.WHITE else -MATE_EVAL_CP, "mate": None}


def build_report(moves: list[chess.Move], evals: list[dict], best: list[chess.Move | None]) -> dict:
    """`evals[i]` is the White-POV eval of the position before ply i (len(moves) + 1 entries,
    the last one for the final position); `best[i]` is the engine's best move there."""
    if len(evals) != len(moves) + 1 or len(best) < len(moves):
        raise ValueError("need one eval per position and one best move per ply")
    board = chess.Board()
    plies = []
    totals = {c: {"accuracy": [], "inaccuracy": 0, "mistake": 0, "blunder": 0} for c in ("white", "black")}
    for i, move in enumerate(moves):
        color = "white" if board.turn == chess.WHITE else "black"
        sign = 1 if board.turn == chess.WHITE else -1
        before = win_pct(sign * eval_cp(evals[i]))
        after = win_pct(sign * eval_cp(evals[i + 1]))
        drop = max(0.0, before - after)
        mark = None if move == best[i] else classify(drop)
        acc = 100.0 if move == best[i] else move_accuracy(drop)
        best_san = board.san(best[i]) if best[i] is not None and best[i] in board.legal_moves else None
        san = board.san(move)
        board.push(move)
        totals[color]["accuracy"].append(acc)
        if mark:
            totals[color][mark] += 1
        plies.append({
            "ply": i + 1, "san": san, "color": color, "fen": board.fen(),
            "eval": evals[i + 1], "white_pct": round(win_pct(eval_cp(evals[i + 1])), 1),
            "best_san": best_san, "mark": mark, "symbol": SYMBOLS.get(mark or "", ""),
            "accuracy": round(acc, 1),
        })
    summary = {}
    for color, t in totals.items():
        accs = t.pop("accuracy")
        summary[color] = {**t, "accuracy": round(sum(accs) / len(accs), 1) if accs else None}
    return {"plies": plies, "summary": summary, "start_pct": round(win_pct(eval_cp(evals[0])), 1)}


def analyse_game(moves: list[chess.Move], evaluate: Callable[[chess.Board], tuple[dict, chess.Move | None]]) -> dict:
    """Synchronous helper: `evaluate(board)` returns (white-POV eval, best move)."""
    board = chess.Board()
    evals, best = [], []
    for move in [*moves, None]:
        ev, mv = position_eval(board, evaluate)
        evals.append(ev)
        best.append(mv)
        if move is not None:
            board.push(move)
    return build_report(moves, evals, best)


def position_eval(board: chess.Board, evaluate) -> tuple[dict, chess.Move | None]:
    terminal = terminal_eval(board)
    if terminal is not None:
        return terminal, None
    return evaluate(board)
