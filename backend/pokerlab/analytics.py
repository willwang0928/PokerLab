"""Performance analytics over saved hands (v0.4.8).

Everything here is computed from hand *summaries* (see :func:`pokerlab.history.summarize`),
so it reuses the stored hand histories and needs no new tables.

**Units.** Results are in big blinds, so games with different blinds can be combined.
A win rate is bb/100: the mean result per hand × 100.

**Uncertainty.** Poker results are extremely noisy: a heads-up hand has a standard
deviation of roughly 8–12 bb, so after 1,000 hands a 95% interval is still about
±60 bb/100 wide. Every win rate therefore comes with an interval, and nothing is called
"winning" or "losing" unless the interval excludes zero.

* *Variance estimate.* With few hands the sample standard deviation is usually too
  small: the large pots that dominate the variance have not happened yet. The variance
  is therefore a weighted mix of a prior (σ₀ = :data:`PRIOR_SD_BB` bb per hand, worth
  :data:`PRIOR_WEIGHT` hands) and the sample, the posterior mean of a scaled
  inverse-χ² model. After a few hundred hands the sample dominates.
* *Interval.* Student-t with n − 1 degrees of freedom around the mean. Hands from one
  session share stack depths and an opponent that adapts during the session, so they
  are not quite independent: once there are :data:`MIN_CLUSTERS` sessions, a
  session-clustered standard error (CR1) is also computed and the larger of the two is
  used.
* *Small samples.* Below :data:`MIN_HANDS` hands no interval or verdict is given.

**Variance versus skill.** Three measures separate luck from improvement:

1. *All-in adjusted results* replace the outcome of every hand that was all-in before
   the river with its expected value at that moment (see
   :func:`pokerlab.history.allin_adjustment`). The total luck from all-ins is tested
   against its own standard deviation.
2. *Earlier versus recent* compares the first and second halves of the sample with a
   Welch interval on the difference: a change in results that the interval does not
   exclude from zero is consistent with variance alone.
3. *Decision quality* (the coach's estimated bb lost to graded mistakes per hand) is
   far less noisy than results; when it improves significantly that is real progress
   even while results are still inside their variance.

**Starting hands.** All 169 classes are listed. Per-hand results are shrunk toward the
overall rate (normal–normal empirical Bayes with a prior spread of :data:`HAND_PRIOR_SD`
bb per hand), so a hand dealt three times cannot look like the best or the worst in
the grid by luck; the raw figures are reported next to the shrunk ones.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from .cards import RANK_CHARS

LEVEL = 0.95
MIN_HANDS = 30  # no interval or verdict below this
MIN_CLUSTERS = 10  # sessions needed before the clustered standard error is trusted
PRIOR_SD_BB = 10.0  # typical heads-up standard deviation per hand, in bb
PRIOR_WEIGHT = 50  # how many hands the prior variance is worth
HAND_PRIOR_SD = 1.0  # spread of true starting-hand win rates, bb per hand
HAND_MIN_INTERVAL = 10  # hands of one class needed before its interval is shown
TREND_MIN_HALF = 50  # hands needed in each half before comparing them
MAX_POINTS = 1200  # bankroll graph points sent to the client
PRECISION_TARGET = 10.0  # bb/100: "hands needed for ±10 bb/100"
DIFFICULTIES = ("beginner", "intermediate", "advanced")
POSITIONS = ("button", "big blind")


# --------------------------------------------------------------------------- statistics


def normal_ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam's rational approximation, |error| < 1.2e-9)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    a = (-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00)  # fmt: skip
    b = (-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01)  # fmt: skip
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00)  # fmt: skip
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00)  # fmt: skip
    lo = 0.02425
    if p < lo:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    if p > 1 - lo:
        return -normal_ppf(1 - p)
    q = p - 0.5
    r = q * q
    return (
        (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
        * q
        / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    )


def normal_sf(z: float) -> float:
    """Upper tail of the standard normal."""
    return 0.5 * math.erfc(z / math.sqrt(2))


def t_ppf(p: float, df: float) -> float:
    """Student-t quantile (Hill's 1970 expansion; within 1e-4 for df ≥ 2)."""
    if df <= 0:
        raise ValueError("df must be positive")
    if df == math.inf or df > 1e7:
        return normal_ppf(p)
    if df == 1:
        return math.tan(math.pi * (p - 0.5))
    if df == 2:
        a = 4 * p * (1 - p)
        return 2 * (p - 0.5) * math.sqrt(2 / a)
    z = normal_ppf(p)
    g1 = (z**3 + z) / 4
    g2 = (5 * z**5 + 16 * z**3 + 3 * z) / 96
    g3 = (3 * z**7 + 19 * z**5 + 17 * z**3 - 15 * z) / 384
    g4 = (79 * z**9 + 776 * z**7 + 1482 * z**5 - 1920 * z**3 - 945 * z) / 92160
    return z + g1 / df + g2 / df**2 + g3 / df**3 + g4 / df**4


def blended_variance(sample_var: float, n: int) -> float:
    """Posterior-mean variance: the prior (σ₀², worth ``PRIOR_WEIGHT`` hands) mixed with
    the sample variance (worth n − 1)."""
    k = max(n - 1, 0)
    return (PRIOR_WEIGHT * PRIOR_SD_BB**2 + k * sample_var) / (PRIOR_WEIGHT + k)


def _mean_var(xs: Sequence[float]) -> tuple[float, float]:
    n = len(xs)
    if n == 0:
        return 0.0, 0.0
    m = math.fsum(xs) / n
    v = math.fsum((x - m) ** 2 for x in xs) / (n - 1) if n > 1 else 0.0
    return m, v


def clustered_se(xs: Sequence[float], clusters: Sequence[Any]) -> tuple[float, int]:
    """CR1 standard error of the mean with observations grouped by ``clusters``."""
    n = len(xs)
    groups: dict[Any, float] = defaultdict(float)
    m = math.fsum(xs) / n if n else 0.0
    for x, c in zip(xs, clusters, strict=True):
        groups[c] += x - m
    g = len(groups)
    if g < 2:
        return math.inf, g
    s = math.fsum(r * r for r in groups.values())
    return math.sqrt(s * g / (g - 1)) / n, g


def win_rate(xs: Sequence[float], clusters: Sequence[Any] | None = None) -> dict[str, Any]:
    """bb/100 of per-hand results ``xs`` (bb), with a 95% interval and a verdict.

    The verdict is ``insufficient`` (fewer than ``MIN_HANDS``), ``winning`` / ``losing``
    (the interval excludes zero) or ``unclear`` (it does not).
    """
    n = len(xs)
    mean, sample_var = _mean_var(xs)
    out: dict[str, Any] = {
        "hands": n,
        "bb_per_100": mean * 100 if n else None,
        "total_bb": math.fsum(xs),
        "sd_bb": math.sqrt(sample_var) if n > 1 else None,
        "se": None,
        "low": None,
        "high": None,
        "level": LEVEL,
        "method": None,
        "verdict": "insufficient" if n < MIN_HANDS else "unclear",
        "p_value": None,
        "hands_for_precision": None,
        "hands_to_confirm": None,
    }
    if n < MIN_HANDS:
        return out
    var = blended_variance(sample_var, n)
    se = math.sqrt(var / n)
    method = "t"
    if clusters is not None:
        cse, g = clustered_se(xs, clusters)
        if g >= MIN_CLUSTERS and cse > se:
            se, method = cse, "clustered"
    q = t_ppf(0.5 + LEVEL / 2, n - 1)
    lo, hi = mean - q * se, mean + q * se
    z = mean / se if se > 0 else 0.0
    sd = math.sqrt(var)
    zq = normal_ppf(0.5 + LEVEL / 2)
    out.update(
        se=se * 100,
        low=lo * 100,
        high=hi * 100,
        method=method,
        verdict="winning" if lo > 0 else "losing" if hi < 0 else "unclear",
        p_value=2 * normal_sf(abs(z)),
        hands_for_precision=math.ceil((zq * sd * 100 / PRECISION_TARGET) ** 2),
    )
    # If the true rate equalled the estimate, how many more hands until the interval
    # would exclude zero (only meaningful while it does not)?
    if out["verdict"] == "unclear" and abs(mean) > 1e-9:
        need = math.ceil((zq * sd / abs(mean)) ** 2)
        out["hands_to_confirm"] = max(need - n, 0)
    return out


def compare(a: Sequence[float], b: Sequence[float]) -> dict[str, Any]:
    """Welch interval for mean(b) − mean(a) (per hand), with the variance blending above."""
    na, nb = len(a), len(b)
    ma, va = _mean_var(a)
    mb, vb = _mean_var(b)
    va, vb = blended_variance(va, na), blended_variance(vb, nb)
    sa, sb = va / na, vb / nb
    se = math.sqrt(sa + sb)
    df = (sa + sb) ** 2 / (sa**2 / (na - 1) + sb**2 / (nb - 1))
    q = t_ppf(0.5 + LEVEL / 2, df)
    diff = mb - ma
    return {
        "diff": diff,
        "low": diff - q * se,
        "high": diff + q * se,
        "p_value": 2 * normal_sf(abs(diff) / se) if se > 0 else 1.0,
        "significant": diff - q * se > 0 or diff + q * se < 0,
    }


def _plain_compare(a: Sequence[float], b: Sequence[float]) -> dict[str, Any] | None:
    """Welch comparison without the poker-variance prior (for decision quality)."""
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return None
    ma, va = _mean_var(a)
    mb, vb = _mean_var(b)
    sa, sb = va / na, vb / nb
    if sa + sb <= 0:
        # No spread at all: any difference is exact.
        d = mb - ma
        return {"diff": d, "low": d, "high": d, "p_value": 1.0 if d == 0 else 0.0,
                "significant": d != 0}  # fmt: skip
    se = math.sqrt(sa + sb)
    df = (sa + sb) ** 2 / ((sa**2 / (na - 1)) + (sb**2 / (nb - 1)))
    q = t_ppf(0.5 + LEVEL / 2, max(df, 1.0))
    diff = mb - ma
    return {
        "diff": diff,
        "low": diff - q * se,
        "high": diff + q * se,
        "p_value": 2 * normal_sf(abs(diff) / se),
        "significant": diff - q * se > 0 or diff + q * se < 0,
    }


# --------------------------------------------------------------------------- helpers


def all_classes_grid() -> list[list[str]]:
    """The 13×13 starting-hand grid: pairs on the diagonal, suited above, offsuit below."""
    ranks = RANK_CHARS[::-1]  # A K Q ... 2
    grid = []
    for i, r1 in enumerate(ranks):
        row = []
        for j, r2 in enumerate(ranks):
            if i == j:
                row.append(r1 + r2)
            elif j > i:
                row.append(r1 + r2 + "s")
            else:
                row.append(r2 + r1 + "o")
        grid.append(row)
    return grid


def combos(label: str) -> int:
    return 6 if len(label) == 2 else 4 if label.endswith("s") else 12


def _rate(k: int, n: int) -> float | None:
    return k / n if n else None


def _ev(s: Mapping[str, Any]) -> float:
    v = s.get("ev_net_bb")
    return float(v) if isinstance(v, (int, float)) else float(s["net_bb"])


def _session_key(s: Mapping[str, Any]) -> str:
    return str(s.get("session") or "")


def _style(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """VPIP, PFR, went-to-showdown and won-at-showdown rates."""
    n = len(rows)
    vpip = sum(1 for s in rows if s.get("vpip"))
    pfr = sum(1 for s in rows if s.get("pfr"))
    saw_flop = [s for s in rows if s.get("last_street") != "preflop" or s.get("showdown")]
    sd = [s for s in rows if s.get("showdown")]
    won = sum(1 for s in sd if s.get("won_showdown", s.get("net", 0) > 0))
    return {
        "vpip": _rate(vpip, n),
        "pfr": _rate(pfr, n),
        "saw_flop": _rate(len(saw_flop), n),
        "wtsd": _rate(len(sd), len(saw_flop)),
        "wsd": _rate(won, len(sd)),
    }


def _group(
    rows: Sequence[Mapping[str, Any]], key: Callable[[Mapping[str, Any]], Any]
) -> dict[Any, list[Mapping[str, Any]]]:
    out: dict[Any, list[Mapping[str, Any]]] = defaultdict(list)
    for s in rows:
        out[key(s)].append(s)
    return out


def _segment(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    nets = [float(s["net_bb"]) for s in rows]
    sessions = [_session_key(s) for s in rows]
    return {
        "hands": len(rows),
        "sessions": len(set(sessions)),
        "win_rate": win_rate(nets, sessions),
        "allin_adjusted": win_rate([_ev(s) for s in rows], sessions),
        **_style(rows),
    }


# --------------------------------------------------------------------------- sections


def bankroll(rows: Sequence[Mapping[str, Any]], max_points: int = MAX_POINTS) -> dict[str, Any]:
    """The cumulative result after every hand (bb), actual and all-in adjusted.

    Long histories are thinned to about ``max_points`` points; each bucket keeps its
    lowest and highest point of the actual line (and its last point), so swings and
    drawdowns survive the thinning.
    """
    cum = ev = 0.0
    full = []
    starts = []
    prev = None
    peak = 0.0
    drawdown = 0.0
    for i, s in enumerate(rows):
        cum += float(s["net_bb"])
        ev += _ev(s)
        peak = max(peak, cum)
        drawdown = max(drawdown, peak - cum)
        sess = _session_key(s)
        if sess != prev:
            starts.append(i + 1)
            prev = sess
        full.append(
            {
                "n": i + 1,
                "t": s.get("t"),
                "id": s.get("id"),
                "net": round(float(s["net_bb"]), 3),
                "cum": round(cum, 3),
                "ev": round(ev, 3),
            }
        )
    points = full
    if len(full) > max_points:
        size = math.ceil(len(full) / (max_points / 3))
        keep: set[int] = {0, len(full) - 1}
        for b in range(0, len(full), size):
            chunk = range(b, min(b + size, len(full)))
            keep.add(min(chunk, key=lambda k: full[k]["cum"]))
            keep.add(max(chunk, key=lambda k: full[k]["cum"]))
            keep.add(chunk[-1])
        points = [full[k] for k in sorted(keep)]
    nets = [float(s["net_bb"]) for s in rows]
    _, v = _mean_var(nets)
    return {
        "points": points,
        "thinned": len(points) < len(full),
        "session_starts": starts if len(starts) <= 400 else [],
        # Per-hand SD for the client's break-even band: ±1.96·sd·√n around zero.
        "sd_bb": math.sqrt(blended_variance(v, len(nets))) if nets else None,
        "max_drawdown_bb": drawdown,
        "peak_bb": peak,
    }


def starting_hands(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Every one of the 169 classes: how often it was dealt and what it won.

    ``shrunk`` is the empirical-Bayes estimate (bb per hand): the class mean pulled
    toward the overall mean with weight n / (n + σ²/τ²), where σ² is the per-hand
    variance and τ = ``HAND_PRIOR_SD``. A class dealt a handful of times stays near the
    overall rate; one dealt hundreds of times keeps close to its own mean.
    """
    by = _group(rows, lambda s: s.get("hand_class"))
    nets_all = [float(s["net_bb"]) for s in rows]
    overall, v = _mean_var(nets_all)
    sigma2 = blended_variance(v, len(nets_all))
    k = sigma2 / HAND_PRIOR_SD**2
    total = len(rows)
    cells = []
    for label in (c for row in all_classes_grid() for c in row):
        hs = by.get(label, [])
        n = len(hs)
        nets = [float(s["net_bb"]) for s in hs]
        mean, cv = _mean_var(nets)
        cell: dict[str, Any] = {
            "hand": label,
            "dealt": n,
            "expected": total * combos(label) / 1326,
            "net_bb": math.fsum(nets),
            "bb_per_hand": mean if n else None,
            "shrunk": overall + (mean - overall) * n / (n + k) if n else overall,
            "weight": n / (n + k),
            "vpip": _rate(sum(1 for s in hs if s.get("vpip")), n),
            "pfr": _rate(sum(1 for s in hs if s.get("pfr")), n),
            "low": None,
            "high": None,
        }
        if n >= HAND_MIN_INTERVAL:
            se = math.sqrt(blended_variance(cv, n) / n)
            q = t_ppf(0.5 + LEVEL / 2, n - 1)
            cell["low"], cell["high"] = mean - q * se, mean + q * se
        cells.append(cell)
    dealt_classes = sum(1 for c in cells if c["dealt"])
    return {
        "cells": cells,
        "overall_bb_per_hand": overall if rows else None,
        "classes_dealt": dealt_classes,
        "prior_sd": HAND_PRIOR_SD,
        "shrink_hands": k,  # a class needs this many hands to be weighted half its own
    }


def luck(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """All-in luck: actual minus expected over the hands all-in before the river."""
    spots = [s for s in rows if s.get("allin")]
    if not spots:
        return {"spots": 0, "luck_bb": 0.0, "sd_bb": 0.0, "z": None, "p_value": None,
                "verdict": "none"}  # fmt: skip
    diff = math.fsum(float(s["net_bb"]) - _ev(s) for s in spots)
    var = math.fsum(float(s["allin"]["variance"]) / float(s["big_blind"]) ** 2 for s in spots)
    sd = math.sqrt(var)
    z = diff / sd if sd > 0 else 0.0
    p = 2 * normal_sf(abs(z))
    verdict = "normal"
    if len(spots) >= 5 and p < 1 - LEVEL:
        verdict = "lucky" if z > 0 else "unlucky"
    return {
        "spots": len(spots),
        "luck_bb": diff,
        "sd_bb": sd,
        "z": z,
        "p_value": p,
        "verdict": verdict,
        "mean_equity": math.fsum(float(s["allin"]["equity"]) for s in spots) / len(spots),
    }


def _quality(rows: Sequence[Mapping[str, Any]]) -> tuple[list[float], int, int]:
    """Per-hand coach estimate of bb lost (hands with coaching), flagged and graded counts."""
    losses = []
    flagged = graded = 0
    for s in rows:
        if s.get("ev_loss_bb") is None and not s.get("decisions"):
            continue
        losses.append(float(s.get("ev_loss_bb") or 0.0))
        for d in s.get("decisions") or []:
            if d.get("grade") in ("good", "close", "questionable", "mistake"):
                graded += 1
                if d.get("grade") in ("questionable", "mistake"):
                    flagged += 1
    return losses, flagged, graded


def trend(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Earlier half versus recent half: results, all-in adjusted results, decisions."""
    n = len(rows)
    half = n // 2
    if half < TREND_MIN_HALF:
        return {"status": "insufficient", "needed": 2 * TREND_MIN_HALF, "hands": n}
    early, late = rows[:half], rows[half:]
    res = compare([float(s["net_bb"]) for s in early], [float(s["net_bb"]) for s in late])
    adj = compare([_ev(s) for s in early], [_ev(s) for s in late])
    le, fe, ge = _quality(early)
    ll, fl, gl = _quality(late)
    q = _plain_compare(le, ll)
    quality = None
    if q is not None:
        quality = {
            "early_loss_per_100": math.fsum(le) / len(le) * 100,
            "late_loss_per_100": math.fsum(ll) / len(ll) * 100,
            # Negative = fewer bb lost per hand recently = better decisions.
            "diff_per_100": q["diff"] * 100,
            "low": q["low"] * 100,
            "high": q["high"] * 100,
            "significant": q["significant"],
            "p_value": q["p_value"],
            "early_flag_rate": _rate(fe, ge),
            "late_flag_rate": _rate(fl, gl),
        }
    results = {k: (v * 100 if k in ("diff", "low", "high") else v) for k, v in res.items()}
    adjusted = {k: (v * 100 if k in ("diff", "low", "high") else v) for k, v in adj.items()}
    if quality and quality["significant"]:
        verdict = "decisions_improved" if quality["diff_per_100"] < 0 else "decisions_worse"
    elif results["significant"] or adjusted["significant"]:
        better = (adjusted if adjusted["significant"] else results)["diff"] > 0
        verdict = "results_improved" if better else "results_worse"
    else:
        verdict = "variance"
    return {
        "status": "ok",
        "split_at": half,
        "early_hands": len(early),
        "late_hands": len(late),
        "early": win_rate([float(s["net_bb"]) for s in early]),
        "late": win_rate([float(s["net_bb"]) for s in late]),
        "results": results,
        "adjusted": adjusted,
        "quality": quality,
        "verdict": verdict,
    }


# --------------------------------------------------------------------------- report


def _level_order(d: str) -> int:
    return DIFFICULTIES.index(d) if d in DIFFICULTIES else len(DIFFICULTIES)


def _ordered(summaries: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    rows = [s for s in summaries if isinstance(s.get("net_bb"), (int, float))]
    rows.sort(key=lambda s: (float(s.get("t") or 0), str(s.get("session") or ""),
                             int(s.get("hand_number") or 0)))  # fmt: skip
    return rows


def analyze(
    summaries: Iterable[Mapping[str, Any]],
    *,
    difficulty: str | None = None,
    last: int | None = None,
) -> dict[str, Any]:
    """The full analytics report. ``difficulty`` keeps one opponent level; ``last`` keeps
    the most recent hands (after the difficulty filter)."""
    rows = _ordered(summaries)
    available = sorted({s["difficulty"] for s in rows if s.get("difficulty")}, key=_level_order)
    if difficulty:
        rows = [s for s in rows if s.get("difficulty") == difficulty]
    if last:
        rows = rows[-last:]
    overall = _segment(rows)
    positions = []
    by_pos = _group(rows, lambda s: s.get("position"))
    for p in POSITIONS:
        positions.append({"position": p, **_segment(by_pos.get(p, []))})
    opponents = []
    for d, hs in sorted(
        _group(rows, lambda s: s.get("difficulty") or "unknown").items(),
        key=lambda kv: _level_order(kv[0]),
    ):
        seg = {"difficulty": d, **_segment(hs)}
        adaptive = [s for s in hs if s.get("adaptive")]
        seg["adaptive_share"] = _rate(len(adaptive), len(hs))
        opponents.append(seg)
    return {
        "hands": len(rows),
        "filters": {"difficulty": difficulty, "last": last},
        "available_difficulties": available,
        "first_t": rows[0].get("t") if rows else None,
        "last_t": rows[-1].get("t") if rows else None,
        "overall": overall,
        "bankroll": bankroll(rows),
        "starting_hands": starting_hands(rows),
        "positions": positions,
        "opponents": opponents,
        "luck": luck(rows),
        "trend": trend(rows),
        "settings": {
            "level": LEVEL,
            "min_hands": MIN_HANDS,
            "prior_sd_bb": PRIOR_SD_BB,
            "prior_weight": PRIOR_WEIGHT,
            "min_clusters": MIN_CLUSTERS,
            "hand_prior_sd": HAND_PRIOR_SD,
            "precision_target": PRECISION_TARGET,
        },
    }
