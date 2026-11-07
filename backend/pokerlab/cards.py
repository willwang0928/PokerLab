"""Core card and deck primitives shared by every poker variant."""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import IntEnum

RANK_CHARS = "23456789TJQKA"
SUIT_CHARS = "shdc"  # spades, hearts, diamonds, clubs


class Rank(IntEnum):
    TWO = 2
    THREE = 3
    FOUR = 4
    FIVE = 5
    SIX = 6
    SEVEN = 7
    EIGHT = 8
    NINE = 9
    TEN = 10
    JACK = 11
    QUEEN = 12
    KING = 13
    ACE = 14

    @property
    def char(self) -> str:
        return RANK_CHARS[self - 2]

    @property
    def plural(self) -> str:
        name = RANK_NAMES[self]
        return name + ("es" if name == "Six" else "s")


RANK_NAMES = {
    Rank.TWO: "Two",
    Rank.THREE: "Three",
    Rank.FOUR: "Four",
    Rank.FIVE: "Five",
    Rank.SIX: "Six",
    Rank.SEVEN: "Seven",
    Rank.EIGHT: "Eight",
    Rank.NINE: "Nine",
    Rank.TEN: "Ten",
    Rank.JACK: "Jack",
    Rank.QUEEN: "Queen",
    Rank.KING: "King",
    Rank.ACE: "Ace",
}


class Suit(IntEnum):
    SPADES = 0
    HEARTS = 1
    DIAMONDS = 2
    CLUBS = 3

    @property
    def char(self) -> str:
        return SUIT_CHARS[self]


@dataclass(frozen=True, slots=True, order=True)
class Card:
    rank: Rank
    suit: Suit

    @classmethod
    def parse(cls, text: str) -> Card:
        """Parse a two-character card code such as ``"As"`` or ``"Td"``."""
        if len(text) != 2:
            raise ValueError(f"Invalid card code: {text!r}")
        r, s = text[0].upper(), text[1].lower()
        if r not in RANK_CHARS or s not in SUIT_CHARS:
            raise ValueError(f"Invalid card code: {text!r}")
        return cls(Rank(RANK_CHARS.index(r) + 2), Suit(SUIT_CHARS.index(s)))

    @property
    def code(self) -> str:
        return self.rank.char + self.suit.char

    @property
    def index(self) -> int:
        """Dense integer id 0..51: ``(rank - 2) * 4 + suit`` (used by fast evaluators)."""
        return (self.rank - 2) * 4 + self.suit

    def __str__(self) -> str:
        return self.code

    def __repr__(self) -> str:
        return f"Card({self.code})"


def parse_cards(text: str) -> list[Card]:
    """Parse a whitespace-separated (or concatenated) list of card codes."""
    compact = "".join(text.split())
    if len(compact) % 2:
        raise ValueError(f"Invalid card list: {text!r}")
    return [Card.parse(compact[i : i + 2]) for i in range(0, len(compact), 2)]


FULL_DECK: tuple[Card, ...] = tuple(Card(r, s) for s in Suit for r in Rank)


class Deck:
    """A standard 52-card deck with Fisher–Yates shuffling via ``random.Random``."""

    def __init__(self, rng: random.Random | None = None, cards: list[Card] | None = None):
        self._rng = rng or random.Random()
        self._cards: list[Card] = list(cards) if cards is not None else list(FULL_DECK)

    @classmethod
    def stacked(cls, cards: list[Card]) -> Deck:
        """A deck that deals ``cards`` in order first (for tests/replays), then the rest."""
        rest = [c for c in FULL_DECK if c not in set(cards)]
        if len(set(cards)) != len(cards):
            raise ValueError("Duplicate cards in stacked deck")
        return cls(cards=list(cards) + rest)

    def shuffle(self) -> None:
        self._rng.shuffle(self._cards)

    def deal(self, n: int = 1) -> list[Card]:
        if n > len(self._cards):
            raise ValueError("Not enough cards left in deck")
        dealt, self._cards = self._cards[:n], self._cards[n:]
        return dealt

    def remaining(self) -> list[Card]:
        """The undealt cards in dealing order (a copy, e.g. to replay the same deal)."""
        return list(self._cards)

    def burn(self) -> None:
        self.deal(1)

    def __len__(self) -> int:
        return len(self._cards)
