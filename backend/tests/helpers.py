"""Test helpers: build stacked decks that produce exact hole cards and boards."""

from __future__ import annotations

from pokerlab.cards import FULL_DECK, Card, Deck, parse_cards


def stacked_deck(holes: list[str], board: str = "", button: int = 0) -> Deck:
    """Deck that deals ``holes[seat]`` to each seat and ``board`` as the community cards.

    Hole cards are dealt one at a time starting left of the button; a burn card precedes
    the flop, turn and river, exactly as :class:`HoldemHand` deals.
    """
    hole_cards = [parse_cards(h) for h in holes]
    board_cards = parse_cards(board) if board else []
    used = {c for h in hole_cards for c in h} | set(board_cards)
    spare = iter(c for c in FULL_DECK if c not in used)
    n = len(holes)
    order = [(button + 1 + i) % n for i in range(n)]
    seq: list[Card] = [hole_cards[s][r] for r in range(2) for s in order]
    for i, street_size in enumerate((3, 1, 1)):
        start = sum((3, 1, 1)[:i])
        chunk = board_cards[start : start + street_size]
        if not chunk:
            break
        seq.append(next(spare))  # burn
        seq.extend(chunk)
    return Deck.stacked(seq)
