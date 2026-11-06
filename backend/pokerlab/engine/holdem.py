"""No-Limit Texas Hold'em hand state machine.

A :class:`HoldemHand` plays exactly one hand: blinds, dealing, four betting rounds,
showdown and pot distribution. It supports 2+ players (with the heads-up blind/position
rules when there are two) and records every state change as an :class:`Event` so that
callers (the API, a replayer, analytics) can observe the hand step by step.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..cards import Card, Deck
from ..evaluator import HandValue, evaluate
from .actions import Action, ActionType, IllegalActionError, LegalActions
from .pots import build_pots, split_pot


class Street(StrEnum):
    PREFLOP = "preflop"
    FLOP = "flop"
    TURN = "turn"
    RIVER = "river"
    SHOWDOWN = "showdown"
    COMPLETE = "complete"


BETTING_STREETS = (Street.PREFLOP, Street.FLOP, Street.TURN, Street.RIVER)
_BOARD_CARDS = {Street.FLOP: 3, Street.TURN: 1, Street.RIVER: 1}
_NEXT_STREET = {
    Street.PREFLOP: Street.FLOP,
    Street.FLOP: Street.TURN,
    Street.TURN: Street.RIVER,
    Street.RIVER: Street.SHOWDOWN,
}


@dataclass(slots=True)
class Event:
    type: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PlayerState:
    seat: int
    name: str
    stack: int
    hole: list[Card] = field(default_factory=list)
    bet: int = 0  # chips committed on the current street
    contributed: int = 0  # chips committed over the whole hand
    folded: bool = False
    needs_to_act: bool = True
    can_raise: bool = True  # False once acted, until a full raise re-opens the action
    last_action: str | None = None

    @property
    def all_in(self) -> bool:
        return self.stack == 0 and not self.folded

    @property
    def live(self) -> bool:
        """In the hand and still able to make decisions."""
        return not self.folded and self.stack > 0


@dataclass(frozen=True, slots=True)
class ShowdownResult:
    seat: int
    hand: HandValue


class HoldemHand:
    def __init__(
        self,
        stacks: Sequence[int],
        button: int,
        small_blind: int,
        big_blind: int,
        *,
        names: Sequence[str] | None = None,
        deck: Deck | None = None,
        rng: random.Random | None = None,
        listener: Callable[[Event], None] | None = None,
        autostart: bool = True,
    ):
        if len(stacks) < 2:
            raise ValueError("Hold'em needs at least two players")
        if any(s <= 0 for s in stacks):
            raise ValueError("Every player must start the hand with chips")
        if not 0 < small_blind <= big_blind:
            raise ValueError("Blinds must satisfy 0 < small blind <= big blind")
        if not 0 <= button < len(stacks):
            raise ValueError("Button seat out of range")

        names = list(names) if names else [f"Player {i + 1}" for i in range(len(stacks))]
        self.players = [PlayerState(i, names[i], int(s)) for i, s in enumerate(stacks)]
        self.button = button
        self.small_blind = small_blind
        self.big_blind = big_blind
        self.board: list[Card] = []
        self.street = Street.PREFLOP
        self.current_bet = 0
        self.last_raise_size = big_blind
        self.to_act: int | None = None
        self.events: list[Event] = []
        self.winnings: dict[int, int] = {}
        self.showdown: list[ShowdownResult] = []
        self._revealed = False
        self._listener = listener
        if deck is None:
            deck = Deck(rng)
            deck.shuffle()
        self._deck = deck
        self._started = False
        if autostart:
            self.start()

    # ------------------------------------------------------------------ queries

    @property
    def n(self) -> int:
        return len(self.players)

    @property
    def is_complete(self) -> bool:
        return self.street == Street.COMPLETE

    @property
    def pot(self) -> int:
        """All chips committed this hand (including bets still in front of players)."""
        return sum(p.contributed for p in self.players)

    @property
    def collected_pot(self) -> int:
        """Chips already gathered into the middle (excluding current-street bets)."""
        return self.pot - sum(p.bet for p in self.players)

    def total_chips(self) -> int:
        return sum(p.stack + p.contributed for p in self.players)

    def _next_seat(self, seat: int) -> int:
        return (seat + 1) % self.n

    def _seats_from(self, start: int) -> list[int]:
        return [(start + i) % self.n for i in range(self.n)]

    @property
    def small_blind_seat(self) -> int:
        return self.button if self.n == 2 else self._next_seat(self.button)

    @property
    def big_blind_seat(self) -> int:
        return self._next_seat(self.small_blind_seat)

    def _max_reachable(self, seat: int) -> int:
        """Largest street bet any *other* in-hand player could reach."""
        return max(
            (p.bet + p.stack for p in self.players if p.seat != seat and not p.folded),
            default=0,
        )

    def legal_actions(self) -> LegalActions:
        if self.to_act is None:
            raise IllegalActionError("No player is due to act")
        p = self.players[self.to_act]
        own_max = p.bet + p.stack
        reachable = self._max_reachable(p.seat)
        # Never ask a player to call more than any opponent could ever match.
        target = min(self.current_bet, max(reachable, p.bet))
        call_amount = min(max(target - p.bet, 0), p.stack)
        max_to = min(own_max, reachable)
        can_raise = p.can_raise and max_to > self.current_bet and max_to > p.bet + call_amount
        min_to = min(self.current_bet + self.last_raise_size, max_to)
        if self.current_bet == 0:
            min_to = min(max(self.big_blind, 1), max_to)
        return LegalActions(
            seat=p.seat,
            can_fold=call_amount > 0,
            can_check=call_amount == 0,
            can_call=call_amount > 0,
            call_amount=call_amount,
            can_raise=can_raise,
            raise_type=ActionType.BET if self.current_bet == 0 else ActionType.RAISE,
            min_raise_to=min_to if can_raise else 0,
            max_raise_to=max_to if can_raise else 0,
            current_bet=self.current_bet,
            player_bet=p.bet,
            player_stack=p.stack,
        )

    # ------------------------------------------------------------------ actions

    def apply(self, action: Action) -> None:
        if not self._started or self.street not in BETTING_STREETS or self.to_act is None:
            raise IllegalActionError("The hand is not awaiting an action")
        try:
            kind = ActionType(action.type)
        except ValueError as exc:
            raise IllegalActionError(f"Unknown action {action.type!r}") from exc
        legal = self.legal_actions()
        p = self.players[self.to_act]

        if kind == ActionType.ALL_IN:
            if legal.can_raise:
                kind, amount = legal.raise_type, legal.max_raise_to
            elif legal.can_call:
                kind, amount = ActionType.CALL, None
            else:
                raise IllegalActionError("All-in is not possible here; check instead")
            action = Action(kind, amount)

        if kind == ActionType.FOLD:
            if not legal.can_fold:
                raise IllegalActionError("Cannot fold when you can check")
            p.folded = True
            p.needs_to_act = False
            self._record_action(p, "fold", 0)
        elif kind == ActionType.CHECK:
            if not legal.can_check:
                raise IllegalActionError(f"Cannot check facing a bet of {legal.call_amount}")
            self._record_action(p, "check", 0)
        elif kind == ActionType.CALL:
            if not legal.can_call:
                raise IllegalActionError("Nothing to call; check instead")
            self._commit(p, legal.call_amount)
            self._record_action(p, "call", legal.call_amount)
        elif kind in (ActionType.BET, ActionType.RAISE):
            self._apply_raise(p, kind, action.amount, legal)
        else:  # pragma: no cover - exhaustive
            raise IllegalActionError(f"Unknown action {action.type}")

        p.needs_to_act = False
        p.can_raise = False
        self._advance()

    def _apply_raise(
        self, p: PlayerState, kind: ActionType, amount: int | None, legal: LegalActions
    ) -> None:
        if not legal.can_raise:
            raise IllegalActionError("Raising is not allowed here")
        if kind != legal.raise_type:
            hint = "bet" if legal.raise_type == ActionType.BET else "raise"
            raise IllegalActionError(f"Cannot {kind.value} here; did you mean {hint}?")
        if amount is None or isinstance(amount, bool) or not isinstance(amount, int):
            raise IllegalActionError(f"A {kind.value} needs an integer amount")
        if not legal.min_raise_to <= amount <= legal.max_raise_to:
            raise IllegalActionError(
                f"{kind.value.title()} must be between {legal.min_raise_to} and "
                f"{legal.max_raise_to} (got {amount})"
            )
        raise_size = amount - self.current_bet
        full_raise = raise_size >= self.last_raise_size
        self._commit(p, amount - p.bet)
        if full_raise:
            self.last_raise_size = raise_size
            for other in self.players:
                if other.seat != p.seat:
                    other.can_raise = True
        self.current_bet = amount
        for other in self.players:
            if other.seat != p.seat and other.live:
                other.needs_to_act = True
        self._record_action(p, kind.value, amount)

    def _commit(self, p: PlayerState, chips: int) -> None:
        chips = min(chips, p.stack)
        p.stack -= chips
        p.bet += chips
        p.contributed += chips

    def _record_action(self, p: PlayerState, kind: str, amount: int) -> None:
        label = "all-in" if p.all_in and kind not in ("fold", "check") else kind
        p.last_action = label
        self._emit(
            "action",
            seat=p.seat,
            action=kind,
            amount=amount,
            all_in=p.all_in,
            label=label,
            stack=p.stack,
            bet=p.bet,
        )

    # ------------------------------------------------------------------ flow

    def _emit(self, type_: str, **data: Any) -> None:
        event = Event(type_, data)
        self.events.append(event)
        if self._listener:
            self._listener(event)

    def start(self) -> None:
        """Post blinds and deal. Called automatically unless ``autostart=False``."""
        if self._started:
            raise RuntimeError("Hand already started")
        self._started = True
        self._emit(
            "hand_start",
            button=self.button,
            small_blind=self.small_blind,
            big_blind=self.big_blind,
            stacks=[p.stack for p in self.players],
        )
        for seat, blind, label in (
            (self.small_blind_seat, self.small_blind, "small_blind"),
            (self.big_blind_seat, self.big_blind, "big_blind"),
        ):
            p = self.players[seat]
            self._commit(p, blind)
            self._emit("post_blind", seat=seat, blind=label, amount=p.bet, all_in=p.all_in)
        # A short all-in blind does not lower the amount others must match.
        self.current_bet = self.big_blind
        self.last_raise_size = self.big_blind

        # Deal one card at a time, starting left of the button.
        order = self._seats_from(self._next_seat(self.button))
        for _ in range(2):
            for seat in order:
                self.players[seat].hole.extend(self._deck.deal(1))
        self._emit("deal_hole", seats=order)

        first = self._next_seat(self.big_blind_seat) if self.n > 2 else self.button
        self._set_next_to_act(first)
        if self.to_act is None:
            self._end_betting_round()

    def _set_next_to_act(self, start: int) -> None:
        """Point ``to_act`` at the first player from ``start`` who has a real decision."""
        self.to_act = None
        for seat in self._seats_from(start):
            p = self.players[seat]
            if not p.live or not p.needs_to_act:
                continue
            self.to_act = seat
            legal = self.legal_actions()
            if legal.can_check and not legal.can_raise:
                # Only option is to check (everyone else is all-in): skip the formality.
                p.needs_to_act = False
                self.to_act = None
                continue
            return

    def _advance(self) -> None:
        in_hand = [p for p in self.players if not p.folded]
        if len(in_hand) == 1:
            self._return_uncalled()
            self._award_uncontested(in_hand[0])
            return
        assert self.to_act is not None
        self._set_next_to_act(self._next_seat(self.to_act))
        if self.to_act is None:
            self._end_betting_round()

    def _return_uncalled(self) -> None:
        ranked = sorted(self.players, key=lambda p: p.contributed, reverse=True)
        top, second = ranked[0], ranked[1]
        excess = top.contributed - second.contributed
        if excess > 0:
            excess = min(excess, top.bet)
            top.contributed -= excess
            top.bet -= excess
            top.stack += excess
            self._emit("uncalled_returned", seat=top.seat, amount=excess)

    def _end_betting_round(self) -> None:
        self._return_uncalled()
        for p in self.players:
            p.bet = 0
        self.current_bet = 0
        self.last_raise_size = self.big_blind
        self.to_act = None
        self._emit("bets_collected", pot=self.pot)

        live = [p for p in self.players if p.live]
        runout = len(live) < 2
        if runout and self.street != Street.RIVER and not self._revealed:
            self._revealed = True
            self._emit("reveal", hands=self._hole_codes(only_in_hand=True))

        while True:
            self.street = _NEXT_STREET[self.street]
            if self.street == Street.SHOWDOWN:
                self._showdown()
                return
            self._deck.burn()
            cards = self._deck.deal(_BOARD_CARDS[self.street])
            self.board.extend(cards)
            self._emit(
                "street",
                street=self.street.value,
                cards=[c.code for c in cards],
                board=[c.code for c in self.board],
            )
            if runout:
                continue
            for p in self.players:
                p.last_action = None
                if p.live:
                    p.needs_to_act = True
                    p.can_raise = True
            self._set_next_to_act(self._next_seat(self.button))
            if self.to_act is not None:
                return
            runout = True

    def _hole_codes(self, only_in_hand: bool) -> dict[int, list[str]]:
        return {
            p.seat: [c.code for c in p.hole]
            for p in self.players
            if not (only_in_hand and p.folded)
        }

    def _award_uncontested(self, winner: PlayerState) -> None:
        amount = self.pot
        self._pay({winner.seat: amount})
        self.street = Street.COMPLETE
        self.to_act = None
        self._emit(
            "pot_awarded",
            pot_index=0,
            amount=amount,
            winners=[winner.seat],
            payouts={winner.seat: amount},
            hand=None,
        )
        self._finish(uncontested=True)

    def _showdown(self) -> None:
        contenders = [p for p in self.players if not p.folded]
        values = {p.seat: evaluate(p.hole + self.board) for p in contenders}
        self.showdown = [ShowdownResult(s, v) for s, v in values.items()]
        self._emit(
            "showdown",
            hands={
                s: {
                    "cards": [c.code for c in self.players[s].hole],
                    "category": v.category.label,
                    "description": v.description,
                }
                for s, v in values.items()
            },
        )
        pots = build_pots([p.contributed for p in self.players], [p.folded for p in self.players])
        # Odd chips go to the first winner clockwise from the button.
        order = {seat: i for i, seat in enumerate(self._seats_from(self._next_seat(self.button)))}
        payouts_total: dict[int, int] = {}
        for i, pot in enumerate(pots):
            best = max(values[s] for s in pot.eligible)
            winners = [s for s in pot.eligible if values[s] == best]
            payouts = split_pot(pot.amount, winners, order.__getitem__)
            for s, amt in payouts.items():
                payouts_total[s] = payouts_total.get(s, 0) + amt
            self._emit(
                "pot_awarded",
                pot_index=i,
                amount=pot.amount,
                winners=winners,
                payouts=payouts,
                hand=best.description,
            )
        self._pay(payouts_total)
        self.street = Street.COMPLETE
        self._finish(uncontested=False)

    def _pay(self, payouts: dict[int, int]) -> None:
        total = sum(payouts.values())
        assert total == self.pot, f"payout {total} != pot {self.pot}"
        for p in self.players:
            p.stack += payouts.get(p.seat, 0)
            p.contributed = 0
            p.bet = 0
        self.winnings = dict(payouts)

    def _finish(self, uncontested: bool) -> None:
        self._emit(
            "hand_complete",
            winnings=self.winnings,
            stacks=[p.stack for p in self.players],
            uncontested=uncontested,
        )
