"""Controlled experiment: does adaptation help, against which players, and by how much?

``python -m pokerlab.ai.experiments advanced --vs aggressive passive folder intermediate
--deals 2000 --jobs 8``

For each opponent, an adaptive agent and the same agent with adaptation switched off
play their own copies of the opponent on identical cards
(:func:`pokerlab.ai.match.paired_comparison`). The report gives each arm's win rate and
the paired difference, with standard errors. Runs split across processes use different
seeds and are pooled.
"""

from __future__ import annotations

import random
from dataclasses import replace

from .difficulty import PROFILES, Difficulty
from .equity_agent import EquityAgent
from .heuristic import HeuristicAgent
from .match import MatchResult, PairedComparison, paired_comparison, pool
from .random_agent import RandomAgent
from .styles import STYLES, StyleAgent

OPPONENTS = [*STYLES, "intermediate", "basic", "random"]


def _opponent(name: str):
    if name in STYLES:
        return lambda rng: StyleAgent(name, rng)
    if name == "basic":
        return lambda rng: HeuristicAgent(rng)
    if name == "random":
        return lambda rng: RandomAgent(rng)
    # Balanced reference: the fixed (non-adaptive) Intermediate opponent.
    profile = replace(PROFILES[Difficulty(name)], adapt=0.0)
    return lambda rng: EquityAgent(rng, profile=profile)


def _run(args: tuple[str, str, int, int]) -> PairedComparison:
    level, opponent, deals, seed = args
    profile = PROFILES[Difficulty(level)]
    return paired_comparison(
        lambda rng: EquityAgent(rng, profile=profile),
        lambda rng: EquityAgent(rng, profile=replace(profile, adapt=0.0)),
        _opponent(opponent),
        deals,
        seed=seed,
    )


def run_experiment(
    level: str, opponent: str, deals: int, seed: int = 0, jobs: int = 1
) -> tuple[MatchResult, MatchResult, MatchResult]:
    """(adaptive, non-adaptive, difference) for ``level`` against ``opponent``."""
    jobs = max(1, min(jobs, deals // 2))
    chunks = [(level, opponent, deals // jobs, seed + 7919 * i) for i in range(jobs)]
    if jobs == 1:
        parts = [_run(chunks[0])]
    else:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(jobs) as ex:
            parts = list(ex.map(_run, chunks))
    return (
        pool([p.a for p in parts]),
        pool([p.b for p in parts]),
        pool([p.difference for p in parts]),
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m pokerlab.ai.experiments")
    parser.add_argument("level", choices=[d.value for d in Difficulty])
    parser.add_argument("--vs", nargs="+", choices=OPPONENTS, default=OPPONENTS)
    parser.add_argument("--deals", type=int, default=1000, help="deals per opponent")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--jobs", type=int, default=1)
    args = parser.parse_args(argv)
    random.seed(args.seed)
    print(f"{args.level}: adaptive vs non-adaptive, {args.deals} deals (x2 seats) per opponent")
    for opp in args.vs:
        adaptive, fixed, diff = run_experiment(args.level, opp, args.deals, args.seed, args.jobs)
        print(
            f"  vs {opp:12s} adaptive {adaptive.bb_per_100:+7.1f} ± {adaptive.std_error:5.1f}"
            f" | fixed {fixed.bb_per_100:+7.1f} ± {fixed.std_error:5.1f}"
            f" | difference {diff.bb_per_100:+6.1f} ± {diff.std_error:4.1f}"
            f" (z = {diff.z_score:+.1f}) bb/100",
            flush=True,
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
