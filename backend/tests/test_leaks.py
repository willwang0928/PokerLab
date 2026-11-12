"""Personalised leak detection (v0.4.4): evidence rules, small samples, ambiguity,
outcome independence, hidden information, trends and practice tracking."""

from __future__ import annotations

import json
import math

import pytest

from pokerlab.coach import leaks as L
from pokerlab.coach import training

T0 = 1.8e12


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "data" / "progress.sqlite3")


def dec(
    action="call",
    *,
    street="river",
    spot="facing_bet",
    grade="good",
    tags=(),
    confidence="high",
    robust=True,
    pot=200,
    to_call=50,
    stack=900,
    equity=0.3,
    required=0.25,
    i=0,
):
    return {
        "i": i,
        "street": street,
        "spot": spot,
        "action": action,
        "grade": grade,
        "confidence": confidence,
        "robust": robust,
        "tags": list(tags),
        "concept": "pot_odds",
        "pot": pot,
        "to_call": to_call,
        "stack": stack,
        "equity": equity,
        "required": required,
        "ev_loss": 0,
    }


def bad_call(**kw):
    return dec("call", grade="mistake", tags=("called_without_odds",), equity=0.15, **kw)


def bad_fold(**kw):
    return dec("fold", grade="mistake", tags=("folded_with_odds",), equity=0.45, **kw)


def hand(n, decisions, *, difficulty="intermediate", net=0, opp=None):
    return {
        "id": f"h{n:023d}",
        "t": T0 + n * 60_000,
        "hand_number": n,
        "session": "s",
        "difficulty": difficulty,
        "big_blind": 10,
        "hero_cards": ["Ah", "Kd"],
        "opponent_cards": opp,
        "board": [],
        "net": net,
        "net_bb": net / 10,
        "result": "won" if net > 0 else "lost" if net < 0 else "even",
        "decisions": decisions,
    }


def hands(decisions, **kw):
    """One hand per decision, in time order."""
    return [hand(n + 1, [d], **kw) for n, d in enumerate(decisions)]


def item(report, key):
    for group in ("leaks", "strengths", "watch", "other"):
        for i in report[group]:
            if i["key"] == key:
                return i
    return None


# --------------------------------------------------------------------------- statistics


def test_wilson_and_two_proportion_match_hand_computed_values():
    # 4/10 at 90%: p=.4, z²=2.7057; centre (.4+.13528)/1.27057=.42131,
    # half = 1.6449*sqrt(.024+.006764)/1.27057 = .22706
    low, high = L.wilson(4, 10)
    assert low == pytest.approx(0.19425, abs=1e-4) and high == pytest.approx(0.64837, abs=1e-4)
    # 0/25: upper bound z²/(n+z²) = 2.7057/27.7057
    assert L.wilson(0, 25)[1] == pytest.approx(2.7057 / 27.7057, abs=1e-4)
    assert L.wilson(0, 0) == (0.0, 1.0)
    # 8/16 vs 1/16: pooled .28125, se = sqrt(.28125*.71875*(2/16)) = .15899, z = 2.75
    assert L.two_proportion(8, 16, 1, 16)
    # 5/16 vs 3/16: z = .79, not significant
    assert not L.two_proportion(5, 16, 3, 16)
    assert not L.two_proportion(0, 10, 0, 10) and not L.two_proportion(3, 0, 1, 5)


# --------------------------------------------------------------------------- small samples


def test_an_empty_history_is_insufficient_not_clean():
    r = L.find_leaks([])
    assert r["status"] == "insufficient" and r["needed"] == L.MIN_GRADED
    assert r["leaks"] == [] and r["strengths"] == []


def test_a_few_flagged_hands_are_never_a_leak():
    r = L.find_leaks(hands([bad_call(), bad_call(), dec("call")]))
    i = item(r, "called_without_odds")
    assert i["status"] == "possible" and r["leaks"] == []
    assert any("more chances" in m for m in i["missing"])


def test_a_high_rate_from_few_opportunities_is_not_a_leak():
    # 3 of 4 flagged: a 75% rate, but only 4 chances.
    r = L.find_leaks(hands([bad_call(), bad_call(), bad_call(), dec("call")]))
    assert item(r, "called_without_odds")["status"] == "possible"


def test_a_low_rate_over_many_opportunities_is_normal_variation():
    # 3 of 40 (7.5%): the 90% lower bound is ~3%, below 15%.
    r = L.find_leaks(hands([bad_call()] * 3 + [dec("call")] * 37))
    i = item(r, "called_without_odds")
    assert i["status"] == "possible" and "within normal variation" in i["missing"][0]


def test_few_opportunities_without_flags_are_insufficient():
    r = L.find_leaks(hands([dec("call")] * 4))
    assert item(r, "called_without_odds")["status"] == "insufficient"


# --------------------------------------------------------------------------- detection


def test_a_recurring_weakness_with_enough_evidence_is_a_leak_with_its_hands():
    ds = [bad_call() if n % 2 == 0 else dec("call") for n in range(12)]  # 6 of 12
    hs = hands(ds)
    r = L.find_leaks(hs)
    i = item(r, "called_without_odds")
    assert i["status"] == "leak" and r["leaks"][0]["key"] == "called_without_odds"
    assert (i["flagged"], i["opportunities"]) == (6, 12)
    assert i["low"] >= L.MIN_RATE
    # The supporting hands are exactly the flagged ones, newest first.
    flagged_ids = {h["id"] for h, d in zip(hs, ds, strict=True) if d["grade"] == "mistake"}
    assert {h["id"] for h in i["hands"]} == flagged_ids
    assert [h["t"] for h in i["hands"]] == sorted((h["t"] for h in i["hands"]), reverse=True)
    assert all(h["kind"] == "flagged" and h["decision"] == 0 for h in i["hands"])
    # The recommendation is the weakness's own concept.
    assert i["recommendation"]["concept"] == "pot_odds"


def test_opportunities_are_only_the_decisions_where_the_weakness_can_happen():
    # Many folds and checks do not dilute a calling leak.
    ds = [bad_call()] * 4 + [dec("call")] * 6
    ds += [dec("fold")] * 40 + [dec("check", spot="unbet")] * 40
    i = item(L.find_leaks(hands(ds)), "called_without_odds")
    assert i["opportunities"] == 10 and i["status"] == "leak"


def test_a_preflop_tag_does_not_count_towards_a_postflop_weakness():
    pre = dec("call", street="preflop", grade="mistake", tags=("called_without_odds",))
    i = item(L.find_leaks(hands([pre] * 12)), "called_without_odds")
    assert i is None or i["opportunities"] == 0


# --------------------------------------------------------------------------- ambiguity


def test_low_confidence_flags_are_uncertain_never_a_leak():
    ds = [bad_call(confidence="low")] * 6 + [dec("call")] * 6
    r = L.find_leaks(hands(ds))
    i = item(r, "called_without_odds")
    assert i["status"] == "uncertain" and i["flagged"] == 0 and i["uncertain"] == 6
    assert r["leaks"] == [] and "assumption" in i["missing"][0]


def test_range_dependent_flags_are_uncertain():
    ds = [bad_call(confidence="medium", robust=False)] * 6 + [dec("call")] * 6
    assert item(L.find_leaks(hands(ds)), "called_without_odds")["status"] == "uncertain"


def test_summaries_without_a_robustness_field_rely_on_confidence():
    # v0.4.3 summaries have no "robust": medium/high confidence still counts.
    ds = [{k: v for k, v in bad_call().items() if k != "robust"}] * 6 + [dec("call")] * 6
    assert item(L.find_leaks(hands(ds)), "called_without_odds")["status"] == "leak"


def test_a_tag_on_a_decision_graded_good_is_ignored():
    contradictory = dec("call", grade="good", tags=("called_without_odds",))
    i = item(L.find_leaks(hands([contradictory] * 12)), "called_without_odds")
    assert i["flagged"] == 0 and i["uncertain"] == 0 and i["status"] == "ok"


def test_ungraded_decisions_are_not_opportunities():
    ds = [dec("call", grade="ungraded")] * 30 + [bad_call()] * 2
    r = L.find_leaks(hands(ds))
    assert r["decisions"] == 2 and item(r, "called_without_odds")["opportunities"] == 2


def test_malformed_decision_fields_do_not_crash():
    weird = {"grade": "mistake", "tags": None, "street": None, "pot": "x", "stack": True}
    r = L.find_leaks([hand(1, [weird, {}]), {"id": "x", "t": T0, "decisions": None}])
    assert r["hands"] == 2


def test_errors_in_both_directions_are_reported_as_mixed_pot_odds():
    ds = [bad_call(), bad_fold(), dec("call"), dec("fold")] * 4  # 4 + 4 flagged of 16
    r = L.find_leaks(hands(ds))
    mixed = item(r, "pot_odds_mixed")
    assert mixed["status"] == "leak" and mixed["split"] == {"calls": 4, "folds": 4}


def test_errors_in_one_direction_are_not_called_mixed():
    ds = [bad_call(), dec("call"), dec("fold"), dec("fold")] * 6  # 6 of 12 calls
    r = L.find_leaks(hands(ds))
    assert item(r, "pot_odds_mixed") is None
    assert item(r, "called_without_odds")["status"] == "leak"


# --------------------------------------------------------------------------- strengths


def test_a_strength_needs_many_opportunities_and_a_confidently_low_rate():
    assert item(L.find_leaks(hands([dec("call")] * 25)), "called_without_odds")["status"] == (
        "strength"
    )
    # 15 clean calls: enough to look, not enough to call it a strength.
    assert item(L.find_leaks(hands([dec("call")] * 15)), "called_without_odds")["status"] == "ok"
    # Guideline-only (low-confidence) decisions can never be flagged: they are no strength.
    guide = [dec("call", street="preflop", spot="unopened", grade="good", confidence="low")] * 40
    i = item(L.find_leaks(hands(guide)), "preflop_loose")
    assert i["status"] == "ok" and i["judged"] == 0
    # Uncertain flags count against a strength.
    ds = [dec("call")] * 22 + [bad_call(confidence="medium", robust=False)] * 3
    assert item(L.find_leaks(hands(ds)), "called_without_odds")["status"] != "strength"
    ds = [dec("call")] * 22 + [bad_call(confidence="low")] * 3
    assert item(L.find_leaks(hands(ds)), "called_without_odds")["status"] != "strength"


# --------------------------------------------------------------------------- outcomes, hidden cards


def test_results_and_opponent_cards_do_not_change_the_report():
    ds = [bad_call() if n % 3 == 0 else dec("call") for n in range(30)]
    base = hands(ds)
    won = [dict(h, net=500, net_bb=50, result="won", opponent_cards=["2c", "7d"]) for h in base]
    lost = [dict(h, net=-500, net_bb=-50, result="lost") for h in base]

    def strip(r):
        return json.dumps(r, sort_keys=True)

    assert strip(L.find_leaks(base)) == strip(L.find_leaks(won)) == strip(L.find_leaks(lost))


def test_the_report_never_contains_opponent_cards():
    ds = [bad_call() if n % 2 == 0 else dec("call") for n in range(20)]
    hs = [dict(h, opponent_cards=["Qs", "Jc"]) for h in hands(ds)]
    text = json.dumps(L.find_leaks(hs))
    assert "Qs" not in text and "Jc" not in text and "opponent_cards" not in text


def test_input_order_does_not_matter():
    ds = [bad_call() if n % 2 == 0 else dec("call") for n in range(20)]
    hs = hands(ds)
    assert L.find_leaks(hs) == L.find_leaks(list(reversed(hs)))


# --------------------------------------------------------------------------- concentration


def test_a_leak_names_the_condition_it_is_concentrated_in_with_a_practice_focus():
    river = [bad_call(street="river")] * 6 + [dec("call", street="river")] * 2
    turn = [dec("call", street="turn")] * 12
    i = item(L.find_leaks(hands(river + turn)), "called_without_odds")
    keys = [c["key"] for c in i["conditions"]]
    assert "river" in keys
    assert i["recommendation"]["focus"] == "river"


def test_a_condition_spread_evenly_is_not_reported():
    ds = [bad_call(street="river"), dec("call", street="river"), bad_call(street="turn"),
          dec("call", street="turn")] * 4  # fmt: skip
    i = item(L.find_leaks(hands(ds)), "called_without_odds")
    assert all(c["key"] not in ("river", "turn") for c in i["conditions"])


def test_opponent_level_concentration():
    vs_adv = hands([bad_call()] * 7 + [dec("call")] * 2, difficulty="advanced")
    vs_beg = [
        dict(h, id=f"b{n:023d}", t=T0 + 10**8 + n)
        for n, h in enumerate(hands([dec("call")] * 12, difficulty="beginner"))
    ]
    i = item(L.find_leaks(vs_adv + vs_beg), "called_without_odds")
    assert "vs_advanced" in [c["key"] for c in i["conditions"]]
    # No focus: the generator cannot reproduce an opponent level exactly.
    assert all(c["focus"] is None for c in i["conditions"] if c["key"] == "vs_advanced")


def test_focuses_are_only_offered_for_concepts_the_generator_supports():
    for focus, concepts in L.FOCUS_CONCEPTS.items():
        assert focus in training.FOCUSES
        assert set(concepts) == set(training.FOCUSES[focus])


# --------------------------------------------------------------------------- change over time


def test_fewer_flags_recently_is_reported_as_improving_only_when_significant():
    early = [bad_call()] * 8 + [dec("call")] * 2
    late = [dec("call")] * 10
    i = item(L.find_leaks(hands(early + late)), "called_without_odds")
    assert i["status"] == "leak" and i["trend"]["change"] == "improving"
    # A small gap is not a trend.
    ds = [bad_call(), dec("call")] * 5 + [bad_call(), dec("call"), dec("call")] * 3 + [dec("call")]
    i = item(L.find_leaks(hands(ds)), "called_without_odds")
    assert i["trend"]["change"] == "no_clear_change"


def test_trends_need_enough_opportunities_on_each_side():
    i = item(L.find_leaks(hands([bad_call()] * 5 + [dec("call")] * 7)), "called_without_odds")
    assert i["status"] == "leak" and i["trend"]["change"] == "insufficient"


# --------------------------------------------------------------------------- practice


def test_practice_answers_aimed_at_a_leak_are_tracked_with_the_table_since():
    early = [bad_call()] * 8 + [dec("call")] * 2
    late = [dec("call")] * 10
    hs = hands(early + late)
    start = hs[10]["t"] - 1  # first practice just before the later hands
    leak = "called_without_odds"
    answers = [
        {"id": "a1", "t": start, "grade": "mistake", "leak": leak},
        {"id": "a2", "t": start + 1, "grade": "good", "leak": leak},
        {"id": "a3", "t": start + 2, "grade": "close", "leak": leak},
        {"id": "a4", "t": start + 3, "grade": "good"},  # not aimed at it
        {"id": "a5", "t": start + 4, "grade": "good", "leak": "missed_value"},
    ]
    p = item(L.find_leaks(hs, answers), "called_without_odds")["practice"]
    assert p["answers"] == 3 and p["score"] == pytest.approx(0.5)
    assert p["first_at"] == start
    assert p["since"]["before"] == {"flagged": 8, "opportunities": 10, "rate": 0.8}
    assert p["since"]["after"]["opportunities"] == 10 and p["since"]["change"] == "improving"


def test_no_practice_yet():
    hs = hands([bad_call()] * 6 + [dec("call")] * 6)
    p = item(L.find_leaks(hs), "called_without_odds")["practice"]
    assert p == {"answers": 0, "score": None, "recent_score": None, "first_at": None,
                 "last_at": None, "since": None}  # fmt: skip


# --------------------------------------------------------------------------- recommendations


@pytest.mark.parametrize("w", L.WEAKNESSES, ids=lambda w: w.key)
def test_every_weakness_practises_a_real_training_concept(w):
    assert w.concept in training.CONCEPTS
    assert w.practice


def test_each_leak_recommends_its_own_concept_not_a_generic_one():
    value = dec("check", spot="unbet", street="river", grade="mistake", tags=("missed_value",),
                equity=0.7, to_call=0)  # fmt: skip
    ds = [value, dec("check", spot="unbet", equity=0.7, to_call=0)] * 6
    r = L.find_leaks(hands(ds))
    assert [i["key"] for i in r["leaks"]] == ["missed_value"]
    assert r["leaks"][0]["recommendation"]["concept"] == "value_betting"


def test_report_numbers_are_finite():
    ds = [bad_call() if n % 2 else dec("call") for n in range(40)]
    for group in ("leaks", "strengths", "watch", "other"):
        for i in L.find_leaks(hands(ds))[group]:
            for k in ("low", "high"):
                assert math.isfinite(i[k])


# --------------------------------------------------------------------------- focused scenarios


def test_a_river_focus_puts_the_decision_on_the_river_and_reloads_from_its_id():
    s = training.generate("beginner", "pot_odds", 3, "river")
    assert s.analysis.street == "river" and s.focus == "river"
    assert s.id == "v2.beginner.pot_odds.3.river"
    again = training.load(s.id)
    assert again.spot == s.spot and again.choices == s.choices
    view = training.scenario_view(s)
    assert view["focus"] == "river" and "verdict" not in json.dumps(view)
    # Graded by the same analysis as any scenario.
    assert training.answer(s.id, s.choices[0])["analysis"]["grade"] in (
        "good", "close", "questionable", "mistake")  # fmt: skip


def test_a_large_bet_focus_faces_at_least_three_quarters_of_the_pot():
    for seed in (1, 2):
        a = training.generate("intermediate", "pot_odds", seed, "large_bets").analysis
        before = a.pot - a.to_call
        assert a.to_call / before >= 0.74


def test_unfocused_scenario_ids_are_unchanged():
    s = training.generate("beginner", "pot_odds", 3)
    assert s.id == "v2.beginner.pot_odds.3" and s.focus is None
    assert training.scenario_view(s)["focus"] is None


def test_a_focus_the_generator_cannot_reproduce_is_refused():
    with pytest.raises(training.ScenarioError):
        training.generate("beginner", "preflop", 1, "river")
    with pytest.raises(training.ScenarioError):
        training.load("v2.beginner.pot_odds.3.nonsense")
    with pytest.raises(training.ScenarioError):
        training.load("v1.beginner.pot_odds.3.river")


# --------------------------------------------------------------------------- API


def _api(db_path):
    from fastapi.testclient import TestClient

    from pokerlab import storage as S
    from pokerlab.api.app import create_app

    store = S.ProgressStore(db_path)
    c = TestClient(create_app(progress=store, allow_seed=True))
    key = c.post("/api/profiles", json={"name": "Ana"}).json()["key"]
    return c, store, {"Authorization": f"Bearer {key}"}, key


def test_the_leak_endpoint_over_real_hands(db_path):
    from tests.test_history import MULTI, play

    c, _, h, _ = _api(db_path)
    _, rec = play(["AhKh", "QsQd"], "Kd7c2h4s9d", MULTI)
    assert c.post("/api/profile/hands", json={"hands": [rec]}, headers=h).json()["added"] == 1
    r = c.get("/api/profile/leaks", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["hands"] == 1 and body["status"] == "insufficient" and body["leaks"] == []
    assert "QsQd" not in r.text and "Qs" not in json.dumps(body["other"])
    assert c.get("/api/profile/leaks").status_code == 401


def test_practice_answers_carry_the_leak_they_targeted(db_path):
    c, store, h, key = _api(db_path)
    good = {"id": "v2.beginner.pot_odds.3.river", "t": T0, "concept": "pot_odds",
            "difficulty": "beginner", "grade": "good", "leak": "called_without_odds"}  # fmt: skip
    bad = dict(good, id="x2", leak="not_a_leak")
    r = c.post("/api/profile/records", json={"answers": [good, bad]}, headers=h).json()
    assert r["accepted"]["answers"] == 1 and len(r["rejected"]) == 1
    p = store.profile(key)
    assert store.focused_answers(p) == [good]
    # Answers without the field (v0.4.3 and earlier) still validate and are not "focused".
    old = {k: v for k, v in good.items() if k != "leak"} | {"id": "old1"}
    assert (
        c.post("/api/profile/records", json={"answers": [old]}, headers=h).json()["accepted"][
            "answers"
        ]
        == 1
    )
    assert len(store.focused_answers(p)) == 1


def test_the_report_is_cached_until_the_profile_changes(db_path, monkeypatch):
    from pokerlab.api import app as app_module

    c, _, h, _ = _api(db_path)
    calls = []
    real = app_module.find_leaks
    monkeypatch.setattr(app_module, "find_leaks", lambda *a: calls.append(1) or real(*a))
    c.get("/api/profile/leaks", headers=h)
    c.get("/api/profile/leaks", headers=h)
    assert len(calls) == 1
    ans = {"id": "a1", "t": T0, "concept": "pot_odds", "difficulty": "beginner",
           "grade": "good", "leak": "folded_with_odds"}  # fmt: skip
    c.post("/api/profile/records", json={"answers": [ans]}, headers=h)
    c.get("/api/profile/leaks", headers=h)
    assert len(calls) == 2


def test_summaries_from_v0_4_3_are_rebuilt_once_with_robustness(db_path):
    from pokerlab.history import summarize
    from tests.test_history import MULTI, play

    c, store, h, key = _api(db_path)
    _, rec = play(["AhKh", "QsQd"], "Kd7c2h4s9d", MULTI)
    old = summarize(rec)
    old.pop("sv")
    for d in old["decisions"]:
        d.pop("robust")
    p = store.profile(key)
    assert store.add_hands(p, [(rec, old)]) == 1
    listed = c.get("/api/profile/hands", headers=h).json()["hands"][0]
    assert listed["sv"] == 2 and all("robust" in d for d in listed["decisions"])
    stored = store.hand_summaries(p, 5)[0]
    assert stored == summarize(rec)  # saved, so it is rebuilt only once
    assert store.hand(p, rec["id"]) == rec  # the record itself is untouched


def test_a_reset_clears_the_report(db_path):
    from tests.test_history import MULTI, play

    c, _, h, _ = _api(db_path)
    _, rec = play(["AhKh", "QsQd"], "Kd7c2h4s9d", MULTI)
    c.post("/api/profile/hands", json={"hands": [rec]}, headers=h)
    assert c.get("/api/profile/leaks", headers=h).json()["hands"] == 1
    assert c.delete("/api/profile", headers=h).status_code == 204
    assert c.get("/api/profile/leaks", headers=h).status_code == 401


def test_the_scenario_endpoint_accepts_a_focus(db_path):
    c, _, _, _ = _api(db_path)
    r = c.get("/api/training/scenario?difficulty=beginner&concept=pot_odds&seed=3&focus=river")
    assert r.status_code == 200 and r.json()["street"] == "river"
    assert r.json()["focus_label"] == "on the river"
    bad = c.get("/api/training/scenario?difficulty=beginner&concept=preflop&seed=3&focus=river")
    assert bad.status_code == 400
    assert c.get("/api/training/scenario?concept=pot_odds&focus=turn").status_code == 422
