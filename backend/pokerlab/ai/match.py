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


def _observe(agents: list[Agent], hand: HoldemHand) -> None:
    """Let learning agents see the finished hand's public events."""
    for seat, agent in enumerate(agents):
        agent.observe_hand(hand.events, seat)


def _summary(per_deal: list[float], big_blind: int, hands_per_deal: int) -> MatchResult:
    n = len(per_deal)
    mean = sum(per_deal) / n
    var = sum((x - mean) ** 2 for x in per_deal) / (n - 1)
    scale = 100 / (hands_per_deal * big_blind)
    return MatchResult(
        deals=n,
        big_blind=big_blind,
        net_chips=round(sum(per_deal)),
        bb_per_100=mean * scale,
        std_error=math.sqrt(var / n) * scale,
    )


@dataclass(frozen=True, slots=True)
class PairedComparison:
    """Two agents against identical copies of one opponent, on identical cards."""

    a: MatchResult
    b: MatchResult
    difference: MatchResult  # A minus B, paired per deal


def paired_comparison(
    make_a: AgentFactory,
    make_b: AgentFactory,
    make_opponent: AgentFactory,
    deals: int,
    *,
    seed: int = 0,
    stack: int = 1000,
    small_blind: int = 5,
    big_blind: int = 10,
) -> PairedComparison:
    """Compare A and B by playing each against its own copy of the same opponent.

    The two copies start from the same random seed, and each deal is played from both
    seats with the same cards for A and B. Each deal therefore gives a *paired*
    difference A − B, and the luck that both arms share cancels out. Each arm keeps its
    own state, so a learning agent only learns from its own games.
    """
    if deals < 2:
        raise ValueError("A paired comparison needs at least two deals")
    rng = random.Random(seed)
    agent_seed, opp_seed = rng.random(), rng.random()
    arms = [
        (make_a(random.Random(agent_seed)), make_opponent(random.Random(opp_seed))),
        (make_b(random.Random(agent_seed)), make_opponent(random.Random(opp_seed))),
    ]
    results: list[list[int]] = [[], []]
    for i in range(deals):
        deck = Deck(random.Random(rng.random()))
        deck.shuffle()
        order = deck.remaining()
        button = i % 2
        for arm, (agent, opponent) in enumerate(arms):
            total = 0
            for seat in (0, 1):
                agents = [agent, opponent] if seat == 0 else [opponent, agent]
                hand = play_hand(
                    agents, [stack, stack], button, Deck(cards=order), small_blind, big_blind
                )
                _observe(agents, hand)
                total += hand.players[seat].stack - stack
            results[arm].append(total)
    diffs = [x - y for x, y in zip(results[0], results[1], strict=True)]
    return PairedComparison(
        a=_summary(results[0], big_blind, 2),
        b=_summary(results[1], big_blind, 2),
        difference=_summary(diffs, big_blind, 2),
    )


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
            _observe(agents, hand)
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


def _level_factory(level: str) -> AgentFactory:
    from .difficulty import make_agent
    from .heuristic import HeuristicAgent

    if level == "basic":
        return lambda rng: HeuristicAgent(rng)
    return lambda rng: make_agent(level, rng)


def _run_chunk(args: tuple[str, str, int, int]) -> MatchResult:
    a, b, deals, seed = args
    return duplicate_match(_level_factory(a), _level_factory(b), deals, seed=seed)


def pool(results: list[MatchResult]) -> MatchResult:
    """Combine independent matches (different seeds) into one estimate."""
    deals = sum(r.deals for r in results)
    bb100 = sum(r.bb_per_100 * r.deals for r in results) / deals
    se = math.sqrt(sum((r.std_error * r.deals) ** 2 for r in results)) / deals
    return MatchResult(
        deals=deals,
        big_blind=results[0].big_blind,
        net_chips=sum(r.net_chips for r in results),
        bb_per_100=bb100,
        std_error=se,
    )


def main(argv: list[str] | None = None) -> int:
    """``python -m pokerlab.ai.match advanced intermediate --deals 2000 --jobs 4``"""
    import argparse
    from concurrent.futures import ProcessPoolExecutor

    levels = ["beginner", "intermediate", "advanced", "basic"]
    parser = argparse.ArgumentParser(prog="python -m pokerlab.ai.match")
    parser.add_argument("a", choices=levels)
    parser.add_argument("b", choices=levels)
    parser.add_argument("--deals", type=int, default=1000, help="duplicate deals (2 hands each)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--jobs", type=int, default=1, help="parallel processes (one seed each)")
    args = parser.parse_args(argv)
    jobs = max(1, min(args.jobs, args.deals // 2))
    chunks = [(args.a, args.b, args.deals // jobs, args.seed + i) for i in range(jobs)]
    if jobs == 1:
        results = [_run_chunk(chunks[0])]
    else:
        with ProcessPoolExecutor(jobs) as ex:
            results = list(ex.map(_run_chunk, chunks))
    r = pool(results)
    print(
        f"{args.a} vs {args.b}: {r.bb_per_100:+.1f} ± {r.std_error:.1f} bb/100 "
        f"(z = {r.z_score:+.1f}) over {r.deals} deals / {2 * r.deals} hands"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
