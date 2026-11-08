"""Training scenarios: generation, determinism, grading and no leaked answers."""

from __future__ import annotations

import json

import pytest

from pokerlab.cards import parse_cards
from pokerlab.coach import training as T
from pokerlab.coach.spot import Spot

FAST = [
    ("beginner", "pot_odds"),
    ("beginner", "preflop"),
    ("intermediate", "value_betting"),
    ("intermediate", "draws"),
    ("advanced", "bet_sizing"),
    ("advanced", "reads"),
    ("beginner", "bluffing"),
    ("intermediate", "bluffing"),
]


@pytest.fixture(autouse=True)
def _fewer_iterations(monkeypatch):
    monkeypatch.setattr(T, "ITERATIONS", 800)
    monkeypatch.setattr(T, "FILTER_ITERATIONS", 300)
    T._generate.cache_clear()
    T._from_spot_cached.cache_clear()


@pytest.mark.parametrize(("difficulty", "concept"), FAST)
def test_generated_scenarios_teach_their_concept(difficulty, concept):
    s = T.generate(difficulty, concept, seed=3)
    a = s.analysis
    _, obs, legal = s.spot.replay()  # replays legally to the hero's decision
    assert obs.seat == 0 and len(s.choices) >= 2
    assert all(a.option(k).ev is not None for k in s.choices)
    if concept == "draws":
        assert a.concept == "draws"
    if concept == "pot_odds":
        assert a.spot == "facing_bet"
    if concept == "preflop":
        assert a.street == "preflop" and s.choices == ["fold", "call"]
    if concept == "reads":
        assert s.spot.villain_stats and a.reads
    if concept in ("value_betting", "bet_sizing"):
        assert a.spot == "unbet"
    if concept == "bluffing":
        assert a.concept == "bluffing" and a.spot == "unbet" and a.equity < 0.5
    pot = max(a.pot, 10)
    if concept in ("value_betting", "bluffing"):
        # The question is bet or check: the best bet and checking differ by the margin.
        bets = [a.option(k).ev for k in s.choices if a.option(k).action == "bet"]
        check = a.option("check").ev
        assert abs(max(bets) - check) / pot >= T.margin(concept, difficulty) - 1e-9
    else:
        # The best offered choice beats the next one by the difficulty's margin.
        evs = sorted((a.option(k).ev for k in s.choices), reverse=True)
        assert (evs[0] - evs[1]) / pot >= T.margin(concept, difficulty) - 1e-9


def test_beginner_choices_are_simple():
    s = T.generate("beginner", "pot_odds", seed=4)
    assert s.choices == ["fold", "call"]
    view = T.scenario_view(s)
    assert view["hints"] and "Pot odds" in view["hints"][0]
    assert not T.scenario_view(T.generate("advanced", "pot_odds", seed=4))["hints"]


def test_scenarios_are_deterministic_by_id():
    a = T.generate("intermediate", "value_betting", seed=11)
    T._generate.cache_clear()
    b = T.load(a.id)
    assert a.spot == b.spot and a.choices == b.choices
    assert T.answer(a.id, a.choices[0]) == T.answer(b.id, b.choices[0])


def test_view_sent_before_answering_contains_no_verdicts():
    s = T.generate("intermediate", "draws", seed=5)
    view = T.scenario_view(s)
    blob = json.dumps(view).lower()
    for leak in ('"ev"', '"best"', '"grade"', "equity", '"explanation"'):
        assert leak not in blob
    assert {c["key"] for c in view["choices"]} == set(s.choices)


def test_answer_grades_choice_and_compares_every_alternative():
    s = T.generate("beginner", "pot_odds", seed=6)
    best = max(s.choices, key=lambda k: s.analysis.option(k).ev)
    worst = min(s.choices, key=lambda k: s.analysis.option(k).ev)
    good = T.answer(s.id, best)
    assert good["correct"] and good["acceptable"]
    assert good["analysis"]["grade"] == "good"
    bad = T.answer(s.id, worst)
    assert not bad["correct"] and bad["analysis"]["grade"] in ("questionable", "mistake")
    assert [x["key"] for x in bad["alternatives"]] == s.choices
    assert {x["grade"] for x in bad["alternatives"] if x["key"] == best} == {"good"}
    with pytest.raises(T.ScenarioError):
        T.answer(s.id, "raise-99999")


def test_reads_scenarios_change_the_answer_because_of_tendencies():
    s = T.generate("advanced", "reads", seed=2)
    neutral = T._analyse(Spot(**{**T._fields(s.spot), "villain_stats": {}}), T.ITERATIONS)
    with_reads = max(s.choices, key=lambda k: s.analysis.option(k).ev)
    keys = [k for k in s.choices if k in {o.key for o in neutral.options}]
    without = max(keys, key=lambda k: neutral.option(k).ev)
    assert with_reads != without


def test_practice_from_a_real_spot_round_trips_through_its_id():
    spot = Spot(
        hero=tuple(parse_cards("9h8h")),  # type: ignore[arg-type]
        board=tuple(parse_cards("Th7c2h")),
        actions=(("raise", 25), ("call", None), ("check", None), ("bet", 30)),
        button=1,
        stacks=(1000, 1000),
        small_blind=5,
        big_blind=10,
    )
    s = T.from_spot(spot)
    assert s.id.startswith("spot.") and "fold" in s.choices and "call" in s.choices
    r = T.answer(s.id, "fold")
    assert r["analysis"]["chosen"] == "fold"
    with pytest.raises(T.ScenarioError):
        T.load("spot.not-base64!!")
    with pytest.raises(T.ScenarioError):
        T.load("v1.expert.pot_odds.1")


def test_practising_a_reviewed_decision_gives_the_reviews_verdict():
    """Training feedback and the post-hand review use one engine and agree exactly."""
    from pokerlab.ai.base import Observation
    from pokerlab.coach.review import HandCoach
    from pokerlab.engine import Action, HoldemHand

    from .helpers import stacked_deck

    coach = HandCoach(0, 1, 5, 10)
    hand = HoldemHand([1000, 1000], 1, 5, 10, deck=stacked_deck(["Ah9h", "KsKd"], "Th7c2h4s9d", 1))
    coach.start_hand(1, [1000, 1000], 1)
    hand.apply(Action.raise_to(25))
    for act in (Action.call(), Action.check()):
        coach.record(Observation.from_hand(hand, 0), hand.legal_actions(), act)
        hand.apply(act)
    hand.apply(Action.bet(30))
    odd_raise = Action.raise_to(97)  # a size that is not one of the standard options
    coach.record(Observation.from_hand(hand, 0), hand.legal_actions(), odd_raise)
    hand.apply(odd_raise)
    hand.apply(Action.fold())
    coach.finish_hand(hand.events, hand.players[0].stack - 1000)
    review = coach.review(1)
    for d in review["decisions"]:
        spot = Spot.from_dict(d["spot_data"])
        s = T.from_spot(spot, "intermediate", d["chosen"])
        assert d["chosen"] in s.choices  # the exact size played is offered
        got = T.answer(s.id, d["chosen"])["analysis"]
        assert got["grade"] == d["grade"]
        assert got["equity"] == d["equity"]
        assert {o["key"]: o["ev"] for o in got["options"]} == {
            o["key"]: o["ev"] for o in d["options"] if o["key"] in s.choices
        }
