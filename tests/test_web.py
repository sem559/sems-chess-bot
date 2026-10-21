import pytest
from fastapi.testclient import TestClient

from sems.config import stockfish_path
from sems.web.app import app

client = TestClient(app, base_url="http://127.0.0.1:8000")


def test_meta():
    data = client.get("/api/meta").json()
    assert "aggressive" in data["styles"]
    assert any(o["id"] == "najdorf" and o["side"] == "black" for o in data["openings"])


def test_index_served():
    html = client.get("/").text
    assert "Sem's Chess Bot" in html
    assert "cdn.jsdelivr" not in html  # everything is served locally


def test_bundled_board_assets_served():
    js = client.get("/static/vendor/chessground/chessground.min.js")
    assert js.status_code == 200 and "Chessground" in js.text
    assert "javascript" in js.headers["content-type"]  # ES modules need a JS MIME type
    assert client.get("/static/vendor/chessground/chessground.cburnett.css").status_code == 200


def _recv_state(ws):
    while True:
        msg = ws.receive_json()
        if msg["type"] == "state":
            return msg


def test_game_flow():
    try:
        stockfish_path()
    except FileNotFoundError:
        pytest.skip("Stockfish not installed")
    with client.websocket_connect("ws://127.0.0.1:8000/ws/game") as ws:
        _recv_state(ws)
        ws.send_json({"type": "new_game", "color": "white", "settings": {"elo": 1500, "movetime": 0.1}})
        state = _recv_state(ws)
        assert state["human"] == "white" and "e2" in state["dests"]

        ws.send_json({"type": "move", "uci": "e2e4"})
        _recv_state(ws)  # after our move
        state = _recv_state(ws)  # after bot reply
        assert len(state["moves"]) == 2 and state["turn"] == "white"

        ws.send_json({"type": "move", "uci": "a1a8"})
        assert ws.receive_json()["type"] == "error"

        ws.send_json({"type": "undo"})
        state = _recv_state(ws)
        assert state["moves"] == []

        ws.send_json({"type": "analyse", "seconds": 0.1})
        msg = ws.receive_json()
        assert msg["type"] == "analysis" and len(msg["lines"]) == 3

        ws.send_json({"type": "resign"})
        state = _recv_state(ws)
        assert state["result"]["result"] == "0-1"


def test_bot_opens_when_playing_white_with_repertoire():
    try:
        stockfish_path()
    except FileNotFoundError:
        pytest.skip("Stockfish not installed")
    with client.websocket_connect("ws://127.0.0.1:8000/ws/game") as ws:
        _recv_state(ws)
        ws.send_json({"type": "new_game", "color": "black", "settings": {"opening": "london", "movetime": 0.1}})
        _recv_state(ws)
        state = _recv_state(ws)
        assert state["moves"] == ["d2d4"] and state["bot_source"].startswith("repertoire")
