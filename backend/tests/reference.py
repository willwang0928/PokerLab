"""An independent poker reference for validation tests.

Written from the rules of the game with no PokerLab imports: cards are plain strings
("Ah", "Td"), a five-card hand is ranked by counting ranks and suits, a seven-card hand
takes the best of its 21 five-card subsets, and equity is plain enumeration of every
run-out with ties split evenly. It is slow on purpose: nothing is shared with the
optimised evaluator or the equity engine, so agreement is a genuine cross-check.
"""

from __future__ import annotations

from collections import Counter
from itertools import combinations

RANKS = "23456789TJQKA"
SUITS = "shdc"
DECK = [r + s for s in SUITS for r in RANKS]


def _rank(card: str) -> int:
    return RANKS.index(card[0]) + 2


def rank5(cards: list[str]) -> tuple[int, ...]:
    """Comparable value of a five-card hand (category first, then tie-breakers)."""
    ranks = sorted((_rank(c) for c in cards), reverse=True)
    flush = len({c[1] for c in cards}) == 1
    distinct = sorted(set(ranks), reverse=True)
    straight = None
    if len(distinct) == 5:
        if distinct[0] - distinct[4] == 4:
            straight = distinct[0]
        elif distinct == [14, 5, 4, 3, 2]:  # the wheel: the ace plays low
            straight = 5
    groups = sorted(Counter(ranks).items(), key=lambda kv: (-kv[1], -kv[0]))
    shape = [n for _, n in groups]
    order = tuple(r for r, _ in groups)
    if straight and flush:
        return (8, straight)
    if shape == [4, 1]:
        return (7, *order)
    if shape == [3, 2]:
        return (6, *order)
    if flush:
        return (5, *ranks)
    if straight:
        return (4, straight)
    if shape == [3, 1, 1]:
        return (3, *order)
    if shape == [2, 2, 1]:
        return (2, *order)
    if shape == [2, 1, 1, 1]:
        return (1, *order)
    return (0, *ranks)


def best_hand(cards: list[str]) -> tuple[int, ...]:
    return max(rank5(list(c)) for c in combinations(cards, 5))


def equity(holes: list[list[str]], board: list[str]) -> tuple[list[float], int]:
    """Exact all-in equity of known hands: every run-out, ties shared equally."""
    used = set(board) | {c for h in holes for c in h}
    rest = [c for c in DECK if c not in used]
    shares = [0.0] * len(holes)
    deals = 0
    for runout in combinations(rest, 5 - len(board)):
        full = board + list(runout)
        values = [best_hand(h + full) for h in holes]
        top = max(values)
        winners = [i for i, v in enumerate(values) if v == top]
        for i in winners:
            shares[i] += 1 / len(winners)
        deals += 1
    return [s / deals for s in shares], deals
