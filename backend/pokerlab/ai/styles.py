"""Fixed player styles for controlled experiments (and later, training opponents).

Each style is a simple, *non-adaptive* strategy built on the hand-strength heuristics in
:mod:`pokerlab.ai.strength`. It has a deliberate, measurable leak, which is what an
adaptive opponent should learn to exploit:

* ``aggressive``: plays most hands, bets and raises far too often, rarely folds.
* ``passive``: a calling station that calls almost everything and seldom bets or raises.
* ``folder``: plays tight and folds to most bets unless it holds a strong hand.

The balanced reference opponents are the existing agents (Intermediate, the v0.1 bot).
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from ..engine import Action, LegalActions, Street
from .base import Agent, Observation, make_legal, raise_to_pot_fraction
from .strength import postflop_strength, preflop_strength


@dataclass(frozen=True, slots=True)
class Style:
    name: str
    enter: float  # preflop strength needed to play a hand (lower = looser)
    raise_pre: float  # preflop strength to raise
    raise_pre_bluff: float  # chance to raise preflop with any playable hand
    bet: float  # postflop strength to bet for value
    bluff: float  # chance to bet with anything when checked to
    call: float  # postflop strength to call a bet
    raise_post: float  # postflop strength to raise a bet
    raise_bluff: float  # chance to raise a bet with anything


STYLES = {
    "aggressive": Style("aggressive", 0.10, 0.35, 0.45, 0.35, 0.60, 0.12, 0.60, 0.20),
    "passive": Style("passive", 0.12, 0.85, 0.00, 0.80, 0.00, 0.06, 0.95, 0.00),
    "folder": Style("folder", 0.40, 0.60, 0.05, 0.55, 0.05, 0.62, 0.85, 0.00),
}


class StyleAgent(Agent):
    def __init__(self, style: Style | str, rng: random.Random | None = None):
        self.style = STYLES[style] if isinstance(style, str) else style
        self.name = self.style.name
        self.rng = rng or random.Random()

    def decide(self, obs: Observation, legal: LegalActions) -> Action:
        st, r = self.style, self.rng.random()
        if obs.street == Street.PREFLOP:
            s = preflop_strength(obs.hole)
            if s < st.enter and legal.can_fold:
                return Action.fold()
            if legal.can_raise and (s >= st.raise_pre or r < st.raise_pre_bluff):
                return make_legal(raise_to_pot_fraction(obs, legal, 0.75), legal)
            return Action.call() if legal.can_call else Action.check()

        s = postflop_strength(obs.hole, obs.board)
        if legal.can_call:  # facing a bet
            if legal.can_raise and (s >= st.raise_post or r < st.raise_bluff):
                return make_legal(raise_to_pot_fraction(obs, legal, 0.8), legal)
            if s >= st.call:
                return Action.call()
            return Action.fold()
        if s >= st.bet or r < st.bluff:
            return make_legal(raise_to_pot_fraction(obs, legal, 0.6), legal)
        return Action.check()
