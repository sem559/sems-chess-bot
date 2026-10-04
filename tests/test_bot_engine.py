"""Integration tests against the real Stockfish binary (skipped if it isn't installed)."""
import random
import time

import chess
import chess.engine
import pytest

from sems.bot import SemsBot
from sems.config import BotSettings, Style, stockfish_path
from sems.engine import EngineWrapper

try:
    stockfish_path()
except FileNotFoundError:
    pytest.skip("Stockfish not installed", allow_module_level=True)

FENS = [
    chess.STARTING_FEN,
    "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3",
    "8/8/8/4k3/8/8/4P3/4K3 w - - 0 1",
    "6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1",  # back-rank mate in 1: Rd8#
    "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1",
]


@pytest.fixture(scope="module")
def bot():
    with EngineWrapper() as engine:
        yield SemsBot(engine, rng=random.Random(7))


@pytest.mark.parametrize("fen", FENS)
@pytest.mark.parametrize(
    "settings",
    [
        BotSettings(movetime=0.1),
        BotSettings(elo=1500, movetime=0.1),
        BotSettings(elo=700, movetime=0.1),
        BotSettings(elo=2200, style=Style.AGGRESSIVE, movetime=0.1),
        BotSettings(style=Style.SOLID, movetime=0.1),
        BotSettings(elo=1000, style=Style.GAMBIT, movetime=0.1),
    ],
    ids=["max", "1500", "700", "2200-aggr", "max-solid", "1000-gambit"],
)
def test_choose_move_is_legal(bot, fen, settings):
    board = chess.Board(fen)
    decision = bot.choose_move(board, settings)
    assert decision.move in board.legal_moves


def test_full_strength_finds_mate_in_one(bot):
    board = chess.Board(FENS[3])
    for style in Style:
        decision = bot.choose_move(board, BotSettings(style=style, movetime=0.2))
        assert decision.move.uci() == "d1d8", style


def test_easy_move_returns_early(bot):
    # Black's queen hangs to the c3 pawn: cxd4 is far better than anything else.
    board = chess.Board("rnb1kbnr/pppp1ppp/8/4p3/3qP3/2P5/PP1P1PPP/RNBQKBNR w KQkq - 0 3")
    start = time.perf_counter()
    decision = bot.choose_move(board, BotSettings(movetime=2.0))
    elapsed = time.perf_counter() - start
    assert decision.move.uci() == "c3d4" and decision.source == "easy"
    assert elapsed < 1.2  # probe is 25% of 2 s, plus overhead


def test_hard_move_uses_full_budget(bot):
    board = chess.Board()  # many near-equal moves: no early exit
    start = time.perf_counter()
    decision = bot.choose_move(board, BotSettings(movetime=0.8, use_polyglot=False))
    assert decision.source == "engine" and time.perf_counter() - start >= 0.7


def test_easy_move_skipped_when_elo_limited(bot):
    board = chess.Board("rnb1kbnr/pppp1ppp/8/4p3/3qP3/2P5/PP1P1PPP/RNBQKBNR w KQkq - 0 3")
    assert bot.choose_move(board, BotSettings(elo=1500, movetime=0.3)).source == "engine"


def test_clock_limit_search(bot):
    clock = chess.engine.Limit(white_clock=5, black_clock=5, white_inc=0, black_inc=0)
    board = chess.Board(FENS[1])
    decision = bot.choose_move(board, BotSettings(), clock=clock)
    assert decision.move in board.legal_moves


def test_repertoire_used_first(bot):
    decision = bot.choose_move(chess.Board(), BotSettings(opening="london", movetime=0.1))
    assert decision.source == "repertoire" and decision.move.uci() == "d2d4"


def test_analyse_returns_lines(bot):
    lines = bot.analyse(chess.Board(), seconds=0.2, lines=3)
    assert len(lines) == 3 and all(l.pv for l in lines)
