"""The leak detector's statistics on synthetic data with known flag rates.

This isolates the statistical layer from the coach: every decision's grade is drawn
with a known probability, so the *true* flagged rate of each weakness is known exactly
and error rates can be measured precisely (thousands of replicates in seconds).

Decisions are built in the compact form history summaries use, so they go through the
detector exactly as real ones do. Hands can hold several opportunities of one kind, with
a chosen within-hand correlation (a hand-level rate drawn from a Beta distribution with
intra-class correlation ``icc``), to test dependence between decisions of the same hand.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any

# One template per weakness: an opportunity of that kind, and the tag it gets if flagged.
TEMPLATES: dict[str, dict[str, Any]] = {
    "called_without_odds": {"spot": "facing_bet", "action": "call", "equity": 0.3},
    "folded_with_odds": {"spot": "facing_bet", "action": "fold", "equity": 0.3},
    "missed_value": {"spot": "unbet", "action": "check", "equity": 0.6},
    "overaggressive": {"spot": "unbet", "action": "bet", "equity": 0.3},
    "preflop_loose": {"spot": "facing_bet", "action": "call", "equity": 0.4, "street": "preflop"},
    "preflop_tight": {"spot": "facing_bet", "action": "fold", "equity": 0.4, "street": "preflop"},
}
STREETS = ("flop", "turn", "river")
BET_FRACTIONS = (0.33, 0.5, 0.66, 0.75, 1.0)


def _decision(key: str, flagged: bool, rng: random.Random, i: int) -> dict[str, Any]:
    t = TEMPLATES[key]
    street = t.get("street") or rng.choice(STREETS)
    frac = rng.choice(BET_FRACTIONS) if t["spot"] == "facing_bet" else None
    before = 100
    to_call = round(frac * before) if frac else 0
    return {
        "i": i,
        "street": street,
        "spot": t["spot"],
        "action": t["action"],
        "grade": "questionable" if flagged else "good",
        "confidence": "medium",
        "robust": True,
        "tags": [key] if flagged else [],
        "pot": before + to_call,
        "to_call": to_call,
        "stack": rng.choice((200, 500, 1000, 1500)),
        "equity": t["equity"],
        "required": to_call / (before + 2 * to_call) if to_call else None,
    }


def synthetic_hands(
    rates: dict[str, float],
    hands: int,
    rng: random.Random,
    per_hand: Callable[[random.Random], int] = lambda r: 1,
    icc: float = 0.0,
    t0: float = 0.0,
) -> list[dict[str, Any]]:
    """Hand summaries where each listed weakness has the given true flagged rate.

    Every hand holds ``per_hand(rng)`` opportunities of each listed weakness.
    """
    out = []
    for h in range(hands):
        decisions = []
        for key, p in rates.items():
            if icc > 0 and 0 < p < 1:
                scale = 1 / icc - 1
                ph = rng.betavariate(p * scale, (1 - p) * scale)
            else:
                ph = p
            for _ in range(per_hand(rng)):
                decisions.append(_decision(key, rng.random() < ph, rng, len(decisions)))
        out.append(
            {
                "id": f"s{t0 + h:.0f}-{rng.randrange(10**9)}",
                "t": t0 + h,
                "hand_number": h + 1,
                "big_blind": 10,
                "difficulty": rng.choice(("beginner", "intermediate", "advanced")),
                "hero_cards": ["As", "Kd"],
                "decisions": decisions,
            }
        )
    return out


def reported(report: dict[str, Any], status: str) -> set[str]:
    group = {"leak": "leaks", "strength": "strengths"}[status]
    return {i["key"] for i in report[group]}


def rate(detector, trial: Callable[[random.Random], bool], reps: int, seed: int) -> float:
    rng = random.Random(seed)
    return sum(trial(rng) for _ in range(reps)) / reps


# --------------------------------------------------------------------------- experiments


def leak_probability(detector, p: float, n: int, reps: int, seed: int, icc: float = 0.0,
                     per_hand: int = 1) -> float:  # fmt: skip
    """P(reported as a leak) for one weakness with true rate ``p`` over ``n`` opportunities."""
    hands = max(1, n // per_hand)

    def trial(rng):
        s = synthetic_hands({"called_without_odds": p}, hands, rng, lambda r: per_hand, icc)
        return "called_without_odds" in reported(detector.find_leaks(s), "leak")

    return rate(detector, trial, reps, seed)


def familywise(detector, p: float, n: int, reps: int, seed: int) -> float:
    """P(at least one false leak) when all six directional weaknesses sit at rate ``p``."""

    def trial(rng):
        s = synthetic_hands(dict.fromkeys(TEMPLATES, p), n, rng)
        return bool(reported(detector.find_leaks(s), "leak"))

    return rate(detector, trial, reps, seed)


def sequential(detector, p: float, n: int, every: int, reps: int, seed: int) -> float:
    """P(ever reported as a leak) when the report is re-run every ``every`` hands."""

    def trial(rng):
        s = synthetic_hands({"called_without_odds": p}, n, rng)
        for k in range(every, n + 1, every):
            if "called_without_odds" in reported(detector.find_leaks(s[:k]), "leak"):
                return True
        return False

    return rate(detector, trial, reps, seed)


def spurious_concentration(detector, p: float, n: int, reps: int, seed: int) -> float:
    """P(a leak names a concentration) when the true rate is the same everywhere."""

    def trial(rng):
        s = synthetic_hands({"called_without_odds": p}, n, rng)
        leaks = detector.find_leaks(s)["leaks"]
        return any(item.get("conditions") for item in leaks)

    return rate(detector, trial, reps, seed)


def true_concentration(detector, p_in: float, p_out: float, n: int, reps: int, seed: int):
    """P(the river is named) when the rate is ``p_in`` on the river and ``p_out`` elsewhere."""

    def trial(rng):
        s = synthetic_hands({"called_without_odds": p_out}, n, rng)
        for h in s:
            for d in h["decisions"]:
                if d["street"] == "river":
                    f = rng.random() < p_in
                    d["grade"] = "questionable" if f else "good"
                    d["tags"] = ["called_without_odds"] if f else []
        leaks = detector.find_leaks(s)["leaks"]
        return any(c["key"] == "river" for item in leaks for c in item.get("conditions") or [])

    return rate(detector, trial, reps, seed)


def spurious_trend(detector, p: float, n: int, reps: int, seed: int) -> float:
    """P(a leak's trend says improving or worsening) when the rate never changes."""

    def trial(rng):
        s = synthetic_hands({"called_without_odds": p}, n, rng)
        leaks = detector.find_leaks(s)["leaks"]
        return any(i.get("trend", {}).get("change") in ("improving", "worsening") for i in leaks)

    return rate(detector, trial, reps, seed)


def strength_probability(detector, p: float, n: int, reps: int, seed: int) -> float:
    def trial(rng):
        s = synthetic_hands({"called_without_odds": p}, n, rng)
        return "called_without_odds" in reported(detector.find_leaks(s), "strength")

    return rate(detector, trial, reps, seed)


def run(detector, reps: int = 400, seed: int = 7) -> dict[str, Any]:
    """Every experiment for one detector module (``find_leaks``)."""
    ns = (10, 20, 40, 80, 160, 320)
    out: dict[str, Any] = {"reps": reps}
    out["leak_by_rate"] = {
        f"{p:.2f}": {str(n): leak_probability(detector, p, n, reps, seed + n) for n in ns}
        for p in (0.05, 0.10, 0.15, 0.25, 0.35, 0.50)
    }
    out["familywise"] = {
        f"{p:.2f}": {str(n): familywise(detector, p, n, reps, seed + 1 + n) for n in (40, 160)}
        for p in (0.10, 0.15)
    }
    out["sequential"] = {
        f"{p:.2f}": sequential(detector, p, 300, 5, max(100, reps // 4), seed + 2)
        for p in (0.10, 0.15)
    }
    out["dependence"] = {
        f"icc={icc}": leak_probability(detector, 0.15, 120, reps, seed + 3, icc=icc, per_hand=3)
        for icc in (0.0, 0.3, 0.6)
    }
    out["spurious_concentration"] = {
        str(n): spurious_concentration(detector, 0.35, n, reps, seed + 4 + n) for n in (40, 160)
    }
    out["true_concentration"] = {
        str(n): true_concentration(detector, 0.6, 0.15, n, reps, seed + 5 + n)
        for n in (60, 150, 300)
    }
    out["spurious_trend"] = {
        str(n): spurious_trend(detector, 0.35, n, reps, seed + 6 + n) for n in (20, 40, 160)
    }
    out["strength_by_rate"] = {
        f"{p:.2f}": {
            str(n): strength_probability(detector, p, n, reps, seed + 7 + n)
            for n in (20, 40, 80, 160)
        }  # fmt: skip
        for p in (0.0, 0.05, 0.12, 0.20)
    }
    return out
