"""An opponent that decides by comparing its equity against the price it is offered.

Each decision follows the same steps:

1. Estimate the live opponent's range from public actions (:mod:`pokerlab.ai.range_model`),
   or, for a profile that does not read ranges, assume any two cards.
2. Compute all-in equity against that range: exact when cheap, otherwise Monte Carlo
   seeded from the agent's own RNG, so seeded games are reproducible.
3. Facing a bet, call when equity beats the pot odds ``call / (pot + call)`` plus a
   margin, raise for value when equity is high, otherwise fold. When the call puts the
   agent all-in, equity against pot odds is the chip-EV decision against the estimated
   range. For smaller calls the margin stands in for equity that will not be realised.
4. Not facing a bet, bet for value with high equity, sometimes bet thin or semi-bluff,
   and bluff at a low base rate.

Unopened preflop pots use the precomputed hand ranking instead of a simulation.

How the agent plays is set by an :class:`AgentProfile`. The difficulty levels in
:mod:`pokerlab.ai.difficulty` are profiles. They change *what the agent reasons about*,
not just how random it is.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace

from ..engine import Action, LegalActions, Street
from ..equity import calculate_equity
from ..ranges import HandRange
from .base import Agent, Observation, make_legal, raise_to_pot_fraction
from .preflop import hand_class, percentile
from .range_model import defending_ranges, estimate_range
from .strength import draw_bonus

ANY_HAND = HandRange.parse("any")


@dataclass(frozen=True, slots=True)
class AgentProfile:
    """Every tunable choice the equity agent makes, in one place."""

    name: str
    iterations: int = 1500  # Monte Carlo samples per decision (fewer = noisier reads)
    # What the agent knows about the opponent.
    reads_ranges: bool = True  # estimate the range from actions, or assume any two cards
    reads_checks: bool = False  # a postflop check caps the range (strongest hands bet)
    # Preflop, unopened pot: hand-ranking percentiles (0 = AA).
    open_raise: float = 0.70
    open_limp: float = 0.85
    iso_raise: float = 0.30  # big blind raising a limp
    # Equity thresholds against the estimated range.
    reraise: float = 0.62  # preflop re-raise
    value_raise: float = 0.75  # postflop raise
    value_bet: float = 0.65
    thin_bet: float = 0.52
    thin_bet_rate: float = 0.5
    call_margin: float = 0.03  # extra equity needed for calls that do not put us all-in
    position_margin: float = 0.0  # added out of position, subtracted in position
    trap_rate: float = 0.15  # check a strong hand before the river
    # Bluffing.
    bluff_rate: float = 0.10
    semi_bluff_rate: float = 0.35
    # Choose postflop bets by expected value against a defending range (see _ev_bet).
    ev_bets: bool = False
    bet_sizes: tuple[float, ...] = (0.33, 0.66, 1.0)


class EquityAgent(Agent):
    name = "equity"

    def __init__(
        self,
        rng: random.Random | None = None,
        iterations: int | None = None,
        bluff_rate: float | None = None,
        exact_limit: int = 40_000,
        profile: AgentProfile | None = None,
    ):
        from .difficulty import INTERMEDIATE  # default profile (the v0.3.2 behaviour)

        profile = profile or INTERMEDIATE
        overrides = {}
        if iterations is not None:
            overrides["iterations"] = iterations
        if bluff_rate is not None:
            overrides["bluff_rate"] = bluff_rate
        if overrides:
            profile = replace(profile, **overrides)
        self.profile = profile
        self.name = profile.name
        self.rng = rng or random.Random()
        self.exact_limit = exact_limit

    # ------------------------------------------------------------------ public

    def decide(self, obs: Observation, legal: LegalActions) -> Action:
        if obs.street == Street.PREFLOP and legal.current_bet <= obs.big_blind:
            action = self._unopened_preflop(obs, legal)
        else:
            action = self._by_equity(obs, legal)
        return make_legal(action, legal)

    def opponent_ranges(self, obs: Observation) -> list[HandRange]:
        opponents = obs.opponents_in_hand or tuple(
            s for s in range(obs.num_players) if s != obs.seat
        )
        if not self.profile.reads_ranges:
            return [ANY_HAND for _ in opponents]
        return [
            estimate_range(
                obs.actions,
                s,
                obs.button,
                obs.big_blind,
                obs.board,
                known=obs.hole,
                cap_checks=self.profile.reads_checks,
            )
            for s in opponents
        ]

    def equity(self, obs: Observation, ranges: list[HandRange] | None = None) -> float:
        """All-in equity against the opponent ranges this profile believes in."""
        result = calculate_equity(
            [obs.hole, *(ranges if ranges is not None else self.opponent_ranges(obs))],
            board=obs.board,
            iterations=self.profile.iterations,
            seed=self.rng.randrange(2**63),
            exact_limit=self.exact_limit,
        )
        return result.players[0].equity

    # ------------------------------------------------------------------ preflop

    def _unopened_preflop(self, obs: Observation, legal: LegalActions) -> Action:
        pr = self.profile
        p = percentile(hand_class(obs.hole)) + self.rng.uniform(-0.03, 0.03)
        bb = obs.big_blind
        if legal.can_check:  # big blind facing a limp
            if p < pr.iso_raise:
                return self._raise_to(legal, 4 * bb)
            return Action.check()
        if p < pr.open_raise:
            return self._raise_to(legal, int(2.5 * bb))
        if p < pr.open_limp:
            return Action.call()
        return Action.fold()

    @staticmethod
    def _raise_to(legal: LegalActions, target: int) -> Action:
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

    def required_equity(self, obs: Observation, legal: LegalActions) -> float:
        """Equity needed to call: pot odds, plus margins for calls that are not all-in."""
        call = legal.call_amount
        need = call / (obs.pot + call)
        if call >= legal.player_stack:
            return need  # all-in: no future streets to under-realise
        pr = self.profile
        position = -pr.position_margin if self._in_position(obs) else pr.position_margin
        return need + pr.call_margin + position

    def _facing_bet(self, obs: Observation, legal: LegalActions, eq: float, r: float) -> Action:
        pr = self.profile
        preflop = obs.street == Street.PREFLOP
        raise_at = pr.reraise if preflop else pr.value_raise
        if legal.can_raise and eq >= raise_at and obs.street_raises < 4 and r < 0.85:
            return raise_to_pot_fraction(obs, legal, 1.0 if preflop else 0.8)
        if eq >= self.required_equity(obs, legal):
            return Action.call()
        return Action.fold()

    def _unbet(self, obs: Observation, legal: LegalActions, eq: float, r: float) -> Action:
        pr = self.profile
        if pr.ev_bets and legal.can_raise and len(obs.opponents_in_hand) == 1:
            action = self._ev_bet(obs, legal, eq)
            if action is not None:
                return action
            if r < pr.bluff_rate:  # a small, unpredictable mix of pure bluffs
                return raise_to_pot_fraction(obs, legal, 0.5)
            return Action.check()
        river = obs.street == Street.RIVER
        if eq >= pr.value_bet:
            if not river and r < pr.trap_rate:
                return Action.check()
            return raise_to_pot_fraction(obs, legal, 0.66)
        if eq >= pr.thin_bet and r < pr.thin_bet_rate:
            return raise_to_pot_fraction(obs, legal, 0.4)
        drawing = not river and draw_bonus(obs.hole, obs.board) >= 0.14
        bluff = pr.semi_bluff_rate if drawing else pr.bluff_rate
        if r < bluff:
            return raise_to_pot_fraction(obs, legal, 0.5)
        return Action.check()

    def _ev_bet(self, obs: Observation, legal: LegalActions, eq: float) -> Action | None:
        """The bet with the highest expected value, or ``None`` when checking is better.

        For a bet of B into a pot of P, the opponent is assumed to defend at the minimum
        defence frequency ``P / (P + B)`` with the strongest part of their estimated
        range. At that frequency a pure bluff exactly breaks even, so any bet must earn
        its keep with equity when called. Then::

            EV(bet)   = fold * P + defend * (equity_vs_defenders * (P + 2B) - B)
            EV(check) = equity * P            (the hand is checked down)

        Both ignore later betting, so this is a one-street approximation, not a solver.
        """
        pot = obs.pot
        opp = self.opponent_ranges(obs)[0]
        candidates = []
        for fraction in self.profile.bet_sizes:
            action = raise_to_pot_fraction(obs, legal, fraction)
            if action.amount is not None and action not in (c[0] for c in candidates):
                bet = action.amount - legal.player_bet
                candidates.append((action, bet, pot / (pot + bet)))
        defenders = defending_ranges(opp, obs.board, [d for _, _, d in candidates])
        best_action, best_ev = None, eq * pot
        for (action, bet, defend), rng in zip(candidates, defenders, strict=True):
            eq_called = self.equity(obs, [rng])
            ev = (1 - defend) * pot + defend * (eq_called * (pot + 2 * bet) - bet)
            if ev > best_ev + 1e-9:
                best_action, best_ev = action, ev
        return best_action

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _in_position(obs: Observation) -> bool:
        # Heads-up, the button acts last on every street after the flop.
        return obs.num_players == 2 and obs.is_button and obs.street != Street.PREFLOP
