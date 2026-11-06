"""Action types and legal-action descriptions shared by betting games."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ActionType(StrEnum):
    FOLD = "fold"
    CHECK = "check"
    CALL = "call"
    BET = "bet"
    RAISE = "raise"
    ALL_IN = "all_in"


@dataclass(frozen=True, slots=True)
class Action:
    """A player decision. For BET/RAISE, ``amount`` is the *total* street bet ("raise to")."""

    type: ActionType
    amount: int | None = None

    @classmethod
    def fold(cls) -> Action:
        return cls(ActionType.FOLD)

    @classmethod
    def check(cls) -> Action:
        return cls(ActionType.CHECK)

    @classmethod
    def call(cls) -> Action:
        return cls(ActionType.CALL)

    @classmethod
    def bet(cls, to: int) -> Action:
        return cls(ActionType.BET, to)

    @classmethod
    def raise_to(cls, to: int) -> Action:
        return cls(ActionType.RAISE, to)

    @classmethod
    def all_in(cls) -> Action:
        return cls(ActionType.ALL_IN)


@dataclass(frozen=True, slots=True)
class LegalActions:
    """What the player to act may do.

    ``min_raise_to``/``max_raise_to`` are total street-bet amounts. ``raise_type`` is
    BET when no bet has been made this street, otherwise RAISE.
    """

    seat: int
    can_fold: bool
    can_check: bool
    can_call: bool
    call_amount: int
    can_raise: bool
    raise_type: ActionType
    min_raise_to: int
    max_raise_to: int
    current_bet: int
    player_bet: int
    player_stack: int

    @property
    def raise_is_all_in(self) -> bool:
        return self.can_raise and self.max_raise_to == self.player_bet + self.player_stack


class IllegalActionError(ValueError):
    """Raised when a player attempts an action that the rules do not allow."""
