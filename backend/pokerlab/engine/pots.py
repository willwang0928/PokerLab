"""Pot layering (main/side pots) and chip distribution."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field


@dataclass(slots=True)
class Pot:
    amount: int
    eligible: list[int] = field(default_factory=list)


def build_pots(contributions: Sequence[int], folded: Sequence[bool]) -> list[Pot]:
    """Split total contributions into a main pot and side pots.

    Each pot lists the (non-folded) seats eligible to win it. Chips from levels that
    no live player reached are folded into the previous pot (or carried forward).
    """
    levels = sorted({c for c in contributions if c > 0})
    pots: list[Pot] = []
    prev = 0
    carry = 0
    for level in levels:
        amount = sum(min(c, level) - min(c, prev) for c in contributions) + carry
        carry = 0
        eligible = [i for i, c in enumerate(contributions) if c >= level and not folded[i]]
        if not eligible:
            if pots:
                pots[-1].amount += amount
            else:
                carry = amount
        elif pots and pots[-1].eligible == eligible:
            pots[-1].amount += amount
        else:
            pots.append(Pot(amount, eligible))
        prev = level
    if carry:
        # Only possible if every contributor folded; give it to whoever is live (caller's job).
        pots.append(Pot(carry, [i for i, f in enumerate(folded) if not f]))
    return pots


def split_pot(
    amount: int, winners: Sequence[int], order_key: Callable[[int], int]
) -> dict[int, int]:
    """Split ``amount`` evenly among ``winners``; odd chips go out in ``order_key`` order."""
    if not winners:
        raise ValueError("A pot needs at least one winner")
    share, remainder = divmod(amount, len(winners))
    payout = {w: share for w in winners}
    for w in sorted(winners, key=order_key)[:remainder]:
        payout[w] += 1
    return payout
