"""Simulation Lab (v0.5.0): statistics, reproducibility, duplicate dealing, jobs and API."""

from __future__ import annotations

import json
import math
import os
import random
import subprocess
import sys
import threading
import time

import pytest
from fastapi.testclient import TestClient

from pokerlab.api.app import create_app
from pokerlab.lab import config as C
from pokerlab.lab import jobs as J
from pokerlab.lab import report as R
from pokerlab.lab import simulate as SIM
from pokerlab.lab import stats as S
from pokerlab.lab.__main__ import main as cli
from pokerlab.lab.config import LabConfig
from pokerlab.lab.jobs import Busy, LabJobs, run
from pokerlab.lab.strategies import STRATEGIES, hand_cost_ms


def cfg(*strategies: str, **kw) -> LabConfig:
    return LabConfig.build(strategies=list(strategies), **{"hands": 400, "seed": 3, **kw})


def blocks(c: LabConfig, a: str, b: str) -> list[SIM.BlockResult]:
    return [SIM.run_block(t) for t in c.blocks(a, b)]


def wait(lab: LabJobs, job_id: str, timeout: float = 60) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        v = lab.get(job_id)
        if v["status"] not in ("queued", "running"):
            return v
        time.sleep(0.02)
    raise AssertionError("experiment did not finish")


# ----------------------------------------------------------------- distributions


@pytest.mark.parametrize(
    ("t", "df", "p"),
    [
        (0.0, 5, 1.0),
        (2.0, 10, 0.07339),
        (2.228, 10, 0.05),
        (1.96, math.inf, 0.05),
        (3.0, 3, 0.05767),
        (-2.0, 10, 0.07339),
        (12.706, 1, 0.05),
    ],  # fmt: skip
)
def test_t_two_sided_matches_tables(t, df, p):
    assert S.t_two_sided(t, df) == pytest.approx(p, abs=2e-4)


def test_betainc_edges_and_symmetry():
    assert S.betainc(2, 3, 0) == 0 and S.betainc(2, 3, 1) == 1
    for x in (0.1, 0.37, 0.8):
        assert S.betainc(2.5, 4, x) == pytest.approx(1 - S.betainc(4, 2.5, 1 - x), abs=1e-12)


def test_holm_step_down():
    assert S.holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert S.holm([0.5]) == [0.5]
    assert S.holm([0.2, 0.9, 0.6]) == pytest.approx([0.6, 1.0, 1.0])


def test_combine_averages_independent_estimates():
    c = S.combine([(10.0, 3.0, 1e6), (20.0, 4.0, 1e6)])
    assert c["bb_per_100"] == 15.0
    assert c["se"] == pytest.approx(2.5)
    assert c["low"] == pytest.approx(15 - 1.96 * 2.5, abs=0.01)


# ----------------------------------------------------------------- estimates


def test_estimate_units_scale_and_verdict():
    rng = random.Random(1)
    units = [rng.gauss(0.5, 4) for _ in range(2000)]
    e = S.estimate(units)
    m, v = S.mean_var(units)
    assert e["bb_per_100"] == pytest.approx(m * 50)  # two hands per unit
    assert e["se"] == pytest.approx(math.sqrt(v / 2000) * 50)
    assert e["hands"] == 4000 and e["method"] == "t" and e["df"] == 1999
    assert e["verdict"] == ("a_better" if e["low"] > 0 else "unclear")
    assert S.estimate([1.0])["verdict"] == "insufficient"


def test_estimate_is_exact_when_there_is_no_spread():
    e = S.estimate([0.0] * 50)
    assert (e["bb_per_100"], e["se"], e["low"], e["high"]) == (0, 0, 0, 0)
    assert e["verdict"] == "unclear" and e["p_value"] == 1.0
    assert S.estimate([1.0] * 50)["verdict"] == "a_better"


def test_interval_coverage_on_skewed_results():
    """Poker results are skewed and heavy-tailed; the t interval still covers ~95%."""
    rng = random.Random(7)
    hits = 0
    reps = 400
    for _ in range(reps):
        # Mostly small losses, occasional big wins: mean 0.
        units = [(rng.expovariate(1 / 30) - 30) if rng.random() < 0.1 else rng.gauss(0, 3)
                 for _ in range(300)]  # fmt: skip
        e = S.estimate(units)
        hits += e["low"] <= 0 <= e["high"]
    assert 0.92 <= hits / reps <= 0.98


def test_clustered_error_restores_coverage_when_sessions_drift():
    """An adaptive strategy makes units of one session alike. The iid interval is then
    too narrow; the clustered one covers about 95%."""
    rng = random.Random(11)
    reps, hit_iid, hit_cl = 300, 0, 0
    for _ in range(reps):
        units, clusters = [], []
        for g in range(20):
            effect = rng.gauss(0, 1.5)  # the session's drift
            for _ in range(50):
                units.append(effect + rng.gauss(0, 4))
                clusters.append(g)
        iid = S.estimate(units)
        cl = S.estimate(units, clusters, dependent=True)
        assert cl["method"] in ("t", "clustered") and cl["clusters"] == 20
        hit_iid += iid["low"] <= 0 <= iid["high"]
        hit_cl += cl["low"] <= 0 <= cl["high"]
    assert hit_iid / reps < 0.8  # the naive interval fails badly
    assert 0.91 <= hit_cl / reps <= 0.99


def test_few_sessions_are_flagged_not_trusted():
    units = [float(i % 7) for i in range(100)]
    e = S.estimate(units, [i // 20 for i in range(100)], dependent=True)
    assert e["dependence_warning"] and e["method"] == "t" and e["clusters"] == 5
    assert not S.estimate(units, [i // 20 for i in range(100)])["dependence_warning"]


# ----------------------------------------------------------------- config


@pytest.mark.parametrize(
    ("kw", "message"),
    [
        ({"strategies": ["advanced"]}, "between 2 and 6"),
        ({"strategies": ["a", "b", "c", "d", "e", "f", "g"]}, "between 2 and 6"),
        ({"strategies": ["advanced", "nobody"]}, "Unknown strategy"),
        ({"strategies": ["basic", "basic", "random"]}, "only once"),
        ({"strategies": ["basic", "random"], "hands": 100}, "between 200"),
        ({"strategies": ["basic", "random"], "hands": 200_000}, "between 200"),
        ({"strategies": ["basic", "random"], "mode": "best"}, "Mode"),
        ({"strategies": ["basic", "random"], "stack_bb": 75}, "Stack"),
        ({"strategies": ["basic", "random"], "session_hands": 51}, "Session"),
        ({"strategies": ["basic", "random"], "seed": -1}, "Seed"),
        ({"strategies": list(STRATEGIES)[:6], "hands": 50_000}, "too many"),
    ],
)
def test_config_validation(kw, message):
    with pytest.raises(ValueError, match=message):
        LabConfig.build(**kw)


def test_config_plan():
    c = cfg("advanced", "intermediate", "basic", hands=1002)
    assert c.hands == 1000  # rounded down to a multiple of four
    assert c.matchups == [("advanced", "intermediate"), ("advanced", "basic"),
                          ("intermediate", "basic")]  # fmt: skip
    assert c.deals == 500 and c.total_hands == 3000
    bl = c.blocks("advanced", "basic")
    assert [b.deals for b in bl] == [200, 200, 100] and [b.session for b in bl] == [0, 1, 2]
    std = cfg("basic", "random", hands=1000, mode="standard")
    assert std.deals == 1000 and all(b.deals % 2 == 0 for b in std.blocks("basic", "random"))
    # A mirror match is allowed for two strategies.
    assert cfg("basic", "basic").matchups == [("basic", "basic")]


def test_matchup_seed_does_not_depend_on_the_other_strategies():
    h2h, rr = cfg("basic", "random"), cfg("basic", "random", "passive")
    assert h2h.blocks("basic", "random") == rr.blocks("basic", "random")
    assert h2h.matchup_key("basic", "random") == rr.matchup_key("basic", "random")


def test_estimate_seconds_scales_with_cost_and_workers():
    slow = cfg("advanced", "intermediate", hands=10_000)
    fast = cfg("basic", "random", hands=10_000)
    assert slow.estimate_seconds(1) > 100 * fast.estimate_seconds(1)
    # Diminishing returns from more workers (measured: 4 workers ran 2.3x as fast as one).
    assert slow.estimate_seconds(4) == pytest.approx(slow.estimate_seconds(1) / 4**0.6)
    assert hand_cost_ms("advanced", "basic") > hand_cost_ms("intermediate", "basic")
    # Equity agents take each other's hands deep: about twice the cost (measured 61 ms).
    assert hand_cost_ms("intermediate", "intermediate_fixed") == pytest.approx(60.2)
    assert hand_cost_ms("intermediate", "basic") == pytest.approx(14.5)


def test_estimates_learn_from_finished_runs():
    lab = LabJobs(workers=0)
    c = cfg("basic", "random", hands=10_000)
    assert lab.estimate(c) == pytest.approx(c.estimate_seconds(0))
    job = J.Job(id="x", config=c, created=0, workers=0, status="done", started=100.0,
                finished=100.0 + 3 * 60, hands_run=c.total_hands)  # fmt: skip
    with monkeypatched_estimate(c, 60.0):
        lab._learn_speed(job)  # took 3x the modelled 60 s
        assert lab._speed == pytest.approx(0.6 + 0.4 * 3)
        short = J.Job(id="y", config=c, created=0, workers=0, status="done", started=0.0,
                      finished=100.0, hands_run=1)  # fmt: skip
        lab._learn_speed(short)  # predicted under 5 s: ignored
        assert lab._speed == pytest.approx(1.8)


class monkeypatched_estimate:
    """Pin LabConfig.estimate_seconds for one config (frozen dataclass: patch the class)."""

    def __init__(self, c: LabConfig, seconds: float):
        self.c, self.seconds = c, seconds

    def __enter__(self):
        self.orig = LabConfig.estimate_seconds
        target, value = self.c, self.seconds
        LabConfig.estimate_seconds = lambda s, w: value if s == target else self.orig(s, w)

    def __exit__(self, *exc):
        LabConfig.estimate_seconds = self.orig


# ----------------------------------------------------------------- simulation


def test_duplicate_deals_the_same_cards_with_seats_swapped(monkeypatch):
    seen = []
    play = SIM._play

    def record(agents, stack, button, deck, sb, bb):
        hand = play(agents, stack, button, deck, sb, bb)
        seen.append(([list(p.hole) for p in hand.players], list(hand.board), button))
        return hand

    monkeypatch.setattr(SIM, "_play", record)
    SIM.run_block(cfg("aggressive", "basic").blocks("aggressive", "basic")[0])
    assert len(seen) == 400
    for (h1, b1, bt1), (h2, b2, bt2) in zip(seen[::2], seen[1::2], strict=True):
        assert h1 == h2 and bt1 == bt2  # seat k holds the same cards at both tables
        n = min(len(b1), len(b2))
        assert b1[:n] == b2[:n]  # same board as far as both hands went
    assert {bt for *_, bt in seen} == {0, 1}


def test_a_deterministic_strategy_against_itself_cancels_exactly():
    """The calling station never randomises, so each duplicate deal sums to zero."""
    c = cfg("passive", "passive", hands=800)
    rep = R.matchup_report(c, "passive", "passive", blocks(c, "passive", "passive"))
    e = rep["estimate"]
    assert (e["bb_per_100"], e["se"], e["verdict"]) == (0, 0, "unclear")
    assert rep["variance"]["reduction"] is None  # no duplicate variance left
    assert rep["variance"]["table_correlation"] == pytest.approx(-1)
    assert rep["variance"]["unpaired_se"] > 0  # the same hands unpaired are noisy


def test_runs_are_reproducible_and_standard_is_table_one():
    c = cfg("aggressive", "folder", hands=800)
    first = blocks(c, "aggressive", "folder")
    again = [SIM.run_block(t) for t in reversed(c.blocks("aggressive", "folder"))]
    rep = R.matchup_report(c, "aggressive", "folder", first)
    assert R.matchup_report(c, "aggressive", "folder", again) == rep  # order does not matter
    std = cfg("aggressive", "folder", hands=400, mode="standard")
    sb = blocks(std, "aggressive", "folder")
    assert [list(b.table1) for b in sb] == [list(b.table1) for b in first]
    assert all(b.table2 is None for b in sb)
    other = cfg("aggressive", "folder", hands=800, seed=4)
    changed = R.matchup_report(other, "aggressive", "folder", blocks(other, "aggressive", "folder"))
    assert changed != rep


def test_counters_are_consistent():
    c = cfg("basic", "passive", hands=800)
    rep = R.matchup_report(c, "basic", "passive", blocks(c, "basic", "passive"))
    a, b = rep["styles"]["a"], rep["styles"]["b"]
    assert a["hands"] == b["hands"] == rep["hands"] == 800
    assert a["saw_flop"] == b["saw_flop"] == rep["table"]["flop"]  # heads-up: both or neither
    for side in (a, b):
        for k in ("vpip", "pfr", "saw_flop", "wtsd", "wsd"):
            assert 0 <= side[k] <= 1
        assert side["pfr"] <= side["vpip"]
    assert a["wsd"] + b["wsd"] <= 1 + 1e-9  # split pots count for neither
    assert b["vpip"] > a["vpip"]  # the calling station plays more hands
    assert rep["table"]["avg_pot_bb"] > 1


def test_trajectory_adds_up_and_keeps_extremes():
    c = cfg("aggressive", "aggressive", hands=4000)
    bl = blocks(c, "aggressive", "aggressive")
    rep = R.matchup_report(c, "aggressive", "aggressive", bl)
    tr = rep["trajectory"]
    assert tr["x_label"] == "deals" and tr["x"][-1] == 2000
    assert tr["thinned"] and len(tr["x"]) <= R.MAX_POINTS  # 2,000 deals, thinned
    for t, x, y in zip(tr["total"], tr["table1"], tr["table2"], strict=True):
        assert t == pytest.approx(x + y, abs=0.02)
    total = sum(x + y for blk in bl for x, y in zip(blk.table1, blk.table2, strict=True)) / 10
    assert tr["total"][-1] == pytest.approx(total, abs=0.01)
    # Thinning keeps every series' extremes.
    full = R._cumulative([x / 10 for blk in bl for x in blk.table1])
    idx = R._thin(len(full), [full], max_points=100)
    assert len(idx) <= 100 and max(full) in [full[i] for i in idx]
    assert min(full) in [full[i] for i in idx]
    conv = rep["convergence"]
    assert conv["paired"][-1]["hands"] == 4000 and conv["unpaired"][-1]["hands"] == 4000
    final = rep["estimate"]["bb_per_100"]
    assert conv["paired"][-1]["bb_per_100"] == pytest.approx(final, abs=1e-3)


def test_duplicate_reduces_variance_against_a_similar_opponent():
    c = cfg("aggressive", "aggressive", hands=4000)
    v = R.matchup_report(c, "aggressive", "aggressive", blocks(c, "aggressive", "aggressive"))[
        "variance"
    ]
    assert v["reduction"] > 3 and v["table_correlation"] < -0.5
    assert v["paired_se"] < v["unpaired_se"]
    assert v["equivalent_hands"] > 3 * 4000


@pytest.mark.parametrize("mode", ["duplicate", "standard"])
def test_mirror_matches_cover_zero(mode):
    """A strategy against itself (different seeds) has a true result of zero by symmetry.
    Over 40 independent seeds, the 95% interval should cover zero about 38 times."""
    hits = 0
    for seed in range(40):
        c = cfg("aggressive", "aggressive", hands=400, seed=1000 + seed, mode=mode)
        e = R.matchup_report(c, "aggressive", "aggressive",
                             blocks(c, "aggressive", "aggressive"))["estimate"]  # fmt: skip
        hits += e["low"] <= 0 <= e["high"]
    assert hits >= 34  # P(X <= 33) < 0.001 for Binomial(40, 0.95)


def test_duplicate_and_standard_estimate_the_same_thing():
    d = cfg("basic", "passive", hands=4000, seed=21)
    s = cfg("basic", "passive", hands=4000, seed=22, mode="standard")
    ed = R.matchup_report(d, "basic", "passive", blocks(d, "basic", "passive"))["estimate"]
    es = R.matchup_report(s, "basic", "passive", blocks(s, "basic", "passive"))["estimate"]
    diff = ed["bb_per_100"] - es["bb_per_100"]
    assert abs(diff) < 3 * math.hypot(ed["se"], es["se"])
    assert ed["se"] < es["se"]  # duplicate is more precise on the same number of hands


def test_adaptive_strategies_use_session_clusters():
    c = cfg("intermediate", "random", hands=200, session_hands=50, mode="standard")
    rep = R.matchup_report(c, "intermediate", "random", blocks(c, "intermediate", "random"))
    assert rep["adaptive"] and rep["sessions"] == 4
    assert rep["estimate"]["clusters"] == 4 and rep["estimate"]["dependence_warning"]
    fixed = cfg("beginner", "random", hands=200, session_hands=50, mode="standard")
    rep = R.matchup_report(fixed, "beginner", "random", blocks(fixed, "beginner", "random"))
    assert not rep["adaptive"] and not rep["estimate"]["dependence_warning"]


def test_round_robin_league_and_matrix():
    c = cfg("aggressive", "passive", "folder", hands=800)
    reports = [R.matchup_report(c, a, b, blocks(c, a, b)) for a, b in c.matchups]
    rr = R.round_robin(c, reports)
    assert rr["comparisons"] == 3 and len(rr["cells"]) == 6 and len(rr["league"]) == 3
    assert [r["rank"] for r in rr["league"]] == [1, 2, 3]
    assert rr["league"][0]["bb_per_100"] >= rr["league"][-1]["bb_per_100"]
    cells = {(x["row"], x["col"]): x for x in rr["cells"]}
    for (row, col), x in cells.items():
        y = cells[(col, row)]
        assert x["bb_per_100"] == -y["bb_per_100"] and x["low"] == -y["high"]
        assert x["p_holm"] >= x["p_value"]
    # The league entry is the mean of the strategy's matchups.
    s = rr["league"][0]["strategy"]
    mine = [x["bb_per_100"] for x in rr["cells"] if x["row"] == s]
    assert rr["league"][0]["bb_per_100"] == pytest.approx(sum(mine) / 2)
    assert R.round_robin(cfg("basic", "random"), reports[:1]) is None


# ----------------------------------------------------------------- jobs


def test_run_matches_the_job_manager_and_the_cache():
    c = cfg("aggressive", "folder", "random", hands=400)
    direct = run(c)
    lab = LabJobs(workers=0)
    try:
        v = wait(lab, lab.submit(c)["id"])
        assert v["status"] == "done" and v["report"] == direct
        assert v["progress"]["hands_done"] == 1200
        # Same experiment again: every matchup from the cache, finished at once.
        again = lab.submit(c)
        assert again["status"] == "done" and all(m["cached"] for m in again["matchups"])
        assert again["report"] == direct
        # A head-to-head inside the round robin is reused too.
        h2h = lab.submit(cfg("aggressive", "folder", hands=400))
        assert h2h["status"] == "done" and h2h["report"]["matchups"][0] == direct["matchups"][0]
        assert [j["id"] for j in lab.list()][0] == h2h["id"]
    finally:
        lab.shutdown()


def test_worker_processes_give_identical_results():
    c = cfg("aggressive", "basic", hands=800, session_hands=100)
    assert run(c, workers=2) == run(c, workers=0)


def test_cancel_keeps_finished_sessions(monkeypatch):
    gate = threading.Event()
    real = J.run_block

    def slow(task):
        if task.session >= 1:
            gate.wait(5)
        return real(task)

    monkeypatch.setattr(J, "run_block", slow)
    lab = LabJobs(workers=0)
    try:
        job = lab.submit(cfg("basic", "random", hands=800, session_hands=100))
        end = time.time() + 10
        while lab.get(job["id"])["progress"]["hands_done"] < 200 and time.time() < end:
            time.sleep(0.01)
        live = lab.get(job["id"])
        assert live["status"] == "running"
        assert live["matchups"][0]["live"]["trajectory"]["x"][-1] == 100
        lab.cancel(job["id"])
        gate.set()
        v = wait(lab, job["id"])
        assert v["status"] == "cancelled"
        m = v["report"]["matchups"][0]
        assert not m["complete"] and 200 <= m["hands"] < 800
        # A partial result is never cached.
        again = lab.submit(cfg("basic", "random", hands=800, session_hands=100))
        assert not again["matchups"][0]["cached"]
        lab.cancel(again["id"])
    finally:
        gate.set()
        lab.shutdown()


def test_queue_limit_and_queued_cancel(monkeypatch):
    gate = threading.Event()
    real = J.run_block
    monkeypatch.setattr(J, "run_block", lambda t: (gate.wait(5), real(t))[1])
    monkeypatch.setattr(J, "MAX_QUEUED", 2)
    lab = LabJobs(workers=0)
    try:
        lab.submit(cfg("basic", "random"))
        queued = lab.submit(cfg("basic", "folder"))
        with pytest.raises(Busy):
            lab.submit(cfg("basic", "passive"))
        assert lab.cancel(queued["id"])["status"] == "cancelled"
        assert lab.cancel("nope") is None and lab.get("nope") is None
    finally:
        gate.set()
        lab.shutdown()


def test_a_failing_session_fails_the_job(monkeypatch):
    def boom(task):
        raise RuntimeError("agent exploded")

    monkeypatch.setattr(J, "run_block", boom)
    lab = LabJobs(workers=0)
    try:
        v = wait(lab, lab.submit(cfg("basic", "random"))["id"])
        assert v["status"] == "failed" and "agent exploded" in v["error"]
    finally:
        lab.shutdown()


# ----------------------------------------------------------------- API


@pytest.fixture
def client():
    with TestClient(create_app(lab=LabJobs(workers=0))) as c:
        yield c


def poll(client, job_id: str) -> dict:
    end = time.time() + 60
    while time.time() < end:
        v = client.get(f"/api/lab/experiments/{job_id}").json()
        if v["status"] not in ("queued", "running"):
            return v
        time.sleep(0.02)
    raise AssertionError("experiment did not finish")


def test_api_strategies_and_estimate(client):
    info = client.get("/api/lab/strategies").json()
    ids = [s["id"] for s in info["strategies"]]
    assert {"advanced", "advanced_fixed", "intermediate", "beginner", "basic", "random"} <= set(ids)
    assert info["limits"]["max_hands"] == C.MAX_HANDS and info["workers"] == 0
    est = client.post("/api/lab/estimate", json={"strategies": ["advanced", "basic"]}).json()
    assert est["matchups"] == 1 and est["estimate_seconds"] > 100


def test_api_run_poll_export(client):
    body = {"strategies": ["passive", "folder"], "hands": 400, "seed": 9}
    job = client.post("/api/lab/experiments", json=body)
    assert job.status_code == 202
    v = poll(client, job.json()["id"])
    assert v["status"] == "done"
    m = v["report"]["matchups"][0]
    assert m["a"] == "passive" and m["hands"] == 400 and m["estimate"]["bb_per_100"] is not None
    assert v["report"]["config"]["seed"] == 9 and v["report"]["version"]
    csv = client.get(f"/api/lab/experiments/{v['id']}/deals.csv")
    assert csv.headers["content-type"].startswith("text/csv")
    lines = csv.text.strip().split("\n")
    assert lines[0].startswith("a,b,session,deal,button") and len(lines) == 1 + 200
    total = sum(float(x.split(",")[5]) + float(x.split(",")[6]) for x in lines[1:])
    assert total / 4 == pytest.approx(m["estimate"]["bb_per_100"], abs=1e-6)  # 400 hands
    assert client.get("/api/lab/experiments").json()["experiments"][0]["id"] == v["id"]
    assert client.delete(f"/api/lab/experiments/{v['id']}").json()["status"] == "done"


@pytest.mark.parametrize(
    "body",
    [
        {"strategies": ["passive", "nobody"]},
        {"strategies": ["passive"]},
        {"strategies": ["passive", "folder"], "hands": 100},
        {"strategies": ["passive", "folder"], "hands": "400"},
        {"strategies": ["passive", "folder"], "mode": "fast"},
        {"strategies": ["passive", "folder"], "stack_bb": 30},
        {"strategies": list(STRATEGIES)[:6], "hands": 100_000},
    ],
)
def test_api_rejects_bad_experiments(client, body):
    assert client.post("/api/lab/experiments", json=body).status_code == 422


def test_api_missing_experiment(client):
    assert client.get("/api/lab/experiments/nope").status_code == 404
    assert client.delete("/api/lab/experiments/nope").status_code == 404
    assert client.get("/api/lab/experiments/nope/deals.csv").status_code == 404


# ----------------------------------------------------------------- CLI


def test_cli_prints_and_writes_json(tmp_path, capsys):
    out = tmp_path / "r.json"
    assert cli(["passive", "folder", "random", "--hands", "400", "--workers", "0",
                "--json", str(out)]) == 0  # fmt: skip
    text = capsys.readouterr().out
    assert "Calling station vs Rock" in text and "Average against the field" in text
    report = json.loads(out.read_text())
    assert len(report["matchups"]) == 3 and report["round_robin"]["league"]


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX process semantics")
def test_workers_exit_when_the_server_is_killed(tmp_path):
    """A server killed outright cannot shut its pool down; the workers must not live on."""
    script = tmp_path / "parent.py"
    script.write_text(
        "import time\n"
        "from pokerlab.lab.jobs import _pool\n"
        "if __name__ == '__main__':\n"
        "    ex = _pool(2)\n"
        "    list(ex.map(abs, [1, 2, 3, 4]))\n"
        "    print(' '.join(str(p) for p in ex._processes), flush=True)\n"
        "    time.sleep(60)\n"
    )
    proc = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True,
                            cwd=os.path.dirname(os.path.dirname(__file__)))  # fmt: skip
    try:
        workers = [int(p) for p in proc.stdout.readline().split()]
        assert workers  # the pool started
        proc.kill()  # SIGKILL: no shutdown code runs
        proc.wait(5)

        def alive(pid: int) -> bool:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return False
            return True

        end = time.time() + 8
        while any(alive(p) for p in workers) and time.time() < end:
            time.sleep(0.1)
        assert not any(alive(p) for p in workers)
    finally:
        proc.kill()


def crash_once(task):
    """Session function for the restart test: the first call kills its worker process."""
    marker = os.environ["POKERLAB_TEST_CRASH_MARKER"]
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL)  # atomic: exactly one worker dies
    except FileExistsError:
        return SIM.run_block(task)
    os.close(fd)
    os._exit(1)


def test_a_dead_worker_is_replaced_and_results_are_unchanged(tmp_path, monkeypatch):
    """A worker dying (memory, an interpreter crash) breaks the pool. The unfinished
    sessions run again on a new pool; being deterministic, the result is unchanged."""
    monkeypatch.setenv("POKERLAB_TEST_CRASH_MARKER", str(tmp_path / "crashed"))
    c = cfg("aggressive", "basic", hands=800, session_hands=50)
    pool = J.WorkerPool(2)
    try:
        got = [res for _, res in J.execute(c.blocks("aggressive", "basic"), pool, fn=crash_once)]
    finally:
        pool.stop()
    assert pool.restarts == 1 and (tmp_path / "crashed").exists()
    assert sorted(r.session for r in got) == list(range(8))  # each session exactly once
    expected = R.matchup_report(c, "aggressive", "basic", blocks(c, "aggressive", "basic"))
    assert R.matchup_report(c, "aggressive", "basic", got) == expected
