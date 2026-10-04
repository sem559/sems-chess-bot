# Sem's Chess Bot

A chess bot built on **Stockfish 19** that adds features Stockfish doesn't have:
selectable **openings**, **playstyles**, **ELO levels**, a **browser GUI**, and a **Lichess BOT** runner.

> **About strength:** at full strength the bot *is* Stockfish, so it's as strong as the best engine
> in the world, but not stronger. Stockfish improves through millions of distributed test games
> ([Fishtest](https://tests.stockfishchess.org/)), and a small project can't out-tune that.
> The playstyles deliberately give up a few centipawns to play "in character". Use
> `scripts/match.py` to measure what any change really costs or gains, instead of guessing.

## Setup (Windows)

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python scripts/get_stockfish.py      # official build from github.com/official-stockfish
.venv/Scripts/python -m pytest                     # 84 tests
```

## Play in the browser

```bash
.venv/Scripts/python -m sems.web
```

Open http://127.0.0.1:8000. You can choose:

| Setting | What it does |
|---|---|
| **Play as** | White, Black or Random |
| **Bot strength** | 400–3190 ELO, or *Full strength*. At 1320+ with the Balanced style this uses Stockfish's own calibrated `UCI_Elo`. Below that, or with a style, the bot samples from Stockfish's top moves, so the rating is approximate. |
| **Playstyle** | Balanced, Aggressive, Solid, Tactical, Gambit |
| **Bot's opening** | 32 named repertoires (Najdorf, King's Gambit, London, Stafford, …). The bot follows the line until you leave it. |
| **Think time** | 0.2–5 s per move |

The page also has an eval bar, move list, undo, flip, resign, PGN copy, and an **analysis mode**
that shows the engine's top 3 lines.

Optional: drop Polyglot `.bin` opening books into `books/`, and the bot will use them once it's out of its repertoire.

## Lichess BOT account

The bot can play on Lichess **as an openly labelled BOT** through the official Bot API.
It only plays games that people challenge it to. It never plays on, or helps with, a human account's games.

1. Create a **new** Lichess account for the bot. Upgrading is permanent, and the account must have no games played.
2. Create an API token at https://lichess.org/account/oauth/token with the **`bot:play`** scope.
3. Copy `.env.example` to `.env` and set `LICHESS_TOKEN=...`
4. Upgrade the account (one time, irreversible):
   ```bash
   curl -d '' https://lichess.org/api/bot/account/upgrade -H "Authorization: Bearer YOUR_TOKEN"
   ```
5. Test locally, then go live:
   ```bash
   .venv/Scripts/python -m sems.lichess --dry-run
   .venv/Scripts/python -m sems.lichess
   ```

`sems/lichess/config.yaml` controls which challenges are accepted (variants, speeds, rated/casual,
bots/humans, how many games at once) and which persona (ELO/style/opening) is used per time control.

## Thinking time

- **On Lichess** the real clocks go to Stockfish, so its own time manager decides how long to think:
  quick on obvious moves, longer in critical positions. `move_overhead_ms` in `config.yaml`
  reserves time for network lag.
- **With a fixed think time** (GUI, `match.py`), a full-strength bot first searches for 25% of the budget.
  If the best move is at least 1.5 pawns better than the next one (a recapture, a hanging piece, a mate),
  it plays that move right away. The GUI then shows the source as `easy`. ELO-limited play always uses
  the full budget, so the limiter stays calibrated.

## Measuring strength

```bash
# Does the ELO slider scale? (the higher setting should win clearly)
.venv/Scripts/python scripts/match.py --a elo=1500 --b elo=2000 --games 100

# What does the aggressive style cost against pure Stockfish?
.venv/Scripts/python scripts/match.py --a style=aggressive --b max --games 400

# SPRT: is a change at least 5 Elo better? Stops early when the answer is clear.
.venv/Scripts/python scripts/match.py --a style=tactical --b max --sprt 0 5
```

## How a move is chosen

`sems/bot.py` → `SemsBot.choose_move`:

1. **Repertoire** (`openings.py`, `data/openings.json`): play the next book move if the game is still on a chosen line.
2. **Polyglot book**: used if one is in `books/`.
3. **Stockfish** (`engine.py`): either Stockfish picks the move itself (native mode), or it returns its top N moves.
4. **Style and strength** (`styles.py`, `strength.py`): among the moves within a centipawn window of the best,
   add style bonuses (checks and king attacks for Aggressive, castling and safe trades for Solid, …),
   then pick greedily, or with a softmax temperature at lower ELO. A forced mate is never given up.

## Project layout

```
sems/            bot core (config, engine, strength, styles, openings, bot)
sems/web/        FastAPI app + chessground board UI
sems/lichess/    Lichess BOT runner + config.yaml
scripts/         get_stockfish.py, match.py
tests/           pytest suite
```
