"""Equity: exact answers, an independent brute-force cross-check, and Monte Carlo accuracy."""

from itertools import combinations, product

import pytest

from pokerlab.cards import FULL_DECK, parse_cards
from pokerlab.equity import calculate_equity, main
from pokerlab.evaluator import evaluate
from pokerlab.ranges import parse_range


def naive_equity(players, board="", dead=""):
    """Slow, independent reference: the reference evaluator, plain itertools, no shortcuts."""
    board_cards = parse_cards(board) if board else []
    known = set(board_cards) | set(parse_cards(dead) if dead else [])
    ranges = [list(parse_range(p)) for p in players]
    totals = [0.0] * len(players)
    deals = 0
    for holes in product(*ranges):
        cards = [c for h in holes for c in h]
        if len(set(cards)) != len(cards) or known & set(cards):
            continue
        rest = [c for c in FULL_DECK if c not in known and c not in cards]
        for runout in combinations(rest, 5 - len(board_cards)):
            full = board_cards + list(runout)
            values = [evaluate(list(h) + full) for h in holes]
            best = max(values)
            winners = [i for i, v in enumerate(values) if v == best]
            for w in winners:
                totals[w] += 1 / len(winners)
            deals += 1
    return [t / deals for t in totals], deals


def assert_within(result, expected, sigmas=4.5):
    for p, e in zip(result.players, expected, strict=True):
        assert p.std_error > 0
        assert abs(p.equity - e) <= sigmas * p.std_error + 1e-12, (p, e)


# ----------------------------------------------------------------- deterministic


def test_river_winner_takes_all():
    r = calculate_equity(["AsAh", "KsKh"], board="2c7d9hJcQd")
    assert r.method == "exact" and r.trials == 1
    assert r.equities == [1.0, 0.0]
    assert (r.players[0].win, r.players[0].tie) == (1.0, 0.0)


def test_board_plays_splits_evenly_three_ways():
    r = calculate_equity(["2c3d", "4c5d", "6h7h"], board="AsKsQsJsTs")
    assert r.equities == pytest.approx([1 / 3] * 3)
    assert all(p.tie == 1.0 and p.win == 0.0 for p in r.players)


def test_partial_split_between_two_of_three():
    # Ace-high straight on board, ace kicker irrelevant: AhXx and AdXx chop, 72 loses.
    r = calculate_equity(["Ah3c", "Ad4c", "7s2h"], board="KsQdJcTh9s")
    assert r.equities == pytest.approx([0.5, 0.5, 0.0])


def test_quads_vs_straight_flush_draw_on_the_turn():
    # Quads vs a straight flush draw with exactly two outs (5h and Th) among 44 cards.
    r = calculate_equity(["9c9d", "6h7h"], board="9h9s8hKc")
    assert r.method == "exact" and r.trials == 44
    assert r.equities == pytest.approx([42 / 44, 2 / 44])


def test_equities_sum_to_one():
    r = calculate_equity(["AhKh", "QQ", "JTs"], board="Qs7d2c")
    assert sum(r.equities) == pytest.approx(1.0)


# ----------------------------------------------------------------- exact vs reference


@pytest.mark.parametrize(
    "players,board,dead",
    [
        (["AhKh", "QsQd"], "Qh7h2c", ""),
        (["AhKh", "QsQd", "Tc9c"], "8c7h2c", ""),
        (["AsAh", "KK"], "Ks7d2c8h", ""),  # card removal: KK combos holding Ks drop out
        (["AhKh", "QQ+, AKs"], "Qs7d2c", ""),
        (["AA", "AA", "KK"], "2c3d4h5s", ""),  # ranges that block each other
        (["JTs", "99"], "9h8h2c", "Th"),  # dead card removes combos and runouts
    ],
)
def test_exact_matches_independent_brute_force(players, board, dead):
    expected, deals = naive_equity(players, board, dead)
    r = calculate_equity(players, board=board, dead=dead, method="exact")
    assert r.trials == deals
    assert r.equities == pytest.approx(expected, abs=1e-12)


def test_known_preflop_reference_values():
    # Published values: AhAs vs KhKs wins 82.36%; AA vs a random hand ~85.2%.
    r = calculate_equity(["AsAh", "KsKh"], iterations=60_000, seed=11)
    assert abs(r.players[0].win - 0.8236) < 4.5 * r.players[0].std_error
    r = calculate_equity(["AsAh", "any"], iterations=60_000, seed=12)
    assert abs(r.players[0].equity - 0.852) < 0.01


# ----------------------------------------------------------------- Monte Carlo


def test_monte_carlo_matches_exact_heads_up():
    exact = calculate_equity(["AhKh", "QsQd"], board="Qh7h2c", method="exact")
    mc = calculate_equity(
        ["AhKh", "QsQd"], board="Qh7h2c", method="monte_carlo", iterations=40_000, seed=1
    )
    assert mc.method == "monte_carlo"
    assert_within(mc, exact.equities)


def test_monte_carlo_matches_exact_with_overlapping_ranges():
    """Joint rejection sampling stays unbiased when ranges block each other.

    Drawing players one at a time from what is left would fail this test: it over-weights
    whoever is drawn first when ranges compete for the same cards.
    """
    players = ["AA, KK", "AA, AKs", "AK, KK"]
    board = "2c7d9h"
    exact = calculate_equity(players, board=board, method="exact")
    mc = calculate_equity(players, board=board, method="monte_carlo", iterations=60_000, seed=5)
    assert_within(mc, exact.equities)


def test_symmetric_hands_have_equal_equity():
    # Swapping spades and hearts maps the deal onto itself and swaps the players.
    r = calculate_equity(["AsKs", "AhKh"], iterations=40_000, seed=3)
    assert_within(r, [0.5, 0.5])


def test_seed_reproduces_results_and_is_reported():
    a = calculate_equity(["AhKh", "22"], iterations=5_000, seed=42)
    b = calculate_equity(["AhKh", "22"], iterations=5_000, seed=42)
    c = calculate_equity(["AhKh", "22"], iterations=5_000, seed=43)
    assert a == b
    assert a.equities != c.equities
    unseeded = calculate_equity(["AhKh", "22"], iterations=100)
    assert unseeded.seed is not None
    again = calculate_equity(["AhKh", "22"], iterations=100, seed=unseeded.seed)
    assert again == unseeded


def test_auto_picks_exact_when_cheap_and_monte_carlo_otherwise():
    assert calculate_equity(["AhKh", "QsQd"], board="Qh7h2c").method == "exact"
    assert calculate_equity(["AhKh", "QsQd"], iterations=1000, seed=1).method == "monte_carlo"


def test_standard_error_shrinks_with_more_iterations():
    small = calculate_equity(["AhKh", "QQ"], iterations=2_000, seed=1).players[0]
    large = calculate_equity(["AhKh", "QQ"], iterations=32_000, seed=1).players[0]
    assert large.std_error == pytest.approx(small.std_error / 4, rel=0.15)
    lo, hi = large.interval()
    assert lo < large.equity < hi


# ----------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "players,board,dead,message",
    [
        (["AhKh"], "", "", "at least two"),
        (["AhKh", "AhQd"], "", "", "share a card"),
        (["AhKh", "QQ"], "Ah7d2c", "", "no possible hand"),
        (["AhKh", "QQ"], "7d2c", "", "0, 3, 4 or 5"),
        (["AhKh", "QQ"], "7d2c3c", "7d", "more than once"),
        (["AA", "AA", "AA"], "", "", "no possible deal"),
        (["AsKs", "KK"], "", "Kh Kd Kc", "no possible hand"),
        (["any"] * 24, "", "", "Not enough cards"),
    ],
)
def test_rejects_impossible_inputs(players, board, dead, message):
    with pytest.raises(ValueError, match=message):
        calculate_equity(players, board=board or None, dead=dead or None, iterations=100, seed=1)


def test_rejects_bad_options():
    with pytest.raises(ValueError):
        calculate_equity(["AhKh", "QQ"], method="guess")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        calculate_equity(["AhKh", "QQ"], iterations=0)


def test_cli(capsys):
    assert main(["AhKh", "QsQd", "--board", "Qh7h2c"]) == 0
    out = capsys.readouterr().out
    assert "exact, 990 deals" in out and "equity" in out


def test_equity_by_street_skips_unreached_streets():
    from pokerlab.equity import equity_by_street

    hands = [parse_cards("AhKh"), parse_cards("QsQd")]
    rows = equity_by_street(hands, parse_cards("Qh7h2c3d9s"), seed=1)
    assert [s for s, _ in rows] == ["preflop", "flop", "turn"]
    assert rows[1][1].equities == calculate_equity(hands, board="Qh7h2c").equities
    assert equity_by_street(hands, [], seed=1)[0][0] == "preflop"
    assert len(equity_by_street(hands, [], seed=1)) == 1


def test_rejection_budget_stops_hopeless_sampling(monkeypatch):
    import pokerlab.equity as eq

    monkeypatch.setattr(eq, "MAX_REJECTIONS_PER_TRIAL", 5)
    monkeypatch.setattr(eq, "MAX_REJECTIONS_PER_ITERATION", 0)
    # Valid deals exist (so the up-front check passes) but most draws clash.
    with pytest.raises(ValueError, match="too rare"):
        calculate_equity(["AA", "AA", "AsAh, KK"], iterations=1000, seed=1)
