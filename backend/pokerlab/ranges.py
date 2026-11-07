"""Hold'em starting-hand ranges in standard notation.

Supported tokens (comma- or whitespace-separated, case-insensitive ranks):

=================  ==============================================  ==========
Token              Meaning                                         Combos
=================  ==============================================  ==========
``AA``             one pocket pair                                 6
``AKs`` / ``AKo``  suited / offsuit                                4 / 12
``AK``             suited and offsuit                              16
``TT+``            pairs from TT up to AA                          30
``A9s+``           kicker climbs up to just below the top card     20 (A9s–AKs)
``99-66``          pairs from 99 down to 66                        24
``A5s-A2s``        same top card, kicker range                     16
``T9s-65s``        same gap, both cards step down together         20
``AhKh``           one exact combo                                 1
``any``            every two-card hand                             1326
=================  ==============================================  ==========

Every combo in a range is equally likely. Weighted ranges are not supported yet.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from itertools import combinations

from .cards import FULL_DECK, RANK_CHARS, Card, Rank, Suit

Combo = tuple[Card, Card]  # canonical: higher card first (by rank, then suit order)

_R = "[2-9TJQKA]"
_PAIR = re.compile(rf"^({_R})\1(\+)?$")
_PAIR_DASH = re.compile(rf"^({_R})\1-({_R})\2$")
_NONPAIR = re.compile(rf"^({_R})({_R})([SO])?(\+)?$")
_NONPAIR_DASH = re.compile(rf"^({_R})({_R})([SO])?-({_R})({_R})([SO])?$")
_COMBO = re.compile(r"^[2-9TJQKA][SHDC][2-9TJQKA][SHDC]$")


class RangeError(ValueError):
    """A range string could not be parsed."""


def _rank(ch: str) -> Rank:
    return Rank(RANK_CHARS.index(ch) + 2)


def canonical(a: Card, b: Card) -> Combo:
    return (a, b) if a > b else (b, a)


def _pair_combos(r: Rank) -> list[Combo]:
    cards = [Card(r, s) for s in Suit]
    return [canonical(a, b) for a, b in combinations(cards, 2)]


def _nonpair_combos(hi: Rank, lo: Rank, kind: str | None) -> list[Combo]:
    out = []
    for s1 in Suit:
        for s2 in Suit:
            suited = s1 == s2
            if (kind == "S" and not suited) or (kind == "O" and suited):
                continue
            out.append(canonical(Card(hi, s1), Card(lo, s2)))
    return out


def _ordered(a: Rank, b: Rank, token: str) -> tuple[Rank, Rank]:
    if a == b:
        raise RangeError(f"{token!r}: use a pair token like 'TT' for pocket pairs")
    return (a, b) if a > b else (b, a)


def _expand_token(token: str) -> list[Combo]:
    t = token.upper()
    if t in ("ANY", "RANDOM", "XX"):
        return [canonical(a, b) for a, b in combinations(FULL_DECK, 2)]

    if _COMBO.match(t):
        a, b = Card.parse(token[:2]), Card.parse(token[2:])
        if a == b:
            raise RangeError(f"{token!r}: a hand cannot hold the same card twice")
        return [canonical(a, b)]

    if m := _PAIR.match(t):
        r = _rank(m[1])
        ranks = range(r, Rank.ACE + 1) if m[2] else [r]
        return [c for x in ranks for c in _pair_combos(Rank(x))]

    if m := _PAIR_DASH.match(t):
        a, b = sorted((_rank(m[1]), _rank(m[2])))
        return [c for x in range(a, b + 1) for c in _pair_combos(Rank(x))]

    if m := _NONPAIR.match(t):
        hi, lo = _ordered(_rank(m[1]), _rank(m[2]), token)
        kind = m[3]
        kickers = range(lo, hi) if m[4] else [lo]
        return [c for k in kickers for c in _nonpair_combos(hi, Rank(k), kind)]

    if m := _NONPAIR_DASH.match(t):
        if m[3] != m[6]:
            raise RangeError(f"{token!r}: both ends of a dash range need the same s/o suffix")
        h1, l1 = _ordered(_rank(m[1]), _rank(m[2]), token)
        h2, l2 = _ordered(_rank(m[4]), _rank(m[5]), token)
        kind = m[3]
        if h1 == h2:  # A5s-A2s: fixed top card, kicker range
            lo, hi = sorted((l1, l2))
            return [c for k in range(lo, hi + 1) for c in _nonpair_combos(h1, Rank(k), kind)]
        if h1 - l1 == h2 - l2:  # T9s-65s: same gap, slide down
            top, bottom = (h1, h2) if h1 > h2 else (h2, h1)
            gap = h1 - l1
            return [
                c
                for h in range(bottom, top + 1)
                for c in _nonpair_combos(Rank(h), Rank(h - gap), kind)
            ]
        raise RangeError(
            f"{token!r}: a dash range needs the same top card (A5s-A2s) or the same gap (T9s-65s)"
        )

    raise RangeError(f"Unrecognised range token {token!r}")


@dataclass(frozen=True, slots=True)
class HandRange:
    """An immutable set of two-card combos."""

    combos: frozenset[Combo]
    text: str = ""

    @classmethod
    def parse(cls, text: str) -> HandRange:
        tokens = [t for t in re.split(r"[\s,]+", text.strip()) if t]
        if not tokens:
            raise RangeError("Empty range")
        combos: set[Combo] = set()
        for token in tokens:
            combos.update(_expand_token(token))
        return cls(frozenset(combos), text.strip())

    @classmethod
    def of(cls, combos: Iterable[Combo], text: str = "") -> HandRange:
        return cls(frozenset(canonical(a, b) for a, b in combos), text)

    def without(self, dead: Iterable[Card]) -> HandRange:
        """Combos that use none of the ``dead`` cards (card removal)."""
        dead = set(dead)
        return HandRange(
            frozenset(c for c in self.combos if c[0] not in dead and c[1] not in dead), self.text
        )

    def __len__(self) -> int:
        return len(self.combos)

    def __iter__(self) -> Iterator[Combo]:
        return iter(sorted(self.combos, reverse=True))

    def __contains__(self, combo: object) -> bool:
        return isinstance(combo, tuple) and len(combo) == 2 and canonical(*combo) in self.combos

    def __str__(self) -> str:
        return self.text or f"<{len(self)} combos>"


def parse_range(text: str) -> HandRange:
    return HandRange.parse(text)
