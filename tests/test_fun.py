"""Silly styles, personalities and the post-game report."""
import random

import chess
import pytest
from fastapi.testclient import TestClient

from sems.config import Style, stockfish_path
from sems.engine import Candidate
from sems.lichess.runner import FakeClient, load_config, persona_for, play_game
from sems.openings import OpeningBook
from sems.personalities import EVENTS, Chatter, load_personalities
from sems.report import build_report, classify, move_accuracy, win_pct
from sems.styles import _allows_mate_in_one, pick, style_override

PERSONALITIES = load_personalities()
BOOK = OpeningBook.load()


def _has_stockfish() -> bool:
    try:
        stockfish_path()
        return True
    except FileNotFoundError:
        return False


needs_engine = pytest.mark.skipif(not _has_stockfish(), reason="Stockfish not installed")


def C(uci, score=0, **kw):
    return Candidate(chess.Move.from_uci(uci), score, **kw)


# ---------- silly styles ----------

def test_bongcloud_plays_e4_then_ke2():
    board = chess.Board()
    assert style_override(board, Style.BONGCLOUD).uci() == "e2e4"
    board.push_uci("e2e4"); board.push_uci("e7e5")
    assert style_override(board, Style.BONGCLOUD).uci() == "e1e2"
    board = chess.Board(); board.push_uci("e2e4")
    assert style_override(board, Style.BONGCLOUD).uci() == "e7e5"
    assert style_override(chess.Board(), Style.BALANCED) is None


def test_mate_in_one_guard():
    # Fool's mate setup: after 1.f3 e5, 2.g4?? allows Qh4#.
    board = chess.Board()
    board.push_uci("f2f3"); board.push_uci("e7e5")
    assert _allows_mate_in_one(board, chess.Move.from_uci("g2g4"))
    assert not _allows_mate_in_one(board, chess.Move.from_uci("g1h3"))


def test_never_castles_skips_castling_even_when_best():
    board = chess.Board("r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4")
    cands = [C("e1g1", 40), C("d2d3", 35), C("b1c3", 30)]
    assert pick(board, cands, Style.NEVER_CASTLES, 0).move.uci() == "d2d3"
    assert pick(board, [C("e1g1", 40)], Style.NEVER_CASTLES, 0).move.uci() == "e1g1"  # only option


def test_knight_rider_prefers_knights():
    board = chess.Board()
    cands = [C("e2e4", 30), C("g1f3", 20), C("d2d4", 28)]
    assert pick(board, cands, Style.KNIGHT_RIDER, 70).move.uci() == "g1f3"


def test_coward_prefers_trades():
    # Open d-file: White can trade queens on d8 or play a quiet move.
    board = chess.Board("rnbqkbnr/ppp2ppp/8/4p3/4P3/8/PPP2PPP/RNBQKBNR w KQkq - 0 4")
    cands = [C("g1f3", 30), C("d1d8", 0)]
    assert pick(board, cands, Style.COWARD, 50).move.uci() == "d1d8"


def test_pawn_storm_prefers_pawns():
    board = chess.Board()
    cands = [C("g1f3", 30), C("g2g4", 0)]
    assert pick(board, cands, Style.PAWN_STORM, 60).move.uci() == "g2g4"


@needs_engine
def test_bongcloud_bot_actually_plays_it():
    from sems.bot import SemsBot
    from sems.config import BotSettings
    from sems.engine import EngineWrapper

    with EngineWrapper() as engine:
        bot = SemsBot(engine, rng=random.Random(3))
        board = chess.Board()
        settings = BotSettings(style=Style.BONGCLOUD, elo=1200, movetime=0.05, use_polyglot=False)
        board.push(bot.choose_move(board, settings).move)
        board.push_uci("e7e5")
        decision = bot.choose_move(board, settings)
        assert decision.move.uci() == "e1e2" and decision.source == "style"


# ---------- personalities ----------

@pytest.mark.parametrize("p", PERSONALITIES.values(), ids=lambda p: p.id)
def test_personality_is_consistent(p):
    for color, opening in p.openings.items():
        assert opening in BOOK.openings, f"{p.id}: unknown opening {opening}"
        assert BOOK.get(opening).side == color, f"{p.id}: {opening} is not a {color} opening"
    for event in ("greeting", "win", "loss", "draw"):
        assert p.lines.get(event), f"{p.id} has no {event} line"
    assert set(p.lines) <= set(EVENTS)
    s = p.settings(chess.WHITE, movetime=0.5)
    assert s.style == p.style and s.elo == p.elo and s.movetime == 0.5


def test_never_castle_nelly_repertoire_never_castles():
    p = PERSONALITIES["never_castle_nelly"]
    for opening_id in p.openings.values():
        for line in BOOK.get(opening_id).lines:
            board = chess.Board()
            for uci in line:
                move = chess.Move.from_uci(uci)
                assert not board.is_castling(move), f"{opening_id} castles"
                board.push(move)


def test_chatter_reacts_to_blunder_and_game_end():
    chatter = Chatter(PERSONALITIES["gambit_gary"], random.Random(1))
    assert chatter.greet()
    board = chess.Board()
    assert chatter.after_bot_move(board, chess.Move.from_uci("e2e4"), 20) is None  # quiet move, cooldown ok but no event
    line = chatter.after_bot_move(board, chess.Move.from_uci("d2d4"), 400)  # eval jumped +380
    assert line in PERSONALITIES["gambit_gary"].lines["blunder"]
    assert chatter.game_over("1-0", chess.WHITE) in PERSONALITIES["gambit_gary"].lines["win"]
    assert chatter.game_over("1-0", chess.BLACK) in PERSONALITIES["gambit_gary"].lines["loss"]
    assert chatter.game_over("1/2-1/2", chess.BLACK) in PERSONALITIES["gambit_gary"].lines["draw"]


def test_lichess_persona_uses_personality_and_chats():
    cfg = load_config()
    cfg = {**cfg, "personas": {"default": {"personality": "the_wall"}}}
    s = persona_for("blitz", chess.BLACK, cfg)
    assert s.elo == 2200 and s.style == Style.SOLID and s.opening == "caro_kann"


@needs_engine
def test_lichess_game_posts_greeting_and_goodbye():
    from sems.bot import SemsBot
    from sems.engine import EngineWrapper

    cfg = {**load_config(), "personas": {"default": {"personality": "fish_prime"}}}
    client = FakeClient(chess.WHITE, seed=2)
    with EngineWrapper() as engine:
        status = play_game(client, "g", "sems-bot", SemsBot(engine), cfg, max_think=0.02)
    lines = PERSONALITIES["fish_prime"].lines
    assert client.bots.chat[0] in lines["greeting"]
    if status == "mate":
        assert client.bots.chat[-1] in lines["win"]


# ---------- report ----------

def test_win_pct_and_accuracy_formulas():
    assert win_pct(0) == pytest.approx(50)
    assert win_pct(1000) > 95 and win_pct(-1000) < 5
    assert move_accuracy(0) == pytest.approx(100, abs=0.01)
    assert move_accuracy(50) < 15
    assert classify(4) is None and classify(6) == "inaccuracy" and classify(12) == "mistake" and classify(20) == "blunder"


def test_build_report_marks_blunder():
    moves = [chess.Move.from_uci(u) for u in ["e2e4", "e7e5", "d1h5", "g8f6"]]
    evals = [{"cp": 20, "mate": None}, {"cp": 30, "mate": None}, {"cp": 30, "mate": None},
             {"cp": 0, "mate": None}, {"cp": 600, "mate": None}]  # ...Nf6?? lets Qxf7 / Qxe5+
    best = [chess.Move.from_uci(u) for u in ["e2e4", "e7e5", "g1f3", "b8c6"]]
    report = build_report(moves, evals, best)
    plies = report["plies"]
    assert plies[0]["mark"] is None and plies[0]["accuracy"] == 100
    assert plies[3]["mark"] == "blunder" and plies[3]["symbol"] == "??" and plies[3]["best_san"] == "Nc6"
    assert report["summary"]["black"]["blunder"] == 1 and report["summary"]["white"]["blunder"] == 0
    assert 0 <= report["summary"]["black"]["accuracy"] < report["summary"]["white"]["accuracy"] <= 100


def test_build_report_validates_lengths():
    with pytest.raises(ValueError):
        build_report([chess.Move.from_uci("e2e4")], [{"cp": 0, "mate": None}], [None])


# ---------- web: personality game + report ----------

def _recv(ws, kind):
    while True:
        msg = ws.receive_json()
        if msg["type"] == kind:
            return msg


@needs_engine
def test_web_personality_game_and_report():
    from sems.web.app import app

    client = TestClient(app, base_url="http://127.0.0.1:8000")
    assert any(p["id"] == "bongcloud_bob" for p in client.get("/api/meta").json()["personalities"])
    with client.websocket_connect("ws://127.0.0.1:8000/ws/game") as ws:
        _recv(ws, "state")
        ws.send_json({"type": "new_game", "color": "black", "personality": "bongcloud_bob", "settings": {"movetime": 0.1}})
        state = _recv(ws, "state")
        assert state["personality"]["id"] == "bongcloud_bob" and state["settings"]["style"] == "bongcloud"
        chat = _recv(ws, "chat")
        assert chat["name"] == "Bongcloud Bob" and chat["text"]
        state = _recv(ws, "state")
        assert state["moves"] == ["e2e4"]
        ws.send_json({"type": "move", "uci": "e7e5"})
        _recv(ws, "state")
        state = _recv(ws, "state")
        assert state["moves"][-1] == "e1e2"  # the Bongcloud

        ws.send_json({"type": "resign"})
        state = _recv(ws, "state")
        assert state["result"]["result"] == "1-0"
        assert _recv(ws, "chat")["text"] in PERSONALITIES["bongcloud_bob"].lines["win"]

        ws.send_json({"type": "report"})
        report = _recv(ws, "report")
        assert len(report["plies"]) == 3 and report["human"] == "black"
        assert {"accuracy", "blunder"} <= set(report["summary"]["white"])
