"""Held-out training evaluation (v0.4.5), checked against simulated learners whose true
behaviour is known: a memoriser, a genuine learner, a player whose raw score only moves
because the difficulty changed, and one who does not change at all."""

from __future__ import annotations

import random

import pytest

from pokerlab.coach import evaluation as E
from pokerlab.coach.leaks import answer_kinds

T0 = 1.8e12


def answers_from(spec, seed=1):
    """spec: list of (concept, difficulty, p_correct, kind) per answer, in time order."""
    rng = random.Random(seed)
    out, pool = [], []
    for i, (concept, diff, p, kind) in enumerate(spec):
        if kind == "repeat" and pool:
            sid = rng.choice(pool)
        elif kind == "own":
            sid = f"spot.abc{i}"
        else:
            sid = f"v2.{diff}.{concept}.{rng.randrange(10**9)}"
            pool.append(sid)
        out.append({"id": sid, "t": T0 + i * 1000, "concept": concept, "difficulty": diff,
                    "grade": "good" if rng.random() < p else "mistake"})  # fmt: skip
    return out


def test_answer_kinds_separate_new_repeated_and_own_hands():
    a = [{"id": "v2.x.pot_odds.1"}, {"id": "v2.x.pot_odds.2"}, {"id": "v2.x.pot_odds.1"},
         {"id": "spot.zzz"}]  # fmt: skip
    assert answer_kinds(a) == ["new", "new", "repeat", "own_hand"]


def test_cmh_matches_a_hand_computed_value():
    # One stratum: late 15/20, early 8/20. N=40, m1=23, E=20*23/40=11.5,
    # V=20*20*23*17/(1600*39)=2.5064; stat=(|15-11.5|-.5)^2/V=3.5907; p=.0581
    r = E.cmh([(15, 20, 8, 20)])
    assert r["statistic"] == pytest.approx(3.5907, abs=1e-3)
    assert r["p_value"] == pytest.approx(0.0581, abs=1e-3)
    assert r["difference"] == pytest.approx(0.35)
    assert E.cmh([])["p_value"] == 1.0


def test_too_few_new_answers_is_still_evaluating():
    r = E.evaluate_training(answers_from([("pot_odds", "beginner", 0.9, "new")] * 10))
    assert r["status"] == "evaluating" and r["needed"] == E.MIN_NEW - 10


def test_a_genuine_learner_improves_on_new_situations():
    spec = [("pot_odds", "intermediate", 0.35, "new")] * 60
    spec += [("pot_odds", "intermediate", 0.85, "new")] * 60
    r = E.evaluate_training(answers_from(spec))
    assert r["status"] == "improving" and r["held_out"]["difference"] > 0.3


def test_a_memoriser_improves_only_on_repeats_and_is_not_called_improving():
    spec = []
    for i in range(160):
        late = i >= 80
        if i % 2:
            spec.append(("pot_odds", "intermediate", 0.95 if late else 0.4, "repeat"))
        else:
            spec.append(("pot_odds", "intermediate", 0.45, "new"))
    r = E.evaluate_training(answers_from(spec, seed=3))
    assert r["status"] == "no_clear_change"
    assert r["familiar"]["late"]["rate"] > r["familiar"]["early"]["rate"] + 0.3


def test_a_difficulty_change_alone_is_not_mistaken_for_decline():
    # Constant skill per level (80% beginner, 50% advanced); Auto difficulty moves the
    # player up. The raw rate falls, the stratified comparison sees no change.
    spec = [("pot_odds", "beginner", 0.8, "new") if i < 70 or i % 4 == 0
            else ("pot_odds", "advanced", 0.5, "new") for i in range(140)]  # fmt: skip
    r = E.evaluate_training(answers_from(spec, seed=5))
    raw = r["held_out"]
    assert raw["late"]["rate"] < raw["early"]["rate"] - 0.1
    assert r["status"] == "no_clear_change"


def test_no_real_change_rarely_reports_one():
    hits = 0
    for seed in range(60):
        spec = [("pot_odds", "intermediate", 0.6, "new")] * 80
        if E.evaluate_training(answers_from(spec, seed=seed))["status"] != "no_clear_change":
            hits += 1
    assert hits <= 6  # nominal 5% of 60 = 3; generous for a fixed-seed check


def test_table_change_is_measured_before_and_after_the_first_practice():
    ans = answers_from([("pot_odds", "beginner", 0.5, "new")] * 5)
    start = ans[0]["t"]

    def summary(t, flagged):
        g = "mistake" if flagged else "good"
        return {"t": t, "decisions": [{"grade": g, "confidence": "high"}]}

    hs = [summary(start - 1000 - i, i % 2 == 0) for i in range(20)]
    hs += [summary(start + 1000 + i, i % 10 == 0) for i in range(20)]
    r = E.evaluate_training(ans, hs)
    assert r["table"]["before"]["opportunities"] == 20
    assert r["table"]["change"] == "improving"
