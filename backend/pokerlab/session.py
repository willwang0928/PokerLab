"""A heads-up cash-style session: a human seat versus an AI agent across many hands."""

from __future__ import annotations

import random
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .ai import Agent, Observation
from .ai.adaptation import adaptation_policy, explain_change
from .ai.difficulty import DEFAULT_DIFFICULTY, Difficulty, make_agent
from .ai.equity_agent import EquityAgent
from .ai.player_model import MIN_OPPORTUNITIES
from .coach.review import HandCoach
from .engine import Action, Event, HoldemHand, IllegalActionError
from .equity import STREET_BOARD_SIZES, EquityResult, calculate_equity
from .evaluator import best_five, evaluate
from .history import build_record, describe

HUMAN = 0
AI = 1
# Finished hands kept for their hand-history record, so the record can still be fetched
# after the next hand has been dealt (auto-deal, a slow network).
RECENT_HANDS = 5


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
    difficulty: Difficulty = DEFAULT_DIFFICULTY
    adaptive: bool = True  # let the opponent adapt to the player (levels that support it)

    def __post_init__(self) -> None:
        object.__setattr__(self, "difficulty", Difficulty(self.difficulty))
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
            self.agent = make_agent(self.config.difficulty, random.Random(self.rng.random()))
            self.agent.adaptive = self.config.adaptive
        self.stacks = [self.config.starting_stack, self.config.starting_stack]
        self.button = self.rng.randrange(2)
        self.hand_number = 0
        self.hand: HoldemHand | None = None
        self.status = SessionStatus.WAITING
        self.revealed: set[int] = set()
        self.log: list[str] = []
        self.result: dict[str, Any] | None = None
        self._steps: list[Step] = []
        self._equity_cache: dict[int, EquityResult] = {}  # board size -> face-up equity
        self._runout = False
        self._adaptation_notes: list[str] = []  # decisions adaptation changed this hand
        # Records the human's decisions (from the human's own view) for post-hand review.
        self.coach = HandCoach(
            HUMAN,
            AI,
            self.config.small_blind,
            self.config.big_blind,
            names={AI: self.config.ai_name},
        )
        # Serialises requests for this session (the API runs handlers on a threadpool).
        # hand number -> what is needed to build its history record (see hand_record)
        self._recent: dict[int, dict[str, Any]] = {}
        self.lock = threading.RLock()
        self.last_active = time.monotonic()

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
        self._equity_cache = {}
        self._runout = False  # True once hands are turned up for an all-in runout
        self._adaptation_notes = []
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
        self.coach.start_hand(self.hand_number, self.stacks, self.button)
        self.hand.start()
        self._run_ai()
        return self._steps

    def act(self, action: Action) -> list[Step]:
        if self.status != SessionStatus.IN_HAND or self.hand is None:
            raise SessionStateError("There is no hand in progress")
        if self.hand.to_act != HUMAN:
            raise SessionStateError("It is not your turn")
        self._steps = []
        # Recorded before applying: the action may end the hand, which closes the review.
        self.coach.record(
            Observation.from_hand(self.hand, HUMAN), self.hand.legal_actions(), action
        )
        try:
            self.hand.apply(action)  # raises IllegalActionError on bad input
        except IllegalActionError:
            self.coach.discard_last()
            raise
        self._run_ai()
        return self._steps

    def _run_ai(self) -> None:
        hand = self.hand
        assert hand is not None and self.agent is not None
        while not hand.is_complete and hand.to_act == AI:
            legal = hand.legal_actions()
            obs = Observation.from_hand(hand, AI)
            agent = self.agent
            # What would it do without adaptation? Same random state, so any difference
            # is caused by adaptation alone.
            neutral = None
            if isinstance(agent, EquityAgent) and not agent.adjustments.neutral:
                neutral = agent.decide_without_adaptation(obs, legal)
            action = agent.decide(obs, legal)
            if neutral is not None and neutral != action:
                self._adaptation_notes.append(
                    explain_change(obs, legal, action, neutral, agent.adjustments)
                )
            try:
                hand.apply(action)
            except IllegalActionError:  # defensive: never let a buggy agent stall the game
                hand.apply(Action.check() if legal.can_check else Action.fold())

    # ------------------------------------------------------------------ events

    def _on_event(self, event: Event) -> None:
        if event.type in ("reveal", "showdown"):
            self.revealed |= {int(s) for s in event.data["hands"]}
        if event.type == "reveal":  # hands turned up before the board is complete (all-in)
            self._runout = True
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
                {
                    "amount": p["amount"],
                    "winners": p["winners"],
                    "hand": p["hand"],
                    "cards": self._winning_codes(p),
                }
                for p in pots
            ],
            "showdown": (
                {int(k): {**v, "best": self._best_codes(int(k))} for k, v in showdown.items()}
                if showdown
                else None
            ),
            "equity": self._equity_summary(showdown),
            "adaptation_notes": list(self._adaptation_notes),
        }
        if self.agent is not None:
            self.agent.observe_hand(self.hand.events, AI)
        start = next(e.data["stacks"] for e in self.hand.events if e.type == "hand_start")
        self.coach.finish_hand(self.hand.events, self.stacks[HUMAN] - start[HUMAN])
        self._recent[self.hand_number] = {
            "events": list(self.hand.events),
            "hero_cards": [c.code for c in self.hand.players[HUMAN].hole],
            "coach_hand": self.coach.pending(self.hand_number),
            "t": time.time() * 1000,
        }
        for old in sorted(self._recent)[:-RECENT_HANDS]:
            del self._recent[old]
        self.status = SessionStatus.GAME_OVER if min(self.stacks) == 0 else SessionStatus.WAITING

    def _equity_rows(self, board_size: int) -> list[dict[str, Any]]:
        """Face-up equity of every hand still in, at each street the board has reached.

        Preflop is a Monte Carlo estimate (with a standard error). Flop and turn are exact.
        Results are cached per hand, so the live all-in display and the post-hand review
        always show the same numbers.
        """
        assert self.hand is not None
        seats = [p.seat for p in self.hand.players if not p.folded]
        rows = []
        for i, (street, size) in enumerate(STREET_BOARD_SIZES):
            if size > board_size:
                break
            r = self._equity_cache.get(size)
            if r is None:
                seed = None if self.seed is None else self.seed * 10_000 + self.hand_number * 10
                r = calculate_equity(
                    [self.hand.players[s].hole for s in seats],
                    board=self.hand.board[:size],
                    iterations=10_000,
                    seed=None if seed is None else seed + i,
                )
                self._equity_cache[size] = r
            rows.append(
                {
                    "street": street,
                    "method": r.method,
                    "equity": {s: p.equity for s, p in zip(seats, r.players, strict=True)},
                    "std_error": {s: p.std_error for s, p in zip(seats, r.players, strict=True)},
                }
            )
        return rows

    def _equity_summary(self, showdown: dict[Any, Any] | None) -> list[dict[str, Any]] | None:
        """Per-street equity for post-hand review, only once every remaining hand is shown."""
        if not showdown or self.hand is None:
            return None
        return self._equity_rows(len(self.hand.board))

    def _live_equity(self) -> list[dict[str, Any]] | None:
        """During an all-in runout with every hand face up: equity so far, street by street.

        Not shown at an ordinary showdown: the post-hand review covers that.

        It is shown only when every remaining hand has been revealed, so it never uses a
        card the human cannot see. Rows stop at the turn. After the river there is nothing
        left to estimate, but the rows stay up until the showdown review replaces them,
        so the panel does not flicker away while the river is dealt.
        """
        hand = self.hand
        if (
            hand is None
            or not self._runout
            or hand.is_complete
            or self.status != SessionStatus.IN_HAND
        ):
            return None
        live = {p.seat for p in hand.players if not p.folded}
        if len(live) < 2 or not live <= self.revealed:
            return None
        return self._equity_rows(len(hand.board))

    def _opponent_model_view(self) -> dict[str, Any] | None:
        agent = self.agent
        if not isinstance(agent, EquityAgent):
            return None
        return {
            "adapts": agent.adapt_weight > 0,
            "weight": agent.adapt_weight,
            "policy": adaptation_policy(self.config.difficulty, self.config.adaptive),
            "hands": agent.model.hands,
            "min_opportunities": MIN_OPPORTUNITIES,
            "stats": agent.model.snapshot(),
            "adjustments": list(agent.adjustments.reasons.values()),
        }

    def _public_event(self, event: Event) -> dict[str, Any]:
        data = dict(event.data)
        if event.type == "showdown":
            data["hands"] = {
                s: {**h, "best": self._best_codes(int(s))} for s, h in data["hands"].items()
            }
        elif event.type == "pot_awarded":
            data["cards"] = self._winning_codes(data)
        return {"type": event.type, **data}

    def _best_codes(self, seat: int) -> list[str]:
        """The five cards ``seat`` plays at showdown (hole cards preferred on ties)."""
        assert self.hand is not None
        return [c.code for c in best_five(self.hand.players[seat].hole + self.hand.board)]

    def _winning_codes(self, pot: dict[str, Any]) -> list[str]:
        """Cards that make the winning hand(s) of an awarded pot; empty if uncontested."""
        if pot["hand"] is None:
            return []
        codes: list[str] = []
        for seat in pot["winners"]:
            codes += [c for c in self._best_codes(seat) if c not in codes]
        return codes

    def _name(self, seat: int) -> str:
        return self.config.player_name if seat == HUMAN else self.config.ai_name

    def _describe(self, event: Event) -> str | None:
        if event.type == "reveal":  # the live log shows revealed cards on the table instead
            return None
        names = [self._name(HUMAN), self._name(AI)]
        return describe(event.type, event.data, names, self.hand_number)

    # ------------------------------------------------------------------ history

    def finished_hand(self, number: int) -> dict[str, Any] | None:
        """What is kept of a recently finished hand (``None`` if unknown or too old)."""
        with self.lock:
            return self._recent.get(number)

    def hand_record(self, number: int) -> dict[str, Any] | None:
        """The hand-history record of a recently finished hand, with its coaching review.

        Slow the first time (the review is computed): call it outside the session lock.
        """
        kept = self.finished_hand(number)
        if kept is None:
            return None
        coach_hand = kept["coach_hand"]
        review = self.coach.review_hand(coach_hand) if coach_hand is not None else None
        return build_record(
            session_id=self.id,
            hand_number=number,
            events=kept["events"],
            hero=HUMAN,
            hero_cards=kept["hero_cards"],
            names=[self._name(HUMAN), self._name(AI)],
            t_ms=kept["t"],
            difficulty=self.config.difficulty.value,
            adaptive=self.config.adaptive,
            review=review,
        )

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
                    "hand_description": (
                        evaluate(p.hole + hand.board).description
                        if show and hand and len(hand.board) >= 3
                        else None
                    ),
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
            "live_equity": self._live_equity(),
            "opponent_model": self._opponent_model_view(),
            "winner": winner,
            "config": {
                "starting_stack": cfg.starting_stack,
                "small_blind": cfg.small_blind,
                "big_blind": cfg.big_blind,
                "difficulty": cfg.difficulty.value,
                "adaptive": cfg.adaptive,
            },
        }
