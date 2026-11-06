import random

import pytest

from pokerlab.cards import FULL_DECK, Card, Deck, Rank, Suit, parse_cards


def test_full_deck_has_52_unique_cards():
    assert len(FULL_DECK) == 52
    assert len(set(FULL_DECK)) == 52


def test_parse_and_code_round_trip():
    for card in FULL_DECK:
        assert Card.parse(card.code) == card
    assert Card.parse("As") == Card(Rank.ACE, Suit.SPADES)
    assert Card.parse("tD") == Card(Rank.TEN, Suit.DIAMONDS)


@pytest.mark.parametrize("bad", ["", "A", "Ax", "1s", "Ass", "10h"])
def test_parse_rejects_invalid_codes(bad):
    with pytest.raises(ValueError):
        Card.parse(bad)


def test_parse_cards_accepts_spaces():
    assert parse_cards("As Kd") == parse_cards("AsKd")
    with pytest.raises(ValueError):
        parse_cards("AsK")


def test_shuffle_is_deterministic_with_seed_and_complete():
    a, b = Deck(random.Random(42)), Deck(random.Random(42))
    a.shuffle()
    b.shuffle()
    dealt_a = a.deal(52)
    assert dealt_a == b.deal(52)
    assert sorted(dealt_a) == sorted(FULL_DECK)
    assert dealt_a != list(FULL_DECK)


def test_deal_and_burn_reduce_deck():
    d = Deck(random.Random(0))
    d.shuffle()
    d.deal(2)
    d.burn()
    assert len(d) == 49
    with pytest.raises(ValueError):
        d.deal(50)


def test_stacked_deck_deals_given_cards_first():
    cards = parse_cards("AsKsQs")
    d = Deck.stacked(cards)
    assert d.deal(3) == cards
    assert len(d) == 49
    with pytest.raises(ValueError):
        Deck.stacked(parse_cards("AsAs"))
