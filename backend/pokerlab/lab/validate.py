"""Validate the Simulation Lab's statistics and measure its efficiency.

``python -m pokerlab.lab.validate [--reps 200] [--workers 6]`` writes
``docs/lab/validation.json`` and ``docs/lab/validation.md``.

1. **Coverage.** A strategy against itself (different seeds) has a true result of zero
   by symmetry. Over many independent seeds, the share of 95% intervals that contain
   zero should be about 95%, in duplicate and standard dealing, and for an adaptive
   strategy with the session-clustered error.
2. **Duplicate efficiency and speed.** For a range of pairings: the estimate, the
   variance reduction from duplicate dealing (from the same hands), and hands per
   second per core.
3. **Agreement.** Duplicate and standard dealing estimate the same quantity.

Everything is seeded: the same command gives the same numbers for a given version.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any

from .. import __version__
from . import report as R
from .config import BlockTask, LabConfig
from .jobs import WorkerPool, default_workers, execute
from .simulate import BlockResult
from .strategies import get

COVERAGE = [("aggressive", 400), ("basic", 400), ("folder", 400), ("random", 400)]
ADAPTIVE_COVERAGE = ("intermediate", 1200, 60)  # strategy, hands, session deals (10 sessions)
PAIRINGS = [
    ("aggressive", "basic", 20_000),
    ("passive", "folder", 20_000),
    ("basic", "random", 20_000),
    ("basic", "basic", 20_000),
    ("beginner", "basic", 4_000),
    ("intermediate", "basic", 4_000),
    ("intermediate", "intermediate_fixed", 10_000),
    ("advanced", "intermediate", 4_000),
]
AGREEMENT = [("aggressive", "basic", 20_000), ("basic", "passive", 20_000)]


def _binom_interval(k: int, n: int) -> tuple[float, float]:
    """Wilson 95% interval for a coverage proportion."""
    if n == 0:
        return (0.0, 1.0)
    z = 1.959964
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def build(reps: int, adaptive_reps: int) -> list[tuple[str, LabConfig]]:
    plans: list[tuple[str, LabConfig]] = []
    for s, hands in COVERAGE:
        for mode in ("duplicate", "standard"):
            for r in range(reps):
                plans.append((f"cov|{s}|{mode}", LabConfig.build(
                    strategies=[s, s], hands=hands, mode=mode, seed=10_000 + r)))  # fmt: skip
    s, hands, session = ADAPTIVE_COVERAGE
    for r in range(adaptive_reps):
        plans.append((f"cov|{s}|duplicate", LabConfig.build(
            strategies=[s, s], hands=hands, seed=20_000 + r, session_hands=session)))  # fmt: skip
    for a, b, hands in PAIRINGS:
        plans.append((f"pair|{a}|{b}", LabConfig.build(strategies=[a, b], hands=hands, seed=1)))
    for a, b, hands in AGREEMENT:
        plans.append((f"agree|{a}|{b}", LabConfig.build(
            strategies=[a, b], hands=hands, seed=2, mode="standard")))  # fmt: skip
        plans.append((f"agree|{a}|{b}", LabConfig.build(strategies=[a, b], hands=hands, seed=2)))
    return plans


def run_plans(plans: list[tuple[str, LabConfig]], workers: int) -> list[list[BlockResult]]:
    results: list[list[BlockResult]] = [[] for _ in plans]
    owner: dict[BlockTask, int] = {}
    tasks = []
    for i, (_, c) in enumerate(plans):
        for a, b in c.matchups:
            for t in c.blocks(a, b):
                owner[t] = i
                tasks.append(t)
    started, done = time.perf_counter(), 0
    pool = WorkerPool(workers) if workers > 0 else None
    try:
        for t, res in execute(tasks, pool):
            results[owner[t]].append(res)
            done += 1
            if done % 50 == 0 or done == len(tasks):
                rate = done / (time.perf_counter() - started)
                print(f"\r  {done}/{len(tasks)} sessions ({rate:.1f}/s)", end="", flush=True)
        print()
    finally:
        if pool is not None:
            print(f"  pool restarts after a worker died: {pool.restarts}")
            pool.stop()
    return results


def summarise(plans, results) -> dict[str, Any]:
    coverage: dict[str, dict] = {}
    pairings: list[dict] = []
    agreement: dict[str, dict] = {}
    for (key, c), blocks in zip(plans, results, strict=True):
        a, b = c.matchups[0]
        rep = R.matchup_report(c, a, b, blocks)
        e = rep["estimate"]
        kind = key.split("|")[0]
        cpu = sum(blk.elapsed for blk in blocks)
        if kind == "cov":
            row = coverage.setdefault(key, {"strategy": a, "mode": c.mode, "hands": c.hands,
                                            "adaptive": rep["adaptive"], "reps": 0, "covered": 0,
                                            "se": [], "clustered": 0, "warned": 0})  # fmt: skip
            row["reps"] += 1
            row["covered"] += e["low"] <= 0 <= e["high"]
            row["se"].append(e["se"])
            row["clustered"] += e["method"] == "clustered"
            row["warned"] += e["dependence_warning"]
        elif kind == "pair":
            v = rep["variance"] or {}
            pairings.append({
                "a": a, "b": b, "a_label": get(a).label, "b_label": get(b).label,
                "hands": rep["hands"], "bb_per_100": e["bb_per_100"], "low": e["low"],
                "high": e["high"], "se": e["se"], "method": e["method"], "verdict": e["verdict"],
                "unpaired_se": v.get("unpaired_se"), "reduction": v.get("reduction"),
                "table_correlation": v.get("table_correlation"),
                "hands_per_cpu_second": rep["hands"] / cpu if cpu else None,
            })  # fmt: skip
        else:
            agreement.setdefault(f"{a}|{b}", {"a_label": get(a).label, "b_label": get(b).label})[
                c.mode
            ] = {"bb_per_100": e["bb_per_100"], "se": e["se"], "hands": rep["hands"]}
    for row in coverage.values():
        ses = row.pop("se")
        row["share"] = row["covered"] / row["reps"]
        row["share_interval"] = _binom_interval(row["covered"], row["reps"])
        row["mean_se"] = sum(ses) / len(ses)
    for row in agreement.values():
        d, s = row["duplicate"], row["standard"]
        row["difference"] = d["bb_per_100"] - s["bb_per_100"]
        row["difference_se"] = math.hypot(d["se"], s["se"])
        row["se_ratio"] = s["se"] / d["se"] if d["se"] else None
    return {"coverage": list(coverage.values()), "pairings": pairings,
            "agreement": list(agreement.values())}  # fmt: skip


def _f(x, d=1, sign=True):
    if x is None:
        return "–"
    return f"{x:+.{d}f}" if sign else f"{x:.{d}f}"


def markdown(out: dict) -> str:
    L = [
        "# Simulation Lab validation (generated)",
        "",
        f"PokerLab {out['version']}, `python -m pokerlab.lab.validate --reps {out['reps']}`, "
        f"{out['workers']} workers, {out['seconds']:.0f} s. Methodology: [`../lab.md`](../lab.md).",
        "",
        "## 1. Coverage of the 95% interval (mirror matches, true value 0)",
        "",
        "| strategy | dealing | hands per run | runs | covered | share (95% CI) | mean SE bb/100 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in out["coverage"]:
        lo, hi = r["share_interval"]
        name = get(r["strategy"]).label + (" (adaptive, clustered)" if r["adaptive"] else "")
        L.append(f"| {name} | {r['mode']} | {r['hands']:,} | {r['reps']} | {r['covered']} | "
                 f"{r['share']:.1%} ({lo:.1%}–{hi:.1%}) | {r['mean_se']:.1f} |")  # fmt: skip
    L += [
        "",
        "## 2. Duplicate efficiency and speed",
        "",
        "Variance reduction = variance of the same hands unpaired ÷ duplicate variance "
        "(how many times as many ordinary hands the duplicate run is worth). Speed is hands "
        "per second on one core.",
        "",
        "| A vs B | hands | A's bb/100 (95% CI) | SE dup. | SE unpaired | reduction | "
        "table corr. | hands/s/core |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for p in out["pairings"]:
        red = "exact" if p["reduction"] is None else f"{p['reduction']:.2f}×"
        L.append(
            f"| {p['a_label']} vs {p['b_label']} | {p['hands']:,} | {_f(p['bb_per_100'])} "
            f"({_f(p['low'])} to {_f(p['high'])}) | {_f(p['se'], sign=False)} | "
            f"{_f(p['unpaired_se'], sign=False)} | {red} | {_f(p['table_correlation'], 2)} | "
            f"{_f(p['hands_per_cpu_second'], 0, False)} |"
        )
    L += [
        "",
        "## 3. Duplicate and standard dealing agree",
        "",
        "| A vs B | duplicate bb/100 | standard bb/100 | difference (SE) | SE ratio std./dup. |",
        "|---|---|---|---|---|",
    ]
    for r in out["agreement"]:
        d, s = r["duplicate"], r["standard"]
        L.append(f"| {r['a_label']} vs {r['b_label']} | {_f(d['bb_per_100'])} ± {d['se']:.1f} | "
                 f"{_f(s['bb_per_100'])} ± {s['se']:.1f} | {_f(r['difference'])} "
                 f"({r['difference_se']:.1f}) | {_f(r['se_ratio'], 2, False)} |")  # fmt: skip
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m pokerlab.lab.validate")
    p.add_argument("--reps", type=int, default=200, help="runs per coverage cell")
    p.add_argument("--adaptive-reps", type=int, default=40)
    p.add_argument("--workers", type=int, default=default_workers())
    p.add_argument("--out", default=str(Path(__file__).resolve().parents[3] / "docs" / "lab"))
    args = p.parse_args(argv)
    plans = build(args.reps, args.adaptive_reps)
    total = sum(c.total_hands for _, c in plans)
    print(f"{len(plans)} runs, {total:,} hands, {args.workers} workers")
    started = time.perf_counter()
    results = run_plans(plans, args.workers)
    out = {"version": __version__, "reps": args.reps, "workers": args.workers,
           "seconds": time.perf_counter() - started, **summarise(plans, results)}  # fmt: skip
    d = Path(args.out)
    d.mkdir(parents=True, exist_ok=True)
    (d / "validation.json").write_text(json.dumps(out, indent=1) + "\n")
    (d / "validation.md").write_text(markdown(out))
    print(markdown(out))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
