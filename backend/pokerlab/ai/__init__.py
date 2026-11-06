"""AI agents. Every agent implements :class:`Agent.decide` and must act legally."""

from .base import Agent, Observation, make_legal
from .heuristic import HeuristicAgent
from .random_agent import RandomAgent

__all__ = ["Agent", "HeuristicAgent", "Observation", "RandomAgent", "make_legal"]
