"""An independent estimate of what a decision cost: counterfactual rollouts.

From the exact game state at a decision (both players' real cards, the stacks and the
betting so far), the rest of the hand is played out ``R`` times after each of two
actions, with the *true* policies of both players and a freshly shuffled run-out. The
difference in the player's final stack, averaged, estimates

    Q(chosen) - Q(alternative)

for this player against this opponent, over every later street. The two branches share
their random draws (same run-out, same agent randomness), so most of the luck cancels.

This uses nothing the coach computes: no range model, no fold-frequency model, no
one-street cut-off, and it sees the opponent's real cards. It is *not* an optimal-play
benchmark either: it measures value against these particular simulated policies, and
the player keeps playing their own (possibly flawed) policy afterwards.

A single decision's estimate is noisy (the opponent's cards are fixed but the run-out
is not). Averaged over many decisions of one kind, the opponent's hands are drawn from
their true distribution at that point, so the average is an unbiased estimate of the
cost of that kind of decision as made by that player.
"""

from __future__ import annotations

import copy
import math
import random
from dataclasses import dataclass

from ..engine import Action, HoldemHand, IllegalActionError


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Everything needed to continue a hand: the engine state and both players' agents."""

    hand: HoldemHand
    agents: tuple  # (agent for seat 0, agent for seat 1)
    hero: int


def snapshot(hand: HoldemHand, agents, hero: int) -> Snapshot:
    listener = hand._listener
    hand._listener = None
    try:
        copied = copy.deepcopy((hand, tuple(agents)))
    finally:
        hand._listener = listener
    return Snapshot(copied[0], copied[1], hero)


def _reseed(agent, seed: int) -> None:
    if hasattr(agent, "reseed"):
        agent.reseed(seed)
    elif hasattr(agent, "rng"):
        agent.rng = random.Random(seed)


def _play_out(snap: Snapshot, action: Action, seed: int) -> int | None:
    hand, agents = copy.deepcopy((snap.hand, snap.agents))
    r = random.Random(seed)
    r.shuffle(hand._deck._cards)  # a fresh run-out; the hole cards are already dealt
    for i, a in enumerate(agents):
        _reseed(a, seed * 7 + i)
    try:
        hand.apply(action)
    except IllegalActionError:
        return None
    from ..ai.base import Observation

    while not hand.is_complete:
        seat = hand.to_act
        assert seat is not None
        legal = hand.legal_actions()
        hand.apply(agents[seat].decide(Observation.from_hand(hand, seat), legal))
    return hand.players[snap.hero].stack


@dataclass(frozen=True, slots=True)
class OracleResult:
    delta: float  # mean final-stack difference, chosen minus alternative (chips)
    se: float  # standard error of that mean
    n: int


def compare(snap: Snapshot, chosen: Action, alternative: Action, rollouts: int, seed: int):
    """Paired rollouts of ``chosen`` versus ``alternative`` (chips, from the decision)."""
    diffs: list[float] = []
    for k in range(rollouts):
        s = seed * 1009 + k
        a = _play_out(snap, chosen, s)
        b = _play_out(snap, alternative, s)
        if a is None or b is None:
            return None
        diffs.append(a - b)
    n = len(diffs)
    mean = sum(diffs) / n
    var = sum((d - mean) ** 2 for d in diffs) / (n - 1) if n > 1 else 0.0
    return OracleResult(mean, math.sqrt(var / n), n)
