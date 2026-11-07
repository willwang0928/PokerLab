"""AI agents. Every agent implements :class:`Agent.decide` and must act legally."""

from .base import ActionRecord, Agent, Observation, make_legal
from .equity_agent import EquityAgent
from .heuristic import HeuristicAgent
from .random_agent import RandomAgent

__all__ = [
    "ActionRecord",
    "Agent",
    "EquityAgent",
    "HeuristicAgent",
    "Observation",
    "RandomAgent",
    "make_legal",
]
