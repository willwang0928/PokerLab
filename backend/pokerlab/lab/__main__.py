"""``python -m pokerlab.lab A B [C ...] --hands 10000 --seed 1 [--mode standard] [--json out]``

Runs an experiment exactly as the Simulation Lab does (same sessions, seeds and
statistics) and prints the results. The same configuration gives the same numbers in
the app, here, and on any machine running the same PokerLab version.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from .config import MODES, STACKS_BB, LabConfig
from .jobs import default_workers, run
from .strategies import STRATEGIES


def _fmt(x: float | None, digits: int = 1, sign: bool = True) -> str:
    if x is None:
        return "—"
    return f"{x:+.{digits}f}" if sign else f"{x:.{digits}f}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m pokerlab.lab", description=__doc__.split("\n\n")[1])
    p.add_argument("strategies", nargs="+", choices=sorted(STRATEGIES))
    p.add_argument("--hands", type=int, default=10_000, help="hands per matchup")
    p.add_argument("--mode", choices=MODES, default="duplicate")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--stack", type=int, choices=STACKS_BB, default=100, help="stack in bb")
    p.add_argument("--session", type=int, default=200, help="deals per session")
    p.add_argument("--workers", type=int, default=default_workers())
    p.add_argument("--json", metavar="PATH", help="also write the full report as JSON")
    args = p.parse_args(argv)
    try:
        cfg = LabConfig.build(
            strategies=args.strategies,
            hands=args.hands,
            mode=args.mode,
            seed=args.seed,
            stack_bb=args.stack,
            session_hands=args.session,
        )
    except ValueError as exc:
        p.error(str(exc))
    total = cfg.total_hands
    done = 0
    started = time.perf_counter()

    def progress(res):
        nonlocal done
        done += res.hands
        rate = done / (time.perf_counter() - started)
        print(f"\r  {done:,}/{total:,} hands ({rate:,.0f}/s)", end="", file=sys.stderr, flush=True)

    print(
        f"{len(cfg.matchups)} matchup(s), {cfg.hands:,} hands each, {cfg.mode} dealing, "
        f"seed {cfg.seed}, {cfg.stack_bb} bb, {args.workers} worker(s)",
        file=sys.stderr,
    )
    report = run(cfg, workers=args.workers, on_block=progress)
    print(file=sys.stderr)
    for m in report["matchups"]:
        e = m["estimate"]
        line = (
            f"{m['a_label']} vs {m['b_label']}: {_fmt(e['bb_per_100'])} bb/100 "
            f"(95% CI {_fmt(e['low'])} to {_fmt(e['high'])}, {e['method']}, "
            f"p = {e['p_value']:.3g}) over {m['hands']:,} hands"
        )
        v = m["variance"]
        if v and v["reduction"]:
            line += f"; duplicate variance reduction {v['reduction']:.1f}x"
        if e["dependence_warning"]:
            line += " [fewer than 10 sessions: interval may be too narrow]"
        print(line)
    rr = report["round_robin"]
    if rr:
        print("\nAverage against the field (bb/100, 95% CI):")
        for row in rr["league"]:
            print(
                f"  {row['rank']}. {row['label']:22s} {_fmt(row['bb_per_100'])} "
                f"({_fmt(row['low'])} to {_fmt(row['high'])})"
            )
    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=1)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
