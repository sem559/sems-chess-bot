"""Lichess BOT runner: accepts matching challenges and plays them with SemsBot.

Only for an account you upgraded to a BOT yourself (see README). It never plays on a
human account and never assists anyone else's game.

    python -m sems.lichess            # connect with LICHESS_TOKEN from .env
    python -m sems.lichess --dry-run  # simulate one game locally, no network
"""
from __future__ import annotations

import argparse
import logging
import os
import queue
import random
import threading
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterator

import chess
import chess.engine
import yaml

from ..bot import SemsBot
from ..config import BotSettings
from ..engine import EngineWrapper
from ..openings import OpeningBook
from ..personalities import Chatter, load_personalities

log = logging.getLogger("sems.lichess")
CONFIG = Path(__file__).resolve().parent / "config.yaml"
BOOK = OpeningBook.load()
PERSONALITIES = load_personalities()


def load_config(path: Path = CONFIG) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def should_accept(challenge: dict, cfg: dict, active_games: int) -> tuple[bool, str]:
    """Return (accept, decline_reason). Reasons are Lichess's decline keys."""
    acc = cfg["accept"]
    if challenge.get("variant", {}).get("key") not in acc["variants"]:
        return False, "variant"
    if challenge.get("speed") not in acc["speeds"]:
        return False, "timeControl"
    mode = "rated" if challenge.get("rated") else "casual"
    if mode not in acc["modes"]:
        return False, mode
    is_bot = (challenge.get("challenger") or {}).get("title") == "BOT"
    if is_bot and not acc["allow_bots"]:
        return False, "noBot"
    if not is_bot and not acc["allow_humans"]:
        return False, "onlyBot"
    if active_games >= acc["max_games"]:
        return False, "later"
    return True, ""


def persona_for(speed: str | None, color: chess.Color, cfg: dict) -> BotSettings:
    engine = cfg.get("engine", {})
    data = {**cfg["personas"]["default"], **cfg["personas"].get(speed or "", {})}
    personality = PERSONALITIES.get(data.get("personality") or "")
    if personality:
        return personality.settings(color, threads=engine.get("threads", 1), hash_mb=engine.get("hash_mb", 128))
    data.pop("personality", None)
    opening = data.get("opening")
    if opening and opening in BOOK.openings and BOOK.get(opening).side != ("white" if color else "black"):
        data["opening"] = None
    return BotSettings(threads=engine.get("threads", 1), hash_mb=engine.get("hash_mb", 128), **data)


def personality_for(speed: str | None, cfg: dict):
    data = {**cfg["personas"]["default"], **cfg["personas"].get(speed or "", {})}
    return PERSONALITIES.get(data.get("personality") or "")


def _chat(client, game_id: str, text: str | None) -> None:
    if not text:
        return
    try:
        client.bots.post_message(game_id, text)
    except Exception:  # chat is cosmetic; never let it break a game
        log.warning("game %s: could not post chat", game_id, exc_info=True)


def _seconds(value: Any) -> float:
    if isinstance(value, timedelta):  # berserk converts gameState clocks to timedelta
        return value.total_seconds()
    return (value or 0) / 1000.0  # raw milliseconds (gameFull's nested state)


def time_budget(state: dict, color: chess.Color) -> float:
    """Seconds to spend on this move: ~1/30 of the clock plus most of the increment."""
    remaining = _seconds(state.get("wtime" if color else "btime"))
    inc = _seconds(state.get("winc" if color else "binc"))
    if remaining <= 0:
        return 1.0  # no clock information
    budget = remaining / 30 + inc * 0.8
    return max(0.05, min(budget, remaining * 0.2, 20.0))


def clock_limit(state: dict) -> chess.engine.Limit | None:
    """The real game clocks, so Stockfish's time manager can spend little on easy moves and
    more on hard ones. None when the game has no clock."""
    wtime, btime = _seconds(state.get("wtime")), _seconds(state.get("btime"))
    if wtime <= 0 and btime <= 0:
        return None
    return chess.engine.Limit(
        white_clock=wtime, black_clock=btime,
        white_inc=_seconds(state.get("winc")), black_inc=_seconds(state.get("binc")),
    )


def play_game(client, game_id: str, my_id: str, bot: SemsBot, cfg: dict, max_think: float | None = None) -> str:
    """Stream one game and answer every position where it's our turn. Returns the final status."""
    color = chess.WHITE
    settings = BotSettings()
    initial_fen = chess.STARTING_FEN
    status = "started"
    chatter: Chatter | None = None
    for event in client.bots.stream_game_state(game_id):
        kind = event.get("type")
        if kind == "gameFull":
            color = chess.WHITE if event["white"].get("id") == my_id else chess.BLACK
            if event.get("variant", {}).get("key", "standard") != "standard":
                log.warning("game %s: unsupported variant, resigning", game_id)
                client.bots.resign_game(game_id)
                return "unsupported"
            fen = event.get("initialFen", "startpos")
            initial_fen = chess.STARTING_FEN if fen == "startpos" else fen
            settings = persona_for(event.get("speed"), color, cfg)
            personality = personality_for(event.get("speed"), cfg)
            chatter = Chatter(personality) if personality else None
            log.info("game %s: playing as %s with %s", game_id, "white" if color else "black",
                     settings.model_dump(mode="json", exclude={"threads", "hash_mb", "use_polyglot", "movetime"}))
            state = event["state"]
            if chatter and len(state.get("moves", "").split()) < 2:  # not on reconnects mid-game
                _chat(client, game_id, chatter.greet())
        elif kind == "gameState":
            state = event
        else:
            continue  # chatLine, opponentGone, ...

        status = state.get("status", "started")
        if status != "started":
            log.info("game %s finished: %s (winner: %s)", game_id, status, state.get("winner"))
            if chatter and status not in ("aborted", "noStart"):
                winner = state.get("winner")
                result = "1/2-1/2" if winner is None else ("1-0" if winner == "white" else "0-1")
                _chat(client, game_id, chatter.game_over(result, color))
            return status

        board = chess.Board(initial_fen)
        for uci in state.get("moves", "").split():
            board.push_uci(uci)
        if board.turn != color or board.is_game_over():
            continue
        clock = clock_limit(state)
        if clock is not None and max_think is None:
            decision = bot.choose_move(board, settings, clock=clock)
        else:
            budget = time_budget(state, color)
            if max_think is not None:
                budget = min(budget, max_think)
            decision = bot.choose_move(board, settings, budget)
        log.info("game %s: %s (%s)", game_id, board.san(decision.move), decision.source)
        client.bots.make_move(game_id, decision.move.uci())
    return status


def _game_thread(client, game_id: str, my_id: str, cfg: dict, active: set[str], lock: threading.Lock) -> None:
    try:
        eng = cfg.get("engine", {})
        with EngineWrapper(threads=eng.get("threads", 1), hash_mb=eng.get("hash_mb", 128)) as engine:
            # Reserve time per move for network lag so clock-based searches never flag.
            engine.configure({"Move Overhead": eng.get("move_overhead_ms", 100)})
            play_game(client, game_id, my_id, SemsBot(engine, BOOK), cfg)
    except Exception:
        log.exception("game %s crashed", game_id)
    finally:
        with lock:
            active.discard(game_id)


def run(client, cfg: dict) -> None:
    account = client.account.get()
    if account.get("title") != "BOT":
        raise SystemExit(
            f"Account {account.get('username')} is not a BOT account. Upgrade a fresh account first (see README)."
        )
    my_id = account["id"]
    log.info("Connected as BOT %s; waiting for challenges", account.get("username"))
    active: set[str] = set()
    lock = threading.Lock()
    for event in client.bots.stream_incoming_events():
        kind = event.get("type")
        if kind == "challenge":
            ch = event["challenge"]
            if (ch.get("challenger") or {}).get("id") == my_id:
                continue  # our own outgoing challenge
            with lock:
                ok, reason = should_accept(ch, cfg, len(active))
            who = (ch.get("challenger") or {}).get("name")
            if ok:
                log.info("accepting challenge %s from %s", ch["id"], who)
                client.bots.accept_challenge(ch["id"])
            else:
                log.info("declining challenge %s from %s (%s)", ch["id"], who, reason)
                client.bots.decline_challenge(ch["id"], reason=reason)
        elif kind == "gameStart":
            game = event["game"]
            game_id = game.get("gameId") or game["id"]
            with lock:
                if game_id in active:
                    continue
                active.add(game_id)
            threading.Thread(target=_game_thread, args=(client, game_id, my_id, cfg, active, lock), daemon=True).start()


# --- Dry run: a fake client whose opponent plays random moves, exercising the real game loop. ---

class _FakeBots:
    def __init__(self, bot_color: chess.Color, seed: int = 1):
        self.board = chess.Board()
        self.bot_color = bot_color
        self.rng = random.Random(seed)
        self.events: queue.Queue = queue.Queue()
        self.chat: list[str] = []

    def _state(self, status: str | None = None) -> dict:
        outcome = self.board.outcome(claim_draw=True)
        winner = None
        if status is None:
            status = "started"
            if outcome:
                status = "mate" if outcome.termination == chess.Termination.CHECKMATE else "draw"
                winner = None if outcome.winner is None else ("white" if outcome.winner else "black")
        return {"type": "gameState", "moves": " ".join(m.uci() for m in self.board.move_stack),
                "wtime": timedelta(seconds=60), "btime": timedelta(seconds=60),
                "winc": timedelta(0), "binc": timedelta(0), "status": status, "winner": winner}

    def _opponent_moves(self) -> None:
        if not self.board.is_game_over(claim_draw=True) and self.board.turn != self.bot_color:
            self.board.push(self.rng.choice(list(self.board.legal_moves)))
        self.events.put(self._state())

    def stream_game_state(self, game_id: str) -> Iterator[dict]:
        me, opp = {"id": "sems-bot"}, {"id": "random-mover"}
        if self.bot_color == chess.BLACK:
            self._opponent_moves()
            first = self.events.get()
        else:
            first = self._state()
        first = {**first, "wtime": 60_000, "btime": 60_000, "winc": 0, "binc": 0}  # gameFull carries raw ms
        yield {"type": "gameFull", "white": me if self.bot_color else opp, "black": opp if self.bot_color else me,
               "variant": {"key": "standard"}, "speed": "blitz", "initialFen": "startpos", "state": first}
        while True:
            event = self.events.get()
            yield event
            if event["status"] != "started":
                return

    def make_move(self, game_id: str, move: str) -> None:
        self.board.push_uci(move)
        self._opponent_moves()

    def resign_game(self, game_id: str) -> None:
        self.events.put(self._state("resign"))

    def post_message(self, game_id: str, text: str, spectator: bool = False) -> None:
        self.chat.append(text)


class FakeClient:
    def __init__(self, bot_color: chess.Color = chess.WHITE, seed: int = 1):
        self.bots = _FakeBots(bot_color, seed)


def dry_run(cfg: dict, bot_color: chess.Color = chess.WHITE, max_think: float = 0.1) -> tuple[str, chess.Board]:
    client = FakeClient(bot_color)
    with EngineWrapper() as engine:
        status = play_game(client, "dryrun", "sems-bot", SemsBot(engine, BOOK), cfg, max_think=max_think)
    return status, client.bots.board


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Sem's Chess Bot on Lichess (BOT accounts only)")
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--dry-run", action="store_true", help="simulate a game against a random mover, no network")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(message)s")
    cfg = load_config(args.config)

    if args.dry_run:
        status, board = dry_run(cfg)
        print(f"Dry run finished: {status} after {len(board.move_stack)} plies. Final FEN: {board.fen()}")
        return 0

    token = os.environ.get("LICHESS_TOKEN")
    if not token:
        print("Set LICHESS_TOKEN in .env (a token with the bot:play scope).")
        return 1
    import berserk

    run(berserk.Client(berserk.TokenSession(token)), cfg)
    return 0
