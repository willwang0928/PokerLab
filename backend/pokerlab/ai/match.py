"""Heads-up agent-vs-agent evaluation with duplicate deals.

Poker results are dominated by card luck. In a *duplicate* match every deal is played
twice with the same cards, and the agents swap seats the second time. Each agent
therefore receives exactly the cards the other one had, and most of the luck cancels.
The paired results are independent across deals, which gives an honest standard error
for the win rate.

Every hand starts from fresh, equal stacks (a cash game with no carry-over), so one big
pot cannot end the match.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass

from ..cards import Deck
from ..engine import HoldemHand, IllegalActionError
from .base import Agent, Observation

AgentFactory = Callable[[random.Random], Agent]


@dataclass(frozen=True, slots=True)
class MatchResult:
    deals: int  # each deal is two hands
    big_blind: int
    net_chips: int  # agent A's total winnings
    bb_per_100: float  # A's win rate in big blinds per 100 hands
    std_error: float  # standard error of bb_per_100

    @property
    def z_score(self) -> float:
        return self.bb_per_100 / self.std_error if self.std_error else math.inf


def play_hand(agents: list[Agent], stacks: list[int], button: int, deck: Deck, sb: int, bb: int):
    hand = HoldemHand(stacks, button, sb, bb, deck=deck)
    while not hand.is_complete:
        seat = hand.to_act
        assert seat is not None
        legal = hand.legal_actions()
        action = agents[seat].decide(Observation.from_hand(hand, seat), legal)
        try:
            hand.apply(action)
        except IllegalActionError as exc:
            raise AssertionError(f"{agents[seat].name} acted illegally: {action} ({exc})") from exc
    return hand


def duplicate_match(
    make_a: AgentFactory,
    make_b: AgentFactory,
    deals: int,
    *,
    seed: int = 0,
    stack: int = 1000,
    small_blind: int = 5,
    big_blind: int = 10,
) -> MatchResult:
    """Play ``deals`` duplicate pairs of hands between agents A and B."""
    if deals < 2:
        raise ValueError("A duplicate match needs at least two deals")
    rng = random.Random(seed)
    a = make_a(random.Random(rng.random()))
    b = make_b(random.Random(rng.random()))
    pair_results: list[int] = []
    for i in range(deals):
        deck = Deck(random.Random(rng.random()))
        deck.shuffle()
        order = deck.remaining()
        button = i % 2
        total = 0
        for a_seat in (0, 1):
            agents = [a, b] if a_seat == 0 else [b, a]
            hand = play_hand(
                agents, [stack, stack], button, Deck(cards=order), small_blind, big_blind
            )
            total += hand.players[a_seat].stack - stack
        pair_results.append(total)

    n = len(pair_results)
    mean = sum(pair_results) / n
    var = sum((x - mean) ** 2 for x in pair_results) / (n - 1)
    to_bb100 = 100 / (2 * big_blind)  # per pair (two hands) -> bb per 100 hands
    return MatchResult(
        deals=n,
        big_blind=big_blind,
        net_chips=sum(pair_results),
        bb_per_100=mean * to_bb100,
        std_error=math.sqrt(var / n) * to_bb100,
    )
