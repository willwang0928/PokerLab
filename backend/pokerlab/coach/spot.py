"""A decision point described only by public information plus the hero's own cards.

A :class:`Spot` is replayed through the real :class:`~pokerlab.engine.HoldemHand`, so
pot sizes, legal options and the betting history always follow the rules exactly. The
opponent receives placeholder hole cards (they are never shown or analysed: the
observation handed to the coach does not contain them).

Spots are plain data, so they serialise to JSON. Training scenarios, and decisions from
real hands that the player wants to practise again, both travel as spots.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from ..ai.adaptation import DEADBAND, Adjustments, compute_adjustments
from ..ai.base import Observation
from ..ai.player_model import BASELINES, PlayerModel, Stat
from ..cards import FULL_DECK, Card, Deck, parse_cards
from ..engine import Action, ActionType, HoldemHand, IllegalActionError, LegalActions

MAX_ACTIONS = 40
MAX_CHIPS = 1_000_000
_BOARD_CHUNKS = (3, 1, 1)


class SpotError(ValueError):
    """The spot is malformed or does not lead to a decision for the hero."""


@dataclass(frozen=True, slots=True)
class Spot:
    hero: tuple[Card, Card]
    board: tuple[Card, ...]
    actions: tuple[tuple[str, int | None], ...]  # every action from the start of the hand
    button: int
    stacks: tuple[int, int]
    small_blind: int
    big_blind: int
    hero_seat: int = 0
    # Observed opponent tendencies: stat -> (count, opportunities).
    villain_stats: dict[str, tuple[float, float]] = field(default_factory=dict)

    # ------------------------------------------------------------------ serialisation

    def to_dict(self) -> dict[str, Any]:
        return {
            "hero": "".join(c.code for c in self.hero),
            "board": "".join(c.code for c in self.board),
            "actions": [[t, a] for t, a in self.actions],
            "button": self.button,
            "stacks": list(self.stacks),
            "blinds": [self.small_blind, self.big_blind],
            "hero_seat": self.hero_seat,
            "villain_stats": {
                k: [round(c, 2), round(o, 2)] for k, (c, o) in sorted(self.villain_stats.items())
            },
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Spot:
        try:
            hero = parse_cards(str(d["hero"]))
            board = parse_cards(str(d.get("board", "")))
            actions = tuple(
                (str(a[0]), None if len(a) < 2 or a[1] is None else int(a[1]))
                for a in d.get("actions", [])
            )
            stacks = tuple(int(s) for s in d["stacks"])
            sb, bb = (int(x) for x in d["blinds"])
            stats = {
                str(k): (float(v[0]), float(v[1])) for k, v in d.get("villain_stats", {}).items()
            }
            spot = cls(
                hero=(hero[0], hero[1]) if len(hero) == 2 else (),  # type: ignore[arg-type]
                board=tuple(board),
                actions=actions,
                button=int(d["button"]),
                stacks=stacks,  # type: ignore[arg-type]
                small_blind=sb,
                big_blind=bb,
                hero_seat=int(d.get("hero_seat", 0)),
                villain_stats=stats,
            )
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise SpotError(f"Malformed spot: {exc}") from exc
        spot.validate()
        return spot

    def key(self) -> str:
        """Stable hash of the spot (seeds the analysis so results are reproducible)."""
        raw = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def seed(self) -> int:
        return int(self.key(), 16)

    # ------------------------------------------------------------------ validation

    def validate(self) -> None:
        if len(self.hero) != 2:
            raise SpotError("The hero needs exactly two hole cards")
        if len(self.board) not in (0, 3, 4, 5):
            raise SpotError("The board must have 0, 3, 4 or 5 cards")
        cards = list(self.hero) + list(self.board)
        if len(set(cards)) != len(cards):
            raise SpotError("Duplicate cards")
        if len(self.stacks) != 2 or self.button not in (0, 1) or self.hero_seat not in (0, 1):
            raise SpotError("Spots are heads-up: two stacks, seats 0 and 1")
        if not 0 < self.small_blind <= self.big_blind <= MAX_CHIPS:
            raise SpotError("Invalid blinds")
        if any(not 0 < s <= MAX_CHIPS for s in self.stacks):
            raise SpotError("Invalid stacks")
        if len(self.actions) > MAX_ACTIONS:
            raise SpotError("Too many actions")
        for k, (c, o) in self.villain_stats.items():
            if k not in BASELINES or not 0 <= c <= o <= 10_000:
                raise SpotError(f"Invalid opponent stat {k!r}")

    # ------------------------------------------------------------------ replay

    def _deck(self, villain_cards: list[Card]) -> Deck:
        holes = {self.hero_seat: list(self.hero), 1 - self.hero_seat: villain_cards}
        order = [(self.button + 1) % 2, self.button]  # dealing starts left of the button
        seq = [holes[s][r] for r in range(2) for s in order]
        used = set(seq) | set(self.board)
        spare = iter(c for c in FULL_DECK if c not in used)
        board = list(self.board)
        i = 0
        for size in _BOARD_CHUNKS:
            if i >= len(board):
                break
            seq.append(next(spare))  # burn
            seq.extend(board[i : i + size])
            i += size
        return Deck.stacked(seq)

    def replay(self) -> tuple[HoldemHand, Observation, LegalActions]:
        """Play the actions through the engine and return the hero's decision point."""
        self.validate()
        used = set(self.hero) | set(self.board)
        villain_cards = [c for c in FULL_DECK if c not in used][-2:]  # placeholders
        hand = HoldemHand(
            list(self.stacks),
            self.button,
            self.small_blind,
            self.big_blind,
            deck=self._deck(villain_cards),
        )
        try:
            for kind, amount in self.actions:
                if hand.is_complete or hand.to_act is None:
                    raise SpotError("The hand ended before the last action")
                hand.apply(Action(ActionType(kind), amount))
        except (IllegalActionError, ValueError) as exc:
            if isinstance(exc, SpotError):
                raise
            raise SpotError(f"Illegal action in spot: {exc}") from exc
        if hand.is_complete or hand.to_act != self.hero_seat:
            raise SpotError("The spot does not end with the hero to act")
        if len(hand.board) != len(self.board):
            raise SpotError("The board does not match the street reached by the actions")
        return hand, Observation.from_hand(hand, self.hero_seat), hand.legal_actions()

    # ------------------------------------------------------------------ opponent model

    def villain_model(self) -> PlayerModel:
        model = PlayerModel()
        for k, (c, o) in self.villain_stats.items():
            model.stats[k] = Stat(count=c, opportunities=o)
        return model

    def opponent_info(self, name: str = "The opponent") -> dict[str, Any]:
        """What is known about the opponent's tendencies at this decision (see
        :func:`opponent_profile`)."""
        if not self.villain_stats:
            return {
                "evidence": [],
                "used": [],
                "style": [],
                "note": "Nothing is known yet about this opponent's tendencies, so their "
                "range is estimated from default tendencies.",
            }
        return opponent_profile(self.villain_model(), name)

    def villain_adjustments(self) -> Adjustments | None:
        if not self.villain_stats:
            return None
        adj = compute_adjustments(self.villain_model(), 1.0)
        return None if adj.neutral else adj


def villain_reads(model: PlayerModel, name: str = "The opponent") -> list[str]:
    """Plain-language notes on which observed tendencies shift the range estimate."""
    adj = compute_adjustments(model, 1.0)
    reads = []
    for key, phrase in (
        ("bet_unopposed", "bets when checked to"),
        ("raise_vs_bet", "raises when facing a bet"),
        ("fold_to_bet", "folds to a bet"),
        ("pfr", "raises preflop"),
        ("vpip", "plays"),
    ):
        if not model.reliable(key):
            continue
        est, ref = model.estimate(key), BASELINES[key]
        if abs(est - ref) < DEADBAND:
            continue
        effect = {
            "bet_unopposed": "so their bets are read as wider"
            if est > ref
            else "so their bets are read as stronger",
            "raise_vs_bet": "so their raises are read as wider"
            if est > ref
            else "so their raises are read as stronger",
            "fold_to_bet": "so the hands they call with are read as stronger"
            if est > ref
            else "so the hands they call with are read as wider and your bets are "
            "expected to be called more often",
            "pfr": "so their preflop raises are read as wider"
            if est > ref
            else "so their preflop raises are read as stronger",
            "vpip": "so their preflop calls are read as wider"
            if est > ref
            else "so their preflop calls are read as stronger",
        }[key]
        what = f"plays {est:.0%} of hands" if key == "vpip" else f"{phrase} {est:.0%} of the time"
        ev = stat_evidence(model, key)
        sure = "" if ev["clear"] else "; tentative"
        reads.append(
            f"{name} {what} (reference {ref:.0%}; roughly {ev['low']:.0%}–{ev['high']:.0%} "
            f"from {ev['seen']:.0f} chances{sure}), {effect}."
        )
    if adj.neutral:
        return []
    return reads


# Stats that change how the coach reads the opponent's range or folds, in display order.
_READ_STATS = ("vpip", "pfr", "bet_unopposed", "raise_vs_bet", "fold_to_bet", "aggression")
_STAT_PHRASE = {
    "vpip": "plays a hand preflop",
    "pfr": "raises preflop",
    "bet_unopposed": "bets when checked to",
    "raise_vs_bet": "raises a bet",
    "fold_to_bet": "folds to a bet",
    "aggression": "bets or raises (of their postflop actions)",
}
Z80 = 1.2816  # two-sided 80% normal quantile


def stat_evidence(model: PlayerModel, key: str) -> dict[str, Any]:
    """One tendency with its evidence: the estimate, a rough 80% interval and the sample.

    The estimate is the model's Beta posterior mean (a prior of ``PRIOR_STRENGTH``
    pseudo-observations at the reference). The interval is a normal approximation to
    that posterior, so it is rough, but it shows honestly how little 15-30 observations
    pin down. A read is *clear* only when the reference lies outside the interval.
    """
    from ..ai.player_model import MIN_OPPORTUNITIES, PRIOR_STRENGTH

    s, ref = model.stats[key], BASELINES[key]
    a = s.count + ref * PRIOR_STRENGTH
    b = (s.opportunities - s.count) + (1 - ref) * PRIOR_STRENGTH
    mean = a / (a + b)
    sd = (a * b / ((a + b) ** 2 * (a + b + 1))) ** 0.5
    low, high = max(0.0, mean - Z80 * sd), min(1.0, mean + Z80 * sd)
    reliable = s.opportunities >= MIN_OPPORTUNITIES
    return {
        "key": key,
        "phrase": _STAT_PHRASE.get(key, key),
        "estimate": mean,
        "low": low,
        "high": high,
        "reference": ref,
        "seen": round(s.opportunities, 1),
        "reliable": reliable,
        "used": reliable and abs(mean - ref) >= DEADBAND,
        "clear": reliable and not (low <= ref <= high),
    }


def opponent_profile(model: PlayerModel, name: str = "The opponent") -> dict[str, Any]:
    """What the coach knows about the opponent's tendencies, and how sure it is."""
    evidence = [stat_evidence(model, k) for k in _READ_STATS]
    used = [e for e in evidence if e["used"]]
    style = []
    by = {e["key"]: e for e in evidence}
    if by["vpip"]["clear"]:
        style.append("loose" if by["vpip"]["estimate"] > by["vpip"]["reference"] else "tight")
    if by["aggression"]["clear"]:
        style.append(
            "aggressive"
            if by["aggression"]["estimate"] > by["aggression"]["reference"]
            else "passive"
        )
    if by["fold_to_bet"]["clear"]:
        style.append(
            "folds often to bets"
            if by["fold_to_bet"]["estimate"] > by["fold_to_bet"]["reference"]
            else "rarely folds to bets"
        )
    most = max((e["seen"] for e in evidence), default=0)
    if not used:
        note = (
            f"Too few hands to read {name.lower() if name == 'The opponent' else name} yet "
            f"(at most {most:.0f} chances for any one tendency; 15 are needed): their range "
            "is estimated from default tendencies."
            if most < 15
            else f"{name}'s observed tendencies are close to the defaults, so the default "
            "range estimate is used."
        )
    elif all(e["clear"] for e in used):
        note = f"{name}'s tendencies below are well supported and shift their estimated range."
    else:
        note = (
            f"{name}'s tendencies shift their estimated range a little, but some reads rest "
            "on a small sample: treat them as tentative."
        )
    return {"evidence": evidence, "used": [e["key"] for e in used], "style": style, "note": note}
