"""Coaching: decision analysis, hand reviews and their guarantees."""

from __future__ import annotations

import json
import random

import pytest

from pokerlab.ai.base import Observation
from pokerlab.ai.range_model import estimate_range
from pokerlab.cards import parse_cards
from pokerlab.coach.analysis import (
    analyze,
    chosen_key,
    fold_rate,
    grade_option,
    with_chosen,
)
from pokerlab.coach.review import HandCoach, verdict
from pokerlab.coach.spot import Spot, SpotError, villain_reads
from pokerlab.engine import Action, HoldemHand
from pokerlab.equity import calculate_equity

from .helpers import stacked_deck

RIVER_LINE = [
    ("raise", 25), ("call", None), ("check", None), ("bet", 30), ("call", None),
    ("check", None), ("bet", 60), ("call", None), ("check", None), ("bet", 200),
]  # fmt: skip


def spot(hero: str, board: str, actions, button: int = 1, stats=None) -> Spot:
    return Spot(
        hero=tuple(parse_cards(hero)),  # type: ignore[arg-type]
        board=tuple(parse_cards(board)),
        actions=tuple(actions),
        button=button,
        stacks=(1000, 1000),
        small_blind=5,
        big_blind=10,
        villain_stats=stats or {},
    )


def analysed(s: Spot, action: Action, **kw):
    _, obs, legal = s.replay()
    kw.setdefault("adjust", s.villain_adjustments())
    return analyze(obs, legal, action, seed=s.seed(), iterations=kw.pop("iterations", 2000), **kw)


# ----------------------------------------------------------------- grading


def test_calling_a_river_bet_with_no_equity_is_a_likely_mistake():
    a = analysed(spot("3c2d", "AhKdQs7c9h", RIVER_LINE), Action.call())
    assert a.equity_method == "exact" and a.equity == pytest.approx(0.0, abs=1e-9)
    assert a.grade == "mistake" and a.confidence == "high"
    assert a.tags == ["called_without_odds"]
    assert a.best == ["fold"]
    assert a.option("call").ev == pytest.approx(-200.0)


def test_folding_the_nuts_on_the_river_is_a_likely_mistake_and_calling_is_good():
    s = spot("AsAc", "AhAdKs7c2h", RIVER_LINE)  # quads
    fold = analysed(s, Action.fold())
    assert fold.grade == "mistake" and "folded_with_odds" in fold.tags
    call = with_chosen(fold, "call")
    assert call.option("call").ev > 400
    # Raising scores even higher, but only under an assumed fold rate: never a "mistake".
    assert call.best[0].startswith("raise") or call.best[0] == "all-in"
    assert call.grade in ("good", "close", "questionable")
    assert call.tags in ([], ["missed_value"])


def test_pot_odds_and_call_ev_follow_the_documented_formulas():
    a = analysed(spot("9h8h", "Th7c2h", [("raise", 25), ("call", None), ("check", None),
                                         ("bet", 30)]), Action.call())  # fmt: skip
    assert a.pot == 80 and a.to_call == 30
    assert a.required_equity == pytest.approx(30 / 110)
    assert a.option("call").ev == pytest.approx(a.equity * 110 - 30)
    assert a.option("fold").ev == 0.0


def test_equity_matches_the_engine_against_the_documented_range():
    s = spot("AhQd", "Qs7d2c4h5s", RIVER_LINE)
    _, obs, legal = s.replay()
    a = analyze(obs, legal, Action.call(), seed=1)
    rng = estimate_range(obs.actions, 1, obs.button, 10, obs.board, known=obs.hole)
    exact = calculate_equity([obs.hole, rng], board=obs.board).players[0].equity
    assert a.equity == pytest.approx(exact) and a.range_size == len(rng)


def test_bet_ev_uses_the_capped_minimum_defence_fold_rate():
    assert fold_rate(50, 100) == pytest.approx(50 / 150)
    assert fold_rate(100, 100) == pytest.approx(0.5)
    assert fold_rate(500, 100) == pytest.approx(0.5)  # overbets never assume more folds
    s = spot("AhAd", "As7c2h", [("raise", 25), ("call", None), ("check", None)], button=0)
    a = analysed(s, Action.check())
    for o in a.options:
        if o.basis == "fold model":
            assert o.fold_rate == pytest.approx(fold_rate(o.amount, a.pot))


def test_huge_overbets_are_not_modelled_and_not_graded():
    s = spot("AhAd", "As7c2h", [("raise", 25), ("call", None), ("check", None)], button=0)
    a = analysed(s, Action.all_in())
    assert a.chosen == "all-in" and a.option("all-in").basis == "not modelled"
    assert a.grade == "ungraded" and "cannot estimate" in a.explanation


def test_fold_model_comparisons_are_never_called_mistakes():
    # Checking a set: betting scores better, but only under an assumed fold rate.
    s = spot("AhAd", "As7c2h", [("raise", 25), ("call", None), ("check", None)], button=0)
    a = analysed(s, Action.check())
    assert a.best and a.best[0].startswith("bet")
    assert a.grade in ("close", "questionable") and a.confidence == "low"


def test_a_pure_bluff_that_only_breaks_even_is_not_shown_as_best():
    a = analysed(spot("3c2d", "AhKdQs7c9h", RIVER_LINE), Action.fold())
    assert a.grade == "good" and a.best == ["fold"]


def test_unopened_preflop_uses_the_labelled_guideline():
    fold_72 = analysed(spot("7h2d", "", [], button=0), Action.fold())
    assert fold_72.spot == "unopened" and fold_72.grade == "good"
    assert fold_72.headline.endswith("(guideline)") and fold_72.confidence == "low"
    fold_aj = analysed(spot("AhJd", "", [], button=0), Action.fold())
    assert fold_aj.grade == "questionable" and fold_aj.tags == ["preflop_tight"]
    raise_aj = with_chosen(fold_aj, "raise-25")
    assert raise_aj.grade == "good"


def test_preflop_reraise_is_a_guideline_not_a_fold_model_bet():
    s = spot("8d2c", "", [("raise", 40)], button=1)  # needs 37.5% to call
    a = analysed(s, Action.raise_to(120))
    reraise = a.option("raise-120")
    assert reraise.basis == "guideline" and reraise.ev is None
    assert a.grade == "questionable" and a.tags == ["preflop_loose"]
    aa = analysed(spot("AsAh", "", [("raise", 25)], button=1), Action.raise_to(75))
    assert aa.grade == "good"


def test_grades_never_claim_optimality():
    texts = []
    for s, act in [
        (spot("3c2d", "AhKdQs7c9h", RIVER_LINE), Action.call()),
        (spot("AsAc", "AhAdKs7c2h", RIVER_LINE), Action.fold()),
        (spot("7h2d", "", [], button=0), Action.call()),
    ]:
        a = analysed(s, act)
        texts += [a.headline, a.explanation, *a.details, *a.caveats]
    blob = " ".join(texts).lower()
    for word in ("optimal", "gto", "correct play", "solver says"):
        assert word not in blob


def test_chosen_key_maps_table_actions_to_options():
    s = spot("9h8h", "Th7c2h", [("raise", 25), ("call", None), ("check", None), ("bet", 30)])
    _, _, legal = s.replay()
    assert chosen_key(Action.fold(), legal) == "fold"
    assert chosen_key(Action.call(), legal) == "call"
    assert chosen_key(Action.raise_to(100), legal) == "raise-100"
    assert chosen_key(Action.all_in(), legal) == "all-in"


def test_analysis_is_reproducible_for_a_seed():
    s = spot("9h8h", "Th7c2h", [("raise", 25), ("call", None), ("check", None), ("bet", 30)])
    a, b = analysed(s, Action.call()), analysed(s, Action.call())
    assert a.to_dict() == b.to_dict()


# ----------------------------------------------------------------- information


def _hand_with(villain: str, board: str):
    deck = stacked_deck(["Ah9h", villain], board, button=1)
    return HoldemHand([1000, 1000], 1, 5, 10, deck=deck)


def test_analysis_does_not_depend_on_the_opponents_hidden_cards():
    results = []
    for villain in ("KsKd", "2c3d", "QhJh"):
        hand = _hand_with(villain, "Th7c2h4s9d")
        for act in (Action.raise_to(25), Action.call(), Action.check(), Action.bet(30)):
            hand.apply(act)
        obs = Observation.from_hand(hand, 0)
        a = analyze(obs, hand.legal_actions(), Action.call(), seed=5, iterations=1500)
        results.append(json.dumps(a.to_dict(), sort_keys=True))
    assert len(set(results)) == 1


def test_review_grades_do_not_depend_on_the_outcome():
    """Same decisions, different hidden cards and run-outs: identical grades."""
    reviews = []
    for villain, board in (("KsKd", "Th7c2h4s9d"), ("2c3d", "Th7c2hAs3h")):
        coach = HandCoach(0, 1, 5, 10, iterations=1200)
        hand = _hand_with(villain, board)
        coach.start_hand(1, [1000, 1000], 1)
        hand.apply(Action.raise_to(25))
        for act in (Action.call(),):
            coach.record(Observation.from_hand(hand, 0), hand.legal_actions(), act)
            hand.apply(act)
        # flop: hero checks, villain bets, hero calls
        coach.record(Observation.from_hand(hand, 0), hand.legal_actions(), Action.check())
        hand.apply(Action.check())
        hand.apply(Action.bet(30))
        coach.record(Observation.from_hand(hand, 0), hand.legal_actions(), Action.call())
        hand.apply(Action.call())
        while not hand.is_complete:
            hand.apply(Action.check())
        net = hand.players[0].stack - 1000
        coach.finish_hand(hand.events, net)
        reviews.append(coach.review(1))
    a, b = reviews
    assert a["net"] != b["net"]  # the results differ...
    assert a["decisions"] == b["decisions"]  # ...the decision reviews do not
    assert a["counts"] == b["counts"] and a["quality"] == b["quality"]
    assert a["hindsight"] and b["hindsight"]  # both reached showdown


def test_villain_model_only_learns_after_the_hand_and_from_public_events():
    coach = HandCoach(0, 1, 5, 10, iterations=800)
    hand = _hand_with("KsKd", "Th7c2h4s9d")
    coach.start_hand(1, [1000, 1000], 1)
    hand.apply(Action.raise_to(25))
    coach.record(Observation.from_hand(hand, 0), hand.legal_actions(), Action.fold())
    assert coach.villain_model.hands == 0
    hand.apply(Action.fold())
    coach.finish_hand(hand.events, -10)
    assert coach.villain_model.hands == 1
    assert coach.villain_model.stats["pfr"].count == 1


def test_review_only_for_the_latest_finished_hand():
    coach = HandCoach(0, 1, 5, 10, iterations=500)
    assert coach.review(1) is None
    hand = _hand_with("KsKd", "")
    coach.start_hand(1, [1000, 1000], 1)
    hand.apply(Action.raise_to(25))
    coach.record(Observation.from_hand(hand, 0), hand.legal_actions(), Action.fold())
    assert coach.review(1) is None  # still in progress
    hand.apply(Action.fold())
    coach.finish_hand(hand.events, -10)
    r = coach.review(1)
    assert r is not None and r["hand_number"] == 1 and len(r["decisions"]) == 1
    assert coach.review(2) is None


def test_record_ignores_the_opponents_observation():
    coach = HandCoach(0, 1, 5, 10)
    hand = _hand_with("KsKd", "")
    coach.start_hand(1, [1000, 1000], 1)
    coach.record(Observation.from_hand(hand, 1), hand.legal_actions(), Action.call())
    assert coach._hand is not None and coach._hand.decisions == []


# ----------------------------------------------------------------- verdicts


def test_verdict_separates_decision_quality_from_the_result():
    sound = {"good": 2, "close": 0, "questionable": 0, "mistake": 0, "ungraded": 0}
    bad = {"good": 0, "close": 0, "questionable": 1, "mistake": 1, "ungraded": 0}
    lost_well = verdict("sound", -120, sound)
    assert "lost 120" in lost_well and "held up" in lost_well and "short run" in lost_well
    won_badly = verdict("costly", 300, bad)
    assert "won 300" in won_badly and "2 decisions gave up value" in won_badly
    assert "does not make a decision right" in won_badly


# ----------------------------------------------------------------- spots


def test_spot_round_trips_and_rejects_bad_input():
    s = spot("9h8h", "Th7c2h", [("raise", 25), ("call", None), ("check", None), ("bet", 30)],
             stats={"fold_to_bet": (10.0, 20.0)})  # fmt: skip
    assert Spot.from_dict(json.loads(json.dumps(s.to_dict()))) == s
    bad = s.to_dict()
    with pytest.raises(SpotError):
        Spot.from_dict({**bad, "hero": "9h9h"})
    with pytest.raises(SpotError):
        Spot.from_dict({**bad, "actions": [["raise", 25], ["raise", 26]]}).replay()
    with pytest.raises(SpotError):
        Spot.from_dict({**bad, "board": "Th7c"})
    with pytest.raises(SpotError):
        Spot.from_dict({**bad, "board": "Th7c2h5d"}).replay()  # turn card before the turn
    with pytest.raises(SpotError):
        Spot.from_dict({**bad, "villain_stats": {"nonsense": [1, 2]}})


def test_observed_tendencies_change_the_range_and_are_explained():
    line = [("raise", 25), ("call", None), ("check", None), ("bet", 30)]
    plain = spot("Kc9d", "Kh7c2s", line)
    maniac = spot("Kc9d", "Kh7c2s", line, stats={"bet_unopposed": (28.0, 40.0)})
    a, b = analysed(plain, Action.call()), analysed(maniac, Action.call())
    assert b.range_size > a.range_size  # a frequent bettor's bets are read as wider
    assert b.equity > a.equity
    reads = villain_reads(maniac.villain_model(), "Dealer Bot")
    assert reads and "bets when checked to 62%" in reads[0] and "wider" in reads[0]
    assert any("keeps their strongest" in s for s in b.range_steps)


def test_grade_option_can_regrade_every_alternative():
    a = analysed(spot("9h8h", "Th7c2h", [("raise", 25), ("call", None), ("check", None),
                                         ("bet", 30)]), Action.call())  # fmt: skip
    grades = {o.key: with_chosen(a, o.key).grade for o in a.options}
    assert grades["fold"] in ("questionable", "mistake")
    assert set(a.best) <= {k for k, g in grades.items() if g == "good"}
    grade_option(a, "fold")
    assert a.chosen == "fold"


@pytest.mark.parametrize("seed", range(6))
def test_random_spots_always_produce_consistent_analyses(seed):
    """Fuzz: play random lines; every decision analyses without error and sensibly."""
    rng = random.Random(seed)
    from pokerlab.ai.random_agent import RandomAgent

    agent = RandomAgent(rng)
    deck_rng = random.Random(seed + 100)
    hand = HoldemHand([1000, 1000], seed % 2, 5, 10, rng=deck_rng)
    checked = 0
    while not hand.is_complete:
        legal = hand.legal_actions()
        obs = Observation.from_hand(hand, hand.to_act)
        action = agent.decide(obs, legal)
        a = analyze(obs, legal, action, seed=seed, iterations=400)
        assert a.grade in ("good", "close", "questionable", "mistake", "ungraded")
        assert a.ev_loss >= 0
        if a.chosen in a.best:
            assert a.grade in ("good", "close") or a.spot == "unopened"
        checked += 1
        hand.apply(action)
    assert checked >= 1
