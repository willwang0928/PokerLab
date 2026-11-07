"""Opponent difficulty levels.

Each level is an :class:`~pokerlab.ai.equity_agent.AgentProfile`. Levels differ in what
the agent *reasons about*, and every difference is a recognisable human tendency:

* **Beginner** plays its own cards. It works out its equity against *any two cards*
  rather than against what your betting suggests. So it overrates top pair, pays off
  big bets, and chases draws at slightly bad prices. It limps a lot, rarely raises
  without a strong hand, and seldom bluffs.
* **Intermediate** (the v0.3.2 opponent) estimates your range from your actions and
  compares its equity with pot odds.
* **Advanced** chooses postflop bets by expected value. For each bet size it assumes
  you defend at the minimum defence frequency with the strongest part of your range.
  It bets when that beats checking, and picks the size that earns most. It also reads a
  check as capping your range (strong hands usually bet) and needs less equity to call
  in position than out of position.

Advanced was chosen by measurement. Threshold tweaks (lower value-bet thresholds, wider
opens, strength-scaled sizing, more continuation bets) did not beat Intermediate in
duplicate matches, so they were dropped. Expected-value betting did, and the final
profile was confirmed on seeds not used to choose it.

None of these levels is a game-theory-optimal (GTO) strategy. Advanced is a stronger
heuristic built on the same equity engine. ``pokerlab.ai.match.duplicate_match``
measures how the levels compare.
"""

from __future__ import annotations

import random
from enum import StrEnum

from .equity_agent import AgentProfile, EquityAgent


class Difficulty(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


BEGINNER = AgentProfile(
    name="beginner",
    iterations=600,
    reads_ranges=False,
    open_raise=0.35,
    open_limp=0.92,
    iso_raise=0.12,
    reraise=0.75,
    value_raise=0.88,
    value_bet=0.72,
    thin_bet=0.60,
    thin_bet_rate=0.25,
    call_margin=-0.05,  # calls slightly bad prices ("I might hit")
    trap_rate=0.30,
    bluff_rate=0.04,
    semi_bluff_rate=0.10,
)

INTERMEDIATE = AgentProfile(name="intermediate")

ADVANCED = AgentProfile(
    name="advanced",
    reads_checks=True,
    call_margin=0.02,
    position_margin=0.02,
    bluff_rate=0.05,
    ev_bets=True,
)

PROFILES: dict[Difficulty, AgentProfile] = {
    Difficulty.BEGINNER: BEGINNER,
    Difficulty.INTERMEDIATE: INTERMEDIATE,
    Difficulty.ADVANCED: ADVANCED,
}

DEFAULT_DIFFICULTY = Difficulty.INTERMEDIATE


def make_agent(difficulty: Difficulty | str, rng: random.Random | None = None) -> EquityAgent:
    """An opponent for ``difficulty`` (``"beginner"``, ``"intermediate"`` or ``"advanced"``)."""
    return EquityAgent(rng, profile=PROFILES[Difficulty(difficulty)])
