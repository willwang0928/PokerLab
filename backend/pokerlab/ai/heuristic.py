"""A basic rule-based opponent driven by hand strength, pot odds and a little randomness."""

from __future__ import annotations

import random

from ..engine import Action, LegalActions, Street
from .base import Agent, Observation, make_legal, raise_to_pot_fraction
from .strength import postflop_strength, preflop_strength


class HeuristicAgent(Agent):
    """Tight-aggressive-ish rules: value-bet strong hands, call with odds, bluff rarely."""

    name = "basic"

    def __init__(self, rng: random.Random | None = None, bluff_rate: float = 0.08):
        self.rng = rng or random.Random()
        self.bluff_rate = bluff_rate

    def decide(self, obs: Observation, legal: LegalActions) -> Action:
        if obs.street == Street.PREFLOP:
            action = self._preflop(obs, legal)
        else:
            action = self._postflop(obs, legal)
        return make_legal(action, legal)

    # ------------------------------------------------------------------ helpers

    def _raise_to(self, obs: Observation, legal: LegalActions, pot_fraction: float) -> Action:
        return raise_to_pot_fraction(obs, legal, pot_fraction)

    def _continue(self, legal: LegalActions) -> Action:
        return Action.call() if legal.can_call else Action.check()

    def _give_up(self, legal: LegalActions) -> Action:
        return Action.check() if legal.can_check else Action.fold()

    # ------------------------------------------------------------------ streets

    def _preflop(self, obs: Observation, legal: LegalActions) -> Action:
        s = preflop_strength(obs.hole) + self.rng.uniform(-0.06, 0.06)
        bb = obs.big_blind
        to_call_bb = legal.call_amount / bb
        unraised = legal.current_bet <= bb

        if unraised:
            if s > 0.5 or (s > 0.3 and self.rng.random() < 0.3):
                return self._raise_to_bb(obs, legal, 2.5 if obs.is_button else 3.5)
            if s > 0.18 or legal.can_check:
                return self._continue(legal)
            return Action.fold()

        # Facing a raise.
        if s > 0.82 and obs.street_raises < 4:
            return self._raise_to(obs, legal, 1.0)
        effective = to_call_bb / max(1.0, (obs.pot + legal.call_amount) / bb)  # pot odds
        if to_call_bb > 25:  # facing a big shove: need a real hand
            return self._continue(legal) if s > 0.7 else self._give_up(legal)
        if s > 0.45 or (s > 0.3 and effective < 0.3):
            return self._continue(legal)
        return self._give_up(legal)

    def _raise_to_bb(self, obs: Observation, legal: LegalActions, multiple: float) -> Action:
        if not legal.can_raise:
            return self._continue(legal)
        target = int(round(multiple * obs.big_blind))
        target = max(target, legal.min_raise_to)
        return Action(legal.raise_type, min(target, legal.max_raise_to))

    def _postflop(self, obs: Observation, legal: LegalActions) -> Action:
        s = postflop_strength(obs.hole, obs.board) + self.rng.uniform(-0.04, 0.04)
        pot_odds = legal.call_amount / (obs.pot + legal.call_amount) if legal.can_call else 0.0
        r = self.rng.random()

        if s >= 0.8:
            if legal.can_check and r < 0.15 and obs.street != Street.RIVER:
                return Action.check()  # occasional slow-play
            return self._raise_to(obs, legal, 0.75 if s < 0.92 else 1.0)

        if s >= 0.55:
            if legal.can_check:
                return self._raise_to(obs, legal, 0.5) if r < 0.7 else Action.check()
            if pot_odds < 0.4:
                if r < 0.12 and obs.street_raises < 2:
                    return self._raise_to(obs, legal, 0.8)
                return Action.call()
            return Action.call() if s > 0.7 else self._give_up(legal)

        if legal.can_check:
            if r < self.bluff_rate or (s > 0.3 and r < 0.25):
                return self._raise_to(obs, legal, 0.5)
            return Action.check()

        # Weak-ish hand facing a bet: call only with good odds (draws, middling pairs).
        if s > pot_odds + 0.08:
            return Action.call()
        return Action.fold()
