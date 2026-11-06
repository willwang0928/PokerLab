"""A heads-up cash-style session: a human seat versus an AI agent across many hands."""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .ai import Agent, HeuristicAgent, Observation
from .engine import Action, Event, HoldemHand, IllegalActionError

HUMAN = 0
AI = 1


class SessionStatus(StrEnum):
    WAITING = "waiting"  # between hands
    IN_HAND = "in_hand"
    GAME_OVER = "game_over"


@dataclass(frozen=True, slots=True)
class SessionConfig:
    starting_stack: int = 1000
    small_blind: int = 5
    big_blind: int = 10
    player_name: str = "You"
    ai_name: str = "Dealer Bot"

    def __post_init__(self) -> None:
        if not 0 < self.small_blind <= self.big_blind:
            raise ValueError("Blinds must satisfy 0 < small blind <= big blind")
        if self.starting_stack < self.big_blind:
            raise ValueError("Starting stack must cover at least one big blind")


class SessionStateError(RuntimeError):
    """Raised when a request does not fit the session's current state."""


@dataclass
class Step:
    """One observable moment of play: the triggering event and the state right after it."""

    event: dict[str, Any]
    state: dict[str, Any]


@dataclass
class HeadsUpSession:
    config: SessionConfig = field(default_factory=SessionConfig)
    seed: int | None = None
    agent: Agent | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)
        if self.agent is None:
            self.agent = HeuristicAgent(random.Random(self.rng.random()))
        self.stacks = [self.config.starting_stack, self.config.starting_stack]
        self.button = self.rng.randrange(2)
        self.hand_number = 0
        self.hand: HoldemHand | None = None
        self.status = SessionStatus.WAITING
        self.revealed: set[int] = set()
        self.log: list[str] = []
        self.result: dict[str, Any] | None = None
        self._steps: list[Step] = []

    # ------------------------------------------------------------------ commands

    def start_hand(self) -> list[Step]:
        if self.status == SessionStatus.IN_HAND:
            raise SessionStateError("A hand is already in progress")
        if self.status == SessionStatus.GAME_OVER:
            raise SessionStateError("The game is over; start a new session")
        self._steps = []
        self.hand_number += 1
        if self.hand_number > 1:
            self.button ^= 1
        self.revealed = set()
        self.log = []
        self.result = None
        self.status = SessionStatus.IN_HAND
        self.hand = HoldemHand(
            self.stacks,
            self.button,
            self.config.small_blind,
            self.config.big_blind,
            names=[self.config.player_name, self.config.ai_name],
            rng=self.rng,
            listener=self._on_event,
            autostart=False,
        )
        self.hand.start()
        self._run_ai()
        return self._steps

    def act(self, action: Action) -> list[Step]:
        if self.status != SessionStatus.IN_HAND or self.hand is None:
            raise SessionStateError("There is no hand in progress")
        if self.hand.to_act != HUMAN:
            raise SessionStateError("It is not your turn")
        self._steps = []
        self.hand.apply(action)  # raises IllegalActionError on bad input
        self._run_ai()
        return self._steps

    def _run_ai(self) -> None:
        hand = self.hand
        assert hand is not None and self.agent is not None
        while not hand.is_complete and hand.to_act == AI:
            legal = hand.legal_actions()
            action = self.agent.decide(Observation.from_hand(hand, AI), legal)
            try:
                hand.apply(action)
            except IllegalActionError:  # defensive: never let a buggy agent stall the game
                hand.apply(Action.check() if legal.can_check else Action.fold())

    # ------------------------------------------------------------------ events

    def _on_event(self, event: Event) -> None:
        if event.type in ("reveal", "showdown"):
            self.revealed |= {int(s) for s in event.data["hands"]}
        if event.type == "hand_complete":
            self._finish_hand(event)
        line = self._describe(event)
        if line:
            self.log.append(line)
        self._steps.append(Step(event=self._public_event(event), state=self.view()))

    def _finish_hand(self, event: Event) -> None:
        assert self.hand is not None
        self.stacks = list(event.data["stacks"])
        pots = [e.data for e in self.hand.events if e.type == "pot_awarded"]
        showdown = next((e.data["hands"] for e in self.hand.events if e.type == "showdown"), None)
        self.result = {
            "winnings": {int(k): v for k, v in event.data["winnings"].items()},
            "uncontested": event.data["uncontested"],
            "pots": [
                {"amount": p["amount"], "winners": p["winners"], "hand": p["hand"]} for p in pots
            ],
            "showdown": {int(k): v for k, v in showdown.items()} if showdown else None,
        }
        self.status = SessionStatus.GAME_OVER if min(self.stacks) == 0 else SessionStatus.WAITING

    def _public_event(self, event: Event) -> dict[str, Any]:
        data = dict(event.data)
        return {"type": event.type, **data}

    def _name(self, seat: int) -> str:
        return self.config.player_name if seat == HUMAN else self.config.ai_name

    def _say(self, seat: int, verb: str, rest: str = "") -> str:
        """Conjugate ``verb`` for the seat ("You call" vs "Dealer Bot calls")."""
        name = self._name(seat)
        second_person = name.lower() == "you"
        if not second_person:
            verb = {"have": "has"}.get(verb, verb + ("es" if verb.endswith("sh") else "s"))
        return f"{name} {verb}" + (f" {rest}" if rest else "")

    def _describe(self, event: Event) -> str | None:
        d = event.data
        t = event.type
        if t == "hand_start":
            return f"Hand #{self.hand_number} — " + self._say(d["button"], "have", "the button")
        if t == "post_blind":
            kind = "small" if d["blind"] == "small_blind" else "big"
            return self._say(d["seat"], "post", f"{kind} blind {d['amount']}")
        if t == "action":
            verb, rest = {
                "fold": ("fold", ""),
                "check": ("check", ""),
                "call": ("call", str(d["amount"])),
                "bet": ("bet", str(d["amount"])),
                "raise": ("raise", f"to {d['amount']}"),
            }[d["action"]]
            return self._say(d["seat"], verb, rest) + (" (all-in)" if d["all_in"] else "")
        if t == "street":
            return f"{d['street'].title()}: {' '.join(d['board'])}"
        if t == "uncalled_returned":
            return f"Uncalled {d['amount']} returned to {self._name(d['seat'])}"
        if t == "showdown":
            return "; ".join(
                self._say(int(s), "show", f"{' '.join(h['cards'])} ({h['description']})")
                for s, h in d["hands"].items()
            )
        if t == "pot_awarded":
            hand = f" with {d['hand']}" if d["hand"] else ""
            if len(d["winners"]) > 1:
                return f"Split pot: {d['amount']} shared{hand}"
            return self._say(d["winners"][0], "win", f"{d['amount']}{hand}")
        return None

    # ------------------------------------------------------------------ views

    def view(self) -> dict[str, Any]:
        """The state as seen by the human seat (AI cards hidden until shown down)."""
        hand = self.hand
        cfg = self.config
        players = []
        for seat in (HUMAN, AI):
            p = hand.players[seat] if hand else None
            show = p is not None and (seat == HUMAN or seat in self.revealed)
            players.append(
                {
                    "seat": seat,
                    "name": self._name(seat),
                    "is_human": seat == HUMAN,
                    "stack": p.stack if p else self.stacks[seat],
                    "bet": p.bet if p else 0,
                    "folded": p.folded if p else False,
                    "all_in": p.all_in if p else False,
                    "last_action": p.last_action if p else None,
                    "hole": [c.code for c in p.hole] if show else None,
                    "has_cards": bool(p and p.hole and not p.folded),
                }
            )
        legal = None
        if hand and self.status == SessionStatus.IN_HAND and hand.to_act == HUMAN:
            la = hand.legal_actions()
            legal = {
                "can_fold": la.can_fold,
                "can_check": la.can_check,
                "can_call": la.can_call,
                "call_amount": la.call_amount,
                "can_raise": la.can_raise,
                "raise_type": la.raise_type.value,
                "min_raise_to": la.min_raise_to,
                "max_raise_to": la.max_raise_to,
                "raise_is_all_in": la.raise_is_all_in,
            }
        winner = None
        if self.status == SessionStatus.GAME_OVER:
            winner = HUMAN if self.stacks[HUMAN] > 0 else AI
        return {
            "session_id": self.id,
            "status": self.status.value,
            "hand_number": self.hand_number,
            "street": hand.street.value if hand else None,
            "board": [c.code for c in hand.board] if hand else [],
            "pot": hand.pot if hand else 0,
            "collected_pot": hand.collected_pot if hand else 0,
            "current_bet": hand.current_bet if hand else 0,
            "button": hand.button if hand else self.button,
            "to_act": hand.to_act if hand and not hand.is_complete else None,
            "players": players,
            "legal": legal,
            "result": self.result,
            "log": list(self.log),
            "winner": winner,
            "config": {
                "starting_stack": cfg.starting_stack,
                "small_blind": cfg.small_blind,
                "big_blind": cfg.big_blind,
            },
        }
