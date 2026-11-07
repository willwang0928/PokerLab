"""Preflop hand classes and their heads-up equity against a random hand.

The 1,326 starting hands fall into 169 classes: 13 pairs (6 combos each), 78 suited
hands (4 combos) and 78 offsuit hands (12 combos). ``PREFLOP_EQUITY`` holds each class's
all-in equity against a uniformly random hand. It was precomputed by Monte Carlo with
:func:`generate_table` and is stored in :mod:`pokerlab.ai.preflop_data`.

Ordering hands by equity against a random hand is the usual simple hand ranking. It is
not perfect: it slightly underrates suited connectors, which play better than their raw
equity. :func:`top_range` turns the ranking into "top X% of hands" ranges, counting
combos rather than classes.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import cache

from ..cards import RANK_CHARS, Card
from ..ranges import Combo, HandRange
from .preflop_data import PREFLOP_EQUITY

TOTAL_COMBOS = 1326


def hand_class(hole: Sequence[Card]) -> str:
    """Class label of two hole cards: ``"AA"``, ``"AKs"`` or ``"AKo"``."""
    a, b = sorted(hole, key=lambda c: c.rank, reverse=True)
    if a.rank == b.rank:
        return a.rank.char * 2
    return a.rank.char + b.rank.char + ("s" if a.suit == b.suit else "o")


def all_classes() -> list[str]:
    out = []
    ranks = RANK_CHARS[::-1]
    for i, hi in enumerate(ranks):
        for lo in ranks[i:]:
            if hi == lo:
                out.append(hi + lo)
            else:
                out += [hi + lo + "s", hi + lo + "o"]
    return out


def class_combos(label: str) -> int:
    return 6 if len(label) == 2 else (4 if label.endswith("s") else 12)


@cache
def ranked_classes() -> tuple[str, ...]:
    """All 169 classes, strongest first (by equity against a random hand)."""
    return tuple(sorted(PREFLOP_EQUITY, key=lambda c: (-PREFLOP_EQUITY[c], c)))


@cache
def _class_range(label: str) -> frozenset[Combo]:
    return HandRange.parse(label).combos


@cache
def percentile(label: str) -> float:
    """Fraction of all combos that rank strictly above ``label`` (AA = 0.0)."""
    above = 0
    for c in ranked_classes():
        if c == label:
            return above / TOTAL_COMBOS
        above += class_combos(c)
    raise KeyError(label)


@cache
def top_range(fraction: float) -> HandRange:
    """The strongest ``fraction`` of starting hands (whole classes, at least one class)."""
    if not 0 < fraction <= 1:
        raise ValueError("fraction must be in (0, 1]")
    target = fraction * TOTAL_COMBOS
    combos: set[Combo] = set()
    for label in ranked_classes():
        if combos and len(combos) + class_combos(label) / 2 > target:
            break
        combos |= _class_range(label)
    return HandRange(frozenset(combos), f"top {fraction:.0%}")


def generate_table(iterations: int = 40_000, seed: int = 2026) -> dict[str, float]:
    """Recompute ``PREFLOP_EQUITY`` (slow: a few minutes at the default size)."""
    from ..equity import calculate_equity

    table = {}
    for i, label in enumerate(all_classes()):
        r = calculate_equity(
            [label, "any"], method="monte_carlo", iterations=iterations, seed=seed + i
        )
        table[label] = round(r.players[0].equity, 4)
    return table


if __name__ == "__main__":  # pragma: no cover
    import pprint

    pprint.pprint(generate_table(), sort_dicts=False, width=100)
