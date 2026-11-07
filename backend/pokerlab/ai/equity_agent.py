"""An opponent that decides by comparing its equity against the price it is offered.

Each decision follows the same steps:

1. Estimate every live opponent's range from their public actions
   (:mod:`pokerlab.ai.range_model`).
2. Compute our all-in equity against those ranges: exact when cheap, otherwise Monte
   Carlo seeded from the agent's own RNG, so seeded games are reproducible.
3. Facing a bet, call when equity beats the pot odds ``call / (pot + call)``, raise for
   value when equity is high, otherwise fold. When the call puts us all-in, that rule
   is exactly the chip-EV-maximising decision against the estimated range. For smaller
   calls a margin covers equity we will not fully realise on later streets.
4. Not facing a bet, bet for value with high equity, sometimes bet thin or as a
   semi-bluff, and bluff at a low base rate.

Unopened preflop pots use the precomputed hand ranking instead of a simulation.
"""

from __future__ import annotations

import random

from ..engine import Action, LegalActions, Street
from ..equity import calculate_equity
from .base import Agent, Observation, make_legal, raise_to_pot_fraction
from .preflop import hand_class, percentile
from .range_model import estimate_range
from .strength import draw_bonus


class EquityAgent(Agent):
    name = "equity"

    # Preflop, unopened pot: hand-ranking percentiles (0 = AA).
    OPEN_RAISE = 0.70  # button opening range
    OPEN_LIMP = 0.85
    ISO_RAISE = 0.30  # big blind raising a limp
    # Equity thresholds against the estimated range.
    RERAISE = 0.62
    VALUE_RAISE = 0.75
    VALUE_BET = 0.65
    THIN_BET = 0.52
    CALL_MARGIN = 0.03  # extra equity needed for calls that do not put us all-in

    def __init__(
        self,
        rng: random.Random | None = None,
        iterations: int = 1500,
        bluff_rate: float = 0.10,
        exact_limit: int = 40_000,
    ):
        self.rng = rng or random.Random()
        self.iterations = iterations
        self.bluff_rate = bluff_rate
        self.exact_limit = exact_limit

    # ------------------------------------------------------------------ public

    def decide(self, obs: Observation, legal: LegalActions) -> Action:
        if obs.street == Street.PREFLOP and legal.current_bet <= obs.big_blind:
            action = self._unopened_preflop(obs, legal)
        else:
            action = self._by_equity(obs, legal)
        return make_legal(action, legal)

    def equity(self, obs: Observation) -> float:
        """Our all-in equity against the estimated ranges of every live opponent."""
        opponents = obs.opponents_in_hand or tuple(
            s for s in range(obs.num_players) if s != obs.seat
        )
        ranges = [
            estimate_range(obs.actions, s, obs.button, obs.big_blind, obs.board, known=obs.hole)
            for s in opponents
        ]
        result = calculate_equity(
            [obs.hole, *ranges],
            board=obs.board,
            iterations=self.iterations,
            seed=self.rng.randrange(2**63),
            exact_limit=self.exact_limit,
        )
        return result.players[0].equity

    # ------------------------------------------------------------------ preflop

    def _unopened_preflop(self, obs: Observation, legal: LegalActions) -> Action:
        p = percentile(hand_class(obs.hole)) + self.rng.uniform(-0.03, 0.03)
        bb = obs.big_blind
        if legal.can_check:  # big blind facing a limp
            if p < self.ISO_RAISE:
                return self._raise_to_bb(legal, 4 * bb)
            return Action.check()
        if p < self.OPEN_RAISE:
            return self._raise_to_bb(legal, int(2.5 * bb))
        if p < self.OPEN_LIMP:
            return Action.call()
        return Action.fold()

    @staticmethod
    def _raise_to_bb(legal: LegalActions, target: int) -> Action:
        if not legal.can_raise:
            return Action.call() if legal.can_call else Action.check()
        return Action(legal.raise_type, max(legal.min_raise_to, min(legal.max_raise_to, target)))

    # ------------------------------------------------------------------ by equity

    def _by_equity(self, obs: Observation, legal: LegalActions) -> Action:
        eq = self.equity(obs)
        r = self.rng.random()
        if legal.can_call:
            return self._facing_bet(obs, legal, eq, r)
        return self._unbet(obs, legal, eq, r)

    def _facing_bet(self, obs: Observation, legal: LegalActions, eq: float, r: float) -> Action:
        call = legal.call_amount
        need = call / (obs.pot + call)
        all_in_call = call >= legal.player_stack
        preflop = obs.street == Street.PREFLOP
        raise_at = self.RERAISE if preflop else self.VALUE_RAISE
        if legal.can_raise and eq >= raise_at and obs.street_raises < 4 and r < 0.85:
            return raise_to_pot_fraction(obs, legal, 1.0 if preflop else 0.8)
        margin = 0.0 if all_in_call else self.CALL_MARGIN
        if eq >= need + margin:
            return Action.call()
        return Action.fold()

    def _unbet(self, obs: Observation, legal: LegalActions, eq: float, r: float) -> Action:
        river = obs.street == Street.RIVER
        if eq >= self.VALUE_BET:
            if not river and r < 0.15:
                return Action.check()  # occasional trap
            return raise_to_pot_fraction(obs, legal, 0.66)
        if eq >= self.THIN_BET and r < 0.5:
            return raise_to_pot_fraction(obs, legal, 0.4)
        drawing = not river and draw_bonus(obs.hole, obs.board) >= 0.14
        if r < (0.35 if drawing else self.bluff_rate):
            return raise_to_pot_fraction(obs, legal, 0.5)
        return Action.check()
