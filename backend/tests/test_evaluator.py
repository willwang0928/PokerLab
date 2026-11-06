import itertools
import random

import pytest

from pokerlab.cards import FULL_DECK, parse_cards
from pokerlab.evaluator import HandCategory, evaluate


def ev(text):
    return evaluate(parse_cards(text))


@pytest.mark.parametrize(
    "cards,category",
    [
        ("AsKsQsJsTs2d3c", HandCategory.STRAIGHT_FLUSH),
        ("5h4h3h2hAh", HandCategory.STRAIGHT_FLUSH),
        ("9c9d9h9sKd", HandCategory.FOUR_OF_A_KIND),
        ("9c9d9hKsKd2c", HandCategory.FULL_HOUSE),
        ("2h7h9hJhKh3c", HandCategory.FLUSH),
        ("As2d3c4h5s", HandCategory.STRAIGHT),
        ("TsJdQcKhAs", HandCategory.STRAIGHT),
        ("7s7d7cKhAs", HandCategory.THREE_OF_A_KIND),
        ("7s7dKcKhAs", HandCategory.TWO_PAIR),
        ("7s7dQcKhAs", HandCategory.ONE_PAIR),
        ("2s7dQcKhAs", HandCategory.HIGH_CARD),
    ],
)
def test_categories(cards, category):
    assert ev(cards).category == category


def test_category_ordering():
    hands = [
        "2s7dQcKhAs9d3c",
        "7s7dQcKhAs2c3d",
        "7s7dKcKhAs2c3d",
        "7s7d7cKhAs2c3d",
        "As2d3c4h5s9cJd",
        "2h7h9hJhKh3c4d",
        "9c9d9hKsKd2c3h",
        "9c9d9h9sKd2c3h",
        "5h4h3h2hAhKcQd",
    ]
    values = [ev(h) for h in hands]
    assert values == sorted(values)
    assert len(set(values)) == len(values)


def test_wheel_is_lowest_straight():
    assert ev("As2d3c4h5s").tiebreak == (5,)
    assert ev("As2d3c4h5s") < ev("2d3c4h5s6d")
    assert ev("As2d3c4h5s6d").tiebreak == (6,)


def test_kickers_decide():
    assert ev("AhAd Kc 9s 4d 3c 2h") > ev("AsAc Qc 9h 4c 3d 2s")
    assert ev("KhKd 8c 8s Ad 3c 2h") > ev("KsKc 8h 8d Qd 3h 2s")


def test_two_pair_uses_best_two_of_three_pairs():
    v = ev("2c2d 3c3d 4c4d As")
    assert v.category == HandCategory.TWO_PAIR
    assert v.tiebreak == (4, 3, 14)


def test_full_house_from_two_trips():
    v = ev("8c8d8h 5c5d5h As")
    assert v.category == HandCategory.FULL_HOUSE
    assert v.tiebreak == (8, 5)


def test_flush_over_straight_and_best_five_flush_cards():
    v = ev("2h4h6h8hTh Qh 9c")
    assert v.category == HandCategory.FLUSH
    assert v.tiebreak == (12, 10, 8, 6, 4)


def test_quads_kicker_uses_highest_remaining_card():
    assert ev("9c9d9h9s 2d 2c Kh").tiebreak == (9, 13)


def test_board_plays_ties():
    board = "AsKsQsJsTs"
    assert ev(board + "2c3d") == ev(board + "4c5d")


def test_descriptions():
    assert ev("AsKsQsJsTs").description == "Royal Flush"
    assert ev("KdKcKh5s5d").description == "Full House, Kings over Fives"
    assert ev("6d6c 2h 9s Jd").description == "Pair of Sixes"
    assert ev("As2d3c4h5s").description == "Straight, Five high"


def test_rejects_bad_input():
    with pytest.raises(ValueError):
        ev("AsKs")
    with pytest.raises(ValueError):
        ev("AsAsKdQdJd")


def _brute_force(cards):
    return max(evaluate(list(c)) for c in itertools.combinations(cards, 5))


def test_seven_card_matches_best_of_21_five_card_hands():
    rng = random.Random(1234)
    for _ in range(2000):
        cards = rng.sample(FULL_DECK, 7)
        assert evaluate(cards) == _brute_force(cards)
