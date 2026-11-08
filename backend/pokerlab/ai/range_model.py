"""Estimate an opponent's range from their public actions (a simple, explainable model).

**Preflop.** Each action caps the range at a "top X% of hands" level, using the ranking
in :mod:`pokerlab.ai.preflop`. The cap depends on how many raises came before the action.
Heads-up ranges are wide, so an opening raise from the button is about 70% of hands.

**Postflop.** Every bet, raise or call keeps the strongest part of the current range,
ranked by made-hand strength on the board at that moment. Flush draws and open-ended
straight draws are always kept, because players bet and call with them. With
``cap_checks``, a check removes the strongest part of the range, since most players bet
their best hands. Some strong hands stay in, because players also trap.

The model has no weights and does not adapt to the opponent. Every surviving combo is
equally likely. That is a deliberate first step. Opponent modelling (adjusting these
fractions per player) builds on it later.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from .. import fasteval
from ..cards import Card
from ..engine import Street
from ..ranges import Combo, HandRange
from .base import ActionRecord
from .preflop import top_range
from .strength import draw_bonus

if TYPE_CHECKING:
    from .adaptation import Adjustments

# Preflop caps, indexed by the number of raises before the action (last entry repeats).
PREFLOP_RAISE = (0.70, 0.15, 0.06)  # open, 3-bet, 4-bet+
PREFLOP_RAISE_VS_LIMP = 0.35  # big blind raising a limp
PREFLOP_CALL = (0.85, 0.65, 0.30, 0.12)  # limp, call an open, call a 3-bet, call a 4-bet+
SHORT_SHOVE_BB = 12  # all-in raises this small (in big blinds) are much wider
SHORT_SHOVE_RANGE = 0.40

# Postflop: fraction of the current range kept after each action.
POSTFLOP_KEEP = {"bet": 0.65, "raise": 0.35, "call": 0.80}
CHECK_CAP = 0.15  # share of the strongest hands removed by a check (cap_checks only)
MIN_COMBOS = 12

_BOARD_SIZE = {Street.FLOP: 3, Street.TURN: 4, Street.RIVER: 5}


def preflop_fraction(
    actions: Sequence[ActionRecord],
    seat: int,
    button: int,
    bb: int,
    raise_scale: float = 1.0,
    call_scale: float = 1.0,
) -> float:
    """The "top X%" cap implied by ``seat``'s preflop actions.

    ``raise_scale`` and ``call_scale`` widen or narrow the raising and calling levels for
    a player whose observed preflop frequencies differ from the baseline.
    """
    fraction = 1.0
    raises = 0
    for a in actions:
        if a.street != Street.PREFLOP:
            break
        if a.seat == seat:
            if a.action in ("bet", "raise"):
                if raises == 0 and seat != button:
                    level = PREFLOP_RAISE_VS_LIMP
                else:
                    level = PREFLOP_RAISE[min(raises, len(PREFLOP_RAISE) - 1)]
                level = min(1.0, level * raise_scale)
                if a.all_in and a.amount <= SHORT_SHOVE_BB * bb:
                    level = max(level, SHORT_SHOVE_RANGE)
                fraction = min(fraction, level)
            elif a.action == "call":
                level = PREFLOP_CALL[min(raises, len(PREFLOP_CALL) - 1)]
                fraction = min(fraction, min(1.0, level * call_scale))
        if a.action in ("bet", "raise"):
            raises += 1
    return fraction


def _narrow(combos: list[Combo], board: list[Card], keep: float) -> list[Combo]:
    """Keep the strongest ``keep`` share of ``combos`` on ``board``, plus strong draws."""
    if len(combos) <= MIN_COMBOS:
        return combos
    board_idx = [c.index for c in board]
    ranked = sorted(
        combos, key=lambda h: fasteval.score((h[0].index, h[1].index, *board_idx)), reverse=True
    )
    n = max(MIN_COMBOS, round(keep * len(ranked)))
    kept = ranked[:n]
    if len(board) < 5:
        kept += [h for h in ranked[n:] if draw_bonus(h, board) >= 0.14]
    return kept


def _cap(combos: list[Combo], board: list[Card], drop: float) -> list[Combo]:
    """Remove the strongest ``drop`` share of ``combos`` on ``board``."""
    if len(combos) <= MIN_COMBOS:
        return combos
    board_idx = [c.index for c in board]
    ranked = sorted(
        combos, key=lambda h: fasteval.score((h[0].index, h[1].index, *board_idx)), reverse=True
    )
    cut = min(round(drop * len(ranked)), len(ranked) - MIN_COMBOS)
    return ranked[cut:]


def estimate_range(
    actions: Sequence[ActionRecord],
    seat: int,
    button: int,
    big_blind: int,
    board: Sequence[Card],
    known: Sequence[Card] = (),
    cap_checks: bool = False,
    adjust: Adjustments | None = None,
) -> HandRange:
    """``seat``'s plausible holdings given the public actions, the board and our cards.

    ``adjust`` (from :mod:`pokerlab.ai.adaptation`) scales the assumed range widths to the
    player's observed frequencies. Without it the fixed defaults apply.
    """
    blocked = set(board) | set(known)
    raise_scale = adjust.raise_range_scale if adjust else 1.0
    call_scale = adjust.vpip_range_scale if adjust else 1.0
    keep_scale = {
        "bet": adjust.bet_range_scale if adjust else 1.0,
        "raise": adjust.raise_range_post_scale if adjust else 1.0,
        "call": adjust.call_range_scale if adjust else 1.0,
    }
    fraction = preflop_fraction(actions, seat, button, big_blind, raise_scale, call_scale)
    base = top_range(round(max(0.02, fraction), 3))
    combos = [h for h in base if h[0] not in blocked and h[1] not in blocked]
    board = list(board)
    for a in actions:
        if a.seat != seat or a.street == Street.PREFLOP:
            continue
        street_board = board[: _BOARD_SIZE[a.street]]
        if a.action in POSTFLOP_KEEP:
            keep = min(1.0, max(0.15, POSTFLOP_KEEP[a.action] * keep_scale[a.action]))
            combos = _narrow(combos, street_board, keep)
        elif a.action == "check" and cap_checks:
            combos = _cap(combos, street_board, CHECK_CAP)
    return HandRange.of(combos, "estimated")


def defending_ranges(
    rng: HandRange, board: Sequence[Card], defends: Sequence[float]
) -> list[HandRange]:
    """For each defence frequency, the part of ``rng`` that continues against a bet: its
    strongest share by made-hand strength on ``board``, plus strong draws.

    The range is ranked once and sliced for every frequency (this runs on every bet
    decision of the expected-value agent).
    """
    combos = list(rng)
    board = list(board)
    if len(combos) <= MIN_COMBOS:
        return [HandRange.of(combos, "defending") for _ in defends]
    board_idx = [c.index for c in board]
    ranked = sorted(
        combos, key=lambda h: fasteval.score((h[0].index, h[1].index, *board_idx)), reverse=True
    )
    draws = [draw_bonus(h, board) >= 0.14 for h in ranked] if len(board) < 5 else None
    out = []
    for defend in defends:
        n = max(MIN_COMBOS, round(defend * len(ranked)))
        kept = ranked[:n]
        if draws is not None:
            kept += [h for h, d in zip(ranked[n:], draws[n:], strict=True) if d]
        out.append(HandRange.of(kept, "defending"))
    return out
