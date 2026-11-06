"""Hand evaluation for 5- to 7-card poker hands.

The evaluator works directly on rank/suit counts (no 21-combination brute force),
returning a :class:`HandValue` that orders correctly with ``<``/``>``/``==``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import IntEnum
from itertools import combinations

from .cards import RANK_NAMES, Card, Rank


class HandCategory(IntEnum):
    HIGH_CARD = 0
    ONE_PAIR = 1
    TWO_PAIR = 2
    THREE_OF_A_KIND = 3
    STRAIGHT = 4
    FLUSH = 5
    FULL_HOUSE = 6
    FOUR_OF_A_KIND = 7
    STRAIGHT_FLUSH = 8

    @property
    def label(self) -> str:
        return self.name.replace("_", " ").title()


@dataclass(frozen=True, slots=True, order=True)
class HandValue:
    category: HandCategory
    tiebreak: tuple[int, ...]

    @property
    def description(self) -> str:
        c, t = self.category, self.tiebreak
        n = lambda r: RANK_NAMES[Rank(r)]  # noqa: E731
        p = lambda r: Rank(r).plural  # noqa: E731
        if c == HandCategory.STRAIGHT_FLUSH:
            return "Royal Flush" if t[0] == Rank.ACE else f"Straight Flush, {n(t[0])} high"
        if c == HandCategory.FOUR_OF_A_KIND:
            return f"Four of a Kind, {p(t[0])}"
        if c == HandCategory.FULL_HOUSE:
            return f"Full House, {p(t[0])} over {p(t[1])}"
        if c == HandCategory.FLUSH:
            return f"Flush, {n(t[0])} high"
        if c == HandCategory.STRAIGHT:
            return f"Straight, {n(t[0])} high"
        if c == HandCategory.THREE_OF_A_KIND:
            return f"Three of a Kind, {p(t[0])}"
        if c == HandCategory.TWO_PAIR:
            return f"Two Pair, {p(t[0])} and {p(t[1])}"
        if c == HandCategory.ONE_PAIR:
            return f"Pair of {p(t[0])}"
        return f"High Card, {n(t[0])}"


def _straight_high(ranks: Iterable[int]) -> int | None:
    """Highest card of the best straight in ``ranks`` (wheel counts as 5-high)."""
    present = set(ranks)
    if Rank.ACE in present:
        present.add(1)
    for high in range(14, 4, -1):
        if all(r in present for r in range(high - 4, high + 1)):
            return high
    return None


def evaluate(cards: Sequence[Card]) -> HandValue:
    """Evaluate the best five-card poker hand available in 5–7 cards."""
    if not 5 <= len(cards) <= 7:
        raise ValueError(f"evaluate() needs 5-7 cards, got {len(cards)}")
    if len(set(cards)) != len(cards):
        raise ValueError("Duplicate cards in hand")

    by_suit: dict[int, list[int]] = {}
    for c in cards:
        by_suit.setdefault(c.suit, []).append(int(c.rank))
    flush_ranks = next((sorted(rs, reverse=True) for rs in by_suit.values() if len(rs) >= 5), None)

    if flush_ranks:
        sf = _straight_high(flush_ranks)
        if sf:
            return HandValue(HandCategory.STRAIGHT_FLUSH, (sf,))

    counts = Counter(int(c.rank) for c in cards)
    # Sort ranks by (multiplicity, rank) descending.
    groups = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    ranks_desc = sorted(counts, reverse=True)

    if groups[0][1] == 4:
        quad = groups[0][0]
        kicker = max(r for r in ranks_desc if r != quad)
        return HandValue(HandCategory.FOUR_OF_A_KIND, (quad, kicker))

    trips = [r for r, k in groups if k == 3]
    pairs = [r for r, k in groups if k == 2]
    if trips and (len(trips) >= 2 or pairs):
        top = trips[0]
        second = max([*trips[1:], *pairs])
        return HandValue(HandCategory.FULL_HOUSE, (top, second))

    if flush_ranks:
        return HandValue(HandCategory.FLUSH, tuple(flush_ranks[:5]))

    st = _straight_high(ranks_desc)
    if st:
        return HandValue(HandCategory.STRAIGHT, (st,))

    if trips:
        kickers = [r for r in ranks_desc if r != trips[0]][:2]
        return HandValue(HandCategory.THREE_OF_A_KIND, (trips[0], *kickers))

    if len(pairs) >= 2:
        hi, lo = pairs[0], pairs[1]
        kicker = max(r for r in ranks_desc if r not in (hi, lo))
        return HandValue(HandCategory.TWO_PAIR, (hi, lo, kicker))

    if pairs:
        kickers = [r for r in ranks_desc if r != pairs[0]][:3]
        return HandValue(HandCategory.ONE_PAIR, (pairs[0], *kickers))

    return HandValue(HandCategory.HIGH_CARD, tuple(ranks_desc[:5]))


def best_five(cards: Sequence[Card]) -> list[Card]:
    """The five cards that make the best hand in ``cards`` (5–7 cards), for display.

    When several five-card combinations tie (e.g. a board-played hand), the first in
    input order is returned, so hole cards win ties when they are listed first.
    """
    target = evaluate(cards)
    for combo in combinations(cards, 5):
        if evaluate(combo) == target:
            return list(combo)
    raise AssertionError("unreachable: some five-card subset always matches")  # pragma: no cover
