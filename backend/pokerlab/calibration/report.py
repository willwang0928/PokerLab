"""Turn simulated sessions into the calibration report (Markdown and JSON).

Sections: the detector's statistics on synthetic data (v0.4.4 versus current); what the
independent oracle says the injected tendencies and the coach's flags actually cost;
detection rates by sample size against two kinds of ground truth (injected tendencies,
and weaknesses the oracle confirms); concentration, trend and repeated-look checks.
"""

from __future__ import annotations

import math
import random
import zlib
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from typing import Any

from ..coach import leaks as current
from . import legacy_leaks_v044 as legacy
from . import stats_eval
from .policies import POLICIES, POLICY_BY_NAME, RunConfig

SIZES = (50, 100, 200, 400, 800)
BOOT = 100
KEYS = [w.key for w in current.WEAKNESSES if w.key != "pot_odds_mixed"]
DETECTORS = {"v0.4.4": legacy, "v0.4.5": current}


# --------------------------------------------------------------------------- helpers


def wmean(values: list[float], weights: list[float]) -> tuple[float, float] | None:
    """Weighted mean and its standard error (ratio-estimator variance)."""
    if len(values) < 2:
        return None
    sw = sum(weights)
    m = sum(w * v for v, w in zip(values, weights, strict=True)) / sw
    var = sum((w * (v - m)) ** 2 for v, w in zip(values, weights, strict=True)) / sw**2
    n = len(values)
    return m, math.sqrt(var * n / (n - 1))


def verdict(est: tuple[float, float] | None, n: int, min_n: int = 15) -> str:
    if est is None or n < min_n:
        return "insufficient"
    m, se = est
    if m + 1.96 * se < 0:
        return "costly"
    if m - 1.96 * se > 0:
        return "gains"
    return "unclear"


def _pct(x: float | None) -> str:
    return "–" if x is None else f"{100 * x:.0f}%"


# --------------------------------------------------------------------------- oracle


def oracle_tables(sessions: dict[str, list[dict]]) -> dict[str, Any]:
    """What the injected deviations and the coach's flags cost, by the oracle."""
    out: dict[str, Any] = {
        "deviation": {},
        "flag": {},
        "flag_by_confidence": {},
        "flag_by_street": {},
        "control": {},
        "flag_by_policy_tag": {},
    }
    groups: dict[tuple, list[tuple[float, float, dict]]] = defaultdict(list)
    for policy, runs in sessions.items():
        for run in runs:
            per_kind: dict[str, list[dict]] = defaultdict(list)
            for o in run["oracle"]:
                per_kind[o["kind"]].append(o)
            for kind, evs in per_kind.items():
                w = evs[0]["sampled_from"] / len(evs)  # undo per-session subsampling
                for o in evs:
                    bb = o["big_blind"]
                    d = o["delta"] / bb
                    if kind == "deviation":
                        groups[("deviation", policy, o["deviation"])].append((d, w, o))
                    elif kind == "control":
                        groups[("control", o["confidence"])].append((d, w, o))
                    else:
                        tag = (o["tags"] or ["(no tag)"])[0]
                        groups[("flag", tag)].append((d, w, o))
                        groups[("flag_by_confidence", o["confidence"], o["robust"])].append(
                            (d, w, o)
                        )
                        groups[("flag_by_street", o["street"], tag)].append((d, w, o))
                        groups[("flag_by_policy_tag", policy, tag)].append((d, w, o))
    for key, rows in groups.items():
        ds = [r[0] for r in rows]
        ws = [r[1] for r in rows]
        est = wmean(ds, ws)
        coach = [
            r[2]["coach_delta"] / r[2]["big_blind"]
            for r in rows
            if r[2].get("coach_delta") is not None
        ]
        entry = {
            "n": len(rows),
            "mean_bb": est[0] if est else None,
            "se_bb": est[1] if est else None,
            "verdict": verdict(est, len(rows)),
            "share_negative": sum(1 for d in ds if d < 0) / len(ds),
            "coach_mean_bb": sum(coach) / len(coach) if coach else None,
        }
        kind = key[0]
        out[kind]["|".join(str(k) for k in key[1:])] = entry
    return out


# --------------------------------------------------------------------------- detection


def _hands(runs: list[dict]) -> list[dict]:
    hs = [h for r in runs for h in r["summaries"]]
    hs.sort(key=lambda h: h["t"])
    return hs


def _detect(detector, hands: list[dict]) -> dict[str, Any]:
    r = detector.find_leaks(hands)
    leaks = {i["key"]: i for i in r["leaks"]}
    return {
        "leaks": sorted(leaks),
        "conditions": {k: [c["key"] for c in i.get("conditions") or []] for k, i in leaks.items()},
        "trend": {k: (i.get("trend") or {}).get("change") for k, i in leaks.items()},
        "strengths": sorted(i["key"] for i in r["strengths"]),
    }


def _boot_task(args: tuple[str, str, int, list[dict], int]) -> tuple[str, str, int, list[dict]]:
    policy, det, size, hands, seed = args
    detector = DETECTORS[det]
    rng = random.Random(seed)
    out = []
    for _ in range(BOOT):
        sample = []
        for j in range(size):  # resample whole hands (keeps within-hand dependence)
            h = rng.choice(hands)
            sample.append({**h, "id": f"{h['id']}-{j}", "t": float(j)})
        out.append(_detect(detector, sample))
    return policy, det, size, out


def boot_seed(seed: int, policy: str, det: str, size: int) -> int:
    """A bootstrap seed that is the same in every process and every run.

    The built-in ``hash`` of strings is randomised per interpreter (PYTHONHASHSEED), which
    made the resampling, and so the detection tables, differ from run to run.
    """
    return zlib.crc32(f"{seed}|{policy}|{det}|{size}".encode())


def detection(
    sessions: dict[str, list[dict]], workers: int | None, log, seed: int = 0
) -> dict[str, Any]:
    tasks = []
    for policy, runs in sessions.items():
        hands = _hands(runs)
        for det in DETECTORS:
            for size in SIZES:
                if size <= len(hands):
                    tasks.append((policy, det, size, hands, boot_seed(seed, policy, det, size)))
    res: dict[str, Any] = defaultdict(lambda: defaultdict(dict))
    log(f"bootstrap: {len(tasks)} tasks x {BOOT} replicates")
    with ProcessPoolExecutor(workers) as pool:
        for policy, det, size, out in pool.map(_boot_task, tasks):
            counts = {k: sum(k in o["leaks"] for o in out) / len(out) for k in KEYS}
            strengths = {k: sum(k in o["strengths"] for o in out) / len(out) for k in KEYS}
            spec = POLICY_BY_NAME[policy]
            cond = {}
            for c in spec.target_conditions:
                hits = [o for o in out for k in spec.targets if k in o["leaks"]]
                cond[c] = (
                    sum(
                        c in o["conditions"].get(k, [])
                        for o in hits
                        for k in spec.targets
                        if k in o["leaks"]
                    )
                    / len(hits)
                    if hits
                    else None
                )
            other_cond = [o["conditions"] for o in out]
            spurious = sum(
                1
                for cs in other_cond
                for k, v in cs.items()
                if v and not set(v) & set(spec.target_conditions)
            )
            n_leaks = sum(len(o["leaks"]) for o in out)
            res[policy][det][size] = {
                "leak": counts,
                "strength": strengths,
                "target_condition_named": cond,
                "leaks_with_other_condition": spurious / n_leaks if n_leaks else None,
            }
    # Full-sequence checks, in real time order (no resampling).
    seq: dict[str, Any] = {}
    for policy, runs in sessions.items():
        hands = _hands(runs)
        seq[policy] = {det: _detect(d, hands) for det, d in DETECTORS.items()}
        looks = {}
        for det, d in DETECTORS.items():
            ever: set[str] = set()
            for k in range(25, len(hands) + 1, 25):
                ever |= {i["key"] for i in d.find_leaks(hands[:k])["leaks"]}
            looks[det] = sorted(ever)
        seq[policy]["ever_in_repeated_looks"] = looks
    return {"bootstrap": {p: dict(v) for p, v in res.items()}, "sequence": seq}


def ground_truth(oracle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per policy and weakness: was it injected, and does the oracle confirm it?"""
    truth: dict[str, dict[str, Any]] = {}
    for spec in POLICIES:
        row = {}
        dev_cost = {
            d.name: oracle["deviation"].get(f"{spec.name}|{d.name}") for d in spec.deviations
        }
        for k in KEYS:
            flags = oracle["flag_by_policy_tag"].get(f"{spec.name}|{k}")
            row[k] = {
                "injected": k in spec.targets,
                "confirmed": (flags or {}).get("verdict", "insufficient"),
                "flags_n": (flags or {}).get("n", 0),
            }
        row["_deviation_cost"] = dev_cost
        truth[spec.name] = row
    return truth


def confusion(det: dict, truth: dict, det_name: str, size: int, positive) -> dict[str, Any]:
    """Expected confusion counts over policies and weaknesses (probabilities summed)."""
    tp = fp = fn = tn = 0.0
    for policy, by_det in det["bootstrap"].items():
        cell = by_det.get(det_name, {}).get(size)
        if cell is None:
            continue
        for k in KEYS:
            pos = positive(truth[policy][k])
            if pos is None:
                continue
            p = cell["leak"][k]
            if pos:
                tp += p
                fn += 1 - p
            else:
                fp += p
                tn += 1 - p
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "fpr": fp / (fp + tn) if fp + tn else None,
    }


# --------------------------------------------------------------------------- report


def build_report(
    cfg: RunConfig,
    stats_only: bool = False,
    reps: int = 1000,
    workers: int | None = None,
    log=print,
    synthetic: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "config": {
            "hands": cfg.hands,
            "session_hands": cfg.session_hands,
            "rollouts": cfg.rollouts,
            "seed": cfg.seed,
            "boot": BOOT,
        }
    }
    log("synthetic statistics")
    data["synthetic"] = synthetic or {
        name: stats_eval.run(d, reps=reps) for name, d in DETECTORS.items()
    }
    if not stats_only:
        from .policies import DISCOVERY
        from .run import load_folder, run_all

        discovery = load_folder(cfg, DISCOVERY)
        if discovery is not None:
            log("discovery run (v0.4.4 coach)")
            data["discovery"] = {"oracle": oracle_tables(discovery)}
            data["discovery"]["truth"] = ground_truth(data["discovery"]["oracle"])
            ddet = detection(discovery, workers, log, cfg.seed)
            data["discovery"]["detection"] = ddet
            data["discovery"]["confusion"] = {
                d: {
                    str(n): {
                        "injected": confusion(
                            ddet, data["discovery"]["truth"], d, n, lambda t: t["injected"]
                        ),
                    }
                    for n in SIZES
                }
                for d in DETECTORS
            }
            data["discovery"]["policies"] = policy_table(discovery)
        sessions = run_all(cfg, workers=workers, log=log)
        data["policies"] = policy_table(sessions)
        log("oracle")
        data["oracle"] = oracle_tables(sessions)
        data["truth"] = ground_truth(data["oracle"])
        log("detection")
        det = detection(sessions, workers, log, cfg.seed)
        data["detection"] = det
        data["confusion"] = {
            d: {
                str(n): {
                    "injected": confusion(det, data["truth"], d, n, lambda t: t["injected"]),
                    "oracle_confirmed": confusion(
                        det,
                        data["truth"],
                        d,
                        n,
                        lambda t: (
                            None
                            if t["confirmed"] in ("insufficient", "unclear")
                            else t["confirmed"] == "costly"
                        ),
                    ),
                }
                for n in SIZES
            }
            for d in DETECTORS
        }
    from .markdown import render

    return {"data": data, "markdown": render(data)}


def policy_table(sessions: dict[str, list[dict]]) -> dict[str, Any]:
    out = {}
    for policy, runs in sessions.items():
        hands = _hands(runs)
        decisions = [d for h in hands for d in h["decisions"]]
        labels = [lab for r in runs for lab in r["labels"]]
        graded = [
            d for d in decisions if d.get("grade") in ("good", "close", "questionable", "mistake")
        ]
        flagged = [d for d in graded if d["grade"] in ("questionable", "mistake")]
        # Opportunities per hand for each weakness (dependence between decisions).
        multi = {}
        for w in current.WEAKNESSES:
            per = [sum(1 for d in h["decisions"] if d.get("grade") and w.applies(d)) for h in hands]
            with_any = [m for m in per if m]
            multi[w.key] = {
                "hands_with_opportunity": len(with_any),
                "share_with_several": (sum(1 for m in with_any if m > 1) / len(with_any))
                if with_any
                else None,
            }
        out[policy] = {
            "hands": len(hands),
            "decisions": len(decisions),
            "graded": len(graded),
            "flagged": len(flagged),
            "confident_flagged": sum(1 for d in flagged if d.get("confidence") != "low"),
            "deviations": sum(1 for lab in labels if lab["deviation"]),
            "net_bb_per_100": 100 * sum(h["net_bb"] for h in hands) / max(1, len(hands)),
            "multi": multi,
        }
    return out
