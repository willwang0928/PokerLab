"""``python -m pokerlab.calibration``: run the calibration and write the report.

    python -m pokerlab.calibration                 # full run (slow: ~1 h on 8 cores)
    python -m pokerlab.calibration --quick         # a small run to check the pipeline
    python -m pokerlab.calibration --stats-only    # synthetic statistics only (~1 min)

Simulated sessions are cached in ``backend/.calibration-cache`` keyed by their
configuration, so re-running only redoes the analysis.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .policies import RunConfig

DOCS = Path(__file__).resolve().parents[3] / "docs"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pokerlab.calibration")
    ap.add_argument("--quick", action="store_true", help="small run (pipeline check)")
    ap.add_argument("--stats-only", action="store_true", help="synthetic statistics only")
    ap.add_argument("--simulate-only", action="store_true", help="run sessions, no report")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--reps", type=int, default=1000, help="synthetic replicates")
    ap.add_argument("--out", type=Path, default=DOCS / "calibration")
    ap.add_argument(
        "--synthetic",
        type=Path,
        default=None,
        help="reuse synthetic results saved by --stats-only (results.json)",
    )
    args = ap.parse_args(argv)

    def log(msg: str) -> None:
        print(time.strftime("%H:%M:%S"), msg, flush=True)

    cfg = RunConfig()
    if args.quick:
        cfg = RunConfig(hands=40, session_hands=20, rollouts=4, oracle_per_session=4)
    from .report import build_report

    if args.simulate_only:
        from .run import run_all

        run_all(cfg, workers=args.workers, log=log)
        return 0
    synthetic = None
    if args.synthetic:
        synthetic = json.loads(args.synthetic.read_text())["synthetic"]
    result = build_report(cfg, stats_only=args.stats_only, reps=args.reps,
                          workers=args.workers, log=log, synthetic=synthetic)  # fmt: skip
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "results.json").write_text(json.dumps(result["data"], indent=1, sort_keys=True))
    (args.out / "report.md").write_text(result["markdown"])
    log(f"wrote {args.out / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
