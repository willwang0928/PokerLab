"""Validation of the coach against hand-derived numbers and its stated guarantees (v0.4.1).

The expected values in this file are derived by hand from the rules of the game, not by
running PokerLab code, so they check the implementation rather than restate it.
"""

from __future__ import annotations

import pytest

from pokerlab.cards import parse_cards
from pokerlab.coach import training as T
from pokerlab.coach.analysis import RANGE_VARIANTS, analyze, grade_option
from pokerlab.coach.spot import Spot
from pokerlab.engine import Action

from .test_coach import RIVER_LINE, analysed, spot

# RIVER_LINE, worked out by hand (blinds 5/10, hero = big blind, seat 0):
#   preflop raise to 25, call          -> pot 50
#   flop bet 30, call                  -> pot 110
#   turn bet 60, call                  -> pot 230
#   river: hero checks, villain bets 200 -> pot 430, hero must call 200
POT, CALL = 430, 200


def test_a_board_that_plays_for_everyone_splits_exactly():
    """Royal flush on the board: every hand ties, whatever the range, so equity is exactly
    1/2 and EV(call) = 1/2 x (430 + 200) - 200 = +115 chips."""
    a = analysed(spot("2c3d", "AhKhQhJhTh", RIVER_LINE), Action.fold())
    assert a.pot == POT and a.to_call == CALL
    assert a.equity == pytest.approx(0.5, abs=1e-12) and a.equity_method == "exact"
    assert a.required_equity == pytest.approx(200 / 630)
    assert a.option("call").ev == pytest.approx(115.0)
    assert all(rc["equity"] == pytest.approx(0.5, abs=1e-12) for rc in a.range_check)
    # Folding gives up 115 chips = 26.7% of the pot on pot odds alone (call vs fold), true
    # for every range: a robust likely mistake. (A raise is also modelled and may score
    # higher through the fold model, which caps confidence at medium.)
    assert a.robust is True and a.grade == "mistake" and a.confidence in ("medium", "high")


def test_the_nuts_win_the_whole_pot_against_any_range():
    """Hero has the nut straight on a board where no flush or full house is possible:
    equity 1, EV(call) = 430 + 200 - 200 = +430 chips."""
    a = analysed(spot("JcTd", "Kd9s5c2hQh", RIVER_LINE), Action.call())
    assert a.equity == pytest.approx(1.0) and a.option("call").ev == pytest.approx(430.0)
    # Calling the nuts is never a likely mistake; raising scores more (missed value).
    assert a.grade != "mistake" and a.option(a.best[0]).action == "raise"
    f = analysed(spot("JcTd", "Kd9s5c2hQh", RIVER_LINE), Action.fold())
    assert f.grade == "mistake" and f.robust is True


def test_a_verdict_that_depends_on_the_range_is_flagged_and_explained():
    """Top pair facing three barrels: about 43% against the default range (31.7% needed),
    but nothing against a tighter one and far more against a looser one. The coach must
    not present the call as a settled, high-confidence answer."""
    a = analysed(spot("3sKs", "Kd9s5c2h7d", RIVER_LINE), Action.call())
    eq = {rc["name"]: rc["equity"] for rc in a.range_check}
    assert eq["tighter"] < a.required_equity < a.equity < eq["looser"]
    assert a.robust is False and a.confidence == "low"
    assert "depends on the range assumption" in a.explanation
    # The opposite choice can never be called a likely mistake either.
    f = analysed(spot("3sKs", "Kd9s5c2h7d", RIVER_LINE), Action.fold())
    assert f.grade != "mistake" and f.confidence == "low"


def test_range_variants_are_narrower_and_wider_than_the_estimate():
    a = analysed(spot("3sKs", "Kd9s5c2h7d", RIVER_LINE), Action.call())
    sizes = {rc["name"]: rc["range_size"] for rc in a.range_check}
    assert [v.name for v in RANGE_VARIANTS] == ["tighter", "looser"]
    assert sizes["tighter"] < a.range_size < sizes["looser"]


def test_a_short_blind_all_in_is_a_pot_odds_decision_with_the_excess_returned():
    """Hero is the button with 8 chips: posts the 5 small blind, 3 behind. The big blind
    is 10, so 2 chips of it can never be matched and go back. The pot hero can win is
    5 + 8 = 13 (not 15), and calling 3 needs 3 / (13 + 3) = 18.75% (not 3/18 = 16.7%)."""
    s = Spot(
        hero=tuple(parse_cards("7c2d")),  # type: ignore[arg-type]
        board=(),
        actions=(),
        button=0,
        stacks=(8, 1000),
        small_blind=5,
        big_blind=10,
    )
    _, obs, legal = s.replay()
    assert legal.call_amount == 3 and not legal.can_raise
    a = analyze(obs, legal, Action.fold(), seed=1, iterations=3000)
    assert a.spot == "facing_bet"  # not the unopened-pot guideline
    assert a.pot == 13 and a.required_equity == pytest.approx(3 / 16)
    # 72o has about 32-35% against any hand: folding gives up chips.
    assert a.best == ["call"] and a.grade in ("questionable", "mistake")


def test_high_confidence_is_only_given_to_robust_pot_odds_verdicts():
    for hero in ("2c3d", "3sKs", "JcTd", "Ah9h", "5d5h", "Qc8c"):
        for action in (Action.fold(), Action.call()):
            a = analysed(spot(hero, "Kd9s5c2h7d", RIVER_LINE), action)
            if a.confidence == "high":
                assert a.robust is True
                assert a.option(a.chosen).basis == "pot odds"
            if a.grade == "mistake":
                assert a.robust is not False


@pytest.mark.parametrize("concept", ["pot_odds", "draws", "value_betting"])
def test_training_answers_do_not_depend_on_the_range_assumption(concept):
    for seed in range(3):
        s = T.generate("beginner", concept, seed)
        a = T._restricted(s.analysis, s.choices)
        best = max(s.choices, key=lambda k: a.option(k).ev or 0.0)
        assert grade_option(a, best).robust is True


def test_old_scenario_ids_still_load_and_keep_their_id():
    s = T.load("v1.beginner.pot_odds.4")
    assert s.id == "v1.beginner.pot_odds.4"
    assert T.load("v2.beginner.pot_odds.4").spot == s.spot
    assert T.load("v2.beginner.pot_odds.4").id == "v2.beginner.pot_odds.4"
    assert T.load("v2.beginner.pot_odds.4.river").id == "v2.beginner.pot_odds.4.river"
    assert T.load("v3.beginner.pot_odds.4").spot == s.spot


@pytest.mark.parametrize("concept", ["pot_odds", "draws"])
def test_facing_a_bet_a_fold_model_raise_is_never_the_taught_answer(concept):
    """v0.4.5 calibration: raises valued only by the fold model lost to calling or
    folding when hands were played out, so training never teaches one as right."""
    for seed in (1, 2, 3):
        s = T.generate("intermediate", concept, seed)
        a = s.analysis
        best = max(s.choices, key=lambda k: a.option(k).ev or 0.0)
        assert a.option(best).basis == "pot odds"


def test_a_hand_that_is_all_on_the_board_is_labelled_as_such():
    """Trips on the board are shared by everyone; calling them "your three of a kind"
    misleads beginners into overrating the hand."""
    line = [("call", None), ("check", None), ("check", None), ("check", None),
            ("check", None), ("check", None), ("check", None), ("bet", 10)]  # fmt: skip
    a = analysed(spot("7c2c", "Kd9s4sKsKc", line), Action.call())
    assert a.hand == "Three of a Kind, Kings (on the board)"
    # Even against any two cards this kicker wins under 10%, below the 25% needed.
    assert a.required_equity == pytest.approx(10 / 40) and a.grade == "mistake"
    b = analysed(spot("Ah9h", "Kd9s4sKs2c", line), Action.call())
    assert "on the board" not in b.hand
