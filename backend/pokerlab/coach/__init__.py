"""Coaching: decision analysis, post-hand reviews and training scenarios.

Everything here works from what the player could see when they acted: their own hole
cards, the board so far and the public betting. The opponent's hole cards are never an
input, not even after a showdown, so a review never uses hindsight.
"""

from .analysis import DecisionAnalysis, Option, analyze, grade_option

__all__ = ["DecisionAnalysis", "Option", "analyze", "grade_option"]
