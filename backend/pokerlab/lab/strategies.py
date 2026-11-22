"""The strategies the Simulation Lab can pit against each other.

Every entry wraps an existing agent unchanged: the three difficulty levels (adaptive, as
played at the table, or with adaptation switched off), the v0.1 heuristic bot, the three
fixed styles with deliberate leaks used in the adaptation experiments, and a uniformly
random player.

``cost_ms`` is the agent's share of the time per hand on one core against an opponent
that ends hands early (a simple bot); ``cost_deep_ms`` against one that takes hands deep
(the equity-based levels and the calling station), where every street means another
equity calculation. Measured single-process in November 2026 on a 4-core Intel laptop
with Python 3.14 (Intermediate vs v0.1 bot 14 ms per hand, Intermediate vs Intermediate
61 ms, Advanced vs Intermediate 122 ms). They drive the time estimate only.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, replace

from ..ai.base import Agent
from ..ai.difficulty import ADVANCED, BEGINNER, INTERMEDIATE
from ..ai.equity_agent import AgentProfile, EquityAgent
from ..ai.heuristic import HeuristicAgent
from ..ai.random_agent import RandomAgent
from ..ai.styles import StyleAgent


@dataclass(frozen=True, slots=True)
class Strategy:
    id: str
    label: str
    family: str  # "opponent" (PokerLab's levels), "reference" (bots) or "style"
    description: str
    adaptive: bool  # learns about its opponent during a session
    cost_ms: float  # its decision time per hand against a simple bot (module docstring)
    cost_deep_ms: float  # ... against an opponent that takes hands deep
    deep: bool  # this strategy takes hands deep (raises its opponent's cost)
    factory: Callable[[random.Random], Agent]

    def make(self, rng: random.Random) -> Agent:
        return self.factory(rng)

    def view(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "family": self.family,
            "description": self.description,
            "adaptive": self.adaptive,
            "cost_ms": self.cost_ms,
            "cost_deep_ms": self.cost_deep_ms,
        }


def _equity(profile: AgentProfile) -> Callable[[random.Random], Agent]:
    return lambda rng: EquityAgent(rng, profile=profile)


def _style(name: str) -> Callable[[random.Random], Agent]:
    return lambda rng: StyleAgent(name, rng)


_FIXED = "Same strategy with adaptation switched off, so it plays every hand the same way."

STRATEGIES: dict[str, Strategy] = {
    s.id: s
    for s in (
        Strategy(
            "advanced",
            "Advanced",
            "opponent",
            "Chooses bet sizes by expected value against your estimated range, reads checks "
            "as weakness, and adapts fully to observed tendencies. The table's hardest level.",
            True,
            29.0,
            91.0,
            True,
            _equity(ADVANCED),
        ),
        Strategy(
            "advanced_fixed",
            "Advanced, fixed",
            "opponent",
            f"Advanced without adaptation. {_FIXED}",
            False,
            29.0,
            91.0,
            True,
            _equity(replace(ADVANCED, adapt=0.0)),
        ),
        Strategy(
            "intermediate",
            "Intermediate",
            "opponent",
            "Estimates your range from your actions and compares its equity with pot odds; "
            "adapts at half strength.",
            True,
            14.0,
            30.0,
            True,
            _equity(INTERMEDIATE),
        ),
        Strategy(
            "intermediate_fixed",
            "Intermediate, fixed",
            "opponent",
            f"Intermediate without adaptation. {_FIXED}",
            False,
            14.0,
            30.0,
            True,
            _equity(replace(INTERMEDIATE, adapt=0.0)),
        ),
        Strategy(
            "beginner",
            "Beginner",
            "opponent",
            "Plays its own cards: equity against any two hands, limps a lot, pays off big "
            "bets and seldom bluffs. Does not adapt.",
            False,
            15.0,
            30.0,
            True,
            _equity(BEGINNER),
        ),
        Strategy(
            "basic",
            "v0.1 bot",
            "reference",
            "PokerLab's first opponent: hand-strength thresholds with occasional bluffs.",
            False,
            0.15,
            0.3,
            False,
            lambda rng: HeuristicAgent(rng),
        ),
        Strategy(
            "aggressive",
            "Maniac",
            "style",
            "Plays most hands, bets and raises far too often, rarely folds.",
            False,
            0.12,
            0.3,
            False,
            _style("aggressive"),
        ),
        Strategy(
            "passive",
            "Calling station",
            "style",
            "Calls almost everything, seldom bets or raises.",
            False,
            0.15,
            0.3,
            True,
            _style("passive"),
        ),
        Strategy(
            "folder",
            "Rock",
            "style",
            "Plays tight and folds to most bets unless it holds a strong hand.",
            False,
            0.12,
            0.25,
            False,
            _style("folder"),
        ),
        Strategy(
            "random",
            "Random",
            "reference",
            "Picks uniformly among its legal actions, with a random raise size. A floor "
            "every strategy should beat.",
            False,
            0.1,
            0.2,
            False,
            lambda rng: RandomAgent(rng),
        ),
    )
}

ENGINE_MS = 0.2  # the engine's own time per hand, outside the agents


def get(strategy_id: str) -> Strategy:
    try:
        return STRATEGIES[strategy_id]
    except KeyError:
        raise ValueError(f"Unknown strategy: {strategy_id!r}") from None


def hand_cost_ms(a: str, b: str) -> float:
    """Rough single-core time per hand for a pairing."""
    sa, sb = get(a), get(b)
    ca = sa.cost_deep_ms if sb.deep else sa.cost_ms
    cb = sb.cost_deep_ms if sa.deep else sb.cost_ms
    return ca + cb + ENGINE_MS
