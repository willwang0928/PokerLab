"""Controlled coaching scenarios (v0.4.2): explanations, realisation and opponent evidence.

Each scenario states the hand-derived numbers it relies on. Blinds are 5/10 and hero is
the big blind (seat 0) unless stated otherwise.

FLOP line: raise to 25, call (pot 50); flop check, bet 30 -> hero calls 30 into 80 and
needs 30 / 110 = 27.3% equity. TURN line adds call 30 (pot 110), check, bet 60 -> hero
calls 60 into 170 and needs 60 / 230 = 26.1%.
"""

from __future__ import annotations

import json

import pytest

from pokerlab.ai.base import Observation
from pokerlab.ai.player_model import BASELINES, MIN_OPPORTUNITIES, PRIOR_STRENGTH
from pokerlab.cards import parse_cards
from pokerlab.coach.analysis import (
    PRINCIPLES,
    REALIZATION_HIGH,
    REALIZATION_LOW,
    analyze,
)
from pokerlab.coach.review import verdict
from pokerlab.coach.spot import Spot, opponent_profile, stat_evidence
from pokerlab.engine import Action, HoldemHand

from .helpers import stacked_deck
from .test_coach import RIVER_LINE, analysed, spot

FLOP = [("raise", 25), ("call", None), ("check", None), ("bet", 30)]
TURN = [*FLOP, ("call", None), ("check", None), ("bet", 60)]
KINDS = {"exact", "estimate", "assumption", "model", "guideline"}


def lesson_ok(a) -> dict:
    """Every graded decision carries the five-step lesson, with labelled maths."""
    les = a.lesson
    assert set(les) == {"did", "known", "estimated", "math", "alternative", "principle", "also"}
    assert les["did"].startswith("You ") and a.street in les["did"]
    assert any(" ".join(a.hole) in k for k in les["known"])
    assert all(step["kind"] in KINDS for step in les["math"])
    assert les["principle"] in PRINCIPLES.values()
    return les


# ----------------------------------------------------------------- clear verdicts


def test_folding_a_big_draw_that_clears_the_price_is_a_likely_mistake():
    """9h8h on Th7c2h: flush draw plus open-ended straight draw, about 60% against the
    estimated range versus 27.3% needed. Even realising only 45% of that equity pays, so
    realisation does not soften the verdict."""
    a = analysed(spot("9h8h", "Th7c2h", FLOP), Action.fold())
    assert a.required_equity == pytest.approx(30 / 110)
    assert a.equity > 0.5 and a.realization_needed < REALIZATION_LOW
    assert a.grade == "mistake"
    les = lesson_ok(a)
    assert les["principle"] == PRINCIPLES["fold_with_odds"]
    assert {"text": "Price: you needed 27% equity (30 to win 110).", "kind": "exact"} in les["math"]
    assert any(s["kind"] == "assumption" and "45%" in s["text"] for s in les["math"])


def test_calling_with_almost_no_equity_is_a_likely_mistake():
    """4c3d on KhQs9c: no pair, no draw, about 5% equity versus 27.3% needed. The call
    would have to realise over five times its equity."""
    a = analysed(spot("4c3d", "KhQs9c", FLOP), Action.call())
    assert a.equity < 0.1 and a.realization_needed > 5
    assert a.grade == "mistake" and a.best == ["fold"]
    assert lesson_ok(a)["principle"] == PRINCIPLES["call_without_odds"]


def test_a_profitable_call_is_not_graded_down_for_not_raising():
    """9h8h calling is clearly profitable; raising scores higher only under the fold
    model. Since v0.4.5 (calibration: such raises lost to the call when hands were played
    out) that is not held against the call: it is a close call, never "fold", and the
    raise is shown as an option resting on an assumed fold rate."""
    a = analysed(spot("9h8h", "Th7c2h", FLOP), Action.call())
    assert a.option("call").ev > 0 and a.option(a.best[0]).action == "raise"
    assert a.grade == "close" and a.tags == [] and a.confidence == "low"
    les = lesson_ok(a)
    assert les["principle"] != PRINCIPLES["call_without_odds"]
    assert "assumed fold rate" in les["alternative"]
    assert "not the answer" in a.explanation


# ----------------------------------------------------------------- close decisions


def test_a_call_just_above_the_threshold_is_never_a_confident_verdict():
    """Ad5d on Th7c2s: about 30% versus 27.3% needed, so it needs about 91% realisation,
    inside the uncertain band. Neither calling nor folding may be called a mistake."""
    call = analysed(spot("Ad5d", "Th7c2s", FLOP), Action.call())
    fold = analysed(spot("Ad5d", "Th7c2s", FLOP), Action.fold())
    assert abs(call.equity - call.required_equity) < 0.05
    assert REALIZATION_LOW < call.realization_needed < REALIZATION_HIGH
    for a in (call, fold):
        assert a.grade != "mistake" and a.confidence == "low"
        lesson_ok(a)


def test_a_fold_that_depends_on_realisation_says_it_is_a_judgement_call():
    """Js9d on Th7c2s: about 34% versus 27.3%, so a call must realise about 80%. A raise
    scores best (fold model), but the fold is weighed against calling, and that comparison
    hinges on realisation: low confidence, and the lesson says why."""
    a = analysed(spot("Js9d", "Th7c2s", FLOP), Action.fold())
    assert REALIZATION_LOW < a.realization_needed < REALIZATION_HIGH
    assert a.grade != "mistake" and a.confidence == "low"
    assert "judgement call" in a.explanation
    assert PRINCIPLES["realization"] in lesson_ok(a)["also"]


def test_realisation_is_not_applied_on_the_river():
    """On the river there are no later streets: equity is realised at showdown."""
    a = analysed(spot("3sKs", "Kd9s5c2h7d", RIVER_LINE), Action.call())
    assert a.realization_needed is None
    assert not any("realise" in s["text"] for s in lesson_ok(a)["math"])


# ----------------------------------------------------------------- stacks and all-ins


def _short_spot(hero: str, board: str, stacks: tuple[int, int], bet: int) -> Spot:
    return Spot(
        hero=tuple(parse_cards(hero)),  # type: ignore[arg-type]
        board=tuple(parse_cards(board)),
        actions=(("raise", 25), ("call", None), ("check", None), ("bet", bet)),
        button=1,
        stacks=stacks,
        small_blind=5,
        big_blind=10,
        villain_stats={},
    )


def test_an_all_in_call_realises_its_equity_in_full():
    """200-chip stacks: after the 25-chip preflop pot each has 175 behind, and the flop
    bet of 175 is all-in. Calling 175 to win 50 + 175 + 175 = 400 needs 175 / 400 =
    43.75%. No more betting can follow, so no realisation assumption applies."""
    a = analysed(_short_spot("AsKd", "Kh7c2s", (200, 200), 175), Action.call())
    assert a.betting_closed and a.realization_needed is None
    assert a.required_equity == pytest.approx(175 / 400)
    assert a.grade == "good" and a.confidence == "high"
    math = lesson_ok(a)["math"]
    assert {
        "text": "Nobody can bet again, so your equity is realised in full.",
        "kind": "exact",
    } in math


def test_stack_depth_decides_whether_realisation_is_an_assumption():
    """The same flop call is all-in with 200-chip stacks but deep with 1000."""
    short = analysed(_short_spot("Js9d", "Th7c2s", (200, 200), 175), Action.call())
    deep = analysed(_short_spot("Js9d", "Th7c2s", (1000, 1000), 175), Action.call())
    assert short.betting_closed and short.realization_needed is None
    assert not deep.betting_closed and deep.realization_needed is not None
    assert short.required_equity == deep.required_equity  # same price either way


# ----------------------------------------------------------------- missing information


def test_an_unopened_pot_has_no_range_and_says_so():
    s = Spot(
        hero=tuple(parse_cards("AhKd")),  # type: ignore[arg-type]
        board=(),
        actions=(),
        button=0,
        stacks=(1000, 1000),
        small_blind=5,
        big_blind=10,
        villain_stats={},
    )
    a = analysed(s, Action.raise_to(25))
    les = lesson_ok(a)
    assert a.spot == "unopened" and a.equity is None
    assert les["principle"] == PRINCIPLES["preflop"]
    assert any("No range to calculate against" in e for e in les["estimated"])
    assert all(s["kind"] == "guideline" for s in les["math"])


# ----------------------------------------------------------------- opponent evidence


def test_tendency_evidence_matches_the_beta_posterior():
    """28 bets in 40 chances, reference 34%, prior 12 pseudo-observations:
    a = 28 + 0.34 x 12 = 32.08, b = 12 + 0.66 x 12 = 19.92, mean = 32.08 / 52 = 0.6169,
    sd = sqrt(a b / (n^2 (n + 1))) with n = 52."""
    model = spot("Kc9d", "Kh7c2s", FLOP, stats={"bet_unopposed": (28.0, 40.0)}).villain_model()
    e = stat_evidence(model, "bet_unopposed")
    a_, b_ = 28 + BASELINES["bet_unopposed"] * PRIOR_STRENGTH, 12 + (1 - 0.34) * PRIOR_STRENGTH
    n = a_ + b_
    sd = (a_ * b_ / (n * n * (n + 1))) ** 0.5
    assert e["estimate"] == pytest.approx(32.08 / 52)
    assert e["low"] == pytest.approx(32.08 / 52 - 1.2816 * sd)
    assert e["high"] == pytest.approx(32.08 / 52 + 1.2816 * sd)
    assert e["seen"] == 40 and e["reliable"] and e["used"] and e["clear"]


def test_a_small_sample_is_not_treated_as_a_read():
    """Five chances is below the 15 needed: the estimate barely moves, the interval
    contains the reference, and the coach says it is using default tendencies."""
    s = spot("Kc9d", "Kh7c2s", FLOP, stats={"bet_unopposed": (5.0, 5.0)})
    e = stat_evidence(s.villain_model(), "bet_unopposed")
    assert e["seen"] < MIN_OPPORTUNITIES and not e["reliable"] and not e["used"]
    info = s.opponent_info()
    assert info["used"] == [] and info["style"] == []
    assert "Too few hands" in info["note"] and "default tendencies" in info["note"]
    a = analysed(s, Action.call(), opponent=info)
    assert info["note"] in lesson_ok(a)["estimated"]
    # ...and the range is the same as with no information at all.
    assert a.range_size == analysed(spot("Kc9d", "Kh7c2s", FLOP), Action.call()).range_size


def test_loose_and_tight_opponents_are_described_and_change_the_range():
    loose = {"vpip": (58.0, 60.0), "aggression": (45.0, 50.0), "bet_unopposed": (35.0, 40.0)}
    tight = {"vpip": (25.0, 60.0), "aggression": (5.0, 50.0), "bet_unopposed": (4.0, 40.0)}
    lo, ti = (spot("Kc9d", "Kh7c2s", FLOP, stats=st) for st in (loose, tight))
    pl, pt = opponent_profile(lo.villain_model()), opponent_profile(ti.villain_model())
    assert {"loose", "aggressive"} <= set(pl["style"])
    assert {"tight", "passive"} <= set(pt["style"])
    a_lo, a_ti = analysed(lo, Action.call()), analysed(ti, Action.call())
    assert a_lo.range_size > a_ti.range_size  # a frequent bettor bets more hands
    assert a_lo.equity > a_ti.equity


def test_no_history_gives_an_explicit_default_note():
    info = spot("Kc9d", "Kh7c2s", FLOP).opponent_info()
    assert info["evidence"] == [] and "Nothing is known yet" in info["note"]


# ----------------------------------------------------------------- outcome independence


def _hand(villain: str, board: str) -> HoldemHand:
    return HoldemHand([1000, 1000], 1, 5, 10, deck=stacked_deck(["Ah9h", villain], board, 1))


def test_lessons_do_not_depend_on_hidden_cards_or_the_run_out():
    """Same public decision, different hidden hands and future boards: identical lesson."""
    seen = set()
    for villain, board in (("KsKd", "Th7c2h4s9d"), ("2c3d", "Th7c2hAs3h"), ("QhJh", "Th7c2hKh2d")):
        hand = _hand(villain, board)
        for act in (Action.raise_to(25), Action.call(), Action.check(), Action.bet(30)):
            hand.apply(act)
        obs = Observation.from_hand(hand, 0)
        a = analyze(obs, hand.legal_actions(), Action.call(), seed=3, iterations=1200)
        assert villain[:2] not in json.dumps(a.lesson)
        seen.add(json.dumps(a.lesson, sort_keys=True))
    assert len(seen) == 1


@pytest.mark.parametrize(
    ("quality", "net", "needle"),
    [
        ("sound", -400, "Good decisions still lose"),  # a strong hand that loses
        ("mixed", 300, "does not make a decision right"),  # a weak call that wins
    ],
)
def test_the_result_never_overrides_the_decision_verdict(quality, net, needle):
    counts = {"good": 1, "close": 0, "questionable": int(quality != "sound"), "mistake": 0}
    assert needle in verdict(quality, net, {**counts, "ungraded": 0})


def test_lessons_are_reproducible():
    runs = {
        json.dumps(analysed(spot("Js9d", "Th7c2s", FLOP), Action.fold()).to_dict(), sort_keys=True)
        for _ in range(3)
    }
    assert len(runs) == 1
