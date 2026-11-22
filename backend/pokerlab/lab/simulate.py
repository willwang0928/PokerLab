"""Play one session of a matchup (runs in a worker process).

**Duplicate dealing.** Each deal is played at two tables. At table 1 strategy A sits in
seat 0; at table 2 the same cards are dealt with B in seat 0 and A in seat 1, with the
same button. The engine deals hole cards by seat and the board from fixed deck
positions, so at table 2 B receives exactly A's table-1 cards and position, and the
board is the same whenever the hand gets that far. Each table has its own agents, seeded
alike, so nothing learned at one table reaches the other, and identical strategies with
identical seeds act identically. Most card luck cancels in the sum of A's results at the
two tables.

**Standard dealing** is table 1 alone, so a duplicate run's table-1 results are exactly
the standard run of half as many hands with the same seed.

Every hand starts from fresh, equal stacks (a cash game with no carry-over), and the
button alternates deal by deal.
"""

from __future__ import annotations

import random
import time
from array import array
from dataclasses import dataclass, field

from ..ai.base import Observation
from ..cards import Deck
from ..engine import HoldemHand, IllegalActionError
from .config import BlockTask
from .strategies import get

# Per-strategy counters (summed over both tables). See ``_count`` for definitions.
COUNTERS = ("hands", "vpip", "pfr", "saw_flop", "showdown", "showdown_won", "post_aggr",
            "post_calls", "post_folds")  # fmt: skip
# Per-hand counters for the matchup as a whole.
HAND_COUNTERS = ("hands", "flop", "showdown", "allin", "pot_bb_x100")


@dataclass(slots=True)
class BlockResult:
    a: str
    b: str
    session: int
    deals: int
    table1: array  # A's net chips per deal at table 1
    table2: array | None  # ... at table 2 (duplicate only)
    counters: dict[str, dict[str, int]] = field(default_factory=dict)  # "a" / "b"
    hand_counters: dict[str, int] = field(default_factory=dict)
    elapsed: float = 0.0  # seconds (not part of the result; for the rate estimate)

    @property
    def hands(self) -> int:
        return self.deals * (2 if self.table2 is not None else 1)


def _play(agents, stack: int, button: int, deck: Deck, sb: int, bb: int) -> HoldemHand:
    hand = HoldemHand([stack, stack], button, sb, bb, deck=deck)
    while not hand.is_complete:
        seat = hand.to_act
        legal = hand.legal_actions()
        action = agents[seat].decide(Observation.from_hand(hand, seat), legal)
        try:
            hand.apply(action)
        except IllegalActionError as exc:  # pragma: no cover - agents must act legally
            raise RuntimeError(f"{agents[seat].name} acted illegally: {action} ({exc})") from exc
    for seat, agent in enumerate(agents):
        agent.observe_hand(hand.events, seat)
    return hand


def _count(hand: HoldemHand, stack: int, seat_of: dict[str, int], counters, totals) -> None:
    """Update per-strategy and per-hand counters from a finished hand's events.

    VPIP: put chips in voluntarily before the flop (call, bet or raise; blinds and the big
    blind's check do not count). PFR: bet or raised before the flop. Saw flop: not folded
    when the flop was dealt. Showdown: still in when hands were shown down; won: showed
    down and finished ahead. Postflop aggression counts bets and raises, calls and folds.
    """
    folded: set[int] = set()
    vpip, pfr = set(), set()
    flop_seen: set[int] | None = None
    showdown = False
    aggr = {0: 0, 1: 0}
    calls = {0: 0, 1: 0}
    folds = {0: 0, 1: 0}
    allin = False
    preflop = True
    pot = 0
    for e in hand.events:
        t = e.type
        if t == "pot_awarded":
            pot += e.data["amount"]
        elif t == "action":
            d = e.data
            s, act = d["seat"], d["action"]
            allin = allin or bool(d.get("all_in"))
            if act == "fold":
                folded.add(s)
            if preflop:
                if act in ("call", "bet", "raise"):
                    vpip.add(s)
                if act in ("bet", "raise"):
                    pfr.add(s)
            elif act in ("bet", "raise"):
                aggr[s] += 1
            elif act == "call":
                calls[s] += 1
            elif act == "fold":
                folds[s] += 1
        elif t == "street":
            if e.data["street"] == "flop":
                flop_seen = {0, 1} - folded
            preflop = False
        elif t == "showdown":
            showdown = True
    for key, seat in seat_of.items():
        c = counters[key]
        c["hands"] += 1
        c["vpip"] += seat in vpip
        c["pfr"] += seat in pfr
        if flop_seen and seat in flop_seen:
            c["saw_flop"] += 1
            if showdown and seat not in folded:
                c["showdown"] += 1
                c["showdown_won"] += hand.players[seat].stack > stack
        c["post_aggr"] += aggr[seat]
        c["post_calls"] += calls[seat]
        c["post_folds"] += folds[seat]
    totals["hands"] += 1
    totals["flop"] += flop_seen is not None
    totals["showdown"] += showdown
    totals["allin"] += allin
    totals["pot_bb_x100"] += round(100 * pot / hand.big_blind)


def run_block(task: BlockTask) -> BlockResult:
    """Play one session. Deterministic: the same task always gives the same result."""
    started = time.perf_counter()
    sa, sb_ = get(task.a), get(task.b)
    rng = random.Random(f"{task.seed_material}:{task.session}")
    a_seed, b_seed = rng.random(), rng.random()
    duplicate = task.mode == "duplicate"
    # Table 1: A in seat 0. Table 2: B in seat 0 (same seeds, so the same agents).
    t1 = [sa.make(random.Random(a_seed)), sb_.make(random.Random(b_seed))]
    t2 = [sb_.make(random.Random(b_seed)), sa.make(random.Random(a_seed))] if duplicate else None
    nets1, nets2 = array("i"), array("i") if duplicate else None
    counters = {k: dict.fromkeys(COUNTERS, 0) for k in ("a", "b")}
    totals = dict.fromkeys(HAND_COUNTERS, 0)
    for i in range(task.deals):
        deck = Deck(random.Random(rng.random()))
        deck.shuffle()
        order = deck.remaining()
        button = i % 2
        h1 = _play(t1, task.stack, button, Deck(cards=order), task.small_blind, task.big_blind)
        nets1.append(h1.players[0].stack - task.stack)
        _count(h1, task.stack, {"a": 0, "b": 1}, counters, totals)
        if t2 is not None:
            h2 = _play(t2, task.stack, button, Deck(cards=order), task.small_blind, task.big_blind)
            nets2.append(h2.players[1].stack - task.stack)
            _count(h2, task.stack, {"a": 1, "b": 0}, counters, totals)
    return BlockResult(
        a=task.a,
        b=task.b,
        session=task.session,
        deals=task.deals,
        table1=nets1,
        table2=nets2,
        counters=counters,
        hand_counters=totals,
        elapsed=time.perf_counter() - started,
    )
