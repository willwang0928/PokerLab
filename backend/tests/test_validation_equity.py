"""Hand evaluation and equity checked against an independent reference (tests/reference.py)."""

from __future__ import annotations

import random
import statistics

import pytest

from pokerlab.cards import parse_cards
from pokerlab.equity import calculate_equity
from pokerlab.evaluator import evaluate

from . import reference as ref


def _split(codes: str) -> list[str]:
    return [codes[i : i + 2] for i in range(0, len(codes), 2)]


def test_reference_reproduces_the_published_five_card_frequencies():
    """Sanity of the reference itself, against the textbook counts (C(52,5) hands)."""
    from collections import Counter
    from itertools import combinations

    counts = Counter(ref.rank5(list(h))[0] for h in combinations(ref.DECK, 5))
    assert [counts[c] for c in range(9)] == [
        1302540, 1098240, 123552, 54912, 10200, 5108, 3744, 624, 40,
    ]  # fmt: skip


def test_evaluator_orders_random_seven_card_hands_like_the_reference():
    rng = random.Random(41)
    for _ in range(3000):
        cards = rng.sample(ref.DECK, 9)
        a, b = cards[:2] + cards[4:], cards[2:4] + cards[4:]
        mine = (evaluate(parse_cards("".join(a))), evaluate(parse_cards("".join(b))))
        theirs = (ref.best_hand(a), ref.best_hand(b))
        assert (mine[0] > mine[1]) == (theirs[0] > theirs[1])
        assert (mine[0] == mine[1]) == (theirs[0] == theirs[1])


@pytest.mark.parametrize(
    "holes,board",
    [
        (["AhKh", "QsQd"], "Qh7h2c"),
        (["AsQd", "AhQc", "KdKs"], "Kh7c2s"),  # partial three-way ties
        (["5c4c", "AdAs"], "3c2d9hKs"),  # wheel draws
        (["2c3d", "4h5s", "7d8d"], "AhKhQhJhTh"),  # the board plays: a three-way split
        (["9h8h", "AcKd"], "7h6c2h"),
    ],
)
def test_exact_equity_matches_the_independent_reference(holes, board):
    expected, deals = ref.equity([_split(h) for h in holes], _split(board))
    r = calculate_equity(holes, board=board, method="exact")
    assert r.trials == deals
    assert r.equities == pytest.approx(expected, abs=1e-12)


def test_monte_carlo_error_bars_are_calibrated_with_ties():
    """Over many seeds, the true (reference) equity should fall inside the reported 95%
    interval about 95% of the time, and the z-scores should have spread about 1."""
    holes, board = ["AsKd", "AhKc", "QdQh"], "Kh7c2s"
    truth = ref.equity([_split(h) for h in holes], _split(board))[0][0]
    zs = []
    for seed in range(300):
        p = calculate_equity(holes, board=board, method="monte_carlo", iterations=300, seed=seed)
        p0 = p.players[0]
        zs.append((p0.equity - truth) / p0.std_error)
    coverage = sum(abs(z) <= 1.96 for z in zs) / len(zs)
    assert 0.91 <= coverage <= 0.99
    assert 0.85 <= statistics.pstdev(zs) <= 1.15
    assert abs(statistics.fmean(zs)) < 0.2


def test_monte_carlo_converges_at_the_square_root_rate():
    holes, board = ["AhKh", "QsQd"], "Qh7h2c"
    truth = ref.equity([_split(h) for h in holes], _split(board))[0][0]
    errs = {}
    for n in (400, 6400):
        runs = [
            calculate_equity(holes, board=board, method="monte_carlo", iterations=n, seed=s)
            for s in range(40)
        ]
        errs[n] = statistics.fmean(abs(r.equities[0] - truth) for r in runs)
    # 16x the samples should cut the typical error about 4x (allow 2.5x to 6.5x).
    assert 2.5 <= errs[400] / errs[6400] <= 6.5
