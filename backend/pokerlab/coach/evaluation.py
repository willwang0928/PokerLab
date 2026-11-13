"""Does practice show genuine improvement? (v0.4.5)

Practice scores mix three different things, and only one of them is skill:

* **repeated situations**: a scenario answered before, or a decision from your own hands
  (you met it at the table and saw its review). Improvement here can be memory.
* **new situations**: a generated scenario answered for the first time. Scenario seeds
  are drawn at random from a billion, so these are *held out*: you have never seen
  this spot. Improvement here is the honest measure of skill at the concept.
* **the table**: graded decisions in real hands, the skill that matters in the end.

**Method.** Answers to new situations are split in time into an earlier and a later
half. Because Auto difficulty raises the level as you improve, and the concept mix
changes with your plan, raw rates would confound skill with *what* you were asked: a
player who improves and is moved up a level can see a lower raw score. The halves are
therefore compared within each (concept, difficulty) stratum and combined with the
Cochran–Mantel–Haenszel test (two-sided, 5%), with the Mantel–Haenszel pooled
difference in the share answered correctly ("good") as the effect size.

**What it cannot show.** This is a before/after comparison of one person, not an
experiment: no control group, and you choose when and what to practise. It can say that
your answers to new situations got better; it cannot say practice *caused* it (you also
played hands, read reviews, and the scenarios you were offered depend on past answers).
Agreement is with PokerLab's own one-street analysis, not with optimal play. With too
few answers, the status is "still evaluating".
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from .leaks import answer_kinds, compare

MIN_NEW = 20  # answers to new situations before any verdict
MIN_STRATUM_SIDE = 3  # a stratum counts only with this many answers in each half
ALPHA = 0.05


def _correct(a: Mapping[str, Any]) -> bool:
    return a.get("grade") == "good"


def chi2_1_sf(x: float) -> float:
    """Upper tail of the chi-square distribution with one degree of freedom."""
    return math.erfc(math.sqrt(max(x, 0.0) / 2))


def cmh(strata: Sequence[tuple[int, int, int, int]]) -> dict[str, Any]:
    """Cochran–Mantel–Haenszel test over 2×2 tables (k_late, n_late, k_early, n_early).

    Returns the pooled Mantel–Haenszel risk difference (late minus early), the test
    statistic (with continuity correction) and its two-sided p-value.
    """
    num = den = 0.0
    a_sum = e_sum = v_sum = 0.0
    used = 0
    for k1, n1, k0, n0 in strata:
        n = n1 + n0
        if n1 == 0 or n0 == 0 or n < 2:
            continue
        used += 1
        w = n1 * n0 / n
        num += w * (k1 / n1 - k0 / n0)
        den += w
        m1 = k1 + k0  # correct answers in the stratum
        a_sum += k1
        e_sum += n1 * m1 / n
        v_sum += n1 * n0 * m1 * (n - m1) / (n * n * (n - 1))
    diff = num / den if den else None
    if v_sum <= 0:
        return {"difference": diff, "statistic": None, "p_value": 1.0, "strata": used}
    stat = (max(0.0, abs(a_sum - e_sum) - 0.5)) ** 2 / v_sum
    return {"difference": diff, "statistic": stat, "p_value": chi2_1_sf(stat), "strata": used}


def _halves(xs: list[Mapping[str, Any]]) -> tuple[list, list]:
    h = len(xs) // 2
    return xs[:h], xs[h:]


def _rate(xs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    k = sum(_correct(a) for a in xs)
    return {"answers": len(xs), "correct": k, "rate": k / len(xs) if xs else None}


def stratified_change(answers: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Earlier versus later half, compared within (concept, difficulty) strata."""
    early, late = _halves(answers)
    groups: dict[tuple, list[list[Mapping[str, Any]]]] = {}
    for side, xs in ((0, early), (1, late)):
        for a in xs:
            g = groups.setdefault((a.get("concept"), a.get("difficulty")), [[], []])
            g[side].append(a)
    tables = []
    for e, lt in groups.values():
        if len(e) >= MIN_STRATUM_SIDE and len(lt) >= MIN_STRATUM_SIDE:
            tables.append((sum(map(_correct, lt)), len(lt), sum(map(_correct, e)), len(e)))
    test = cmh(tables)
    compared = sum(n1 + n0 for _, n1, _, n0 in tables)
    out = {
        "early": _rate(early),
        "late": _rate(late),
        "compared": compared,  # answers in strata present in both halves
        **test,
    }
    if compared < MIN_NEW or test["strata"] == 0:
        out["change"] = "insufficient"
    elif test["p_value"] <= ALPHA and test["difference"] is not None:
        out["change"] = "improving" if test["difference"] > 0 else "declining"
    else:
        out["change"] = "no_clear_change"
    return out


def evaluate_training(
    answers: Sequence[Mapping[str, Any]],
    summaries: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """The training evaluation for a profile (answers and hand summaries, any order)."""
    ordered = sorted(
        (a for a in answers if a.get("grade") not in (None, "ungraded")),
        key=lambda a: (float(a.get("t") or 0), str(a.get("id"))),
    )
    kinds = answer_kinds(ordered)
    new = [a for a, k in zip(ordered, kinds, strict=True) if k == "new"]
    seen = [a for a, k in zip(ordered, kinds, strict=True) if k != "new"]

    held_out = stratified_change(new)
    e, lt = _halves(seen)
    familiar = {"early": _rate(e), "late": _rate(lt), "answers": len(seen)}
    familiar["change"] = compare([not _correct(a) for a in e], [not _correct(a) for a in lt])[
        "change"
    ]  # "improving" = fewer wrong answers later

    # The table: confidently flagged decisions before and after the first practice.
    table = None
    if ordered:
        start = float(ordered[0].get("t") or 0)
        before, after = [], []
        for s in summaries:
            for d in s.get("decisions") or []:
                if d.get("grade") not in ("good", "close", "questionable", "mistake"):
                    continue
                if d.get("confidence") not in ("medium", "high"):
                    continue
                flagged = d.get("grade") in ("questionable", "mistake")
                (before if float(s.get("t") or 0) < start else after).append(flagged)
        table = compare(before, after)

    if len(new) < MIN_NEW or held_out["change"] == "insufficient":
        status = "evaluating"
    else:
        status = held_out["change"]
    return {
        "status": status,
        "answers": len(ordered),
        "new_answers": len(new),
        "seen_answers": len(seen),
        "needed": max(0, MIN_NEW - len(new)),
        "held_out": held_out,
        "familiar": familiar,
        "table": table,
        "rules": {
            "min_new": MIN_NEW,
            "test": "Cochran–Mantel–Haenszel by concept and difficulty, two-sided 5%",
            "correct": "graded good",
        },
    }
