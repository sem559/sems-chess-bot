"""The web app must only be usable from this computer, by this app's own page."""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from sems.web import app as app_module
from sems.web.app import MAX_SESSIONS, app

LOCAL = "http://127.0.0.1:8000"
client = TestClient(app, base_url=LOCAL)


def test_rejects_foreign_host_header():
    # DNS rebinding: evil.example resolves to 127.0.0.1 but the Host header gives it away.
    assert TestClient(app, base_url="http://evil.example:8000").get("/").status_code == 400
    assert client.get("/").status_code == 200


@pytest.mark.parametrize("origin", ["http://evil.example", "http://evil.example:8000", "null"])
def test_rejects_websocket_from_other_websites(origin):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("ws://127.0.0.1:8000/ws/game", headers={"origin": origin}) as ws:
            ws.receive_json()
    assert exc.value.code == 1008


def test_accepts_websocket_from_own_page():
    with client.websocket_connect("ws://127.0.0.1:8000/ws/game", headers={"origin": LOCAL}) as ws:
        assert ws.receive_json()["type"] == "state"


def test_session_cap():
    assert app_module._active_sessions == 0
    sockets = [client.websocket_connect("ws://127.0.0.1:8000/ws/game") for _ in range(MAX_SESSIONS)]
    try:
        for s in sockets:
            s.__enter__().receive_json()
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("ws://127.0.0.1:8000/ws/game") as extra:
                extra.receive_json()
        assert exc.value.code == 1013
    finally:
        for s in sockets:
            s.__exit__(None, None, None)
    assert app_module._active_sessions == 0


def test_client_cannot_set_engine_resources():
    with client.websocket_connect("ws://127.0.0.1:8000/ws/game") as ws:
        ws.receive_json()
        ws.send_json({"type": "settings", "settings": {"threads": 64, "hash_mb": 8192, "elo": 1500}})
        state = ws.receive_json()
        assert state["settings"]["threads"] == 1 and state["settings"]["hash_mb"] == 128
        assert state["settings"]["elo"] == 1500
        ws.send_json({"type": "settings", "settings": "nope"})
        assert ws.receive_json()["type"] == "error"
