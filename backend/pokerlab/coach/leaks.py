"""Personalised leak detection: recurring, evidence-backed weaknesses.

(v0.4.4, statistics revised in v0.4.5 after calibration: see ``docs/calibration.md``.)

For every kind of decision the coach can flag, the report says how strong the evidence
is, and connects each supported weakness to practice and to how it changes over time.

**What counts as evidence.** Only the coach's own grades, made during the hand from what
the player could see (see :mod:`pokerlab.coach.review`). The result of a hand, the
opponent's cards and the run-out are never read: a lost pot is not a mistake and a won
pot is not proof of good play. A decision is *flagged* for a weakness only when

* the coach graded it questionable or a likely mistake and tagged it with that weakness,
* with medium or high confidence, and
* the verdict held against the tighter and the looser range (``robust`` is not false).

Flags that miss the last two conditions are *uncertain*: the coach thought the decision
was probably wrong but the answer depended on an assumption (the range, how much equity
a call realises, a preflop guideline). Uncertain flags never make a weakness on their own.

**Opportunities.** Each weakness is measured against the decisions where it could have
happened (calling without the odds needs a call facing a bet), never against all
decisions, so playing many hands of one kind does not dilute or inflate another.

**What a "leak" means statistically.** A weakness is *supported* when its flagged rate
is credibly above ``MIN_RATE``. The flagged rate is not the probability that the player
"has a leak", and a supported weakness is a pattern in the coach's grades, not a proof
of strategically wrong play (the coach itself rests on a one-street model).

* **Test.** An exact one-sided binomial test of "flagged rate ≤ ``MIN_RATE``", with the
  matching 90% Clopper–Pearson interval shown (its lower end is above ``MIN_RATE``
  exactly when the test passes at 5% before any correction).
* **Decisions from the same hand are not independent** (same cards, same opponent). The
  number of opportunities is replaced by an *effective* number ``n / deff``, where the
  design effect ``deff`` (at least 1) is estimated from how flags cluster within hands.
* **Multiple testing.** Up to eight weaknesses are tested on every report. Their
  p-values are corrected with Benjamini–Hochberg at ``Q_FINDINGS`` (false discovery
  rate). With no real weakness at all, this keeps the chance of *any* false leak at or
  below 5% (v0.4.4: up to 39% at the threshold). BH assumes independent or positively
  dependent tests: weaknesses either use disjoint decisions or share them (calls are
  opportunities for two kinds), which is positive dependence.
* **Repeated looks.** The report is recomputed after every hand, so a player sitting
  exactly at the threshold will eventually cross it by chance on some look. Calibration
  measures this (``docs/calibration.md``); the report states that a finding can
  disappear again, and it never reports a weakness whose rate is consistent with
  normal variation at the time.

**Statuses** (``evidence`` gives the wording the interface uses):

* ``leak`` / *supported weakness*: at least ``MIN_OPPORTUNITIES`` opportunities and
  ``MIN_FLAGGED`` flagged, and the corrected test passes.
* ``uncertain`` / *uncertain or contradictory*: the confident flags alone do not pass,
  but together with the uncertain ones they would, and at least a third of the flags
  are uncertain ones.
* ``possible`` / *possible weakness*: two or more flagged and a rate still consistent
  with a weakness (the interval reaches ``MIN_RATE``), but not enough evidence. Says
  what is missing (more chances, more flags, or a rate that clears the threshold once
  the other weaknesses tested at the same time are accounted for).
* ``strength`` / *supported strength*: at least ``STRENGTH_MIN_OPPORTUNITIES``
  opportunities graded with medium or high confidence, and an exact one-sided test that
  flagged-or-uncertain decisions among them are below ``STRENGTH_MAX_RATE`` passes
  (Benjamini–Hochberg across weaknesses). Low-confidence decisions can never be
  flagged, so they never count towards a strength.
* ``ok`` / *no clear weakness*: enough data, neither a leak nor clearly a strength. Not
  the same as a strength.
* ``insufficient`` / *insufficient evidence*: fewer than ``MIN_OPPORTUNITIES``
  opportunities and nothing flagged repeatedly.

**Concentration.** A supported leak can name conditions it is concentrated in (facing
large bets, a street, stack depth, the opponent's level). For each condition with at
least ``MIN_FLAGGED`` flagged decisions inside it, a one-sided Fisher exact test compares
the flagged rate inside with the rate outside; the p-values are corrected with
Benjamini–Yekutieli at ``Q_CONDITIONS``, which stays valid when conditions are
complementary (river versus the other streets) and therefore negatively dependent. In
v0.4.4 half of all leaks named a concentration even when none existed.

**Change over time.** Opportunities are split in time (earlier half and recent half,
and before and after the first practice aimed at the weakness). A change is reported
only when a two-sided Fisher exact test at the 5% level says so, with at least
``TREND_MIN`` opportunities on each side; otherwise "no clear change" or "not enough".
A before/after difference is observational: it does not show that practice caused it.

**Practice.** Each leak names its training concept and, when a concentration matches
something the scenario generator can reproduce exactly (a bet of at least ¾ pot, the
river), a *focus* for generated scenarios. A focus shapes the situation only, never the
answer: focused scenarios are graded by the same analysis as every other scenario.
Practice answers record which weakness they were aimed at, so their scores can be
tracked per weakness, separately for scenarios seen before and new ones.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

MIN_OPPORTUNITIES = 10
MIN_FLAGGED = 3
MIN_RATE = 0.15
STRENGTH_MIN_OPPORTUNITIES = 20
STRENGTH_MAX_RATE = 0.12
EARLY_FLAGGED = 2
TREND_MIN = 8
ALPHA = 0.05  # one-sided level of each test before correction
Q_FINDINGS = 0.05  # Benjamini–Hochberg level across weaknesses (leaks; strengths)
Q_CONDITIONS = 0.10  # Benjamini–Yekutieli level across the conditions of one leak
TREND_ALPHA = 0.05  # two-sided
INTERVAL = 0.90  # two-sided Clopper–Pearson interval shown with each rate
Z90 = 1.6449  # two-sided 90%
Z95 = 1.96
SUPPORTING_HANDS = 12
# Below this many graded decisions the report says it is too early overall.
MIN_GRADED = 20

Decision = Mapping[str, Any]
Row = tuple[Mapping[str, Any], Decision]  # (hand summary, decision)

EVIDENCE = {
    "leak": "supported_weakness",
    "uncertain": "uncertain",
    "possible": "possible",
    "strength": "supported_strength",
    "ok": "no_clear_weakness",
    "insufficient": "insufficient",
}


# --------------------------------------------------------------------------- statistics


def wilson(k: int, n: int, z: float = Z90) -> tuple[float, float]:
    """Wilson score interval for ``k`` successes in ``n`` trials (kept for reference)."""
    if n <= 0:
        return 0.0, 1.0
    k = max(0, min(k, n))
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, mid - half), min(1.0, mid + half)


def _log_pmf(k: int, n: int, p: float) -> float:
    if p <= 0:
        return 0.0 if k == 0 else -math.inf
    if p >= 1:
        return 0.0 if k == n else -math.inf
    return (
        math.lgamma(n + 1)
        - math.lgamma(k + 1)
        - math.lgamma(n - k + 1)
        + k * math.log(p)
        + (n - k) * math.log1p(-p)
    )


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
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    )
    if x < (a + 1) / (a + b + 2):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def binom_sf(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p): I_p(k, n - k + 1)."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    return min(1.0, max(0.0, betainc(k, n - k + 1, p)))


def binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p): 1 - I_p(k + 1, n - k)."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return min(1.0, max(0.0, 1.0 - betainc(k + 1, n - k, p)))


def _bisect(f, lo: float = 0.0, hi: float = 1.0) -> float:
    """The root of a function that is decreasing in p on [lo, hi]."""
    for _ in range(60):
        mid = (lo + hi) / 2
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def clopper_pearson(k: int, n: int, level: float = INTERVAL) -> tuple[float, float]:
    """Exact (Clopper–Pearson) two-sided interval for ``k`` successes in ``n`` trials."""
    if n <= 0:
        return 0.0, 1.0
    k = max(0, min(k, n))
    tail = (1 - level) / 2
    low = 0.0 if k == 0 else _bisect(lambda p: tail - binom_sf(k, n, p))
    high = 1.0 if k == n else _bisect(lambda p: binom_cdf(k, n, p) - tail)
    return low, high


def benjamini_hochberg(pvalues: Sequence[float], q: float) -> list[bool]:
    """Which hypotheses are rejected by the Benjamini–Hochberg step-up procedure."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    cut = 0
    for rank, i in enumerate(order, 1):
        if pvalues[i] <= rank * q / m:
            cut = rank
    keep = set(order[:cut])
    return [i in keep for i in range(m)]


def benjamini_yekutieli(pvalues: Sequence[float], q: float) -> list[bool]:
    """Benjamini–Yekutieli: BH at ``q / H(m)``, valid under any dependence."""
    m = len(pvalues)
    harmonic = sum(1 / i for i in range(1, m + 1)) if m else 1.0
    return benjamini_hochberg(pvalues, q / harmonic)


def adjusted_bh(pvalues: Sequence[float]) -> list[float]:
    """Benjamini–Hochberg adjusted p-values (q-values)."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i], reverse=True)
    out = [1.0] * m
    best = 1.0
    for idx, i in enumerate(order):
        rank = m - idx
        best = min(best, pvalues[i] * m / rank)
        out[i] = min(1.0, best)
    return out


def _hypergeom_log_pmf(x: int, n_in: int, n_out: int, k: int) -> float:
    def lc(a: int, b: int) -> float:
        return math.lgamma(a + 1) - math.lgamma(b + 1) - math.lgamma(a - b + 1)

    return lc(n_in, x) + lc(n_out, k - x) - lc(n_in + n_out, k)


def fisher_greater(k1: int, n1: int, k2: int, n2: int) -> float:
    """One-sided Fisher exact p-value that the rate k1/n1 exceeds k2/n2."""
    k = k1 + k2
    hi = min(k, n1)
    return min(1.0, sum(math.exp(_hypergeom_log_pmf(x, n1, n2, k)) for x in range(k1, hi + 1)))


def fisher_two_sided(k1: int, n1: int, k2: int, n2: int) -> float:
    """Two-sided Fisher exact p-value (tables no more likely than the observed one)."""
    k = k1 + k2
    lo, hi = max(0, k - n2), min(k, n1)
    obs = _hypergeom_log_pmf(k1, n1, n2, k)
    total = 0.0
    for x in range(lo, hi + 1):
        lp = _hypergeom_log_pmf(x, n1, n2, k)
        if lp <= obs + 1e-9:
            total += math.exp(lp)
    return min(1.0, total)


def two_proportion(k1: int, n1: int, k2: int, n2: int, alpha: float = TREND_ALPHA) -> bool:
    """Whether rates k1/n1 and k2/n2 differ (two-sided Fisher exact test at ``alpha``)."""
    if n1 <= 0 or n2 <= 0 or k1 + k2 in (0, n1 + n2):
        return False  # no flags, or all flagged: no evidence of a difference
    return fisher_two_sided(k1, n1, k2, n2) <= alpha


def design_effect(clusters: Sequence[tuple[int, int]]) -> float:
    """How much clustering of flags within hands inflates the variance of the rate.

    ``clusters`` holds (opportunities, flagged) per hand. Ratio-estimator variance
    against the binomial one; never below 1, so it can only make the evidence weaker.
    """
    n = sum(m for m, _ in clusters)
    k = sum(f for _, f in clusters)
    if n == 0 or k in (0, n) or len(clusters) < 2:
        return 1.0
    p = k / n
    resid = sum((f - p * m) ** 2 for m, f in clusters)
    return max(1.0, resid / (p * (1 - p) * n))


def effective(k: int, n: int, deff: float) -> tuple[int, int]:
    """Counts scaled to the effective sample size (rounded half up)."""
    if deff <= 1.0 or n == 0:
        return k, n
    n_eff = max(1, int(n / deff + 0.5))
    return min(n_eff, int(k * n_eff / n + 0.5)), n_eff


def compare(before: Sequence[bool], after: Sequence[bool]) -> dict[str, Any]:
    """Flagged rates before and after, with a verdict that needs real evidence."""
    k1, n1, k2, n2 = sum(before), len(before), sum(after), len(after)
    out: dict[str, Any] = {
        "before": {"flagged": k1, "opportunities": n1, "rate": k1 / n1 if n1 else None},
        "after": {"flagged": k2, "opportunities": n2, "rate": k2 / n2 if n2 else None},
    }
    if n1 < TREND_MIN or n2 < TREND_MIN:
        out["change"] = "insufficient"
    elif two_proportion(k1, n1, k2, n2):
        out["change"] = "improving" if k2 / n2 < k1 / n1 else "worsening"
    else:
        out["change"] = "no_clear_change"
    return out


# --------------------------------------------------------------------------- definitions


def _postflop(d: Decision) -> bool:
    return d.get("street") not in (None, "preflop")


def _facing(d: Decision) -> bool:
    return d.get("spot") == "facing_bet"


def _action(d: Decision) -> str | None:
    return d.get("action")


class Weakness:
    def __init__(
        self,
        key: str,
        title: str,
        description: str,
        concept: str,
        applies: Callable[[Decision], bool],
        tags: tuple[str, ...] | None = None,
        practice: str = "",
    ) -> None:
        self.key = key
        self.title = title
        self.description = description
        self.concept = concept
        self.applies = applies
        self.tags = tags or (key,)
        self.practice = practice


WEAKNESSES: tuple[Weakness, ...] = (
    Weakness(
        "called_without_odds",
        "Calling bets without the odds",
        "Calls facing a bet after the flop where your equity against their likely range "
        "was below the price the pot offered.",
        "pot_odds",
        lambda d: _postflop(d) and _facing(d) and _action(d) == "call",
        practice="Pot-odds spots facing a bet: work out the price first, then ask which "
        "hands bet like this. Some are folds, some are calls.",
    ),
    Weakness(
        "folded_with_odds",
        "Folding when the price was right",
        "Folds facing a bet after the flop where your equity cleared the price.",
        "pot_odds",
        lambda d: _postflop(d) and _facing(d) and _action(d) == "fold",
        practice="Pot-odds spots facing a bet: compare your equity with the price before "
        "folding. Small bets need little equity.",
    ),
    Weakness(
        "pot_odds_mixed",
        "Pot-odds decisions in both directions",
        "Facing bets after the flop you both call without the odds and fold with them: "
        "the issue is estimating equity against their range, not being too loose or "
        "too tight.",
        "pot_odds",
        lambda d: _postflop(d) and _facing(d) and _action(d) in ("call", "fold"),
        tags=("called_without_odds", "folded_with_odds"),
        practice="Pot-odds spots facing a bet, at a level where calls and folds both come "
        "up: estimate your equity before looking at the options.",
    ),
    Weakness(
        "missed_value",
        "Checking or calling strong hands",
        "Checks and calls with a strong hand (at least 50% equity) where betting or "
        "raising scored clearly better.",
        "value_betting",
        lambda d: (
            _postflop(d) and _action(d) in ("check", "call") and (d.get("equity") or 0) >= 0.5
        ),
        practice="Value-betting spots: checked to you with a hand that may be ahead of "
        "the hands that call.",
    ),
    Weakness(
        "overaggressive",
        "Betting or raising when behind",
        "Bets and raises after the flop that mostly get called by better hands.",
        "bluffing",
        lambda d: _postflop(d) and _action(d) in ("bet", "raise"),
        practice="Semi-bluff spots: bet a weak hand mainly when it has a real draw.",
    ),
    Weakness(
        "bet_sizing",
        "Bet sizes that cost value",
        "Bets and raises where another size scored clearly better.",
        "bet_sizing",
        lambda d: _action(d) in ("bet", "raise"),
        practice="Bet-sizing spots: choose the size worse hands still call.",
    ),
    Weakness(
        "preflop_loose",
        "Playing too many hands preflop",
        "Preflop calls and raises against a raise with hands below the equity needed.",
        "preflop",
        lambda d: not _postflop(d) and _action(d) in ("call", "raise", "bet"),
        practice="Preflop defence against a raise: fold, call or re-raise.",
    ),
    Weakness(
        "preflop_tight",
        "Folding too much preflop",
        "Preflop folds with hands that had the equity the price asked for.",
        "preflop",
        lambda d: not _postflop(d) and _action(d) == "fold",
        practice="Preflop defence against a raise: which hands are worth the price.",
    ),
)
WEAKNESS_KEYS = tuple(w.key for w in WEAKNESSES)


def _bet_fraction(d: Decision) -> float | None:
    pot, call = d.get("pot"), d.get("to_call")
    if not isinstance(pot, (int, float)) or not isinstance(call, (int, float)) or call <= 0:
        return None
    before = pot - call  # the pot before their bet (the pot shown includes it)
    return call / before if before > 0 else None


def _stack(d: Decision) -> float | None:
    s = d.get("stack")
    return float(s) if isinstance(s, (int, float)) and not isinstance(s, bool) else None


# key -> (label, test(decision, big_blind, difficulty), training focus or None)
CONDITIONS: dict[str, tuple[str, Callable[[Decision, int, Any], bool], str | None]] = {
    "large_bets": (
        "facing large bets (¾ pot or more)",
        lambda d, bb, lvl: (_bet_fraction(d) or 0) >= 0.75,
        "large_bets",
    ),
    "small_bets": (
        "facing small bets (under half the pot)",
        lambda d, bb, lvl: 0 < (_bet_fraction(d) or 0) < 0.5,
        None,
    ),
    "flop": ("on the flop", lambda d, bb, lvl: d.get("street") == "flop", None),
    "turn": ("on the turn", lambda d, bb, lvl: d.get("street") == "turn", None),
    "river": ("on the river", lambda d, bb, lvl: d.get("street") == "river", "river"),
    "short_stack": (
        "with 20 big blinds or less behind",
        lambda d, bb, lvl: (_stack(d) or 1e9) <= 20 * bb,
        None,
    ),
    "deep_stack": (
        "with more than 100 big blinds behind",
        lambda d, bb, lvl: (_stack(d) or 0) > 100 * bb,
        None,
    ),
    "vs_beginner": (
        "against the Beginner opponent",
        lambda d, bb, lvl: lvl == "beginner",
        None,
    ),
    "vs_intermediate": (
        "against the Intermediate opponent",
        lambda d, bb, lvl: lvl == "intermediate",
        None,
    ),
    "vs_advanced": (
        "against the Advanced opponent",
        lambda d, bb, lvl: lvl == "advanced",
        None,
    ),
}

# Which training concepts can reproduce each focus exactly (see training.FOCUSES).
FOCUS_CONCEPTS = {
    "large_bets": ("pot_odds", "draws"),
    "river": ("pot_odds", "value_betting", "bet_sizing"),
}


# --------------------------------------------------------------------------- per decision


def _flag_kind(d: Decision, tags: tuple[str, ...]) -> str | None:
    """``"flagged"``, ``"uncertain"`` or ``None`` for one decision and weakness."""
    if d.get("grade") not in ("questionable", "mistake"):
        return None  # a tag on a good decision is malformed: ignored
    if not any(t in (d.get("tags") or []) for t in tags):
        return None
    if d.get("confidence") in ("medium", "high") and d.get("robust") is not False:
        return "flagged"
    return "uncertain"


def _graded(d: Decision) -> bool:
    return d.get("grade") in ("good", "close", "questionable", "mistake")


def _hand_ref(s: Mapping[str, Any], d: Decision, kind: str | None) -> dict[str, Any]:
    """A supporting hand: what the player knew at the decision, nothing revealed later."""
    return {
        "id": s.get("id"),
        "hand_number": s.get("hand_number"),
        "t": s.get("t"),
        "decision": d.get("i"),
        "street": d.get("street"),
        "action": d.get("action"),
        "grade": d.get("grade"),
        "confidence": d.get("confidence"),
        "equity": d.get("equity"),
        "required": d.get("required"),
        "pot": d.get("pot"),
        "to_call": d.get("to_call"),
        "big_blind": s.get("big_blind"),
        "hero_cards": list(s.get("hero_cards") or []),
        "kind": kind,
    }


# --------------------------------------------------------------------------- report


def _concentration(opp: list[Row], tags: tuple[str, ...], concept: str) -> list[dict]:
    """Conditions a leak is concentrated in (Fisher exact, Benjamini–Yekutieli)."""
    tested = []
    for key, (label, test, focus) in CONDITIONS.items():
        inside, outside = [], []
        for s, d in opp:
            hit = test(d, int(s.get("big_blind") or 1), s.get("difficulty"))
            (inside if hit else outside).append(_flag_kind(d, tags) == "flagged")
        k_in, k_out = sum(inside), sum(outside)
        if k_in < MIN_FLAGGED or not outside:
            continue
        p = fisher_greater(k_in, len(inside), k_out, len(outside))
        tested.append((key, label, focus, k_in, len(inside), k_out, len(outside), p))
    keep = benjamini_yekutieli([t[-1] for t in tested], Q_CONDITIONS)
    out = []
    for (key, label, focus, k_in, n_in, k_out, n_out, p), ok in zip(tested, keep, strict=True):
        if not ok:
            continue
        out.append(
            {
                "key": key,
                "label": label,
                "flagged": k_in,
                "opportunities": n_in,
                "rate": k_in / n_in,
                "rate_elsewhere": k_out / n_out,
                "opportunities_elsewhere": n_out,
                "p_value": p,
                "tested": len(tested),
                "focus": focus if focus and concept in FOCUS_CONCEPTS[focus] else None,
            }
        )
    out.sort(key=lambda c: -c["rate"])
    return out


def answer_kinds(answers: Sequence[Mapping[str, Any]]) -> list[str]:
    """For each answer (in time order): ``"new"``, ``"repeat"`` or ``"own_hand"``.

    A generated scenario is *new* the first time its id is answered and a *repeat*
    afterwards. A scenario built from one of your own hands (id ``spot.…``) is a
    situation you already met at the table, so it is never new.
    """
    seen: set[str] = set()
    out = []
    for a in answers:
        sid = str(a.get("id") or "")
        if sid.startswith("spot."):
            out.append("own_hand")
        elif sid in seen:
            out.append("repeat")
        else:
            out.append("new")
        seen.add(sid)
    return out


def _score(answers: Sequence[Mapping[str, Any]]) -> float | None:
    credit = {"good": 1.0, "close": 0.5}
    scored = [credit.get(a.get("grade"), 0.0) for a in answers if a.get("grade") != "ungraded"]
    return sum(scored) / len(scored) if scored else None


def _practice_stats(
    key: str, answers: Sequence[Mapping[str, Any]], opp: list[Row], tags: tuple[str, ...]
) -> dict[str, Any]:
    """First answers aimed at this weakness, and the table rate before and after the
    first of them."""
    mine = sorted((a for a in answers if a.get("leak") == key), key=lambda a: a.get("t") or 0)
    kinds = answer_kinds(mine)
    scored = [a for a in mine if a.get("grade") != "ungraded"]
    out: dict[str, Any] = {
        "answers": len(mine),
        "score": _score(scored),
        "recent_score": _score(scored[-10:]),
        # Scores on situations met for the first time are the honest measure of skill;
        # repeats and your own hands can be remembered.
        "new_answers": kinds.count("new"),
        "new_score": _score([a for a, k in zip(mine, kinds, strict=True) if k == "new"]),
        "seen_answers": len(mine) - kinds.count("new"),
        "seen_score": _score([a for a, k in zip(mine, kinds, strict=True) if k != "new"]),
        "first_at": mine[0].get("t") if mine else None,
        "last_at": mine[-1].get("t") if mine else None,
        "since": None,
    }
    if mine:
        start = float(mine[0].get("t") or 0)
        before = [_flag_kind(d, tags) == "flagged" for s, d in opp if float(s.get("t")) < start]
        after = [_flag_kind(d, tags) == "flagged" for s, d in opp if float(s.get("t")) >= start]
        out["since"] = compare(before, after)
    return out


def _recommendation(w: Weakness, conditions: list[dict], evidence: list[dict]) -> dict:
    focus = next((c for c in conditions if c.get("focus")), None)
    return {
        "concept": w.concept,
        "focus": focus["focus"] if focus else None,
        "focus_label": focus["label"] if focus else None,
        "text": w.practice,
        # Your own decisions that can be practised as they were (the review's spot).
        "hands": [h for h in evidence if h["kind"] == "flagged"][:3],
    }


def _clusters(opp: list[Row], flags: list[bool]) -> list[tuple[int, int]]:
    by_hand: dict[Any, list[int]] = {}
    for (s, _), f in zip(opp, flags, strict=True):
        c = by_hand.setdefault(s.get("id"), [0, 0])
        c[0] += 1
        c[1] += int(f)
    return [(m, k) for m, k in by_hand.values()]


def find_leaks(
    summaries: Iterable[Mapping[str, Any]],
    answers: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """The leak report for a profile's hand summaries (any order) and practice answers.

    Reads only coaching grades and decision-time facts from the summaries.
    """
    hands = sorted(summaries, key=lambda s: (float(s.get("t") or 0), str(s.get("id"))))
    rows: list[Row] = [(s, d) for s in hands for d in (s.get("decisions") or []) if _graded(d)]
    confident = sum(1 for _, d in rows if d.get("confidence") in ("medium", "high"))

    # Pass 1: counts and per-weakness tests.
    stats: list[dict[str, Any]] = []
    for w in WEAKNESSES:
        opp = [(s, d) for s, d in rows if w.applies(d)]
        kinds = [_flag_kind(d, w.tags) for _, d in opp]
        n, k, u = len(opp), kinds.count("flagged"), kinds.count("uncertain")
        flags = [kd == "flagged" for kd in kinds]
        deff = design_effect(_clusters(opp, flags))
        k_e, n_e = effective(k, n, deff)
        ku_e, _ = effective(k + u, n, deff)
        judged = [kd for (_, d), kd in zip(opp, kinds, strict=True)
                  if d.get("confidence") in ("medium", "high")]  # fmt: skip
        # Flags against a strength: every flag, confident or not; low-confidence decisions
        # that were not flagged do not count for it.
        sn = len(judged) + u - judged.count("uncertain")
        sk_e, sn_e = effective(k + u, sn, deff)
        st = {
            "w": w,
            "opp": opp,
            "kinds": kinds,
            "flags": flags,
            "n": n,
            "k": k,
            "u": u,
            "deff": deff,
            "k_e": k_e,
            "n_e": n_e,
            "judged": len(judged),
            "p": binom_sf(k_e, n_e, MIN_RATE) if n_e else 1.0,
            "p_uncertain": binom_sf(ku_e, n_e, MIN_RATE) if n_e else 1.0,
            "p_strength": binom_cdf(sk_e, sn_e, STRENGTH_MAX_RATE) if sn_e else 1.0,
            "eligible": n >= MIN_OPPORTUNITIES and k >= MIN_FLAGGED,
            "strength_eligible": len(judged) >= STRENGTH_MIN_OPPORTUNITIES,
        }
        if w.key == "pot_odds_mixed":
            # Only "mixed" when both directions recur: otherwise one of the directional
            # weaknesses already describes it.
            calls = sum(
                1 for (_, d), kd in zip(opp, kinds, strict=True)
                if kd == "flagged" and "called_without_odds" in (d.get("tags") or [])
            )  # fmt: skip
            folds = k - calls
            st["split"] = {"calls": calls, "folds": folds}
            st["eligible"] = st["eligible"] and min(calls, folds) >= EARLY_FLAGGED
            st["eligible"] = st["eligible"] and min(calls, folds) * 3 >= k
            st["strength_eligible"] = False
        stats.append(st)

    # Pass 2: multiple-testing corrections, one family for leaks and one for strengths.
    fam = [st for st in stats if st["eligible"]]
    for st, ok, q in zip(fam, benjamini_hochberg([st["p"] for st in fam], Q_FINDINGS),
                         adjusted_bh([st["p"] for st in fam]), strict=True):  # fmt: skip
        st["supported"], st["q"] = ok, q
    sfam = [st for st in stats if st["strength_eligible"] and not st.get("supported")]
    for st, ok in zip(sfam, benjamini_hochberg([st["p_strength"] for st in sfam], Q_FINDINGS),
                      strict=True):  # fmt: skip
        st["strong"] = ok

    items: list[dict[str, Any]] = []
    for st in stats:
        w, opp, kinds, n, k, u = st["w"], st["opp"], st["kinds"], st["n"], st["k"], st["u"]
        low, high = clopper_pearson(st["k_e"], st["n_e"])
        evidence = [
            _hand_ref(s, d, kind)
            for (s, d), kind in sorted(
                zip(opp, kinds, strict=True), key=lambda x: -float(x[0][0].get("t") or 0)
            )
            if kind is not None
        ]
        evidence.sort(key=lambda h: h["kind"] != "flagged")  # flagged first, newest first
        item: dict[str, Any] = {
            "key": w.key,
            "title": w.title,
            "description": w.description,
            "concept": w.concept,
            "opportunities": n,
            "flagged": k,
            "uncertain": u,
            "judged": st["judged"],  # opportunities graded with medium or high confidence
            "rate": k / n if n else None,
            "low": low,
            "high": high,
            "design_effect": round(st["deff"], 3),
            "effective_opportunities": st["n_e"],
            "p_value": st["p"] if st["eligible"] else None,
            "q_value": st.get("q"),
            "hands": evidence[:SUPPORTING_HANDS],
        }
        if w.key == "pot_odds_mixed":
            if not st.get("supported"):
                continue  # not a separate finding
            item["split"] = st["split"]
        if st.get("supported"):
            item["status"] = "leak"
            item["conditions"] = _concentration(opp, w.tags, w.concept)
            half = n // 2
            item["trend"] = compare(st["flags"][:half], st["flags"][half:])
            item["practice"] = _practice_stats(w.key, answers, opp, w.tags)
            item["recommendation"] = _recommendation(w, item["conditions"], evidence)
        elif (
            n >= MIN_OPPORTUNITIES
            and u > 0
            and 2 * u >= k  # a third or more of the flags rest on an assumption
            and k + u >= MIN_FLAGGED
            and st["p_uncertain"] <= ALPHA
        ):
            item["status"] = "uncertain"
            item["missing"] = [
                f"{u} of the {k + u} flagged decisions depended on an assumption "
                "(the range, equity realisation or a guideline), so this is not reported "
                "as a leak"
            ]
        elif k >= EARLY_FLAGGED and high >= MIN_RATE:
            item["status"] = "possible"
            missing = []
            if n < MIN_OPPORTUNITIES:
                more = MIN_OPPORTUNITIES - n
                missing.append(f"{more} more chance{'s' if more != 1 else ''} at this decision")
            if k < MIN_FLAGGED:
                missing.append(f"at least {MIN_FLAGGED} confidently flagged decisions")
            if st["deff"] >= 1.2 and n >= MIN_OPPORTUNITIES:
                missing.append(
                    "more hands: several flagged decisions came from the same hands, so these "
                    f"{n} decisions count as about {st['n_e']} independent ones"
                )
            if not missing and st["eligible"] and st["p"] <= ALPHA:
                missing.append(
                    f"a clearer rate: {len(fam)} kinds of decision are checked at once, and "
                    "this one is not far enough above the threshold to stand out from chance "
                    "among them"
                )
            if not missing:
                missing.append("a clearer rate: so far it is within normal variation")
            item["missing"] = missing
        elif st.get("strong"):
            item["status"] = "strength"
        elif n >= MIN_OPPORTUNITIES:
            item["status"] = "ok"
        else:
            item["status"] = "insufficient"
            item["missing"] = [f"{MIN_OPPORTUNITIES - n} more chances at this decision"]
        item["evidence"] = EVIDENCE[item["status"]]
        items.append(item)

    order = {"leak": 0, "uncertain": 1, "possible": 2, "strength": 3, "ok": 4, "insufficient": 5}
    items.sort(key=lambda i: (order[i["status"]], -(i["low"] if i["flagged"] else 0), i["key"]))
    graded = len(rows)
    return {
        "hands": len(hands),
        "decisions": graded,
        "confident_decisions": confident,
        "status": "ready" if graded >= MIN_GRADED else "insufficient",
        "needed": max(0, MIN_GRADED - graded),
        "leaks": [i for i in items if i["status"] == "leak"],
        "strengths": [i for i in items if i["status"] == "strength"],
        "watch": [i for i in items if i["status"] in ("uncertain", "possible")],
        "other": [i for i in items if i["status"] in ("ok", "insufficient")],
        "tested": len(fam),
        "rules": {
            "min_opportunities": MIN_OPPORTUNITIES,
            "min_flagged": MIN_FLAGGED,
            "min_rate": MIN_RATE,
            "strength_min_opportunities": STRENGTH_MIN_OPPORTUNITIES,
            "strength_max_rate": STRENGTH_MAX_RATE,
            "trend_min": TREND_MIN,
            "interval": "90% Clopper–Pearson",
            "test": "exact one-sided binomial, 5%",
            "multiple_testing": f"Benjamini–Hochberg at {Q_FINDINGS:.0%} across weaknesses",
            "conditions": f"Fisher exact, Benjamini–Yekutieli at {Q_CONDITIONS:.0%}",
            "dependence": "effective sample size from within-hand clustering",
            "min_graded": MIN_GRADED,
        },
    }
