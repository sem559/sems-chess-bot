"""Engine-free unit tests: openings data, ELO mapping, style picking."""
import random

import chess
import pytest

from sems.config import Style
from sems.engine import MATE_CP, Candidate
from sems.openings import OpeningBook
from sems.strength import SF_MIN_ELO, plan_for
from sems.styles import pick, style_bonus

BOOK = OpeningBook.load()


@pytest.mark.parametrize("opening", BOOK.openings.values(), ids=lambda o: o.id)
def test_every_repertoire_line_is_legal(opening):
    for line in opening.lines:
        board = chess.Board()
        for uci in line:
            move = chess.Move.from_uci(uci)
            assert move in board.legal_moves, f"{opening.id}: illegal {uci} after {board.move_stack}"
            board.push(move)
    assert opening.side in ("white", "black")


def test_opening_follows_line_then_hands_off():
    board = chess.Board()
    assert BOOK.next_move(board, "italian") == chess.Move.from_uci("e2e4")
    for uci in ["e2e4", "e7e5", "g1f3", "b8c6"]:
        board.push_uci(uci)
    assert BOOK.next_move(board, "italian") == chess.Move.from_uci("f1c4")
    board = chess.Board()
    board.push_uci("e2e4")
    board.push_uci("c7c5")  # opponent leaves the Italian
    assert BOOK.next_move(board, "italian") is None
    assert BOOK.next_move(chess.Board(), "no_such_opening") is None


def test_black_repertoire_answers_its_line():
    board = chess.Board()
    board.push_uci("e2e4")
    assert BOOK.next_move(board, "najdorf") == chess.Move.from_uci("c7c5")
    board = chess.Board()
    board.push_uci("d2d4")
    assert BOOK.next_move(board, "najdorf") is None


def test_elo_mapping_edges():
    full = plan_for(None)
    assert full.native and full.uci_options["UCI_LimitStrength"] is False

    at_floor = plan_for(SF_MIN_ELO)
    assert at_floor.native and at_floor.uci_options == {"UCI_LimitStrength": True, "UCI_Elo": SF_MIN_ELO}
    assert plan_for(3190).uci_options["UCI_Elo"] == 3190

    below = plan_for(SF_MIN_ELO - 1)
    assert not below.native and below.multipv > 1 and below.temperature_cp > 0

    weak, strong = plan_for(600), plan_for(1300)
    assert weak.window_cp > strong.window_cp and weak.temperature_cp > strong.temperature_cp


def test_styled_full_strength_is_sampled_but_greedy():
    plan = plan_for(None, Style.AGGRESSIVE)
    assert not plan.native and plan.temperature_cp == 0 and 0 < plan.window_cp <= 50


def _cands(board, scored):
    return [Candidate(chess.Move.from_uci(u), s, pv=[chess.Move.from_uci(u)]) for u, s in scored]


def test_pick_respects_window():
    board = chess.Board()
    cands = _cands(board, [("e2e4", 30), ("d2d4", 25), ("g2g4", -150), ("f2f3", -200)])
    rng = random.Random(1)
    for style in Style:
        for _ in range(50):
            chosen = pick(board, cands, style, window_cp=20, temperature_cp=40, rng=rng)
            assert chosen.move.uci() in ("e2e4", "d2d4")


def test_pick_never_drops_forced_mate():
    board = chess.Board()
    cands = [
        Candidate(chess.Move.from_uci("e2e4"), MATE_CP - 3, mate=3),
        Candidate(chess.Move.from_uci("d2d4"), MATE_CP - 900, mate=None),
    ]
    assert pick(board, cands, Style.GAMBIT, window_cp=10_000, temperature_cp=500).mate == 3


def test_aggressive_prefers_check_solid_prefers_castling():
    # White can give a check with Bb5+ or castle.
    board = chess.Board("rnbqk1nr/ppp2ppp/3p4/2b1p3/4P3/5N2/PPPPBPPP/RNBQK2R w KQkq - 0 4")
    check = Candidate(chess.Move.from_uci("e2b5"), 20)
    castle = Candidate(chess.Move.from_uci("e1g1"), 20)
    assert style_bonus(board, check, Style.AGGRESSIVE) > style_bonus(board, castle, Style.AGGRESSIVE)
    assert style_bonus(board, castle, Style.SOLID) > style_bonus(board, check, Style.SOLID)
    assert pick(board, [castle, check], Style.AGGRESSIVE, 10).move.uci() == "e2b5"
    assert pick(board, [check, castle], Style.SOLID, 10).move.uci() == "e1g1"
