"""FastAPI app: serves the board UI and runs one bot/engine per WebSocket game session."""
from __future__ import annotations

import mimetypes
import random
from pathlib import Path

import chess
import chess.pgn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from ..bot import MoveDecision, SemsBot
from ..config import MAX_ELO, MIN_ELO, BotSettings, Style
from ..engine import EngineWrapper
from ..openings import OpeningBook
from ..personalities import Chatter, Personality, load_personalities
from ..report import build_report, position_eval
from ..strength import SF_MIN_ELO

STATIC = Path(__file__).resolve().parent / "static"
# Some Windows registries map .js to text/plain, which browsers refuse for ES modules.
mimetypes.add_type("text/javascript", ".js")
BOOK = OpeningBook.load()
PERSONALITIES = load_personalities()
REPORT_DEPTH, REPORT_SECONDS = 12, 0.2  # per position: fast enough for a ~10 s report

app = FastAPI(title="Sem's Chess Bot")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/meta")
def meta() -> dict:
    return {
        "styles": [s.value for s in Style],
        "openings": [o.to_dict() for o in BOOK.openings.values()],
        "personalities": [p.to_dict() for p in PERSONALITIES.values()],
        "elo": {"min": MIN_ELO, "max": MAX_ELO, "native_min": SF_MIN_ELO},
    }


def _color(name: str) -> chess.Color:
    return chess.WHITE if name == "white" else chess.BLACK


class GameSession:
    def __init__(self) -> None:
        self.board = chess.Board()
        self.settings = BotSettings()
        self.human = chess.WHITE
        self.resigned: chess.Color | None = None
        self.eval: dict | None = None
        self.bot_source: str | None = None
        self.personality: Personality | None = None
        self.chatter: Chatter | None = None
        self.ended_chat = False
        self.report: dict | None = None
        self._bot: SemsBot | None = None

    async def bot(self) -> SemsBot:
        if self._bot is None:
            engine = await run_in_threadpool(EngineWrapper)
            self._bot = SemsBot(engine, BOOK)
        return self._bot

    def close(self) -> None:
        if self._bot is not None:
            self._bot.engine.close()

    def result(self) -> dict | None:
        if self.resigned is not None:
            winner = "0-1" if self.resigned == chess.WHITE else "1-0"
            who = "You" if self.resigned == self.human else "Bot"
            return {"result": winner, "reason": f"{who} resigned"}
        outcome = self.board.outcome(claim_draw=True)
        if outcome is None:
            return None
        return {"result": outcome.result(), "reason": outcome.termination.name.replace("_", " ").lower()}

    def bot_name(self) -> str:
        if self.personality:
            return self.personality.name
        return f"Sem's Bot ({self.settings.elo or 'max'}, {self.settings.style.value})"

    def pgn(self) -> str:
        game = chess.pgn.Game.from_board(self.board)
        game.headers["Event"] = "Casual game"
        game.headers["White"] = "You" if self.human == chess.WHITE else self.bot_name()
        game.headers["Black"] = self.bot_name() if self.human == chess.WHITE else "You"
        result = self.result()
        game.headers["Result"] = result["result"] if result else "*"
        return str(game)

    def state(self) -> dict:
        board = self.board
        over = self.result()
        dests: dict[str, list[str]] = {}
        if over is None and board.turn == self.human:
            for m in board.legal_moves:
                dests.setdefault(chess.square_name(m.from_square), []).append(chess.square_name(m.to_square))
        replay = chess.Board()
        san = []
        for m in board.move_stack:
            san.append(replay.san(m))
            replay.push(m)
        last = board.move_stack[-1] if board.move_stack else None
        return {
            "type": "state",
            "fen": board.fen(),
            "turn": "white" if board.turn else "black",
            "human": "white" if self.human else "black",
            "moves": [m.uci() for m in board.move_stack],
            "san": san,
            "dests": dests,
            "last_move": [chess.square_name(last.from_square), chess.square_name(last.to_square)] if last else None,
            "check": board.is_check(),
            "result": over,
            "eval": self.eval,
            "bot_source": self.bot_source,
            "settings": self.settings.model_dump(mode="json"),
            "personality": self.personality.to_dict() if self.personality else None,
            "pgn": self.pgn(),
        }


async def _say(session: GameSession, ws: WebSocket, text: str | None) -> None:
    if text and session.personality:
        p = session.personality
        await ws.send_json({"type": "chat", "name": p.name, "avatar": p.avatar, "text": text})


async def _maybe_game_over_chat(session: GameSession, ws: WebSocket) -> None:
    result = session.result()
    if result and session.chatter and not session.ended_chat:
        session.ended_chat = True
        await _say(session, ws, session.chatter.game_over(result["result"], not session.human))


async def _bot_turn(session: GameSession, ws: WebSocket) -> None:
    if session.result() is not None or session.board.turn == session.human:
        return
    await ws.send_json({"type": "thinking"})
    bot = await session.bot()
    before = session.board.copy()
    decision: MoveDecision = await run_in_threadpool(bot.choose_move, before.copy(), session.settings)
    session.eval = decision.white_pov(session.board) or session.eval
    session.bot_source = decision.source
    if decision.source == "repertoire" and session.settings.opening:
        session.bot_source = f"repertoire: {BOOK.get(session.settings.opening).name}"
    session.board.push(decision.move)
    session.report = None
    await ws.send_json(session.state())
    if session.chatter:
        await _say(session, ws, session.chatter.after_bot_move(before, decision.move, decision.score_cp))
    await _maybe_game_over_chat(session, ws)


async def _evaluate(bot: SemsBot, board: chess.Board) -> tuple[dict, chess.Move | None]:
    def run(b: chess.Board):
        cand = bot.evaluate(b, REPORT_DEPTH, REPORT_SECONDS)
        if cand is None:
            return {"cp": 0, "mate": None}, None
        sign = 1 if b.turn == chess.WHITE else -1
        if cand.mate is not None:
            return {"cp": None, "mate": sign * cand.mate}, cand.move
        return {"cp": sign * cand.score_cp, "mate": None}, cand.move

    return await run_in_threadpool(position_eval, board, run)


async def _report(session: GameSession, ws: WebSocket) -> None:
    if session.report is None:
        bot = await session.bot()
        moves = list(session.board.move_stack)
        board, evals, best = chess.Board(), [], []
        total = len(moves) + 1
        for i, move in enumerate([*moves, None]):
            ev, mv = await _evaluate(bot, board.copy())
            evals.append(ev)
            best.append(mv)
            if i % 4 == 0:
                await ws.send_json({"type": "report_progress", "done": i + 1, "total": total})
            if move is not None:
                board.push(move)
        session.report = build_report(moves, evals, best)
    await ws.send_json({"type": "report", "human": "white" if session.human else "black", **session.report})


async def _handle(session: GameSession, ws: WebSocket, msg: dict) -> None:
    kind = msg.get("type")
    if kind == "new_game":
        color = msg.get("color", "white")
        if color == "random":
            color = random.choice(["white", "black"])
        session.human = _color(color)
        base = BotSettings(**msg.get("settings", {}))
        session.personality = PERSONALITIES.get(msg.get("personality") or "")
        if session.personality:
            session.settings = session.personality.settings(not session.human, movetime=base.movetime)
            session.chatter = Chatter(session.personality)
        else:
            session.settings, session.chatter = base, None
        session.board = chess.Board()
        session.resigned, session.eval, session.bot_source, session.report = None, None, None, None
        session.ended_chat = False
        await ws.send_json(session.state())
        if session.chatter:
            await _say(session, ws, session.chatter.greet())
        await _bot_turn(session, ws)

    elif kind == "settings":
        new = BotSettings(**msg.get("settings", {}))
        if session.personality and msg.get("personality") == session.personality.id:
            session.settings = session.settings.model_copy(update={"movetime": new.movetime})
        else:  # switched to custom settings mid-game
            session.settings, session.personality, session.chatter = new, None, None
        await ws.send_json(session.state())

    elif kind == "move":
        if session.result() is not None or session.board.turn != session.human:
            raise ValueError("Not your turn")
        move = chess.Move.from_uci(msg["uci"])
        if move not in session.board.legal_moves:
            raise ValueError(f"Illegal move {msg['uci']}")
        session.board.push(move)
        session.report = None
        await ws.send_json(session.state())
        await _maybe_game_over_chat(session, ws)
        await _bot_turn(session, ws)

    elif kind == "undo":
        session.resigned, session.report, session.ended_chat = None, None, False
        board = session.board
        if board.move_stack:
            board.pop()
        while board.move_stack and board.turn != session.human:
            board.pop()
        await ws.send_json(session.state())
        await _bot_turn(session, ws)  # e.g. undoing to move 0 when the bot plays White

    elif kind == "resign":
        if session.result() is None:
            session.resigned = session.human
        await ws.send_json(session.state())
        await _maybe_game_over_chat(session, ws)

    elif kind == "report":
        await _report(session, ws)

    elif kind == "analyse":
        bot = await session.bot()
        board = session.board.copy()
        lines = await run_in_threadpool(bot.analyse, board, float(msg.get("seconds", 0.8)), 3)
        sign = 1 if board.turn == chess.WHITE else -1
        await ws.send_json({
            "type": "analysis",
            "fen": board.fen(),
            "lines": [{
                "san": board.variation_san(c.pv[:10]),
                "cp": sign * c.score_cp if c.mate is None else None,
                "mate": sign * c.mate if c.mate is not None else None,
                "depth": c.depth,
            } for c in lines],
        })

    else:
        raise ValueError(f"Unknown message type {kind!r}")


@app.websocket("/ws/game")
async def game_socket(ws: WebSocket) -> None:
    await ws.accept()
    session = GameSession()
    try:
        await ws.send_json(session.state())
        while True:
            msg = await ws.receive_json()
            try:
                await _handle(session, ws, msg)
            except (ValueError, KeyError, ValidationError) as exc:
                await ws.send_json({"type": "error", "message": str(exc)})
    except WebSocketDisconnect:
        pass
    finally:
        session.close()
