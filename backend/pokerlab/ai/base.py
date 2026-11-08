"""Agent interface and the observation an agent is allowed to see."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass

from ..cards import Card
from ..engine import Action, ActionType, Event, HoldemHand, LegalActions, Street


@dataclass(frozen=True, slots=True)
class ActionRecord:
    """One public betting action. ``amount`` is the call size or the raise-to total."""

    street: Street
    seat: int
    action: str  # fold / check / call / bet / raise
    amount: int
    all_in: bool


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
    button: int = 0
    opponents_in_hand: tuple[int, ...] = ()  # seats of opponents who have not folded
    actions: tuple[ActionRecord, ...] = ()  # every action this hand, oldest first

    @classmethod
    def from_hand(cls, hand: HoldemHand, seat: int) -> Observation:
        p = hand.players[seat]
        actions: list[ActionRecord] = []
        street = Street.PREFLOP
        for ev in hand.events:
            if ev.type == "street":
                street = Street(ev.data["street"])
            elif ev.type == "action":
                d = ev.data
                actions.append(
                    ActionRecord(street, d["seat"], d["action"], d["amount"], d["all_in"])
                )
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
            button=hand.button,
            opponents_in_hand=tuple(
                q.seat for q in hand.players if q.seat != seat and not q.folded
            ),
            actions=tuple(actions),
        )


class Agent(ABC):
    """A poker decision maker. Implementations must only return legal actions."""

    name: str = "agent"

    @abstractmethod
    def decide(self, obs: Observation, legal: LegalActions) -> Action: ...

    def observe_hand(self, events: Sequence[Event], seat: int) -> None:  # noqa: B027
        """Called with a finished hand's public events (``seat`` = this agent's seat).

        Agents that learn about their opponents override this. The default ignores it.
        """


def uncalled_excess(legal: LegalActions) -> int:
    """Chips of the opponent's bet that the player cannot match (returned to them).

    Bets are capped at what the other player can match, so this is only non-zero when a
    forced blind is larger than the caller's whole stack.
    """
    return max(0, legal.current_bet - (legal.player_bet + legal.player_stack))


def raise_to_pot_fraction(obs: Observation, legal: LegalActions, fraction: float) -> Action:
    """Bet/raise to ``current bet + fraction × (pot after calling)``, clamped to legal sizes.

    Moves all-in when the target is at least 80% of the maximum (a smaller bet would leave
    an awkward, committed stack behind). Calls or checks when raising is not allowed.
    """
    if not legal.can_raise:
        return Action.call() if legal.can_call else Action.check()
    pot_after_call = obs.pot + legal.call_amount
    target = legal.current_bet + int(round(fraction * pot_after_call))
    target = max(legal.min_raise_to, min(legal.max_raise_to, target))
    if target >= 0.8 * legal.max_raise_to:
        target = legal.max_raise_to
    return Action(legal.raise_type, target)


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
