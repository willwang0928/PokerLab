"""The table-driven scorer must agree with the reference evaluator and with combinatorics."""

import random
from collections import Counter
from itertools import combinations

import pytest

from pokerlab.cards import FULL_DECK, parse_cards
from pokerlab.evaluator import HandCategory, evaluate
from pokerlab.fasteval import score, score_cards, to_hand_value


@pytest.mark.parametrize("size", [5, 6, 7])
def test_matches_reference_evaluator(size):
    rng = random.Random(size)
    for _ in range(15_000):
        cards = rng.sample(FULL_DECK, size)
        assert to_hand_value(score_cards(cards)) == evaluate(cards)


def test_score_order_matches_hand_value_order():
    rng = random.Random(99)
    hands = [rng.sample(FULL_DECK, 7) for _ in range(3000)]
    pairs = [(score_cards(h), evaluate(h)) for h in hands]
    for (s1, v1), (s2, v2) in zip(pairs, pairs[1:], strict=False):
        assert (s1 < s2) == (v1 < v2)
        assert (s1 == s2) == (v1 == v2)


def test_every_five_card_hand_matches_known_frequencies():
    """All C(52,5) hands: textbook category counts and 7,462 distinct hand values.

    This checks the ranking against combinatorics, independently of ``evaluate``.
    """
    counts: Counter[int] = Counter()
    for hand in combinations(range(52), 5):
        counts[score(hand)] += 1
    by_category: Counter[HandCategory] = Counter()
    for s, k in counts.items():
        by_category[HandCategory(s >> 20)] += k
    assert by_category == {
        HandCategory.STRAIGHT_FLUSH: 40,
        HandCategory.FOUR_OF_A_KIND: 624,
        HandCategory.FULL_HOUSE: 3_744,
        HandCategory.FLUSH: 5_108,
        HandCategory.STRAIGHT: 10_200,
        HandCategory.THREE_OF_A_KIND: 54_912,
        HandCategory.TWO_PAIR: 123_552,
        HandCategory.ONE_PAIR: 1_098_240,
        HandCategory.HIGH_CARD: 1_302_540,
    }
    assert len(counts) == 7_462


@pytest.mark.parametrize(
    "a,b",
    [
        ("As2d3c4h5s 9c Jd", "2d3c4h5s6d 9c Jd"),  # wheel < six-high straight
        ("AhKh QhJh9h 2c 3d", "KsQsJsTs9s 2c 3d"),  # flush < straight flush
        ("9c9d9h9s 2d 2c 3h", "9c9d9h9s Kd 2c 3h"),  # quad kicker
        ("8c8d8h5c5d5h 2s", "8c8d8h5c5d 9h 9s"),  # 888-55 < 888-99
    ],
)
def test_specific_orderings(a, b):
    assert score_cards(parse_cards(a)) < score_cards(parse_cards(b))


def test_rejects_bad_input():
    with pytest.raises(ValueError):
        score_cards(parse_cards("AsKs"))
    with pytest.raises(ValueError):
        score_cards(parse_cards("AsAsKdQdJd"))
