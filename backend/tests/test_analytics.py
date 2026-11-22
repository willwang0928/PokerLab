"""Performance analytics (v0.4.8): statistics, sections, all-in adjustment and the API.

Synthetic summaries carry only the fields the analytics read; scripted hands use the
stacked-deck helpers from test_history with blinds 5/10.
"""

from __future__ import annotations

import math
import random

import pytest
from fastapi.testclient import TestClient

from pokerlab import analytics as A
from pokerlab import storage as S
from pokerlab.api.app import create_app
from pokerlab.engine import Action
from pokerlab.equity import calculate_equity
from pokerlab.history import SUMMARY_VERSION, summarize

from .test_history import MULTI, T, play

# ----------------------------------------------------------------- synthetic summaries


def summ(
    net_bb: float,
    i: int,
    *,
    session: str = "s1",
    position: str = "button",
    hand: str = "AKs",
    difficulty: str = "beginner",
    adaptive: bool = True,
    vpip: bool = True,
    pfr: bool = False,
    showdown: bool = False,
    last_street: str = "flop",
    ev_net_bb: float | None = None,
    allin: dict | None = None,
    ev_loss_bb: float | None = None,
    decisions: list | None = None,
) -> dict:
    return {
        "id": f"h{i:08d}",
        "t": T + i * 1000,
        "hand_number": i + 1,
        "session": session,
        "position": position,
        "hand_class": hand,
        "difficulty": difficulty,
        "adaptive": adaptive,
        "big_blind": 10,
        "net": net_bb * 10,
        "net_bb": net_bb,
        "ev_net_bb": net_bb if ev_net_bb is None else ev_net_bb,
        "allin": allin,
        "vpip": vpip,
        "pfr": pfr,
        "showdown": showdown,
        "won_showdown": showdown and net_bb > 0,
        "last_street": last_street,
        "ev_loss_bb": ev_loss_bb,
        "decisions": decisions or [],
    }


def normal_hands(n: int, mean: float, sd: float, seed: int, **kw) -> list[dict]:
    rng = random.Random(seed)
    return [summ(rng.gauss(mean, sd), i, **kw) for i in range(n)]


# ----------------------------------------------------------------- distributions


@pytest.mark.parametrize(
    ("p", "z"),
    [(0.975, 1.959964), (0.95, 1.644854), (0.5, 0.0), (0.001, -3.090232), (0.9999, 3.719016)],
)
def test_normal_quantiles_match_tables(p, z):
    assert A.normal_ppf(p) == pytest.approx(z, abs=1e-5)


@pytest.mark.parametrize(
    ("df", "q"),
    [(1, 12.7062), (2, 4.3027), (5, 2.5706), (10, 2.2281), (29, 2.0452), (120, 1.9799)],
)
def test_t_quantiles_match_tables(df, q):
    """97.5% points of Student's t (standard tables)."""
    assert A.t_ppf(0.975, df) == pytest.approx(q, abs=2e-3)


def test_variance_blend_moves_from_the_prior_to_the_sample():
    prior = A.PRIOR_SD_BB**2
    assert A.blended_variance(1.0, 1) == pytest.approx(prior)
    # Equal weight when n - 1 equals the prior weight.
    assert A.blended_variance(4.0, A.PRIOR_WEIGHT + 1) == pytest.approx((prior + 4.0) / 2)
    assert A.blended_variance(4.0, 1_000_000) == pytest.approx(4.0, rel=2e-3)


# ----------------------------------------------------------------- win rate


def test_win_rate_is_mean_times_100_with_a_t_interval():
    xs = [float(x) for x in range(-20, 21)] * 5  # mean 0, n = 205
    r = A.win_rate(xs)
    assert r["hands"] == 205 and r["bb_per_100"] == pytest.approx(0.0, abs=1e-9)
    n = len(xs)
    sample = sum(x * x for x in xs) / (n - 1)
    se = math.sqrt(A.blended_variance(sample, n) / n) * 100
    q = A.t_ppf(0.975, n - 1)
    assert r["se"] == pytest.approx(se)
    assert r["low"] == pytest.approx(-q * se) and r["high"] == pytest.approx(q * se)
    assert r["verdict"] == "unclear" and r["method"] == "t"


def test_small_samples_get_no_interval_or_verdict():
    r = A.win_rate([50.0] * (A.MIN_HANDS - 1))
    assert r["verdict"] == "insufficient"
    assert r["low"] is None and r["high"] is None and r["bb_per_100"] == pytest.approx(5000)


def test_a_few_lucky_hands_are_not_called_winning():
    """30 hands that all won 2 bb have a sample SD of 0; the prior keeps the interval
    honest (±~300 bb/100), so even +200 bb/100 is not yet significant; +500 is."""
    r = A.win_rate([2.0] * 30)
    assert r["verdict"] == "unclear" and r["low"] < 0 < r["high"]
    r = A.win_rate([5.0] * 30)
    assert r["verdict"] == "winning" and r["high"] - r["low"] > 60
    r = A.win_rate([0.3] * 30)
    assert r["verdict"] == "unclear" and r["hands_to_confirm"] > 0


def test_clear_winners_and_losers_are_recognised():
    win = A.win_rate([s["net_bb"] for s in normal_hands(3000, 1.0, 8, seed=1)])
    lose = A.win_rate([s["net_bb"] for s in normal_hands(3000, -1.0, 8, seed=2)])
    assert win["verdict"] == "winning" and win["low"] > 0
    assert lose["verdict"] == "losing" and lose["high"] < 0
    assert win["hands_for_precision"] == pytest.approx((1.96 * 8 * 100 / 10) ** 2, rel=0.1)


def test_interval_coverage_is_close_to_95_percent():
    """Repeated samples of 200 hands from a skewed, heavy-tailed result distribution
    (mostly small pots, occasional big ones) with a known mean."""
    rng = random.Random(7)

    def hand() -> float:
        u = rng.random()
        if u < 0.04:
            return rng.choice((-1, 1)) * rng.uniform(40, 100)
        return rng.gauss(0.2, 3.0)

    true_mean = 0.2 * 0.96  # the big pots are symmetric
    reps, covered = 600, 0
    for _ in range(reps):
        r = A.win_rate([hand() for _ in range(200)])
        covered += r["low"] <= true_mean * 100 <= r["high"]
    assert 0.92 <= covered / reps <= 0.99


def test_correlated_sessions_widen_the_interval():
    """Each session has its own offset (a stack depth or adaptation effect), so hands
    within a session are correlated: the clustered SE takes over."""
    rng = random.Random(3)
    xs, sessions = [], []
    for s in range(40):
        offset = rng.gauss(0, 3)
        for _ in range(50):
            xs.append(offset + rng.gauss(0, 2))
            sessions.append(f"s{s}")
    iid = A.win_rate(xs)
    clustered = A.win_rate(xs, sessions)
    assert clustered["method"] == "clustered" and clustered["se"] > 2 * iid["se"]
    # Too few sessions: the clustered SE is not trusted.
    few = A.win_rate(xs[:400], sessions[:400])
    assert few["method"] == "t"


def test_compare_detects_a_real_shift_but_not_noise():
    a = [s["net_bb"] for s in normal_hands(4000, -0.5, 6, seed=11)]
    b = [s["net_bb"] for s in normal_hands(4000, 0.5, 6, seed=12)]
    c = [s["net_bb"] for s in normal_hands(4000, -0.5, 6, seed=13)]
    assert A.compare(a, b)["significant"] and A.compare(a, b)["diff"] > 0
    assert not A.compare(a, c)["significant"]


# ----------------------------------------------------------------- sections


def test_bankroll_accumulates_results_and_tracks_drawdown():
    rows = [summ(x, i) for i, x in enumerate([5, -10, 3, -4, 20])]
    b = A.bankroll(rows)
    assert [p["cum"] for p in b["points"]] == [5, -5, -2, -6, 14]
    assert b["peak_bb"] == 14 and b["max_drawdown_bb"] == 11
    assert b["session_starts"] == [1] and not b["thinned"]


def test_bankroll_thinning_keeps_the_extremes_and_the_end():
    rows = normal_hands(10_000, 0.0, 5, seed=5)
    b = A.bankroll(rows, max_points=600)
    full = A.bankroll(rows, max_points=10**9)["points"]
    assert b["thinned"] and len(b["points"]) <= 700
    lo = min(full, key=lambda p: p["cum"])
    hi = max(full, key=lambda p: p["cum"])
    kept = {p["n"] for p in b["points"]}
    assert {lo["n"], hi["n"], 1, 10_000} <= kept
    assert b["points"][-1]["cum"] == full[-1]["cum"]


def test_starting_hand_grid_covers_all_169_classes():
    grid = A.all_classes_grid()
    flat = [c for row in grid for c in row]
    assert len(flat) == 169 == len(set(flat))
    assert grid[0][0] == "AA" and grid[0][1] == "AKs" and grid[1][0] == "AKo"
    assert grid[12][12] == "22" and grid[12][11] == "32o" and grid[11][12] == "32s"
    assert sum(A.combos(c) for c in flat) == 1326


def test_starting_hands_shrink_small_samples_toward_the_overall_rate():
    rows = [summ(20.0, i, hand="72o") for i in range(3)]  # three big wins with 72o
    rows += normal_hands(4000, 0.5, 8, seed=51, hand="AKs")
    rows += [summ(r["net_bb"], 5000 + i, hand="QQ")
             for i, r in enumerate(normal_hands(2000, -0.5, 8, seed=52))]  # fmt: skip
    sh = A.starting_hands(rows)
    cells = {c["hand"]: c for c in sh["cells"]}
    assert len(cells) == 169 and sh["classes_dealt"] == 3
    overall = sh["overall_bb_per_hand"]
    seven_two = cells["72o"]
    assert seven_two["bb_per_hand"] == 20 and seven_two["weight"] < 0.2
    assert abs(seven_two["shrunk"] - overall) < 0.2 * (20 - overall) + 1e-9
    assert seven_two["low"] is None  # dealt fewer than HAND_MIN_INTERVAL times
    ak = cells["AKs"]
    assert ak["weight"] > 0.95
    assert ak["shrunk"] == pytest.approx(ak["bb_per_hand"], abs=0.05)
    assert ak["low"] < ak["bb_per_hand"] < ak["high"] and ak["high"] - ak["low"] < 0.6
    assert cells["KK"]["dealt"] == 0 and cells["KK"]["shrunk"] == overall
    assert sum(c["expected"] for c in sh["cells"]) == pytest.approx(len(rows))


def test_luck_compares_all_in_results_with_their_expectation():
    # 30 all-ins at 80% equity for a 100 bb pot, all won: 20 bb above EV each, with a
    # standard deviation of 40 bb each, so z = 600 / (40 √30) ≈ 2.74.
    allin = {"equity": 0.8, "pot": 1000, "variance": 1000**2 * 0.8 * 0.2}
    rows = [summ(50.0, i, ev_net_bb=30.0, allin=allin) for i in range(30)]
    rows += [summ(0.0, 30 + i) for i in range(20)]
    lk = A.luck(rows)
    assert lk["spots"] == 30 and lk["luck_bb"] == pytest.approx(600)
    assert lk["sd_bb"] == pytest.approx(math.sqrt(30 * 40**2))
    assert lk["z"] == pytest.approx(600 / math.sqrt(48_000))
    assert lk["verdict"] == "lucky"
    # Ten of them (z ≈ 1.58) are within normal variance.
    assert A.luck(rows[:10])["verdict"] == "normal"
    assert A.luck(rows[30:])["verdict"] == "none"


def test_trend_separates_better_decisions_from_noisy_results():
    graded = [{"grade": "good"}]
    rng = random.Random(23)
    early = [summ(r["net_bb"], i, ev_loss_bb=rng.expovariate(1 / 1.5), decisions=graded * 3)
             for i, r in enumerate(normal_hands(400, 0, 8, seed=21))]  # fmt: skip
    late = [summ(r["net_bb"], 400 + i, ev_loss_bb=rng.expovariate(1 / 0.4), decisions=graded)
            for i, r in enumerate(normal_hands(400, 0, 8, seed=22))]  # fmt: skip
    tr = A.trend(early + late)
    assert tr["status"] == "ok" and tr["split_at"] == 400
    assert tr["quality"]["significant"] and tr["quality"]["diff_per_100"] < 0
    assert tr["verdict"] == "decisions_improved"
    assert not tr["results"]["significant"]


def test_trend_without_coaching_calls_a_noisy_change_variance():
    rows = normal_hands(300, 0.0, 10, seed=31)
    tr = A.trend(rows)
    assert tr["quality"] is None and tr["verdict"] == "variance"
    assert A.trend(rows[:60])["status"] == "insufficient"


def test_report_groups_by_position_and_opponent_and_filters():
    rows = normal_hands(200, 1.0, 5, seed=41, position="button", difficulty="beginner")
    rows += [
        summ(r["net_bb"], 1000 + i, position="big blind", difficulty="advanced", vpip=i % 2 == 0,
             adaptive=False)
        for i, r in enumerate(normal_hands(100, -1.0, 5, seed=42))
    ]  # fmt: skip
    rep = A.analyze(rows)
    assert rep["hands"] == 300 and rep["available_difficulties"] == ["beginner", "advanced"]
    pos = {p["position"]: p for p in rep["positions"]}
    assert pos["button"]["hands"] == 200 and pos["big blind"]["hands"] == 100
    assert pos["big blind"]["vpip"] == pytest.approx(0.5)
    opp = {o["difficulty"]: o for o in rep["opponents"]}
    assert opp["beginner"]["adaptive_share"] == 1 and opp["advanced"]["adaptive_share"] == 0
    assert [o["difficulty"] for o in rep["opponents"]] == ["beginner", "advanced"]
    only = A.analyze(rows, difficulty="advanced")
    assert only["hands"] == 100 and only["available_difficulties"] == ["beginner", "advanced"]
    assert A.analyze(rows, last=50)["hands"] == 50
    assert A.analyze(rows, last=50)["bankroll"]["points"][0]["n"] == 1


def test_an_empty_history_gives_an_empty_report():
    rep = A.analyze([])
    assert rep["hands"] == 0 and rep["overall"]["win_rate"]["verdict"] == "insufficient"
    assert rep["bankroll"]["points"] == [] and len(rep["starting_hands"]["cells"]) == 169
    assert rep["trend"]["status"] == "insufficient"


# ----------------------------------------------------------------- summaries (v3)


def test_preflop_all_in_is_adjusted_to_its_equity():
    """Villain (button) shoves 1000, hero calls with AA against KK and loses to a set:
    net -1000 (-100 bb). Hero's equity was about 82% of a 2000 pot, so the adjusted
    result is -1000 + 0.82 × 2000 ≈ +640 (+64 bb)."""
    _, rec = play(["AhAd", "KsKc"], "Kd7c2h4s9d", [Action.raise_to(1000), Action.call()],
                  coach=False)  # fmt: skip
    s = summarize(rec)
    assert s["sv"] == SUMMARY_VERSION == 3
    assert s["net"] == -1000 and s["allin"]["street"] == "preflop" and s["allin"]["pot"] == 2000
    eq = s["allin"]["equity"]
    assert 0.80 < eq < 0.84
    assert s["allin"]["adjusted"] == pytest.approx(-1000 + eq * 2000, abs=0.2)
    assert s["ev_net_bb"] == pytest.approx(s["allin"]["adjusted"] / 10)
    assert s["allin"]["variance"] == pytest.approx(2000**2 * eq * (1 - eq), rel=0.05)
    assert s["vpip"] and not s["pfr"] and s["position"] == "big blind"
    assert summarize(rec)["allin"] == s["allin"]  # deterministic (seeded by the hand id)


def test_flop_all_in_uses_the_exact_equity():
    """Preflop 30/30, then on Kd7c2h hero (AK, top pair) bets 970 all-in and QQ calls;
    the board runs out 4s 9d and hero wins 1000. Exact equity over 990 run-outs."""
    actions = [Action.raise_to(30), Action.call(), Action.bet(970), Action.call()]
    _, rec = play(["AhKh", "QsQd"], "Kd7c2h4s9d", actions, coach=False)
    s = summarize(rec)
    exact = calculate_equity(["AhKh", "QsQd"], board="Kd7c2h", method="exact").players[0]
    assert s["net"] == 1000 and s["allin"]["street"] == "flop"
    assert s["allin"]["equity"] == pytest.approx(exact.equity, abs=1e-4)
    assert s["allin"]["adjusted"] == pytest.approx(1000 - 2000 + exact.equity * 2000, abs=0.2)
    assert s["ev_net_bb"] < s["net_bb"]  # hero won more than the expectation


def test_hands_without_a_reveal_keep_their_result():
    _, rec = play(["AhKh", "QsQd"], "Kd7c2h4s9d", MULTI, coach=False)
    s = summarize(rec)
    assert s["allin"] is None and s["ev_net_bb"] == s["net_bb"] == 15
    assert s["vpip"] and not s["pfr"]  # the big blind called a raise
    assert s["stack_bb"] == 100 and s["adaptive"] is True and not s["won_showdown"]


def test_button_raise_counts_as_vpip_and_pfr():
    """Hero (seat 0) on the button raises and the big blind folds."""
    _, rec = play(["AhKh", "7c2d"], "", [Action.raise_to(30), Action.fold()], button=0,
                  coach=False)  # fmt: skip
    s = summarize(rec)
    assert s["position"] == "button" and s["vpip"] and s["pfr"] and s["net"] == 10


# ----------------------------------------------------------------- API


@pytest.fixture
def client(tmp_path):
    return TestClient(
        create_app(progress=S.ProgressStore(str(tmp_path / "p.sqlite3")), allow_seed=True)
    )


def auth(c: TestClient) -> dict:
    return {"Authorization": f"Bearer {c.post('/api/profiles', json={'name': 'A'}).json()['key']}"}


def _records(n: int, sid: str) -> list[dict]:
    out = []
    for i in range(n):
        _, rec = play(["AhKh", "QsQd"], "Kd7c2h4s9d", MULTI, coach=False, sid=f"{sid}-{i}")
        rec["t"] = T + i
        out.append(rec)
    return out


def test_analytics_api_requires_a_profile(client):
    assert client.get("/api/profile/analytics").status_code == 401


def test_analytics_api_reports_saved_hands_and_refreshes_after_uploads(client):
    h = auth(client)
    empty = client.get("/api/profile/analytics", headers=h).json()
    assert empty["hands"] == 0 and empty["truncated"] is False
    recs = _records(4, "a")
    assert (
        client.post("/api/profile/hands", json={"hands": recs[:3]}, headers=h).json()["added"] == 3
    )
    rep = client.get("/api/profile/analytics", headers=h).json()
    assert rep["hands"] == 3 and rep["overall"]["win_rate"]["total_bb"] == pytest.approx(45)
    assert rep["overall"]["win_rate"]["verdict"] == "insufficient"
    cells = {c["hand"]: c for c in rep["starting_hands"]["cells"]}
    assert cells["AKs"]["dealt"] == 3 and len(cells) == 169
    assert rep["positions"][1]["position"] == "big blind" and rep["positions"][1]["hands"] == 3
    assert rep["opponents"][0]["difficulty"] == "beginner"
    # A new upload invalidates the cached report.
    client.post("/api/profile/hands", json={"hands": recs[3:]}, headers=h)
    assert client.get("/api/profile/analytics", headers=h).json()["hands"] == 4
    assert client.get("/api/profile/analytics?difficulty=advanced", headers=h).json()["hands"] == 0
    assert client.get("/api/profile/analytics?last=2", headers=h).json()["hands"] == 2


def test_analytics_api_validates_its_filters(client):
    h = auth(client)
    assert client.get("/api/profile/analytics?difficulty=expert", headers=h).status_code == 422
    assert client.get("/api/profile/analytics?last=0", headers=h).status_code == 422


def test_v2_summaries_are_upgraded_for_the_analytics(client):
    """A hand stored with a v0.4.4 summary (no vpip, no all-in fields) is re-summarised
    when the analytics read it."""
    h = auth(client)
    _, rec = play(["AhAd", "KsKc"], "Kd7c2h4s9d", [Action.raise_to(1000), Action.call()],
                  coach=False)  # fmt: skip
    old = summarize(rec)
    for k in ("adaptive", "stack_bb", "vpip", "pfr", "won_showdown", "allin", "ev_net_bb"):
        old.pop(k)
    old["sv"] = 2
    store = client.app.state.progress if hasattr(client.app.state, "progress") else None
    if store is None:
        client.get("/api/health")
        store = client.app.state.progress
    p = store.profile(h["Authorization"].split()[1])
    assert store.add_hands(p, [(rec, old)]) == 1
    rep = client.get("/api/profile/analytics", headers=h).json()
    assert rep["luck"]["spots"] == 1 and rep["luck"]["luck_bb"] < -150
    assert rep["overall"]["vpip"] == 1
