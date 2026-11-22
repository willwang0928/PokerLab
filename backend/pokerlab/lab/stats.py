"""Statistics for the Simulation Lab: intervals, p-values and multiple comparisons.

**The unit of analysis.** Every observation is a *unit* of two hands that is balanced
across the button:

* in a *duplicate* match, one deal played at two tables with the seats swapped, so
  strategy A holds each of the two hands once, in the same position;
* in a *standard* match, two consecutive deals with the button on each side once.

A's result per unit (in big blinds) is the observation; the win rate in bb/100 is the
mean unit result × 50. Units are independent when neither strategy remembers anything
between hands. An *adaptive* strategy learns during a session, so units of one session
are dependent; sessions are independent (fresh agents, independent seeds), so the
session-clustered (CR1) standard error is valid and is used when it is larger. It needs
:data:`MIN_CLUSTERS` sessions; with fewer, the interval is marked as possibly too narrow.

**Why not the v0.4.8 player analytics.** Those blend a prior variance into small human
samples. A simulation has thousands of units and, with duplicate dealing, a variance far
below the prior, so the plain sample variance is used.

**Intervals and tests.** Student-t with n − 1 degrees of freedom (G − 1 for the
clustered error). Two-sided p-values use the exact Student-t distribution, so that the
Holm correction for round robins stays correct with few clusters.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from ..analytics import normal_ppf, t_ppf

LEVEL = 0.95
MIN_CLUSTERS = 10  # sessions needed before the clustered error is trusted
HANDS_PER_UNIT = 2
TO_BB100 = 100 / HANDS_PER_UNIT  # mean per unit (bb) -> bb per 100 hands
PRECISION_TARGET = 5.0  # bb/100: "hands needed for ±5 bb/100"


# --------------------------------------------------------------------------- distributions


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (modified Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 400):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularised incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(ln + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_two_sided(t: float, df: float) -> float:
    """P(|T| ≥ |t|) for Student's t with ``df`` degrees of freedom."""
    if math.isnan(t):
        return 1.0
    if math.isinf(t):
        return 0.0
    if df == math.inf or df > 1e7:
        return math.erfc(abs(t) / math.sqrt(2))
    return betainc(df / 2.0, 0.5, df / (df + t * t))


# --------------------------------------------------------------------------- estimates


def mean_var(xs: Sequence[float]) -> tuple[float, float]:
    n = len(xs)
    if n == 0:
        return 0.0, 0.0
    m = math.fsum(xs) / n
    v = math.fsum((x - m) ** 2 for x in xs) / (n - 1) if n > 1 else 0.0
    return m, v


def clustered_se(xs: Sequence[float], clusters: Sequence[Any]) -> tuple[float, int]:
    """CR1 standard error of the mean with observations grouped by ``clusters``."""
    n = len(xs)
    m = math.fsum(xs) / n if n else 0.0
    groups: dict[Any, float] = defaultdict(float)
    for x, c in zip(xs, clusters, strict=True):
        groups[c] += x - m
    g = len(groups)
    if g < 2:
        return math.inf, g
    s = math.fsum(r * r for r in groups.values())
    return math.sqrt(s * g / (g - 1)) / n, g


def estimate(
    units: Sequence[float],
    clusters: Sequence[Any] | None = None,
    *,
    dependent: bool = False,
) -> dict[str, Any]:
    """A's win rate from per-unit results (bb per two hands).

    ``dependent``: units within a cluster may be dependent (an adaptive strategy). The
    clustered error is then computed and used when larger; with fewer than
    :data:`MIN_CLUSTERS` clusters it cannot be trusted and ``dependence_warning`` is set.
    """
    n = len(units)
    mean, var = mean_var(units)
    out: dict[str, Any] = {
        "units": n,
        "hands": n * HANDS_PER_UNIT,
        "bb_per_100": mean * TO_BB100 if n else None,
        "sd_unit_bb": math.sqrt(var) if n > 1 else None,
        "se": None,
        "se_iid": None,
        "low": None,
        "high": None,
        "df": None,
        "level": LEVEL,
        "method": None,
        "clusters": None,
        "p_value": None,
        "verdict": "insufficient",
        "dependence_warning": False,
        "hands_for_precision": None,
    }
    if n < 2:
        return out
    se_iid = math.sqrt(var / n)
    se, df, method = se_iid, n - 1, "t"
    if dependent and clusters is not None:
        cse, g = clustered_se(units, clusters)
        out["clusters"] = g
        if g >= MIN_CLUSTERS:
            if cse > se:
                se, df, method = cse, g - 1, "clustered"
        else:
            out["dependence_warning"] = True
    q = t_ppf(0.5 + LEVEL / 2, df)
    lo, hi = mean - q * se, mean + q * se
    if se > 0:
        p = t_two_sided(mean / se, df)
    else:
        p = 1.0 if mean == 0 else 0.0
    zq = normal_ppf(0.5 + LEVEL / 2)
    # Units for a ±PRECISION_TARGET interval at the observed spread. se·√n is the
    # effective per-unit SD, so a clustered error carries over.
    units_needed = (zq * se * math.sqrt(n) * TO_BB100 / PRECISION_TARGET) ** 2
    out.update(
        se=se * TO_BB100,
        se_iid=se_iid * TO_BB100,
        low=lo * TO_BB100,
        high=hi * TO_BB100,
        df=df,
        method=method,
        p_value=p,
        verdict="a_better" if lo > 0 else "b_better" if hi < 0 else "unclear",
        hands_for_precision=math.ceil(units_needed) * HANDS_PER_UNIT if se > 0 else None,
    )
    return out


def holm(p_values: Sequence[float]) -> list[float]:
    """Holm step-down adjusted p-values (family-wise error control, any dependence)."""
    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [1.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * p_values[i]))
        adjusted[i] = running
    return adjusted


def combine(parts: Sequence[tuple[float, float, float]]) -> dict[str, Any]:
    """Mean of independent estimates ``(value, se, df)`` with a Welch–Satterthwaite
    interval: a strategy's average result over its matchups in a round robin."""
    k = len(parts)
    mean = math.fsum(p[0] for p in parts) / k
    var = math.fsum(p[1] ** 2 for p in parts) / k**2
    se = math.sqrt(var)
    denom = math.fsum((p[1] / k) ** 4 / p[2] for p in parts if p[2] > 0)
    df = (var**2 / denom) if denom > 0 else math.inf
    q = t_ppf(0.5 + LEVEL / 2, df) if math.isfinite(df) else normal_ppf(0.5 + LEVEL / 2)
    return {
        "bb_per_100": mean,
        "se": se,
        "low": mean - q * se,
        "high": mean + q * se,
        "df": df if math.isfinite(df) else None,
    }
