"""Statistical model of an opponent's observable tendencies (a "HUD").

The model reads only the public event stream of finished hands: blinds, actions,
streets, who reached showdown and who won. It never reads card contents, not even cards
shown at showdown, so it cannot learn anything the human could not see in the hand
history.

**Statistics.** Each one is a frequency with an explicit opportunity count:

=====================  ==================================================  ==============
Stat                   Counts                                              Opportunity
=====================  ==================================================  ==============
VPIP                   put money in preflop voluntarily (call or raise)    acted preflop
PFR                    raised preflop                                      acted preflop
Aggression (AFq)       postflop bets and raises                            postflop bet,
                                                                           raise, call or
                                                                           fold
Fold to preflop raise  folded facing a preflop raise                       faced one
Bet when checked to    bet postflop when no bet was in front               could bet
Raise vs a bet         raised facing a postflop bet or raise               faced one
Fold to bet            folded facing a postflop bet or raise               faced one
Went to showdown       reached showdown                                    saw the flop
Won at showdown        won chips at showdown                               reached it
=====================  ==================================================  ==============

**Estimation.** A raw frequency from a handful of hands is noise. Each stat is therefore
estimated as the posterior mean of a Beta prior centred on a *baseline* (see
``BASELINES``) and worth ``PRIOR_STRENGTH`` pseudo-observations::

    estimate = (count + baseline * PRIOR_STRENGTH) / (opportunities + PRIOR_STRENGTH)

The estimate starts at the baseline and moves towards the observed rate as evidence
accumulates. A stat is not used for decisions until it has ``MIN_OPPORTUNITIES``.
Older hands are discounted by ``DECAY`` per hand (an effective memory of about 150
hands), so the model follows a player who changes style instead of anchoring on their
first hundred hands.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from ..engine import Event, Street

PRIOR_STRENGTH = 12.0
MIN_OPPORTUNITIES = 15.0
DECAY = 0.993

# Baselines: the prior centre of each estimate and the reference that deviations are
# judged against. They are the frequencies the opponent's default decision model is
# built around, not a judgement of good play:
# * VPIP, PFR, fold to preflop raise, aggression, bet when checked to, raise vs a bet
#   and went-to-showdown: Intermediate's own frequencies in heads-up self-play
#   (2026-11-07, 6,000-8,000 player-hands: 0.83, 0.43, 0.19, 0.51, 0.34, 0.06, 0.25).
# * Fold to bet: 0.40, roughly what the minimum-defence-frequency assumption behind
#   expected-value betting implies for half-pot to pot-sized bets. (Intermediate itself
#   folds 0.585, which is exploitable, so self-play is the wrong reference here.)
# * Won at showdown: 0.50 by symmetry.
BASELINES = {
    "vpip": 0.83,
    "pfr": 0.43,
    "fold_to_raise_pre": 0.19,
    "aggression": 0.51,
    "bet_unopposed": 0.34,
    "raise_vs_bet": 0.06,
    "fold_to_bet": 0.40,
    "wtsd": 0.25,
    "wsd": 0.50,
}

LABELS = {
    "vpip": "VPIP",
    "pfr": "Preflop raise",
    "fold_to_raise_pre": "Fold to preflop raise",
    "bet_unopposed": "Bet when checked to",
    "raise_vs_bet": "Raise vs a bet",
    "aggression": "Aggression",
    "fold_to_bet": "Fold to bet",
    "wtsd": "Went to showdown",
    "wsd": "Won at showdown",
}

HINTS = {
    "vpip": "Hands where you put money in preflop by choice",
    "pfr": "Hands where you raised preflop",
    "fold_to_raise_pre": "How often you fold when facing a preflop raise",
    "bet_unopposed": "How often you bet postflop when nobody has bet yet",
    "raise_vs_bet": "How often you raise when facing a postflop bet",
    "aggression": "Share of your postflop bets and raises among bets, raises, calls and folds",
    "fold_to_bet": "How often you fold when facing a postflop bet or raise",
    "wtsd": "Hands that reached showdown, of those where you saw the flop",
    "wsd": "Showdowns where you won chips",
}


@dataclass(slots=True)
class Stat:
    count: float = 0.0
    opportunities: float = 0.0

    def add(self, hit: bool) -> None:
        self.opportunities += 1.0
        self.count += 1.0 if hit else 0.0

    def decay(self, factor: float) -> None:
        self.count *= factor
        self.opportunities *= factor

    @property
    def rate(self) -> float | None:
        return self.count / self.opportunities if self.opportunities > 0 else None

    def estimate(self, baseline: float) -> float:
        return (self.count + baseline * PRIOR_STRENGTH) / (self.opportunities + PRIOR_STRENGTH)


@dataclass(slots=True)
class PlayerModel:
    """Tendencies of one opponent, learned from finished hands."""

    stats: dict[str, Stat] = field(default_factory=lambda: {k: Stat() for k in BASELINES})
    hands: int = 0

    def estimate(self, key: str) -> float:
        return self.stats[key].estimate(BASELINES[key])

    def reliable(self, key: str) -> bool:
        return self.stats[key].opportunities >= MIN_OPPORTUNITIES

    def observe(self, events: Iterable[Event], seat: int) -> None:
        """Update from one finished hand's public events, tracking ``seat``."""
        for s in self.stats.values():
            s.decay(DECAY)
        self.hands += 1
        observation = observe_hand(events, seat)
        for key, hit in observation:
            self.stats[key].add(hit)

    def snapshot(self) -> list[dict[str, object]]:
        """Display-ready stats: raw rate, sample, estimate, baseline, reliability."""
        out = []
        for key, s in self.stats.items():
            out.append(
                {
                    "key": key,
                    "label": LABELS[key],
                    "hint": HINTS[key],
                    "rate": s.rate,
                    "count": round(s.count, 1),
                    "opportunities": round(s.opportunities, 1),
                    "estimate": self.estimate(key),
                    "baseline": BASELINES[key],
                    "reliable": self.reliable(key),
                }
            )
        return out


_PUBLIC_EVENTS = {"post_blind", "action", "street", "showdown", "pot_awarded"}


def observe_hand(events: Iterable[Event], seat: int) -> list[tuple[str, bool]]:
    """Stat observations for ``seat`` in one hand, as ``(stat, hit)`` pairs.

    Only action, blind, street and outcome events are read, and of the showdown event
    only *which seats* took part. Hole cards never enter the model.
    """
    out: list[tuple[str, bool]] = []
    street = Street.PREFLOP
    bets: dict[int, int] = {}
    acted_preflop = vpip = pfr = False
    raised_preflop = False  # has anyone raised preflop (beyond the blinds)?
    folded = saw_flop = showdown = False
    all_in: set[int] = set()  # seats with no chips left (they cannot be raised)

    for ev in events:
        if ev.type not in _PUBLIC_EVENTS:
            continue
        d = ev.data
        if ev.type == "post_blind":
            bets[d["seat"]] = d["amount"]
            if d.get("all_in"):
                all_in.add(d["seat"])
        elif ev.type == "street":
            street = Street(d["street"])
            bets = {}
            if street == Street.FLOP and not folded:
                saw_flop = True
        elif ev.type == "action":
            actor, kind = d["seat"], d["action"]
            if actor == seat:
                facing = max((v for s, v in bets.items() if s != seat), default=0) > bets.get(
                    seat, 0
                )
                if street == Street.PREFLOP:
                    acted_preflop = True
                    vpip |= kind in ("call", "bet", "raise")
                    pfr |= kind in ("bet", "raise")
                    if facing and raised_preflop:
                        out.append(("fold_to_raise_pre", kind == "fold"))
                else:
                    if kind in ("bet", "raise", "call", "fold"):
                        out.append(("aggression", kind in ("bet", "raise")))
                    if facing:
                        out.append(("fold_to_bet", kind == "fold"))
                        # Raising is only possible against someone with chips behind,
                        # and not when calling already takes the whole stack.
                        call_all_in = kind == "call" and d.get("all_in")
                        if not call_all_in and any(s not in all_in for s in bets if s != seat):
                            out.append(("raise_vs_bet", kind == "raise"))
                    elif kind in ("bet", "check"):
                        out.append(("bet_unopposed", kind == "bet"))
                if kind == "fold":
                    folded = True
            if d.get("all_in"):
                all_in.add(actor)
            if street == Street.PREFLOP and kind in ("bet", "raise"):
                raised_preflop = True
            bets[actor] = d["bet"]
        elif ev.type == "showdown":
            showdown = seat in {int(s) for s in d["hands"]}
        elif ev.type == "pot_awarded" and showdown and d.get("pot_index") == 0:
            out.append(("wsd", seat in d["winners"]))

    if acted_preflop:
        out += [("vpip", vpip), ("pfr", pfr)]
    if saw_flop:
        out.append(("wtsd", showdown))
    return out
