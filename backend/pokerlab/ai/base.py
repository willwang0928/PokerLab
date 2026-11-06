"""Agent interface and the observation an agent is allowed to see."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..cards import Card
from ..engine import Action, ActionType, HoldemHand, LegalActions, Street


@dataclass(frozen=True, slots=True)
class Observation:
    """Everything a seat may legally know when deciding (no opponent hole cards)."""

    seat: int
    hole: tuple[Card, ...]
    board: tuple[Card, ...]
    street: Street
    pot: int  # total committed including current bets
    stack: int
    bet: int
    current_bet: int
    big_blind: int
    is_button: bool
    num_players: int
    num_in_hand: int
    opponent_stacks: tuple[int, ...]
    street_raises: int  # bets/raises made so far on this street

    @classmethod
    def from_hand(cls, hand: HoldemHand, seat: int) -> Observation:
        p = hand.players[seat]
        raises = 0
        for ev in reversed(hand.events):
            if ev.type in ("street", "deal_hole"):
                break
            if ev.type == "action" and ev.data["action"] in ("bet", "raise"):
                raises += 1
        return cls(
            seat=seat,
            hole=tuple(p.hole),
            board=tuple(hand.board),
            street=hand.street,
            pot=hand.pot,
            stack=p.stack,
            bet=p.bet,
            current_bet=hand.current_bet,
            big_blind=hand.big_blind,
            is_button=seat == hand.button,
            num_players=hand.n,
            num_in_hand=sum(not q.folded for q in hand.players),
            opponent_stacks=tuple(q.stack for q in hand.players if q.seat != seat),
            street_raises=raises,
        )


class Agent(ABC):
    """A poker decision maker. Implementations must only return legal actions."""

    name: str = "agent"

    @abstractmethod
    def decide(self, obs: Observation, legal: LegalActions) -> Action: ...


def make_legal(action: Action, legal: LegalActions) -> Action:
    """Coerce an intended action into the nearest legal one (safety net for agents)."""
    t = action.type
    if t in (ActionType.BET, ActionType.RAISE):
        if not legal.can_raise:
            return Action.call() if legal.can_call else Action.check()
        amount = action.amount if action.amount is not None else legal.min_raise_to
        amount = max(legal.min_raise_to, min(legal.max_raise_to, int(amount)))
        return Action(legal.raise_type, amount)
    if t == ActionType.ALL_IN:
        if legal.can_raise or legal.can_call:
            return action
        return Action.check()
    if t == ActionType.CALL and not legal.can_call:
        return Action.check()
    if t == ActionType.CHECK and not legal.can_check:
        return Action.fold()
    if t == ActionType.FOLD and not legal.can_fold:
        return Action.check()
    return action
