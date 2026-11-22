"""Turn finished sessions into a matchup report, and matchups into a round robin."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from .. import __version__
from . import stats as S
from .config import BIG_BLIND, PROTOCOL, LabConfig
from .simulate import COUNTERS, HAND_COUNTERS, BlockResult
from .strategies import get

MAX_POINTS = 900  # trajectory points sent to the client (per series)
CHECKPOINTS = 48  # points on the convergence chart
ALPHA = 1 - S.LEVEL


def _ratio(k: int, n: int) -> float | None:
    return k / n if n else None


def _units(blocks: Sequence[BlockResult], bb: int):
    """Per-unit results (bb), their sessions, and the unpaired comparison units."""
    units: list[float] = []
    clusters: list[int] = []
    unpaired: list[float] = []
    t1_all: list[int] = []
    t2_all: list[int] = []
    for blk in blocks:
        t1 = blk.table1
        t2 = blk.table2
        t1_all.extend(t1)
        if t2 is not None:
            t2_all.extend(t2)
            for x, y in zip(t1, t2, strict=True):
                units.append((x + y) / bb)
                clusters.append(blk.session)
            # The same hands as if dealt independently: button-balanced pairs of
            # consecutive deals at each table (the standard-match unit).
            for k in range(0, len(t1), 2):
                unpaired.append((t1[k] + t1[k + 1]) / bb)
                unpaired.append((t2[k] + t2[k + 1]) / bb)
        else:
            for k in range(0, len(t1), 2):
                units.append((t1[k] + t1[k + 1]) / bb)
                clusters.append(blk.session)
    return units, clusters, unpaired, t1_all, t2_all


def _thin(n: int, series: Sequence[Sequence[float]], max_points: int = MAX_POINTS) -> list[int]:
    """Indices to draw: every bucket keeps each series' lowest and highest point (so
    swings survive thinning) plus its last point."""
    if n <= max_points:
        return list(range(n))
    size = math.ceil(n / (max_points / (2 * len(series) + 1)))
    keep = {0, n - 1}
    for b in range(0, n, size):
        idx = range(b, min(b + size, n))
        for s in series:
            keep.add(min(idx, key=s.__getitem__))
            keep.add(max(idx, key=s.__getitem__))
        keep.add(idx[-1])
    return sorted(keep)


def _cumulative(xs: Sequence[float]) -> list[float]:
    out, c = [], 0.0
    for x in xs:
        c += x
        out.append(c)
    return out


def _trajectory(t1: Sequence[int], t2: Sequence[int], bb: int, duplicate: bool) -> dict:
    """Cumulative result for A in bb: the duplicate total and each table, by deal (or the
    single table, by hand)."""
    c1 = _cumulative([x / bb for x in t1])
    if duplicate:
        c2 = _cumulative([x / bb for x in t2])
        total = [x + y for x, y in zip(c1, c2, strict=True)]
        idx = _thin(len(total), [total, c1, c2])
        return {
            "x_label": "deals",
            "x": [i + 1 for i in idx],
            "total": [round(total[i], 2) for i in idx],
            "table1": [round(c1[i], 2) for i in idx],
            "table2": [round(c2[i], 2) for i in idx],
            "thinned": len(idx) < len(total),
        }
    idx = _thin(len(c1), [c1])
    return {
        "x_label": "hands",
        "x": [i + 1 for i in idx],
        "total": [round(c1[i], 2) for i in idx],
        "table1": None,
        "table2": None,
        "thinned": len(idx) < len(c1),
    }


def _running(units: Sequence[float]) -> list[tuple[int, float, float]]:
    """(units so far, running mean, running iid SE) at roughly geometric checkpoints."""
    n = len(units)
    if n < 4:
        return []
    start = min(20, n)
    marks = sorted({round(start * (n / start) ** (k / (CHECKPOINTS - 1)))
                    for k in range(CHECKPOINTS)} | {n})  # fmt: skip
    out = []
    s = s2 = 0.0
    j = 0
    for k, x in enumerate(units, 1):
        s += x
        s2 += x * x
        if j < len(marks) and k == marks[j]:
            j += 1
            if k < 2:
                continue
            m = s / k
            var = max(0.0, (s2 - k * m * m) / (k - 1))
            out.append((k, m, math.sqrt(var / k)))
    return out


def _convergence(units: Sequence[float], unpaired: Sequence[float]) -> dict:
    """The running estimate with a 95% interval as hands accumulate (descriptive: the
    final interval is the one to read). For duplicate runs, also the interval the same
    hands would give if they had not been paired."""
    q = S.normal_ppf(0.5 + S.LEVEL / 2)

    def pack(rows):
        return [
            {
                "hands": k * S.HANDS_PER_UNIT,
                "bb_per_100": round(m * S.TO_BB100, 3),
                "low": round((m - q * se) * S.TO_BB100, 3),
                "high": round((m + q * se) * S.TO_BB100, 3),
            }
            for k, m, se in rows
        ]

    return {"paired": pack(_running(units)), "unpaired": pack(_running(unpaired)) or None}


def _style(c: dict[str, int]) -> dict[str, Any]:
    return {
        "hands": c["hands"],
        "vpip": _ratio(c["vpip"], c["hands"]),
        "pfr": _ratio(c["pfr"], c["hands"]),
        "aggression": _ratio(c["post_aggr"], c["post_calls"]),
        "saw_flop": _ratio(c["saw_flop"], c["hands"]),
        "wtsd": _ratio(c["showdown"], c["saw_flop"]),
        "wsd": _ratio(c["showdown_won"], c["showdown"]),
    }


def matchup_report(cfg: LabConfig, a: str, b: str, blocks: Sequence[BlockResult]) -> dict:
    """The full report for one matchup from its finished sessions (any subset, in session
    order: a cancelled run reports what it completed)."""
    blocks = sorted(blocks, key=lambda r: r.session)
    duplicate = cfg.mode == "duplicate"
    bb = BIG_BLIND
    units, clusters, unpaired, t1, t2 = _units(blocks, bb)
    dependent = get(a).adaptive or get(b).adaptive
    est = S.estimate(units, clusters, dependent=dependent)

    variance: dict[str, Any] | None = None
    if duplicate and len(units) > 1:
        _, v_dup = S.mean_var(units)
        _, v_unp = S.mean_var(unpaired)
        corr = None
        if len(t1) > 1:
            m1, v1 = S.mean_var(t1)
            m2, v2 = S.mean_var(t2)
            if v1 > 0 and v2 > 0:
                cov = math.fsum((x - m1) * (y - m2) for x, y in zip(t1, t2, strict=True))
                corr = cov / (len(t1) - 1) / math.sqrt(v1 * v2)
        factor = v_unp / v_dup if v_dup > 0 else None
        variance = {
            # Standard error the same hands would have had without pairing.
            "unpaired_se": math.sqrt(v_unp / len(unpaired)) * S.TO_BB100,
            "paired_se": math.sqrt(v_dup / len(units)) * S.TO_BB100,
            "reduction": factor,  # variance ratio: duplicate is worth this many times the hands
            "equivalent_hands": round(est["hands"] * factor) if factor else None,
            "table_correlation": corr,
        }

    counters = {k: dict.fromkeys(COUNTERS, 0) for k in ("a", "b")}
    totals = dict.fromkeys(HAND_COUNTERS, 0)
    for blk in blocks:
        for side in ("a", "b"):
            for key, v in blk.counters[side].items():
                counters[side][key] += v
        for key, v in blk.hand_counters.items():
            totals[key] += v
    hands = totals["hands"]

    return {
        "a": a,
        "b": b,
        "a_label": get(a).label,
        "b_label": get(b).label,
        "adaptive": dependent,
        "mode": cfg.mode,
        "sessions": len(blocks),
        "deals": sum(blk.deals for blk in blocks),
        "hands": hands,
        "planned_hands": cfg.hands,
        "complete": hands == cfg.hands,
        "estimate": est,
        "variance": variance,
        "trajectory": _trajectory(t1, t2, bb, duplicate),
        # Per-step SD of the trajectory's total line, for the break-even band ±1.96·sd·√x.
        "band_sd": (
            math.sqrt(S.mean_var([x + y for x, y in zip(t1, t2, strict=True)])[1]) / bb
            if duplicate and len(t1) > 1
            else math.sqrt(S.mean_var(t1)[1]) / bb
            if len(t1) > 1
            else None
        ),
        "convergence": _convergence(units, unpaired if duplicate else []),
        "styles": {"a": _style(counters["a"]), "b": _style(counters["b"])},
        "table": {
            "avg_pot_bb": totals["pot_bb_x100"] / 100 / hands if hands else None,
            "flop": _ratio(totals["flop"], hands),
            "showdown": _ratio(totals["showdown"], hands),
            "allin": _ratio(totals["allin"], hands),
        },
    }


def round_robin(cfg: LabConfig, matchups: Sequence[dict]) -> dict | None:
    """League table and results matrix for three or more strategies.

    Verdicts in the matrix are Holm-corrected over all matchups, so the chance of any
    false "better" across the whole table stays at 5%. Each strategy's average against
    the field is the mean of its matchup estimates; matchups use independent cards, so
    their errors combine as independent (Welch–Satterthwaite degrees of freedom).
    """
    if len(cfg.strategies) < 3:
        return None
    usable = [m for m in matchups if m["estimate"]["p_value"] is not None]
    adjusted = S.holm([m["estimate"]["p_value"] for m in usable])
    adj = {(m["a"], m["b"]): p for m, p in zip(usable, adjusted, strict=True)}
    cells = []
    parts: dict[str, list[tuple[float, float, float]]] = {s: [] for s in cfg.strategies}
    for m in usable:
        e = m["estimate"]
        p_adj = adj[(m["a"], m["b"])]
        sig = p_adj < ALPHA
        for row, col, sign in ((m["a"], m["b"], 1), (m["b"], m["a"], -1)):
            cells.append(
                {
                    "row": row,
                    "col": col,
                    "bb_per_100": sign * e["bb_per_100"],
                    "low": sign * (e["low"] if sign > 0 else e["high"]),
                    "high": sign * (e["high"] if sign > 0 else e["low"]),
                    "p_value": e["p_value"],
                    "p_holm": p_adj,
                    "significant": sig,
                }
            )
            parts[row].append((sign * e["bb_per_100"], e["se"], e["df"]))
    league = []
    for s in cfg.strategies:
        if len(parts[s]) == len(cfg.strategies) - 1:
            league.append({"strategy": s, "label": get(s).label, **S.combine(parts[s])})
    league.sort(key=lambda r: -r["bb_per_100"])
    for rank, row in enumerate(league, 1):
        row["rank"] = rank
    return {"league": league, "cells": cells, "comparisons": len(usable)}


def experiment_report(cfg: LabConfig, matchups: Sequence[dict]) -> dict:
    return {
        "version": __version__,
        "protocol": PROTOCOL,
        "config": cfg.view(),
        "matchups": list(matchups),
        "round_robin": round_robin(cfg, matchups),
        "settings": {
            "level": S.LEVEL,
            "min_clusters": S.MIN_CLUSTERS,
            "precision_target": S.PRECISION_TARGET,
            "hands_per_unit": S.HANDS_PER_UNIT,
        },
    }
