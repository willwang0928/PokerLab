"""Uniformly random legal agent — a baseline and a fuzzing tool."""

from __future__ import annotations

import random

from ..engine import Action, LegalActions
from .base import Agent, Observation


class RandomAgent(Agent):
    name = "random"

    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()

    def decide(self, obs: Observation, legal: LegalActions) -> Action:
        options: list[Action] = []
        if legal.can_fold:
            options.append(Action.fold())
        if legal.can_check:
            options.append(Action.check())
        if legal.can_call:
            options.append(Action.call())
        if legal.can_raise:
            amount = self.rng.randint(legal.min_raise_to, legal.max_raise_to)
            options.append(Action(legal.raise_type, amount))
        return self.rng.choice(options)
