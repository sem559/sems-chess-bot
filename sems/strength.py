"""Map a target ELO (and style) onto how the bot searches and picks moves.

Two modes:
- native: Stockfish picks the move itself. Used for full strength, and for the balanced
  style at 1320+ ELO, where Stockfish's own UCI_Elo limiter is calibrated.
- sampled: we ask Stockfish (at full strength) for its top-N moves and pick one ourselves,
  using a centipawn window, a softmax temperature and style bonuses. Used below 1320 ELO
  (Stockfish's floor) and whenever a non-balanced style is active. ELO in this mode is an
  approximation; measure it with scripts/match.py.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import MAX_ELO, MIN_ELO, Style

SF_MIN_ELO = 1320

# Full-strength window per style: how many centipawns the bot may give up to play "in style".
STYLE_WINDOW_CP = {
    Style.BALANCED: 0,
    Style.AGGRESSIVE: 30,
    Style.SOLID: 20,
    Style.TACTICAL: 25,
    Style.GAMBIT: 50,
    Style.BONGCLOUD: 60,
    Style.PAWN_STORM: 60,
    Style.KNIGHT_RIDER: 70,
    Style.COWARD: 50,
    Style.NEVER_CASTLES: 0,  # plays the engine's best move that isn't castling
}


@dataclass(frozen=True)
class StrengthPlan:
    native: bool
    uci_options: dict[str, object]
    multipv: int = 1
    window_cp: int = 0
    temperature_cp: float = 0.0
    time_scale: float = 1.0


FULL_STRENGTH = {"UCI_LimitStrength": False, "Skill Level": 20}


def _lerp(elo: int, lo_val: float, hi_val: float) -> float:
    t = (elo - MIN_ELO) / (MAX_ELO - MIN_ELO)
    return lo_val + (hi_val - lo_val) * max(0.0, min(1.0, t))


def plan_for(elo: int | None, style: Style = Style.BALANCED) -> StrengthPlan:
    styled = style != Style.BALANCED

    if elo is None:
        if not styled:
            return StrengthPlan(native=True, uci_options=FULL_STRENGTH)
        return StrengthPlan(native=False, uci_options=FULL_STRENGTH, multipv=5, window_cp=STYLE_WINDOW_CP[style])

    if elo >= SF_MIN_ELO and not styled:
        return StrengthPlan(native=True, uci_options={"UCI_LimitStrength": True, "UCI_Elo": elo})

    # Sampled mode: wider window, hotter sampling and less thinking time the lower the ELO.
    return StrengthPlan(
        native=False,
        uci_options=FULL_STRENGTH,
        multipv=6,
        window_cp=round(max(_lerp(elo, 400, 20), STYLE_WINDOW_CP[style])),
        temperature_cp=round(_lerp(elo, 120, 5), 1),
        time_scale=0.25 if elo < SF_MIN_ELO else 0.6,
    )
