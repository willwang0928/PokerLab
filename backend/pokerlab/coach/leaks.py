"""Personalised leak detection (v0.4.4): recurring, evidence-backed weaknesses.

This replaces the v0.4.3 pattern finder (``patterns.py``) with a report that says, for
every kind of decision the coach can flag, how strong the evidence is, and connects each
supported weakness to practice and to how it changes over time.

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

**Statuses.** For each weakness:

* ``leak``: at least ``MIN_OPPORTUNITIES`` opportunities, ``MIN_FLAGGED`` flagged, and
  the lower end of the 90% Wilson interval for the flagged rate at least ``MIN_RATE``.
* ``uncertain``: the flagged decisions alone do not pass, but with the uncertain ones
  they would. Reported as "the coach is not sure", never as a leak.
* ``possible``: two or more flagged, not enough yet. Says what is missing.
* ``strength``: at least ``STRENGTH_MIN_OPPORTUNITIES`` opportunities the coach graded
  with medium or high confidence, and the upper end of the 90% interval for flagged *or
  uncertain* decisions among them at most ``STRENGTH_MAX_RATE``: even read optimistically
  for a leak, these decisions are rarely flagged. Decisions graded only by a guideline
  (low confidence) can never be flagged, so they never count towards a strength.
* ``ok``: enough data, neither a leak nor clearly a strength.
* ``insufficient``: fewer than ``MIN_OPPORTUNITIES`` opportunities and nothing flagged
  repeatedly.

**Concentration.** A leak can name conditions it is concentrated in (facing large bets,
a street, stack depth, the opponent's level) when the 95% interval of the flagged rate
in that condition lies above the rate outside it. Many conditions are tested, so the
stricter interval is used and at least ``MIN_FLAGGED`` flagged decisions are needed.

**Change over time.** Opportunities are split in time (earlier half and recent half,
and before and after the first practice aimed at the weakness). A change is reported
only when a two-sided two-proportion test at the 5% level says so, with at least
``TREND_MIN`` opportunities on each side; otherwise "no clear change" or "not enough".

**Practice.** Each leak names its training concept and, when a concentration matches
something the scenario generator can reproduce exactly (a bet of at least ¾ pot, the
river), a *focus* for generated scenarios. A focus shapes the situation only, never the
answer: focused scenarios are graded by the same analysis as every other scenario.
Practice answers record which weakness they were aimed at, so their scores can be
tracked per weakness.
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
Z90 = 1.6449  # two-sided 90%
Z95 = 1.96
SUPPORTING_HANDS = 12
# Below this many graded decisions the report says it is too early overall.
MIN_GRADED = 20

Decision = Mapping[str, Any]
Row = tuple[Mapping[str, Any], Decision]  # (hand summary, decision)


# --------------------------------------------------------------------------- statistics


def wilson(k: int, n: int, z: float = Z90) -> tuple[float, float]:
    """Wilson score interval for ``k`` successes in ``n`` trials."""
    if n <= 0:
        return 0.0, 1.0
    k = max(0, min(k, n))
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, mid - half), min(1.0, mid + half)


def two_proportion(k1: int, n1: int, k2: int, n2: int, z: float = Z95) -> bool:
    """Whether rates k1/n1 and k2/n2 differ (two-sided pooled z-test at level ``z``)."""
    if n1 <= 0 or n2 <= 0:
        return False
    pooled = (k1 + k2) / (n1 + n2)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        return False  # both 0% or both 100%: no evidence of a difference
    return abs(k1 / n1 - k2 / n2) / se > z


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
    out = []
    for key, (label, test, focus) in CONDITIONS.items():
        inside, outside = [], []
        for s, d in opp:
            hit = test(d, int(s.get("big_blind") or 1), s.get("difficulty"))
            (inside if hit else outside).append(_flag_kind(d, tags) == "flagged")
        k_in = sum(inside)
        if k_in < MIN_FLAGGED or not outside:
            continue
        low, _ = wilson(k_in, len(inside), Z95)
        rate_out = sum(outside) / len(outside)
        if low > rate_out:
            out.append(
                {
                    "key": key,
                    "label": label,
                    "flagged": k_in,
                    "opportunities": len(inside),
                    "rate": k_in / len(inside),
                    "rate_elsewhere": rate_out,
                    "opportunities_elsewhere": len(outside),
                    "focus": focus if focus and concept in FOCUS_CONCEPTS[focus] else None,
                }
            )
    out.sort(key=lambda c: -c["rate"])
    return out


def _practice_stats(
    key: str, answers: Sequence[Mapping[str, Any]], opp: list[Row], tags: tuple[str, ...]
) -> dict[str, Any]:
    """First answers aimed at this weakness, and the table rate before and after the
    first of them."""
    mine = sorted((a for a in answers if a.get("leak") == key), key=lambda a: a.get("t") or 0)
    credit = {"good": 1.0, "close": 0.5}
    scored = [credit.get(a.get("grade"), 0.0) for a in mine if a.get("grade") != "ungraded"]
    out: dict[str, Any] = {
        "answers": len(mine),
        "score": sum(scored) / len(scored) if scored else None,
        "recent_score": (sum(scored[-10:]) / len(scored[-10:])) if scored else None,
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
    items: list[dict[str, Any]] = []
    for w in WEAKNESSES:
        opp = [(s, d) for s, d in rows if w.applies(d)]
        kinds = [_flag_kind(d, w.tags) for _, d in opp]
        n = len(opp)
        k = kinds.count("flagged")
        u = kinds.count("uncertain")
        low, high = wilson(k, n)
        judged = [kd for (_, d), kd in zip(opp, kinds, strict=True)
                  if d.get("confidence") in ("medium", "high")]  # fmt: skip
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
            "judged": len(judged),  # opportunities graded with medium or high confidence
            "rate": k / n if n else None,
            "low": low,
            "high": high,
            "hands": evidence[:SUPPORTING_HANDS],
        }
        reliable = n >= MIN_OPPORTUNITIES and k >= MIN_FLAGGED and low >= MIN_RATE
        if w.key == "pot_odds_mixed" and reliable:
            # Only "mixed" when both directions recur: otherwise one of the directional
            # weaknesses already describes it.
            calls = sum(
                1 for (_, d), kd in zip(opp, kinds, strict=True)
                if kd == "flagged" and "called_without_odds" in (d.get("tags") or [])
            )  # fmt: skip
            folds = k - calls
            reliable = min(calls, folds) >= EARLY_FLAGGED and min(calls, folds) * 3 >= k
            item["split"] = {"calls": calls, "folds": folds}
            if not reliable:
                continue  # not a separate finding
        elif w.key == "pot_odds_mixed":
            continue
        flags = [kd == "flagged" for kd in kinds]
        if reliable:
            item["status"] = "leak"
            item["conditions"] = _concentration(opp, w.tags, w.concept)
            half = n // 2
            item["trend"] = compare(flags[:half], flags[half:])
            item["practice"] = _practice_stats(w.key, answers, opp, w.tags)
            item["recommendation"] = _recommendation(w, item["conditions"], evidence)
        elif (
            n >= MIN_OPPORTUNITIES
            and k + u >= MIN_FLAGGED
            and wilson(k + u, n)[0] >= MIN_RATE
            and k + u >= EARLY_FLAGGED
        ):
            item["status"] = "uncertain"
            item["missing"] = [
                f"{u} of the {k + u} flagged decisions depended on an assumption "
                "(the range, equity realisation or a guideline), so this is not reported "
                "as a leak"
            ]
        elif k >= EARLY_FLAGGED:
            item["status"] = "possible"
            missing = []
            if n < MIN_OPPORTUNITIES:
                more = MIN_OPPORTUNITIES - n
                missing.append(f"{more} more chance{'s' if more != 1 else ''} at this decision")
            if k < MIN_FLAGGED:
                missing.append(f"at least {MIN_FLAGGED} confidently flagged decisions")
            if not missing:
                missing.append("a clearer rate: so far it is within normal variation")
            item["missing"] = missing
        elif (
            len(judged) >= STRENGTH_MIN_OPPORTUNITIES
            # every flag counts against it, confident or not; low-confidence decisions
            # that were not flagged do not count for it
            and wilson(k + u, len(judged) + u - judged.count("uncertain"))[1] <= STRENGTH_MAX_RATE
        ):
            item["status"] = "strength"
        elif n >= MIN_OPPORTUNITIES:
            item["status"] = "ok"
        else:
            item["status"] = "insufficient"
            item["missing"] = [f"{MIN_OPPORTUNITIES - n} more chances at this decision"]
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
        "rules": {
            "min_opportunities": MIN_OPPORTUNITIES,
            "min_flagged": MIN_FLAGGED,
            "min_rate": MIN_RATE,
            "strength_min_opportunities": STRENGTH_MIN_OPPORTUNITIES,
            "strength_max_rate": STRENGTH_MAX_RATE,
            "trend_min": TREND_MIN,
            "interval": "90% Wilson",
            "min_graded": MIN_GRADED,
        },
    }
