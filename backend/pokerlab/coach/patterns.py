"""Recurring weaknesses across saved hands (v0.4.3), with sample-size safeguards.

The coach already tags each flagged decision with the kind of mistake it was (calling
without the odds, folding with them, missing value, ...). One tagged decision says
little: grades are estimates, and a few hands are noise. A *pattern* is reported only
when it is supported by enough evidence:

* **Opportunities.** Each weakness is measured against the decisions where it could
  have happened (a call without the odds can only happen when you call a bet), not
  against all decisions. At least ``MIN_OPPORTUNITIES`` are needed.
* **Repeats.** At least ``MIN_FLAGGED`` flagged decisions.
* **Rate.** The lower end of a 90% Wilson interval for the flagged rate must be at least
  ``MIN_RATE``: even read pessimistically, a meaningful share of those decisions went
  wrong. With 10 opportunities that takes 4 flagged; with 30, 8.
* **Confidence.** Only grades the coach rated medium or high confidence count as
  flagged. Low-confidence grades (range- or realisation-dependent, assumed fold rates)
  still count as opportunities, so a pattern is never built from guesses.

Anything with at least two flagged decisions that does not pass is listed as *too early
to tell*, with what is missing, never as a pattern. A pattern can also name a condition
it is concentrated in (facing large bets, the river, a short stack) when the flagged rate
there is clearly higher than elsewhere. Each pattern lists the hands that support it.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from typing import Any

MIN_OPPORTUNITIES = 10
MIN_FLAGGED = 3
MIN_RATE = 0.15
Z90 = 1.6449  # two-sided 90%
EARLY_FLAGGED = 2

Decision = Mapping[str, Any]


def wilson(k: int, n: int, z: float = Z90) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, mid - half), min(1.0, mid + half)


def _postflop(d: Decision) -> bool:
    return d.get("street") != "preflop"


def _facing(d: Decision) -> bool:
    return d.get("spot") == "facing_bet"


# key: (title, description, training concept, opportunity filter)
WEAKNESSES: dict[str, tuple[str, str, str, Callable[[Decision], bool]]] = {
    "called_without_odds": (
        "Calling bets without the odds",
        "Calls where your equity against their likely range was below the price.",
        "pot_odds",
        lambda d: _postflop(d) and _facing(d) and d.get("action") == "call",
    ),
    "folded_with_odds": (
        "Folding when the price was right",
        "Folds where your equity cleared the price the pot offered.",
        "pot_odds",
        lambda d: _postflop(d) and _facing(d) and d.get("action") == "fold",
    ),
    "missed_value": (
        "Checking or calling strong hands",
        "Checks and calls with a strong hand where betting or raising scored better.",
        "value_betting",
        lambda d: (
            _postflop(d) and d.get("action") in ("check", "call") and (d.get("equity") or 0) >= 0.5
        ),
    ),
    "overaggressive": (
        "Betting or raising when behind",
        "Bets and raises that mostly get called by better hands.",
        "bluffing",
        lambda d: _postflop(d) and d.get("action") in ("bet", "raise"),
    ),
    "bet_sizing": (
        "Bet sizes that cost value",
        "Bets and raises where another size scored clearly better.",
        "bet_sizing",
        lambda d: d.get("action") in ("bet", "raise"),
    ),
    "preflop_loose": (
        "Playing too many hands preflop",
        "Preflop calls and raises with hands the guideline would fold.",
        "preflop",
        lambda d: not _postflop(d) and d.get("action") in ("call", "raise", "bet"),
    ),
    "preflop_tight": (
        "Folding too much preflop",
        "Preflop folds with hands that are usually worth playing.",
        "preflop",
        lambda d: not _postflop(d) and d.get("action") == "fold",
    ),
}


def _bet_fraction(d: Decision) -> float | None:
    pot, call = d.get("pot"), d.get("to_call")
    if not isinstance(pot, (int, float)) or not isinstance(call, (int, float)) or not call:
        return None
    before = pot - call  # the pot before their bet
    return call / before if before > 0 else None


# Conditions a pattern can be concentrated in: (label, test)
CONDITIONS: dict[str, tuple[str, Callable[[Decision, int], bool]]] = {
    "large_bets": (
        "facing large bets (¾ pot or more)",
        lambda d, bb: (_bet_fraction(d) or 0) >= 0.75,
    ),
    "river": ("on the river", lambda d, bb: d.get("street") == "river"),
    "short_stack": (
        "with 20 big blinds or less behind",
        lambda d, bb: isinstance(d.get("stack"), (int, float)) and d["stack"] <= 20 * bb,
    ),
}


def _flagged(d: Decision, tag: str) -> bool:
    return tag in (d.get("tags") or []) and d.get("confidence") in ("medium", "high")


def find_weaknesses(summaries: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Patterns and early signals from hand summaries (see :func:`history.summarize`)."""
    rows: list[tuple[Mapping[str, Any], Decision]] = []
    hands = 0
    for s in summaries:
        hands += 1
        for d in s.get("decisions") or []:
            if d.get("grade") not in (None, "ungraded"):
                rows.append((s, d))
    patterns, early = [], []
    for tag, (title, desc, concept, applies) in WEAKNESSES.items():
        opp = [(s, d) for s, d in rows if applies(d)]
        bad = [(s, d) for s, d in opp if _flagged(d, tag)]
        n, k = len(opp), len(bad)
        if k < EARLY_FLAGGED:
            continue
        low, high = wilson(k, n)
        item: dict[str, Any] = {
            "key": tag,
            "title": title,
            "description": desc,
            "concept": concept,
            "opportunities": n,
            "flagged": k,
            "rate": k / n,
            "low": low,
            "high": high,
            "hands": [
                {"id": s["id"], "decision": d.get("i"), "hand_number": s.get("hand_number"),
                 "t": s.get("t")}
                for s, d in sorted(bad, key=lambda sd: -float(sd[0].get("t") or 0))
            ][:12],
        }  # fmt: skip
        reliable = n >= MIN_OPPORTUNITIES and k >= MIN_FLAGGED and low >= MIN_RATE
        if reliable:
            item["conditions"] = _concentration(opp, tag)
            patterns.append(item)
        else:
            missing = []
            if n < MIN_OPPORTUNITIES:
                missing.append(f"{MIN_OPPORTUNITIES - n} more chances to make this decision")
            if k < MIN_FLAGGED:
                missing.append(f"at least {MIN_FLAGGED} flagged decisions")
            if not missing:
                missing.append("a clearer rate: so far it is within normal variation")
            item["missing"] = missing
            early.append(item)
    patterns.sort(key=lambda p: (-p["low"], -p["flagged"]))
    early.sort(key=lambda p: -p["flagged"])
    return {
        "hands": hands,
        "decisions": len(rows),
        "patterns": patterns,
        "early": early,
        "rules": {
            "min_opportunities": MIN_OPPORTUNITIES,
            "min_flagged": MIN_FLAGGED,
            "min_rate": MIN_RATE,
            "interval": "90% Wilson",
        },
    }


def _concentration(opp: list[tuple[Mapping[str, Any], Decision]], tag: str) -> list[dict]:
    """Conditions where the flagged rate is clearly higher than elsewhere: the rate
    inside must be at least MIN_FLAGGED flagged, and its interval must lie above the
    rate outside the condition."""
    out = []
    for key, (label, test) in CONDITIONS.items():
        inside = [d for s, d in opp if test(d, int(s.get("big_blind") or 1))]
        outside = [d for s, d in opp if not test(d, int(s.get("big_blind") or 1))]
        k_in = sum(_flagged(d, tag) for d in inside)
        if k_in < MIN_FLAGGED or not outside:
            continue
        low, _ = wilson(k_in, len(inside))
        rate_out = sum(_flagged(d, tag) for d in outside) / len(outside)
        if low > rate_out:
            out.append(
                {
                    "key": key,
                    "label": label,
                    "flagged": k_in,
                    "opportunities": len(inside),
                    "rate": k_in / len(inside),
                    "rate_elsewhere": rate_out,
                }
            )
    return out
