import pytest

from pokerlab.engine import build_pots, split_pot


def test_single_pot():
    pots = build_pots([100, 100], [False, False])
    assert [(p.amount, p.eligible) for p in pots] == [(200, [0, 1])]


def test_side_pots_with_three_all_ins():
    pots = build_pots([50, 100, 200], [False, False, False])
    assert [(p.amount, p.eligible) for p in pots] == [(150, [0, 1, 2]), (100, [1, 2]), (100, [2])]


def test_folded_chips_stay_in_pot_but_not_eligible():
    pots = build_pots([30, 100, 100], [True, False, False])
    assert [(p.amount, p.eligible) for p in pots] == [(230, [1, 2])]


def test_split_pot_odd_chip_order():
    assert split_pot(101, [0, 1], order_key=lambda s: {1: 0, 0: 1}[s]) == {0: 50, 1: 51}
    assert split_pot(100, [0, 1], order_key=lambda s: s) == {0: 50, 1: 50}
    with pytest.raises(ValueError):
        split_pot(10, [], order_key=lambda s: s)
