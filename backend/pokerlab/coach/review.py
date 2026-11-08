"""Post-hand reviews for a heads-up session.

The session calls :meth:`HandCoach.record` just before applying each of the human's
actions, with the observation the human had at that moment. Nothing recorded there can
contain the opponent's cards. After the hand, :meth:`HandCoach.review` analyses those
decisions and separates the quality of the decisions from the result of the hand.

The coach also keeps its own :class:`~pokerlab.ai.player_model.PlayerModel` of the
*opponent*, learned from the same public events, so a review can account for the
opponent's observed tendencies ("they bet 60% when checked to, so their bets are read
as wider"). It is updated only after a hand is reviewed, so each decision is judged
with the tendencies known when it was made.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ..ai.adaptation import Adjustments, compute_adjustments
from ..ai.base import Observation
from ..ai.player_model import PlayerModel
from ..engine import Action, Event, LegalActions
from .analysis import GRADES, analyze
from .spot import Spot, villain_reads


@dataclass(slots=True)
class _Decision:
    obs: Observation
    legal: LegalActions
    action: Action
    adjust: Adjustments | None
    reads: list[str]
    spot: Spot


@dataclass(slots=True)
class _Hand:
    number: int
    stacks: tuple[int, int]
    button: int
    decisions: list[_Decision] = field(default_factory=list)
    net: int | None = None
    showdown: bool = False
    review: dict[str, Any] | None = None


class HandCoach:
    def __init__(
        self,
        hero: int,
        villain: int,
        small_blind: int,
        big_blind: int,
        names: dict[int, str] | None = None,
        iterations: int | None = None,
    ):
        self.hero, self.villain = hero, villain
        self.small_blind, self.big_blind = small_blind, big_blind
        self.names = names or {}
        self.iterations = iterations
        self.villain_model = PlayerModel()
        self._hand: _Hand | None = None
        self._finished: _Hand | None = None  # the last completed hand (reviewable)

    # ------------------------------------------------------------------ recording

    def start_hand(self, number: int, stacks: Sequence[int], button: int) -> None:
        self._hand = _Hand(number, (int(stacks[0]), int(stacks[1])), button)

    def record(self, obs: Observation, legal: LegalActions, action: Action) -> None:
        """Remember a decision. ``obs`` must be the deciding seat's own observation."""
        if self._hand is None or obs.seat != self.hero:
            return
        adjust = compute_adjustments(self.villain_model, 1.0)
        reads = villain_reads(self.villain_model, self.names.get(self.villain, "The opponent"))
        spot = Spot(
            hero=(obs.hole[0], obs.hole[1]),
            board=obs.board,
            actions=tuple(
                (a.action, a.amount if a.action in ("bet", "raise") else None) for a in obs.actions
            ),
            button=obs.button,
            stacks=self._hand.stacks,
            small_blind=self.small_blind,
            big_blind=self.big_blind,
            hero_seat=self.hero,
            villain_stats={
                k: (round(s.count, 2), round(s.opportunities, 2))
                for k, s in self.villain_model.stats.items()
                if s.opportunities > 0
            },
        )
        self._hand.decisions.append(
            _Decision(obs, legal, action, None if adjust.neutral else adjust, reads, spot)
        )

    def discard_last(self) -> None:
        """Forget the last recorded decision (the action turned out to be illegal)."""
        if self._hand is not None and self._hand.decisions:
            self._hand.decisions.pop()

    def finish_hand(self, events: Sequence[Event], net: int) -> None:
        """Close the hand (after its result is known) and learn the opponent's actions."""
        hand = self._hand
        if hand is None:
            return
        hand.net = net
        hand.showdown = any(e.type == "showdown" for e in events)
        self._finished, self._hand = hand, None
        self.villain_model.observe(events, self.villain)

    # ------------------------------------------------------------------ reviewing

    @property
    def last_hand_number(self) -> int | None:
        return self._finished.number if self._finished else None

    def pending(self, number: int) -> _Hand | None:
        """The finished hand ``number`` if it is the latest one, else ``None``."""
        if self._finished is None or self._finished.number != number:
            return None
        return self._finished

    def review(self, number: int) -> dict[str, Any] | None:
        hand = self.pending(number)
        return None if hand is None else self.review_hand(hand)

    def review_hand(self, hand: _Hand) -> dict[str, Any]:
        """Analyse a finished hand (slow: call it outside the session lock)."""
        if hand.review is None:
            hand.review = build_review(hand, self.big_blind, self.names, self.iterations)
        return hand.review


def build_review(
    hand: _Hand, big_blind: int, names: dict[int, str], iterations: int | None = None
) -> dict[str, Any]:
    decisions = []
    for d in hand.decisions:
        kwargs: dict[str, Any] = {} if iterations is None else {"iterations": iterations}
        a = analyze(
            d.obs,
            d.legal,
            d.action,
            adjust=d.adjust,
            reads=d.reads,
            seed=d.spot.seed(),
            names=names,
            **kwargs,
        )
        decisions.append({**a.to_dict(), "spot_data": d.spot.to_dict()})
    counts = {g: sum(1 for d in decisions if d["grade"] == g) for g in GRADES}
    net = hand.net or 0
    ev_loss = sum(d["ev_loss"] for d in decisions if d["grade"] != "ungraded")
    return {
        "hand_number": hand.number,
        "decisions": decisions,
        "counts": counts,
        "net": net,
        "net_bb": net / big_blind,
        "ev_loss_bb": ev_loss / big_blind,
        "quality": _quality(counts),
        "verdict": verdict(_quality(counts), net, counts),
        "hindsight": (
            "The opponent's cards shown at showdown were not used: these grades use only "
            "what you could see when you decided."
            if hand.showdown
            else None
        ),
        "tags": sorted({t for d in decisions for t in d["tags"]}),
    }


def _quality(counts: dict[str, int]) -> str:
    graded = sum(v for k, v in counts.items() if k != "ungraded")
    if graded == 0:
        return "none"
    if counts["mistake"]:
        return "costly"
    if counts["questionable"]:
        return "mixed"
    return "sound"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def verdict(quality: str, net: int, counts: dict[str, int]) -> str:
    """One or two sentences that keep decision quality and the result apart."""
    flagged = counts["mistake"] + counts["questionable"]
    result = (
        f"You won {net} chips"
        if net > 0
        else (f"You lost {-net} chips" if net < 0 else "You broke even")
    )
    if quality == "none":
        return f"{result}. You made no decisions that could be graded this hand."
    if quality == "sound":
        if net < 0:
            return (
                f"{result}, but your decisions held up by these estimates. Good decisions "
                "still lose often in the short run: judge them by the reasoning, not the result."
            )
        return f"{result}, and your decisions held up by these estimates."
    decisions = _plural(flagged, "decision")
    if net > 0:
        return (
            f"{result}, but {decisions} gave up value by these estimates. Winning the pot "
            "does not make a decision right: over many hands, spots like these cost chips."
        )
    return f"{result}, and {decisions} gave up value by these estimates."
