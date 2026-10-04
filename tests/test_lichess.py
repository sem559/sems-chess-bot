from datetime import timedelta

import chess
import pytest

from sems.config import Style, stockfish_path
from sems.lichess.runner import clock_limit, dry_run, load_config, persona_for, should_accept, time_budget

CFG = load_config()


def _challenge(**kw):
    base = {"id": "abc", "variant": {"key": "standard"}, "speed": "blitz", "rated": False,
            "challenger": {"id": "someone", "name": "Someone", "title": None}}
    base.update(kw)
    return base


def test_accepts_standard_blitz():
    assert should_accept(_challenge(), CFG, 0) == (True, "")


@pytest.mark.parametrize("kw, reason", [
    ({"variant": {"key": "chess960"}}, "variant"),
    ({"speed": "correspondence"}, "timeControl"),
])
def test_declines(kw, reason):
    assert should_accept(_challenge(**kw), CFG, 0) == (False, reason)


def test_declines_when_busy():
    assert should_accept(_challenge(), CFG, CFG["accept"]["max_games"]) == (False, "later")


def test_time_budget_handles_timedelta_and_ms():
    state_td = {"wtime": timedelta(seconds=180), "winc": timedelta(seconds=2)}
    state_ms = {"wtime": 180_000, "winc": 2_000}
    assert time_budget(state_td, chess.WHITE) == pytest.approx(time_budget(state_ms, chess.WHITE))
    assert 0.05 <= time_budget({"btime": 1_000, "binc": 0}, chess.BLACK) <= 0.2
    assert time_budget({}, chess.WHITE) == 1.0


def test_clock_limit_passes_real_clocks():
    limit = clock_limit({"wtime": timedelta(seconds=180), "btime": 175_000,
                         "winc": timedelta(seconds=2), "binc": 2_000})
    assert (limit.white_clock, limit.black_clock, limit.white_inc, limit.black_inc) == (180, 175, 2, 2)
    assert limit.time is None
    assert clock_limit({}) is None


def test_persona_drops_opening_for_wrong_color():
    cfg = {**CFG, "personas": {"default": {"elo": 2000, "style": "gambit", "opening": "najdorf"}}}
    assert persona_for("blitz", chess.BLACK, cfg).opening == "najdorf"
    s = persona_for("blitz", chess.WHITE, cfg)
    assert s.opening is None and s.style == Style.GAMBIT and s.elo == 2000


@pytest.mark.parametrize("color", [chess.WHITE, chess.BLACK])
def test_dry_run_plays_full_game(color):
    try:
        stockfish_path()
    except FileNotFoundError:
        pytest.skip("Stockfish not installed")
    status, board = dry_run(CFG, color, max_think=0.02)
    assert status in ("mate", "draw")
    if status == "mate":  # against a random mover, full-strength Stockfish should never be mated
        assert board.outcome().winner == color
