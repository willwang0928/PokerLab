"""Simulated players with known, deliberately injected behavioural tendencies.

Every policy is the PokerLab Intermediate opponent logic (:class:`EquityAgent`, a
reasonable heuristic, not an optimal strategy) plus zero or more :class:`Deviation`\\ s.
A deviation fires only in its condition, with a fixed probability, and replaces the
action the baseline logic chose. Each decision records the baseline action and which
deviation (if any) fired, so the *injected* behaviour is known exactly.

An injected deviation is a behavioural fact, not a proven mistake: calling more often
than the baseline is only a leak if those calls actually lose chips. Whether they do is
measured separately by :mod:`.oracle`, never assumed here.

Deviations use only what the player can see (their observation and legal options).
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from ..ai.base import Agent, Observation, make_legal, raise_to_pot_fraction
from ..ai.difficulty import BEGINNER, INTERMEDIATE
from ..ai.equity_agent import AgentProfile, EquityAgent
from ..engine import Action, ActionType, LegalActions, Street
from ..evaluator import HandCategory, evaluate

Condition = Callable[[Observation, LegalActions, Action], bool]
Transform = Callable[[Observation, LegalActions, Action, random.Random], Action]


# --------------------------------------------------------------------------- conditions


def postflop(obs: Observation) -> bool:
    return obs.street != Street.PREFLOP


def facing_bet(legal: LegalActions) -> bool:
    return legal.can_call and legal.call_amount > 0


def bet_fraction(obs: Observation, legal: LegalActions) -> float:
    """The bet faced as a share of the pot before it (as the leak detector measures it)."""
    before = obs.pot - legal.call_amount
    return legal.call_amount / before if before > 0 else 0.0


def is_passive(a: Action) -> bool:
    return a.type in (ActionType.CHECK, ActionType.CALL)


def is_aggressive(a: Action, legal: LegalActions) -> bool:
    if a.type == ActionType.ALL_IN:
        return legal.can_raise
    return a.type in (ActionType.BET, ActionType.RAISE)


def made_category(obs: Observation) -> HandCategory:
    return evaluate(list(obs.hole) + list(obs.board)).category


def strong_made_hand(obs: Observation) -> bool:
    """Two pair or better that uses a hole card (a pair on a paired board does not count)."""
    if len(obs.board) < 3:
        return False
    mine = made_category(obs)
    if mine < HandCategory.TWO_PAIR:
        return False
    return len(obs.board) < 5 or evaluate(list(obs.board)).category < mine


def no_made_hand(obs: Observation) -> bool:
    return len(obs.board) >= 3 and made_category(obs) == HandCategory.HIGH_CARD


# --------------------------------------------------------------------------- transforms


def to_call(obs, legal, base, rng) -> Action:
    return Action.call()


def to_fold(obs, legal, base, rng) -> Action:
    return Action.fold()


def to_check_or_call(obs, legal, base, rng) -> Action:
    return Action.call() if legal.can_call and legal.call_amount > 0 else Action.check()


def to_bet(fraction: float) -> Transform:
    def f(obs, legal, base, rng) -> Action:
        return raise_to_pot_fraction(obs, legal, fraction)

    return f


def to_random(obs, legal, base, rng) -> Action:
    options: list[Action] = []
    if facing_bet(legal):
        options += [Action.fold(), Action.call()]
    else:
        options.append(Action.check())
    if legal.can_raise:
        options.append(raise_to_pot_fraction(obs, legal, rng.choice((0.33, 0.66, 1.0, 2.0))))
    return rng.choice(options)


@dataclass(frozen=True, slots=True)
class Deviation:
    """Replace the baseline action with ``transform`` when ``applies``, with chance ``p``."""

    name: str
    description: str
    applies: Condition
    transform: Transform
    p: float
    # Hands (by index in the run) the deviation is active in: [start, stop).
    start: int = 0
    stop: int | None = None


@dataclass(frozen=True, slots=True)
class PolicySpec:
    """A simulated player: a baseline profile plus injected deviations."""

    name: str
    description: str
    profile: AgentProfile = INTERMEDIATE
    deviations: tuple[Deviation, ...] = ()
    # Weaknesses the injected deviations are *meant* to produce, with the condition they
    # are concentrated in (leak-detector keys). An expectation, not a ground truth: the
    # oracle checks whether the injected behaviour really loses chips.
    targets: tuple[str, ...] = ()
    target_conditions: tuple[str, ...] = ()
    # The opponent's level, and the hand index where it changes (if it does).
    opponent: str = "intermediate"
    opponent_after: tuple[int, str] | None = None
    notes: str = ""


@dataclass(slots=True)
class DecisionLabel:
    """What the simulated player did, and what the baseline logic would have done."""

    street: str
    base: Action
    action: Action
    deviation: str | None  # the deviation that fired, if any


class SimPlayer(Agent):
    """An agent that plays a :class:`PolicySpec` and labels every decision."""

    def __init__(self, spec: PolicySpec, rng: random.Random):
        self.spec = spec
        self.name = spec.name
        self.base = EquityAgent(random.Random(rng.random()), profile=spec.profile)
        self.dev_rng = random.Random(rng.random())
        self.hand_index = 0
        self.labels: list[DecisionLabel] = []

    def observe_hand(self, events, seat: int) -> None:
        self.base.observe_hand(events, seat)

    def reseed(self, seed: int) -> None:
        """Fresh random state (rollouts give both branches the same draws)."""
        r = random.Random(seed)
        self.base.rng = random.Random(r.random())
        self.dev_rng = random.Random(r.random())

    def decide(self, obs: Observation, legal: LegalActions) -> Action:
        base = self.base.decide(obs, legal)
        action, fired = base, None
        for dev in self.spec.deviations:
            if self.hand_index < dev.start or (
                dev.stop is not None and self.hand_index >= dev.stop
            ):
                continue
            if not dev.applies(obs, legal, base):
                continue
            if self.dev_rng.random() < dev.p:
                candidate = make_legal(dev.transform(obs, legal, base, self.dev_rng), legal)
                if candidate != base:
                    action, fired = candidate, dev.name
                break
        self.labels.append(DecisionLabel(obs.street.value, base, action, fired))
        return action


# --------------------------------------------------------------------------- deviations


def _calls_instead_of_folding(extra: Callable[[Observation, LegalActions], bool] | None = None):
    def applies(obs, legal, base) -> bool:
        ok = postflop(obs) and facing_bet(legal) and base.type == ActionType.FOLD
        return ok and (extra is None or extra(obs, legal))

    return applies


CALL_STATION = Deviation(
    "calls_too_much",
    "After the flop, facing a bet the baseline folds, call instead (70%).",
    _calls_instead_of_folding(),
    to_call,
    0.70,
)
RIVER_STATION = Deviation(
    "calls_too_much_river",
    "On the river only, facing a bet the baseline folds, call instead (85%).",
    _calls_instead_of_folding(lambda o, lg: o.street == Street.RIVER),
    to_call,
    0.85,
)
BIG_BET_STATION = Deviation(
    "calls_big_bets",
    "Facing a bet of ¾ pot or more after the flop that the baseline folds, call (85%).",
    _calls_instead_of_folding(lambda o, lg: bet_fraction(o, lg) >= 0.75),
    to_call,
    0.85,
)
OVER_FOLD = Deviation(
    "folds_too_much",
    "After the flop, facing a bet the baseline calls, fold instead (55%).",
    lambda o, lg, b: postflop(o) and facing_bet(lg) and b.type == ActionType.CALL,
    to_fold,
    0.55,
)
OVER_BLUFF = Deviation(
    "bluffs_too_much",
    "After the flop with no made hand and no bet to face, bet ⅔ pot where the baseline "
    "checks (65%).",
    lambda o, lg, b: (
        postflop(o) and not facing_bet(lg) and b.type == ActionType.CHECK and no_made_hand(o)
    ),
    to_bet(0.66),
    0.65,
)
NEVER_BLUFF = Deviation(
    "never_bluffs",
    "After the flop with no made hand, check or call where the baseline bets or raises (100%).",
    lambda o, lg, b: postflop(o) and is_aggressive(b, lg) and no_made_hand(o),
    to_check_or_call,
    1.0,
)
SLOW_PLAY = Deviation(
    "passive_with_strong_hands",
    "After the flop with two pair or better, check or call where the baseline bets or "
    "raises (75%).",
    lambda o, lg, b: postflop(o) and is_aggressive(b, lg) and strong_made_hand(o),
    to_check_or_call,
    0.75,
)
PREFLOP_LOOSE = Deviation(
    "defends_too_wide_preflop",
    "Preflop, facing a raise the baseline folds, call instead (70%).",
    lambda o, lg, b: (
        o.street == Street.PREFLOP
        and facing_bet(lg)
        and lg.current_bet > o.big_blind
        and b.type == ActionType.FOLD
    ),
    to_call,
    0.70,
)
RANDOM = Deviation(
    "random",
    "Every decision uniformly at random among fold (when facing a bet), check or call, "
    "and a bet or raise of random size.",
    lambda o, lg, b: True,
    to_random,
    1.0,
)


# --------------------------------------------------------------------------- policies

HALF = 400  # hand index where drifting policies change (runs use 800 hands)

POLICIES: tuple[PolicySpec, ...] = (
    PolicySpec(
        "baseline",
        "The Intermediate opponent logic, unmodified: no injected tendency.",
        notes="Uses the same range model as the coach, so agreement with the coach is "
        "partly shared assumptions; the oracle is independent of it.",
    ),
    PolicySpec(
        "baseline_vs_changing_opponent",
        "Baseline player; the opponent switches from Beginner to Advanced halfway.",
        opponent="beginner",
        opponent_after=(HALF, "advanced"),
    ),
    PolicySpec(
        "beginner_style",
        "The Beginner opponent logic (plays its own cards, calls slightly bad prices): "
        "weaknesses that emerge naturally rather than being injected.",
        profile=BEGINNER,
    ),
    PolicySpec(
        "calling_station",
        CALL_STATION.description,
        deviations=(CALL_STATION,),
        targets=("called_without_odds",),
    ),
    PolicySpec(
        "river_station",
        RIVER_STATION.description,
        deviations=(RIVER_STATION,),
        targets=("called_without_odds",),
        target_conditions=("river",),
    ),
    PolicySpec(
        "big_bet_station",
        BIG_BET_STATION.description,
        deviations=(BIG_BET_STATION,),
        targets=("called_without_odds",),
        target_conditions=("large_bets",),
    ),
    PolicySpec(
        "over_folder",
        OVER_FOLD.description,
        deviations=(OVER_FOLD,),
        targets=("folded_with_odds",),
    ),
    PolicySpec(
        "over_bluffer",
        OVER_BLUFF.description,
        deviations=(OVER_BLUFF,),
        targets=("overaggressive",),
    ),
    PolicySpec(
        "never_bluffs",
        NEVER_BLUFF.description,
        profile=replace(INTERMEDIATE, name="never_bluffs", bluff_rate=0.0, semi_bluff_rate=0.0),
        deviations=(NEVER_BLUFF,),
        notes="No leak-detector category describes bluffing too rarely: this measures "
        "whether the detector invents one.",
    ),
    PolicySpec(
        "slow_player",
        SLOW_PLAY.description,
        deviations=(SLOW_PLAY,),
        targets=("missed_value",),
    ),
    PolicySpec(
        "preflop_loose",
        PREFLOP_LOOSE.description,
        deviations=(PREFLOP_LOOSE,),
        targets=("preflop_loose",),
    ),
    PolicySpec(
        "station_and_bluffer",
        "Two injected tendencies at once: "
        + CALL_STATION.description
        + " "
        + OVER_BLUFF.description,
        deviations=(CALL_STATION, OVER_BLUFF),
        targets=("called_without_odds", "overaggressive"),
    ),
    PolicySpec(
        "station_then_fixed",
        "Calls too much for the first half of the run, then plays the baseline.",
        deviations=(replace(CALL_STATION, stop=HALF),),
        targets=("called_without_odds",),
        notes="Tests the trend: the recent half should improve.",
    ),
    PolicySpec(
        "random",
        RANDOM.description,
        deviations=(RANDOM,),
        targets=("called_without_odds", "folded_with_odds", "overaggressive"),
        notes="Random play should be flagged in several directions.",
    ),
)
POLICY_BY_NAME = {p.name: p for p in POLICIES}


@dataclass(slots=True)
class RunConfig:
    hands: int = 800
    session_hands: int = 100  # opponents and coach start fresh every session
    stacks_bb: tuple[int, ...] = (25, 50, 100, 150)
    big_blind: int = 10
    rollouts: int = 12  # per branch, for each decision the oracle evaluates
    oracle_per_session: int = 30  # cap on oracle evaluations per session and kind
    coach_iterations: int | None = None  # None = the production setting
    seed: int = 20261112
    # Part of the cache key: sessions graded by a different coach are not reused.
    extra: dict = field(default_factory=lambda: {"coach": "0.4.5-r2"})


# The discovery run (graded by the v0.4.4 coach, before the fixes it led to) is kept in
# its own cache folder; see docs/calibration.md.
DISCOVERY = "discovery-coach-v0.4.4"
