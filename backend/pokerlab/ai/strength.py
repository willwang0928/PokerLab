"""Cheap hand-strength heuristics (no simulation) used by the basic agent."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence

from ..cards import Card, Rank
from ..evaluator import HandCategory, evaluate


def chen_score(hole: Sequence[Card]) -> float:
    """Bill Chen's preflop formula: roughly -1 (72o) to 20 (AA)."""
    a, b = sorted(hole, key=lambda c: c.rank, reverse=True)
    points = {Rank.ACE: 10, Rank.KING: 8, Rank.QUEEN: 7, Rank.JACK: 6}
    score = points.get(a.rank, a.rank / 2)
    if a.rank == b.rank:
        return float(max(5, score * 2))
    if a.suit == b.suit:
        score += 2
    gap = a.rank - b.rank - 1
    score -= {0: 0, 1: 1, 2: 2, 3: 4}.get(gap, 5)
    if gap <= 1 and a.rank < Rank.QUEEN:
        score += 1
    return float(math.ceil(score))


def preflop_strength(hole: Sequence[Card]) -> float:
    """Map the Chen score to 0..1 (calibrated for heads-up hand ranges).

    Linear up to a score of 10, then compressed so premium hands still separate
    (AKs ≈ 0.86, KK ≈ 0.93, AA = 1.0).
    """
    score = chen_score(hole)
    if score <= 10:
        return max(0.0, score / 12)
    return min(1.0, 10 / 12 + (score - 10) / 10 * (2 / 12))


def _draw_bonus(hole: Sequence[Card], board: Sequence[Card]) -> float:
    if len(board) >= 5:
        return 0.0
    cards = list(hole) + list(board)
    bonus = 0.0
    suits = Counter(c.suit for c in cards)
    for suit, count in suits.items():
        if count == 4 and any(c.suit == suit for c in hole):
            bonus = max(bonus, 0.18)
    ranks = {int(c.rank) for c in cards}
    if Rank.ACE in ranks:
        ranks.add(1)
    hole_ranks = {int(c.rank) for c in hole} | (
        {1} if any(c.rank == Rank.ACE for c in hole) else set()
    )
    for low in range(1, 11):
        window = set(range(low, low + 5))
        have = window & ranks
        if len(have) == 4 and have & hole_ranks:
            missing = (window - have).pop()
            open_ended = missing in (low, low + 4) and low not in (1, 10)
            bonus = max(bonus, 0.14 if open_ended else 0.07)
    return bonus


def postflop_strength(hole: Sequence[Card], board: Sequence[Card]) -> float:
    """Heuristic 0..1 strength of a made hand plus a bonus for draws."""
    value = evaluate(list(hole) + list(board))
    cat = value.category
    board_ranks = sorted((int(c.rank) for c in board), reverse=True)
    hole_ranks = sorted((int(c.rank) for c in hole), reverse=True)

    # If the board alone is just as good, our cards are not helping.
    if len(board) == 5 and evaluate(board) == value:
        return 0.15 + _draw_bonus(hole, board)

    if cat == HandCategory.HIGH_CARD:
        strength = 0.05 + 0.02 * sum(r > board_ranks[0] for r in hole_ranks)
    elif cat == HandCategory.ONE_PAIR:
        pair = value.tiebreak[0]
        if pair not in hole_ranks:
            strength = 0.12  # paired board
        elif hole_ranks[0] == hole_ranks[1] and pair > board_ranks[0]:
            strength = 0.66 + 0.02 * (pair - 10)  # overpair
        elif pair == board_ranks[0]:
            kicker = (
                max(r for r in hole_ranks if r != pair) if hole_ranks[0] != hole_ranks[1] else pair
            )
            strength = 0.55 + 0.01 * (kicker - 8)
        elif pair >= board_ranks[min(1, len(board_ranks) - 1)]:
            strength = 0.4
        else:
            strength = 0.3
    elif cat == HandCategory.TWO_PAIR:
        uses_both = all(r in hole_ranks for r in value.tiebreak[:2])
        strength = 0.8 if uses_both else 0.6
    elif cat == HandCategory.THREE_OF_A_KIND:
        strength = 0.84
    elif cat == HandCategory.STRAIGHT:
        strength = 0.88
    elif cat == HandCategory.FLUSH:
        strength = 0.9 + 0.005 * (value.tiebreak[0] - 10)
    elif cat == HandCategory.FULL_HOUSE:
        strength = 0.95
    else:
        strength = 0.99
    return min(1.0, strength + _draw_bonus(hole, board))
