"""Playstyles: bonuses (in centipawns) that tilt the choice between near-equal engine moves."""
from __future__ import annotations

import math
import random

import chess

from .config import Style
from .engine import Candidate

PIECE_CP = {chess.PAWN: 100, chess.KNIGHT: 300, chess.BISHOP: 320, chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 0}


def _is_sacrifice(board: chess.Board, move: chess.Move) -> bool:
    """Moved piece lands on an attacked square and is worth more than what it captures."""
    piece = board.piece_at(move.from_square)
    if piece is None or piece.piece_type == chess.KING:
        return False
    captured = board.piece_at(move.to_square)
    gain = PIECE_CP[captured.piece_type] if captured else (100 if board.is_en_passant(move) else 0)
    after = board.copy(stack=False)
    after.push(move)
    attacked = after.is_attacked_by(after.turn, move.to_square)
    defended = after.is_attacked_by(not after.turn, move.to_square)
    loss = PIECE_CP[piece.piece_type]
    return attacked and (not defended or loss > gain + 50) and loss > gain


def _king_distance(square: chess.Square, king: chess.Square | None) -> int:
    return chess.square_distance(square, king) if king is not None else 8


def style_bonus(board: chess.Board, cand: Candidate, style: Style) -> float:
    if style == Style.BALANCED:
        return 0.0
    move = cand.move
    us = board.turn
    piece = board.piece_at(move.from_square)
    enemy_king = board.king(not us)
    own_king = board.king(us)
    capture = board.is_capture(move)
    check = board.gives_check(move)
    sac = _is_sacrifice(board, move)
    approach = _king_distance(move.from_square, enemy_king) - _king_distance(move.to_square, enemy_king)
    opening_phase = board.fullmove_number <= 15
    bonus = 0.0

    if style == Style.AGGRESSIVE:
        bonus += 25 * check + 10 * capture + 20 * sac + 6 * approach
        if piece and piece.piece_type == chess.PAWN and enemy_king is not None:
            if abs(chess.square_file(move.to_square) - chess.square_file(enemy_king)) <= 1:
                bonus += 10  # pawn storm
        if piece and piece.piece_type == chess.QUEEN and approach > 0:
            bonus += 5

    elif style == Style.SOLID:
        if board.is_castling(move):
            bonus += 20
        if not capture and not check:
            bonus += 8
        if capture and piece is not None:
            captured = board.piece_at(move.to_square)
            if captured and abs(PIECE_CP[captured.piece_type] - PIECE_CP[piece.piece_type]) <= 30 and cand.score_cp > 100:
                bonus += 15  # simplify when ahead
        if piece and piece.piece_type == chess.PAWN and own_king is not None:
            if abs(chess.square_file(move.from_square) - chess.square_file(own_king)) <= 1 and not capture:
                bonus -= 20  # don't loosen the king
        bonus -= 30 * sac

    elif style == Style.TACTICAL:
        bonus += 20 * (check or capture)
        # Forcing continuation: captures and checks in the first plies of the PV.
        probe = board.copy(stack=False)
        forcing = 0
        for ply, mv in enumerate(cand.pv[:6]):
            if not probe.is_legal(mv):
                break
            if ply and (probe.is_capture(mv) or probe.gives_check(mv)):
                forcing += 1
            probe.push(mv)
        bonus += 8 * forcing

    elif style == Style.GAMBIT:
        bonus += 10 * check
        if opening_phase:
            bonus += 35 * sac
            if piece and piece.piece_type in (chess.KNIGHT, chess.BISHOP):
                back_rank = 0 if us == chess.WHITE else 7
                if chess.square_rank(move.from_square) == back_rank:
                    bonus += 15  # develop fast
            if piece and piece.piece_type == chess.QUEEN and board.fullmove_number <= 8:
                bonus -= 15  # no early queen walks
        else:
            bonus += 15 * sac

    elif style == Style.BONGCLOUD:
        if piece and piece.piece_type == chess.KING and not board.is_castling(move):
            bonus += 20  # the king likes to get out and about

    elif style == Style.PAWN_STORM:
        if piece and piece.piece_type == chess.PAWN:
            bonus += 25 + 10 * capture
            if enemy_king is not None and abs(chess.square_file(move.to_square) - chess.square_file(enemy_king)) <= 2:
                bonus += 15

    elif style == Style.KNIGHT_RIDER:
        if piece and piece.piece_type == chess.KNIGHT:
            bonus += 40 + 10 * capture

    elif style == Style.COWARD:
        captured = board.piece_at(move.to_square)
        if capture and piece is not None and captured is not None:
            if abs(PIECE_CP[captured.piece_type] - PIECE_CP[piece.piece_type]) <= 30:
                bonus += 40  # any trade is a good trade
                if captured.piece_type == chess.QUEEN:
                    bonus += 60  # especially the scary one
        if piece and piece.piece_type != chess.PAWN:
            toward_home = -1 if us == chess.WHITE else 1
            if (chess.square_rank(move.to_square) - chess.square_rank(move.from_square)) * toward_home > 0:
                bonus += 10  # retreat!
        bonus -= 10 * check  # don't poke the bear

    return bonus


def forbidden(board: chess.Board, move: chess.Move, style: Style) -> bool:
    """Hard rules a style never breaks while it has any alternative."""
    return style == Style.NEVER_CASTLES and board.is_castling(move)


def _allows_mate_in_one(board: chess.Board, move: chess.Move) -> bool:
    after = board.copy(stack=False)
    after.push(move)
    for reply in after.legal_moves:
        after.push(reply)
        mate = after.is_checkmate()
        after.pop()
        if mate:
            return True
    return False


def style_override(board: chess.Board, style: Style) -> chess.Move | None:
    """Signature moves played without asking the engine: the Bongcloud's 1.e4 and 2.Ke2."""
    if style != Style.BONGCLOUD or board.root() != chess.Board() or len(board.move_stack) >= 4:
        return None
    white = board.turn == chess.WHITE
    king_walk = chess.Move.from_uci("e1e2" if white else "e8e7")
    pawn = chess.Move.from_uci("e2e4" if white else "e7e5")
    for move in (king_walk, pawn):
        if move in board.legal_moves and not _allows_mate_in_one(board, move):
            return move
    return None


def pick(
    board: chess.Board,
    candidates: list[Candidate],
    style: Style,
    window_cp: int,
    temperature_cp: float = 0.0,
    rng: random.Random | None = None,
) -> Candidate:
    """Pick among candidates within `window_cp` of the best, biased by style, optionally sampled."""
    if not candidates:
        raise ValueError("no candidates")
    allowed = [c for c in candidates if not forbidden(board, c.move, style)]
    candidates = allowed or candidates
    best = max(c.score_cp for c in candidates)
    eligible = [c for c in candidates if best - c.score_cp <= window_cp]
    # Never trade a forced mate for a non-mate.
    if any(c.mate is not None and c.mate > 0 for c in eligible):
        eligible = [c for c in eligible if c.mate is not None and c.mate > 0]
    utilities = [c.score_cp - best + style_bonus(board, c, style) for c in eligible]

    if temperature_cp <= 0:
        return eligible[max(range(len(eligible)), key=utilities.__getitem__)]

    rng = rng or random.Random()
    top = max(utilities)
    weights = [math.exp((u - top) / temperature_cp) for u in utilities]
    return rng.choices(eligible, weights=weights, k=1)[0]
