"""Scenario-generation benchmark: ``python -m pokerlab.calibration.bench``.

Generates the same seeds for every concept and difficulty twice, with the v0.4.4
screening (exact enumeration whenever the engine allows it) and the current screening,
and reports wall-clock times and whether each seed produced the same scenario.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time

from ..coach import training as T

OLD_LIMIT = 600_000  # the equity engine's default: v0.4.4 screening


def _time(difficulty: str, concept: str, seed: int, limit: int):
    T._generate.cache_clear()
    saved = T.FILTER_EXACT_LIMIT
    T.FILTER_EXACT_LIMIT = limit
    try:
        t = time.perf_counter()
        try:
            s = T.generate(difficulty, concept, seed)
            key = (s.spot.to_dict(), s.choices)
        except T.ScenarioError:
            key = None
        return time.perf_counter() - t, key
    finally:
        T.FILTER_EXACT_LIMIT = saved
        T._generate.cache_clear()


def run(seeds: int = 3, start: int = 5000, difficulties=T.DIFFICULTIES, concepts=T.CONCEPTS):
    rows = []
    for d in difficulties:
        for c in concepts:
            old, new, same = [], [], 0
            for seed in range(start, start + seeds):
                t0, k0 = _time(d, c, seed, OLD_LIMIT)
                t1, k1 = _time(d, c, seed, T.FILTER_EXACT_LIMIT)
                old.append(t0)
                new.append(t1)
                same += k0 == k1
            rows.append(
                {
                    "difficulty": d,
                    "concept": c,
                    "old_median_s": statistics.median(old),
                    "new_median_s": statistics.median(new),
                    "old_max_s": max(old),
                    "new_max_s": max(new),
                    "same_scenario": same,
                    "seeds": seeds,
                }
            )
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=3)
    args = ap.parse_args(argv)
    print(json.dumps(run(args.seeds), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
