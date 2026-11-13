"""The calibration harness (v0.4.5): simulated policies, the rollout oracle and the
session pipeline, on small fixed-seed cases. The full calibration is slow and lives in
``python -m pokerlab.calibration``; these check that each piece does what it says."""

from __future__ import annotations

import random

import pytest

from pokerlab.ai.base import Observation
from pokerlab.ai.difficulty import make_agent
from pokerlab.calibration import oracle, stats_eval
from pokerlab.calibration import policies as P
from pokerlab.calibration.policies import RunConfig, SimPlayer
from pokerlab.calibration.simulate import run_session
from pokerlab.coach import leaks as L
from pokerlab.engine import Action, ActionType, HoldemHand

from .helpers import stacked_deck


def _river_spot():
    """Hero (seat 0, big blind) holds 3c2d, villain QQ on AhKdQs7c9h; villain bets 200."""
    deck = stacked_deck(["3c2d", "QhQd"], "AhKdQs7c9h", button=1)
    h = HoldemHand([1000, 1000], 1, 5, 10, deck=deck)
    for a in (Action.raise_to(25), Action.call(), Action.check(), Action.check(),
              Action.check(), Action.check(), Action.check(), Action.bet(200)):  # fmt: skip
        h.apply(a)
    return h


def test_the_oracle_matches_a_hand_computed_river_call():
    h = _river_spot()
    agents = (make_agent("intermediate", random.Random(1)), make_agent("intermediate",
              random.Random(2)))  # fmt: skip
    snap = oracle.snapshot(h, agents, 0)
    r = oracle.compare(snap, Action.call(), Action.fold(), rollouts=4, seed=3)
    # Calling loses exactly the 200 called; nothing is left to deal or decide.
    assert r.delta == pytest.approx(-200) and r.se == 0
    # The snapshot is a copy: the real hand is untouched.
    assert h.to_act == 0 and not h.is_complete


def test_policies_label_injected_deviations_and_the_baseline_action():
    h = _river_spot()
    obs, legal = Observation.from_hand(h, 0), h.legal_actions()
    station = SimPlayer(P.POLICY_BY_NAME["calling_station"], random.Random(5))
    base = SimPlayer(P.POLICY_BY_NAME["baseline"], random.Random(5))
    a_base = base.decide(obs, legal)
    assert a_base.type == ActionType.FOLD and base.labels[-1].deviation is None
    fired = 0
    for seed in range(40):
        s = SimPlayer(P.POLICY_BY_NAME["calling_station"], random.Random(seed))
        a = s.decide(obs, legal)
        lab = s.labels[-1]
        assert lab.base.type == ActionType.FOLD
        if lab.deviation:
            fired += 1
            assert a.type == ActionType.CALL and lab.deviation == "calls_too_much"
    assert 18 <= fired <= 38  # p = 0.7 of 40
    del station


def test_deviations_only_use_what_the_player_can_see():
    # Different opponent cards, same observation: the same condition outcome.
    for villain in ("QhQd", "4h5d"):
        deck = stacked_deck(["3c2d", villain], "AhKdQs7c9h", button=1)
        h = HoldemHand([1000, 1000], 1, 5, 10, deck=deck)
        for a in (Action.raise_to(25), Action.call(), Action.check(), Action.check(),
                  Action.check(), Action.check(), Action.check(), Action.bet(200)):  # fmt: skip
            h.apply(a)
        obs, legal = Observation.from_hand(h, 0), h.legal_actions()
        assert P.CALL_STATION.applies(obs, legal, Action.fold())
        assert not P.OVER_BLUFF.applies(obs, legal, Action.fold())


def test_every_policy_names_known_weaknesses():
    for spec in P.POLICIES:
        assert set(spec.targets) <= set(L.WEAKNESS_KEYS)
        assert set(spec.target_conditions) <= set(L.CONDITIONS)


def test_a_short_session_runs_through_the_real_pipeline_reproducibly():
    cfg = RunConfig(hands=4, session_hands=4, rollouts=2, oracle_per_session=2,
                    coach_iterations=300)  # fmt: skip
    a = run_session("calling_station", 0, cfg)
    b = run_session("calling_station", 0, cfg)
    assert a["summaries"] == b["summaries"] and a["oracle"] == b["oracle"]
    assert len(a["summaries"]) == 4
    n_dec = sum(len(s["decisions"]) for s in a["summaries"])
    assert len(a["labels"]) == n_dec
    # The summaries are what the leak detector reads.
    assert L.find_leaks(a["summaries"])["hands"] == 4
    for o in a["oracle"]:
        assert o["kind"] in ("deviation", "flag", "control") and o["n"] == 2


def test_synthetic_streams_have_the_requested_rate():
    hs = stats_eval.synthetic_hands({"called_without_odds": 0.3}, 4000, random.Random(1))
    flags = [d["grade"] == "questionable" for h in hs for d in h["decisions"]]
    assert sum(flags) / len(flags) == pytest.approx(0.3, abs=0.02)
    r = L.find_leaks(hs)
    assert [i["key"] for i in r["leaks"]] == ["called_without_odds"]


def test_report_aggregation_helpers():
    from pokerlab.calibration import report as R

    m, se = R.wmean([1.0, 3.0], [1.0, 1.0])
    assert m == pytest.approx(2.0) and se == pytest.approx(1.0)
    assert R.wmean([1.0], [1.0]) is None
    assert R.verdict((-5.0, 1.0), 20) == "costly" and R.verdict((5.0, 1.0), 20) == "gains"
    assert R.verdict((-5.0, 1.0), 5) == "insufficient" and R.verdict((0.5, 1.0), 20) == "unclear"
    det = {
        "bootstrap": {
            "calling_station": {
                "v0.4.5": {100: {"leak": dict.fromkeys(R.KEYS, 0.0) | {"called_without_odds": 0.8}}}
            }
        }
    }
    truth = {"calling_station": {k: {"injected": k == "called_without_odds"} for k in R.KEYS}}
    cm = R.confusion(det, truth, "v0.4.5", 100, lambda t: t["injected"])
    assert cm["tp"] == pytest.approx(0.8) and cm["fn"] == pytest.approx(0.2)
    assert cm["fp"] == 0 and cm["precision"] == 1.0 and cm["recall"] == pytest.approx(0.8)


def test_bootstrap_seeds_are_stable_across_interpreters():
    """Seeds must not depend on PYTHONHASHSEED: an earlier version used ``hash()`` and
    produced different detection tables on every run."""
    import subprocess
    import sys

    from pokerlab.calibration.report import boot_seed

    code = (
        "from pokerlab.calibration.report import boot_seed; "
        "print(boot_seed(7, 'random', 'v0.4.5', 100))"
    )
    outs = {
        subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True,
            env={**__import__("os").environ, "PYTHONHASHSEED": str(h)},
        ).stdout.strip()
        for h in (1, 2, 3)
    }  # fmt: skip
    assert outs == {str(boot_seed(7, "random", "v0.4.5", 100))}
    assert boot_seed(7, "random", "v0.4.5", 100) != boot_seed(8, "random", "v0.4.5", 100)
