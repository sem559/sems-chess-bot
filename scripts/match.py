"""Measure strength honestly: play two bot configs against each other and report Elo + SPRT.

Examples:
    python scripts/match.py --a elo=1500 --b elo=2000 --games 100
    python scripts/match.py --a style=aggressive --b max --games 400 --movetime 0.1
    python scripts/match.py --a style=gambit --b max --sprt 0 5

A spec is "max" (plain full-strength Stockfish) or comma-separated BotSettings fields,
e.g. "elo=1800,style=solid,opening=italian". Each start position is played twice with
colors swapped, starting from the first 8 plies of every repertoire line.
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from pathlib import Path

import chess

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sems.bot import SemsBot  # noqa: E402
from sems.config import BotSettings  # noqa: E402
from sems.engine import EngineWrapper  # noqa: E402
from sems.openings import OpeningBook  # noqa: E402


def parse_spec(spec: str) -> BotSettings:
    if spec == "max":
        return BotSettings(use_polyglot=False)
    fields: dict[str, object] = {"use_polyglot": False}
    for part in spec.split(","):
        key, _, value = part.partition("=")
        fields[key.strip()] = None if value.strip() in ("none", "null", "max") else value.strip()
    return BotSettings(**fields)


def start_positions(plies: int = 8) -> list[list[str]]:
    seen, out = set(), []
    for opening in OpeningBook.load().openings.values():
        for line in opening.lines:
            key = line[:plies]
            if key not in seen:
                seen.add(key)
                out.append(list(key))
    return out


def play_one(white: SemsBot, ws: BotSettings, black: SemsBot, bs: BotSettings, opening: list[str],
             movetime: float, max_plies: int = 300) -> str:
    board = chess.Board()
    for uci in opening:
        board.push_uci(uci)
    while not board.is_game_over(claim_draw=True) and len(board.move_stack) < max_plies:
        bot, settings = (white, ws) if board.turn == chess.WHITE else (black, bs)
        board.push(bot.choose_move(board, settings, movetime).move)
    outcome = board.outcome(claim_draw=True)
    return outcome.result() if outcome else "1/2-1/2"


def elo_from_score(score: float) -> float:
    score = min(max(score, 1e-6), 1 - 1e-6)
    return -400 * math.log10(1 / score - 1)


def summary(w: int, d: int, l: int) -> tuple[float, float, float]:
    """Elo difference, 95% error bar, and per-game score variance (trinomial)."""
    n = w + d + l
    s = (w + d / 2) / n
    var = (w * (1 - s) ** 2 + d * (0.5 - s) ** 2 + l * s ** 2) / n
    se = math.sqrt(var / n) if var > 0 else 0.0
    lo, hi = elo_from_score(s - 1.96 * se), elo_from_score(s + 1.96 * se)
    return elo_from_score(s), (hi - lo) / 2, var


def llr(w: int, d: int, l: int, elo0: float, elo1: float) -> float:
    """Normal-approximation GSPRT log-likelihood ratio for H1: elo1 vs H0: elo0."""
    n = w + d + l
    if n == 0:
        return 0.0
    s = (w + d / 2) / n
    _, _, var = summary(w, d, l)
    if var == 0:
        return 0.0
    s0, s1 = (1 / (1 + 10 ** (-e / 400)) for e in (elo0, elo1))
    return n * (s1 - s0) * (2 * s - s0 - s1) / (2 * var)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--a", required=True, help="config under test")
    ap.add_argument("--b", default="max", help="baseline config")
    ap.add_argument("--games", type=int, default=100)
    ap.add_argument("--movetime", type=float, default=0.1)
    ap.add_argument("--sprt", nargs=2, type=float, metavar=("ELO0", "ELO1"), help="stop early on SPRT verdict")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252 (mangles ±)

    a_set, b_set = parse_spec(args.a), parse_spec(args.b)
    positions = start_positions()
    random.Random(args.seed).shuffle(positions)
    bounds = (math.log(0.05 / 0.95), math.log(0.95 / 0.05))
    w = d = l = 0

    with EngineWrapper() as ea, EngineWrapper() as eb:
        a, b = SemsBot(ea, rng=random.Random(args.seed)), SemsBot(eb, rng=random.Random(args.seed + 1))
        for i in range(args.games):
            opening = positions[(i // 2) % len(positions)]
            a_white = i % 2 == 0
            res = play_one(a, a_set, b, b_set, opening, args.movetime) if a_white \
                else play_one(b, b_set, a, a_set, opening, args.movetime)
            if res == "1/2-1/2":
                d += 1
            elif (res == "1-0") == a_white:
                w += 1
            else:
                l += 1
            elo, err, _ = summary(w, d, l)
            line = f"game {i + 1:4d}: {res:7s}  A: +{w} ={d} -{l}  Elo {elo:+7.1f} ± {err:5.1f}"
            if args.sprt:
                ratio = llr(w, d, l, *args.sprt)
                print(f"{line}  LLR {ratio:+.2f} [{bounds[0]:.2f}, {bounds[1]:.2f}]", flush=True)
                if ratio >= bounds[1]:
                    print(f"SPRT: H1 accepted (A is about {args.sprt[1]:g}+ Elo better)")
                    break
                if ratio <= bounds[0]:
                    print(f"SPRT: H0 accepted (A is not {args.sprt[1]:g} Elo better)")
                    break
            else:
                print(line, flush=True)

    elo, err, _ = summary(w, d, l)
    print(f"\nA = {args.a}\nB = {args.b}\nResult: +{w} ={d} -{l}  ->  A is {elo:+.1f} ± {err:.1f} Elo vs B (95% CI)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
