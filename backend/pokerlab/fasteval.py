"""Table-driven hand scoring for hot loops such as equity simulation.

Cards are dense integers ``0..51`` (see :attr:`pokerlab.cards.Card.index`):
``index = (rank - 2) * 4 + suit``.

A *score* is a plain ``int``. Higher scores are better hands, and equal scores are
exactly tied hands. The encoding is ``category << 20`` followed by up to five 4-bit
rank nibbles (most significant first), which makes score order identical to
:class:`pokerlab.evaluator.HandValue` order. :func:`to_hand_value` converts back.

How it works:

* Without a flush, a hand's value depends only on its rank multiset. Each card adds
  ``5 ** rank`` to a key. A rank appears at most four times, so the base-5 key is unique,
  and a precomputed table maps every 5–7 card rank multiset to its score.
* Each card also adds ``1 << (4 * suit)`` to a suit counter. A 64K-entry table maps that
  counter to the flush suit, or ``-1``. When a flush exists, a 13-bit rank mask of the
  flush cards indexes a second table (flushes and straight flushes).
* The answer is the larger of the two lookups. With seven cards a flush can never coexist
  with quads or a full house, but taking the maximum is correct in general.

``pokerlab.evaluator.evaluate`` stays the readable reference implementation. The tests
check that the two agree.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from .cards import Card
from .evaluator import HandCategory, HandValue

__all__ = [
    "FLUSH_SUIT",
    "FLUSH_TABLE",
    "NOFLUSH_TABLE",
    "RANK_KEY",
    "SUIT_KEY",
    "score",
    "score_cards",
    "to_hand_value",
]

_TIEBREAK_LEN = {
    HandCategory.HIGH_CARD: 5,
    HandCategory.ONE_PAIR: 4,
    HandCategory.TWO_PAIR: 3,
    HandCategory.THREE_OF_A_KIND: 3,
    HandCategory.STRAIGHT: 1,
    HandCategory.FLUSH: 5,
    HandCategory.FULL_HOUSE: 2,
    HandCategory.FOUR_OF_A_KIND: 2,
    HandCategory.STRAIGHT_FLUSH: 1,
}


def _encode(category: int, ranks: Sequence[int]) -> int:
    value = category
    for i in range(5):
        value = (value << 4) | (ranks[i] if i < len(ranks) else 0)
    return value


def to_hand_value(score_: int) -> HandValue:
    """Convert a score back to a :class:`HandValue` (for display and comparison)."""
    category = HandCategory(score_ >> 20)
    nibbles = [(score_ >> (16 - 4 * i)) & 0xF for i in range(5)]
    return HandValue(category, tuple(nibbles[: _TIEBREAK_LEN[category]]))


def _straight_high_from_mask(mask: int) -> int:
    """Highest rank (2..14) of the best straight in a 13-bit rank mask, or 0."""
    for high in range(14, 5, -1):
        window = 0b11111 << (high - 6)
        if mask & window == window:
            return high
    wheel = (1 << 12) | 0b1111  # A, 2, 3, 4, 5
    return 5 if mask & wheel == wheel else 0


def _build_flush_table() -> list[int]:
    table = [0] * (1 << 13)
    for mask in range(1 << 13):
        if mask.bit_count() < 5:
            continue
        high = _straight_high_from_mask(mask)
        if high:
            table[mask] = _encode(HandCategory.STRAIGHT_FLUSH, (high,))
        else:
            ranks = [r + 2 for r in range(12, -1, -1) if mask >> r & 1][:5]
            table[mask] = _encode(HandCategory.FLUSH, ranks)
    return table


def _score_rank_counts(counts: Sequence[int]) -> int:
    """Score a rank multiset (``counts[r]`` copies of rank ``r + 2``), ignoring suits."""
    present = [r + 2 for r in range(12, -1, -1) if counts[r]]  # descending ranks
    groups = sorted(((counts[r - 2], r) for r in present), reverse=True)
    quads = [r for k, r in groups if k == 4]
    trips = [r for k, r in groups if k == 3]
    pairs = [r for k, r in groups if k == 2]

    if quads:
        kicker = max(r for r in present if r != quads[0])
        return _encode(HandCategory.FOUR_OF_A_KIND, (quads[0], kicker))
    if trips and (len(trips) >= 2 or pairs):
        return _encode(HandCategory.FULL_HOUSE, (trips[0], max([*trips[1:], *pairs])))
    mask = 0
    for r in present:
        mask |= 1 << (r - 2)
    high = _straight_high_from_mask(mask)
    if high:
        return _encode(HandCategory.STRAIGHT, (high,))
    if trips:
        kickers = [r for r in present if r != trips[0]][:2]
        return _encode(HandCategory.THREE_OF_A_KIND, (trips[0], *kickers))
    if len(pairs) >= 2:
        hi, lo = pairs[0], pairs[1]
        kicker = max(r for r in present if r not in (hi, lo))
        return _encode(HandCategory.TWO_PAIR, (hi, lo, kicker))
    if pairs:
        kickers = [r for r in present if r != pairs[0]][:3]
        return _encode(HandCategory.ONE_PAIR, (pairs[0], *kickers))
    return _encode(HandCategory.HIGH_CARD, present[:5])


def _build_noflush_table() -> dict[int, int]:
    table: dict[int, int] = {}
    counts = [0] * 13

    def rec(rank: int, cards: int, key: int) -> None:
        if rank == 13:
            if cards >= 5:
                table[key] = _score_rank_counts(counts)
            return
        for k in range(min(4, 7 - cards) + 1):
            counts[rank] = k
            rec(rank + 1, cards + k, key + k * 5**rank)
        counts[rank] = 0

    rec(0, 0, 0)
    return table


def _build_flush_suit_table() -> list[int]:
    table = [-1] * (1 << 16)
    for key in range(1 << 16):
        for suit in range(4):
            if (key >> (4 * suit)) & 0xF >= 5:
                table[key] = suit
                break
    return table


RANK_KEY: tuple[int, ...] = tuple(5 ** (i >> 2) for i in range(52))
SUIT_KEY: tuple[int, ...] = tuple(1 << (4 * (i & 3)) for i in range(52))
RANK_BIT: tuple[int, ...] = tuple(1 << (i >> 2) for i in range(52))
NOFLUSH_TABLE: dict[int, int] = _build_noflush_table()
FLUSH_TABLE: list[int] = _build_flush_table()
FLUSH_SUIT: list[int] = _build_flush_suit_table()


def score(cards: Iterable[int]) -> int:
    """Score 5–7 distinct card indices. No validation (hot path); see :func:`score_cards`."""
    cards = tuple(cards)
    key = 0
    suits = 0
    for c in cards:
        key += RANK_KEY[c]
        suits += SUIT_KEY[c]
    best = NOFLUSH_TABLE[key]
    flush_suit = FLUSH_SUIT[suits]
    if flush_suit >= 0:
        mask = 0
        for c in cards:
            if c & 3 == flush_suit:
                mask |= RANK_BIT[c]
        flush = FLUSH_TABLE[mask]
        if flush > best:
            best = flush
    return best


def score_cards(cards: Sequence[Card]) -> int:
    """Validated :func:`score` for :class:`Card` objects (5–7 distinct cards)."""
    if not 5 <= len(cards) <= 7:
        raise ValueError(f"score_cards() needs 5-7 cards, got {len(cards)}")
    if len(set(cards)) != len(cards):
        raise ValueError("Duplicate cards in hand")
    return score(c.index for c in cards)
