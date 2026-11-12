"""Hand histories: a completed hand as public data, and its replay (v0.4.3).

**The record.** When a hand ends, :func:`build_record` turns the engine's event log into
a self-contained JSON document: blinds, button, starting stacks, the hero's cards, every
public event (blinds, actions with amounts, streets, uncalled bets, showdown and pots),
the result, and the coaching review computed during the hand. The opponent's cards are
included only if they were shown (an all-in reveal or a showdown); the deal itself, and
anything a folded hand held, never appears.

**The replay.** :func:`replay` rebuilds the table after every event from the events
alone: stacks, bets in front of each player, the pot, the board, who has folded, and
which opponent cards are visible *at that moment*. Nothing is filled in: every chip
movement is recomputed and checked against the amounts the engine recorded (each action
records the resulting stack and bet, each collection the pot, the final event the
stacks), and chips must be conserved. A record that does not add up raises
:class:`HistoryError`, so a corrupted record is refused rather than replayed wrongly.

**Validation.** :func:`validate_record` checks an uploaded record field by field, then
replays it. It also refuses records that would leak hidden information: opponent cards
that no reveal or showdown event shows.

**Coaching.** Decisions are graded during the hand from the hero's own observation (see
:mod:`pokerlab.coach.review`), so neither the opponent's cards nor later streets can
influence them. The record stores those grades as they were; a replay never re-grades a
decision with hindsight.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .ai.preflop import hand_class, percentile
from .cards import parse_cards
from .evaluator import evaluate

RECORD_VERSION = 1
MAX_EVENTS = 400
MAX_RECORD_BYTES = 400_000
MAX_DECISIONS = 60
MAX_SEATS = 10

# Engine events kept in a record. ``deal_hole`` is left out: it carries no public facts.
PUBLIC_EVENTS = (
    "hand_start",
    "post_blind",
    "action",
    "uncalled_returned",
    "bets_collected",
    "reveal",
    "street",
    "showdown",
    "pot_awarded",
    "hand_complete",
)
ACTIONS = ("fold", "check", "call", "bet", "raise")
STREETS = ("preflop", "flop", "turn", "river")
BOARD_SIZE = {"flop": 3, "turn": 4, "river": 5}
_CARD = re.compile(r"^[2-9TJQKA][cdhs]$")
_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class HistoryError(ValueError):
    """A hand record is malformed or does not add up."""


# --------------------------------------------------------------------------- text


def describe(
    type_: str, data: Mapping[str, Any], names: Sequence[str], hand_number: int
) -> str | None:
    """One log line for an event ("You call 20", "Flop: Ah 7c 2d"), or ``None``."""

    def name(seat: int) -> str:
        return names[seat] if 0 <= seat < len(names) else f"Seat {seat + 1}"

    def say(seat: int, verb: str, rest: str = "") -> str:
        who = name(seat)
        if who.lower() != "you":
            verb = {"have": "has"}.get(verb, verb + ("es" if verb.endswith("sh") else "s"))
        return f"{who} {verb}" + (f" {rest}" if rest else "")

    d = data
    if type_ == "hand_start":
        return f"Hand #{hand_number} — " + say(d["button"], "have", "the button")
    if type_ == "post_blind":
        kind = "small" if d["blind"] == "small_blind" else "big"
        return say(d["seat"], "post", f"{kind} blind {d['amount']}")
    if type_ == "action":
        verb, rest = {
            "fold": ("fold", ""),
            "check": ("check", ""),
            "call": ("call", str(d["amount"])),
            "bet": ("bet", str(d["amount"])),
            "raise": ("raise", f"to {d['amount']}"),
        }[d["action"]]
        return say(d["seat"], verb, rest) + (" (all-in)" if d["all_in"] else "")
    if type_ == "street":
        return f"{d['street'].title()}: {' '.join(d['board'])}"
    if type_ == "uncalled_returned":
        return f"Uncalled {d['amount']} returned to {name(d['seat'])}"
    if type_ == "showdown":
        return "; ".join(
            say(int(s), "show", f"{' '.join(h['cards'])} ({h['description']})")
            for s, h in d["hands"].items()
        )
    if type_ == "reveal":
        return "Hands are turned face up: " + "; ".join(
            f"{name(int(s))} {' '.join(c)}" for s, c in d["hands"].items()
        )
    if type_ == "pot_awarded":
        hand = f" with {d['hand']}" if d["hand"] else ""
        if len(d["winners"]) > 1:
            return f"Split pot: {d['amount']} shared{hand}"
        return say(d["winners"][0], "win", f"{d['amount']}{hand}")
    return None


# --------------------------------------------------------------------------- building


def record_id(session_id: str, hand_number: int) -> str:
    """A stable id: the same hand uploaded twice is stored once."""
    digest = hashlib.sha256(f"{session_id}:{hand_number}".encode()).hexdigest()
    return "h" + digest[:23]


def _jsonable(data: Mapping[str, Any]) -> dict[str, Any]:
    """Event data as JSON would return it (integer seat keys become strings)."""
    return json.loads(json.dumps(data))


def build_record(
    *,
    session_id: str,
    hand_number: int,
    events: Sequence[Any],
    hero: int,
    hero_cards: Sequence[str],
    names: Sequence[str],
    t_ms: float,
    difficulty: str | None = None,
    adaptive: bool | None = None,
    review: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """A completed hand as public data. ``events`` are the engine's events.

    Opponent cards come only from ``reveal`` / ``showdown`` events, which the engine
    emits only for hands that are turned up, so a folded hand can never be included.
    """
    public = [{"type": e.type, **_jsonable(e.data)} for e in events if e.type in PUBLIC_EVENTS]
    start = next(e for e in public if e["type"] == "hand_start")
    seats = len(start["stacks"])
    shown: dict[int, list[str]] = {}
    for e in public:
        if e["type"] == "reveal":
            shown.update({int(s): list(c) for s, c in e["hands"].items()})
        elif e["type"] == "showdown":
            shown.update({int(s): list(h["cards"]) for s, h in e["hands"].items()})
    opponents = {s: shown.get(s) for s in range(seats) if s != hero}
    board: list[str] = []
    for e in public:
        if e["type"] == "street":
            board = list(e["board"])
    decisions = list((review or {}).get("decisions", []))
    hero_actions = [i for i, e in enumerate(public) if e["type"] == "action" and e["seat"] == hero]
    linked = bool(decisions) and len(hero_actions) == len(decisions)
    if linked:
        for k, i in enumerate(hero_actions):
            public[i]["decision"] = k
    return {
        "v": RECORD_VERSION,
        "id": record_id(session_id, hand_number),
        "t": float(t_ms),
        "session": session_id,
        "hand_number": hand_number,
        "mode": "play",
        "difficulty": difficulty,
        "adaptive": adaptive,
        "hero": hero,
        "names": list(names),
        "hero_cards": list(hero_cards),
        "opponent_cards": {str(s): c for s, c in opponents.items()},
        "board": board,
        "events": public,
        "decisions_linked": linked,
        "coaching": dict(review) if review is not None else None,
    }


# --------------------------------------------------------------------------- replay


def _int(v: Any, what: str, low: int = 0) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or v < low:
        raise HistoryError(f"{what} must be an integer ≥ {low}")
    return v


def replay(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The table after each event, rebuilt from the events and checked as it goes.

    Each frame has: ``index`` (event position), ``type``, ``text`` (log line),
    ``street``, ``board``, ``pot`` (all chips committed and not yet paid out, including
    bets in front of players), ``bets``, ``stacks``, ``folded``, ``actor`` (seat or
    ``None``), ``decision`` (index into the coaching decisions, for the hero's actions),
    ``to_call`` (what the actor faced before acting), ``visible`` (opponent cards shown
    so far, by seat) and ``awarded`` (payouts of a pot, by seat).
    """
    events = record.get("events")
    if not isinstance(events, list) or not events:
        raise HistoryError("record has no events")
    names = record.get("names") or []
    number = record.get("hand_number", 0)
    first = events[0]
    if not isinstance(first, dict) or first.get("type") != "hand_start":
        raise HistoryError("the first event must be hand_start")
    start = first.get("stacks")
    if not isinstance(start, list) or not 2 <= len(start) <= MAX_SEATS:
        raise HistoryError("hand_start needs 2 to 10 stacks")
    n = len(start)
    stacks = [_int(s, "stack") for s in start]
    total = sum(stacks)
    bets = [0] * n
    folded = [False] * n
    big_blind = _int(first.get("big_blind"), "big_blind", 1)
    current = 0  # the bet to match (a short all-in blind does not lower it)
    collected = 0
    board: list[str] = []
    street = "preflop"
    visible: dict[str, list[str]] = {}
    frames: list[dict[str, Any]] = []
    finished = False
    awarded_total = 0
    payable = 0

    def seat_of(v: Any) -> int:
        s = _int(v, "seat")
        if s >= n:
            raise HistoryError(f"seat {s} is not at the table")
        return s

    for i, e in enumerate(events):
        if not isinstance(e, dict) or e.get("type") not in PUBLIC_EVENTS:
            raise HistoryError(f"event {i} has an unknown type")
        if finished:
            raise HistoryError("events after hand_complete")
        t = e["type"]
        if t == "hand_start" and i:
            raise HistoryError("hand_start may only come first")
        actor: int | None = None
        to_call = None
        awarded: dict[str, int] | None = None
        if t == "post_blind":
            actor = seat_of(e.get("seat"))
            amt = _int(e.get("amount"), "blind")
            if amt > stacks[actor]:
                raise HistoryError("a blind larger than the stack")
            stacks[actor] -= amt
            bets[actor] += amt
            current = max(current, big_blind if e.get("blind") == "big_blind" else amt)
        elif t == "action":
            actor = seat_of(e.get("seat"))
            kind = e.get("action")
            if kind not in ACTIONS:
                raise HistoryError(f"unknown action {kind!r}")
            if folded[actor]:
                raise HistoryError("a folded player acted")
            amt = _int(e.get("amount", 0), "amount")
            # As the engine: never more than any opponent still in could match.
            reachable = max(
                (bets[s] + stacks[s] for s in range(n) if s != actor and not folded[s]),
                default=0,
            )
            target = min(current, max(reachable, bets[actor]))
            to_call = min(max(target - bets[actor], 0), stacks[actor])
            if kind == "fold":
                folded[actor] = True
            elif kind == "check":
                if to_call:
                    raise HistoryError("a check facing a bet")
            elif kind == "call":
                if amt != to_call or amt == 0:
                    raise HistoryError(f"call of {amt} does not match {to_call} to call")
                stacks[actor] -= amt
                bets[actor] += amt
            else:  # bet / raise: amount is the total bet ("raise to")
                add = amt - bets[actor]
                if add <= 0 or add > stacks[actor] or amt <= current:
                    raise HistoryError(f"{kind} to {amt} is impossible here")
                stacks[actor] -= add
                bets[actor] = current = amt
            if "stack" in e and (e["stack"] != stacks[actor] or e.get("bet") != bets[actor]):
                raise HistoryError(f"event {i}: stack or bet does not match the replay")
        elif t == "uncalled_returned":
            actor = seat_of(e.get("seat"))
            amt = _int(e.get("amount"), "amount", 1)
            if amt > bets[actor]:
                raise HistoryError("more returned than was bet")
            bets[actor] -= amt
            stacks[actor] += amt
        elif t == "bets_collected":
            collected += sum(bets)
            bets, current = [0] * n, 0
            if e.get("pot") != collected:
                raise HistoryError(f"pot {e.get('pot')} does not match the replay ({collected})")
        elif t == "street":
            name = e.get("street")
            cards = e.get("board")
            if name not in BOARD_SIZE or not isinstance(cards, list):
                raise HistoryError("bad street event")
            if len(cards) != BOARD_SIZE[name] or cards[: len(board)] != board:
                raise HistoryError("the board does not follow from the previous street")
            if any(bets):
                raise HistoryError("a new street with bets not collected")
            board, street = list(cards), name
        elif t in ("reveal", "showdown"):
            hands = e.get("hands")
            if not isinstance(hands, dict) or not hands:
                raise HistoryError(f"bad {t} event")
            for s, h in hands.items():
                seat = seat_of(int(s))
                if folded[seat]:
                    raise HistoryError("a folded hand was shown")
                cards = h if t == "reveal" else (h or {}).get("cards")
                if seat != record.get("hero"):
                    visible[str(seat)] = list(cards or [])
            if t == "showdown":
                street = "showdown"
        elif t == "pot_awarded":
            if not awarded_total:
                payable = collected + sum(bets)
            pay = e.get("payouts")
            if not isinstance(pay, dict) or not pay:
                raise HistoryError("pot_awarded without payouts")
            awarded = {}
            for s, amt in pay.items():
                seat = seat_of(int(s))
                if folded[seat]:
                    raise HistoryError("a folded player was paid")
                awarded[str(seat)] = _int(amt, "payout")
                stacks[seat] += awarded[str(seat)]
            if sum(awarded.values()) != e.get("amount"):
                raise HistoryError("payouts do not add up to the pot")
            awarded_total += sum(awarded.values())
            if awarded_total > payable:
                raise HistoryError("more paid out than was in the pot")
        elif t == "hand_complete":
            if awarded_total != payable or payable == 0:
                raise HistoryError("the pot was not paid out exactly")
            collected, bets = 0, [0] * n
            final = e.get("stacks")
            if final != stacks:
                raise HistoryError(f"final stacks {final} do not match the replay {stacks}")
            finished = True
        pot = collected + sum(bets) - awarded_total if t != "hand_complete" else 0
        if sum(stacks) + pot != total:
            raise HistoryError(f"event {i}: chips are not conserved")
        frames.append(
            {
                "index": i,
                "type": t,
                "text": describe(t, e, names, number) if t != "hand_complete" else "Hand over",
                "street": street,
                "board": list(board),
                "pot": pot,
                "bets": list(bets),
                "stacks": list(stacks),
                "folded": list(folded),
                "actor": actor,
                "decision": e.get("decision") if t == "action" else None,
                "to_call": to_call,
                "visible": {k: list(v) for k, v in visible.items()},
                "awarded": awarded,
            }
        )
    if not finished:
        raise HistoryError("the hand is incomplete (no hand_complete event)")
    return frames


# --------------------------------------------------------------------------- validation


def _cards(v: Any, what: str, low: int, high: int) -> list[str]:
    if not isinstance(v, list) or not low <= len(v) <= high:
        raise HistoryError(f"{what} must list {low}–{high} cards")
    for c in v:
        if not isinstance(c, str) or not _CARD.match(c):
            raise HistoryError(f"{what}: {c!r} is not a card")
    return list(v)


def validate_record(data: Any) -> dict[str, Any]:
    """A clean copy of an uploaded record, or :class:`HistoryError` with the reason."""
    if not isinstance(data, dict):
        raise HistoryError("a record must be an object")
    if len(json.dumps(data, separators=(",", ":"))) > MAX_RECORD_BYTES:
        raise HistoryError("record too large")
    if data.get("v") != RECORD_VERSION:
        raise HistoryError(f"unsupported record version {data.get('v')!r}")
    rid = data.get("id")
    if not isinstance(rid, str) or not _ID.match(rid):
        raise HistoryError("bad id")
    t = data.get("t")
    if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t):
        raise HistoryError("t must be a finite number")
    if not 1e12 <= t <= 1e14:  # milliseconds since 1970 (2001 to 5138)
        raise HistoryError("t must be a timestamp in milliseconds")
    number = _int(data.get("hand_number"), "hand_number", 1)
    names = data.get("names")
    if not isinstance(names, list) or not all(isinstance(x, str) and len(x) <= 40 for x in names):
        raise HistoryError("names must be short strings")
    events = data.get("events")
    if not isinstance(events, list) or not 1 <= len(events) <= MAX_EVENTS:
        raise HistoryError(f"events must be a list of at most {MAX_EVENTS}")
    seats = len((events[0] or {}).get("stacks") or []) if isinstance(events[0], dict) else 0
    if len(names) != seats:
        raise HistoryError("one name per seat is needed")
    hero = _int(data.get("hero"), "hero")
    if hero >= seats:
        raise HistoryError("hero is not at the table")
    hero_cards = _cards(data.get("hero_cards"), "hero_cards", 2, 2)
    board = _cards(data.get("board"), "board", 0, 5)
    opp = data.get("opponent_cards")
    if not isinstance(opp, dict):
        raise HistoryError("opponent_cards must be an object")
    opponent_cards: dict[str, list[str] | None] = {}
    for s, cards in opp.items():
        if not isinstance(s, str) or not s.isdigit() or int(s) == hero or int(s) >= seats:
            raise HistoryError("opponent_cards has a bad seat")
        opponent_cards[s] = None if cards is None else _cards(cards, "opponent_cards", 2, 2)
    seen = hero_cards + board + [c for v in opponent_cards.values() if v for c in v]
    if len(set(seen)) != len(seen):
        raise HistoryError("a card appears twice")
    for k in ("session", "mode"):
        if not isinstance(data.get(k), str) or len(data[k]) > 64:
            raise HistoryError(f"{k} must be a short string")
    if data["mode"] != "play":
        raise HistoryError("unknown mode")
    coaching = data.get("coaching")
    if coaching is not None:
        decisions = coaching.get("decisions") if isinstance(coaching, dict) else None
        if not isinstance(decisions, list) or len(decisions) > MAX_DECISIONS:
            raise HistoryError("coaching.decisions must be a list")
        for d in decisions:
            if not isinstance(d, dict) or d.get("hole") != hero_cards:
                raise HistoryError("a coaching decision does not belong to this hand")

    clean = dict(data)
    clean.update(
        id=rid, t=float(t), hand_number=number, hero=hero, hero_cards=hero_cards,
        board=board, opponent_cards=opponent_cards,
    )  # fmt: skip
    frames = replay(clean)
    # Hidden information: every opponent card in the record must have been shown.
    for s, cards in opponent_cards.items():
        if cards is not None and frames[-1]["visible"].get(s) != cards:
            raise HistoryError("opponent cards that were never shown")
    if frames[-1]["board"] != board:
        raise HistoryError("board does not match the events")
    hero_shown = [
        h["cards"]
        for e in events
        if e.get("type") == "showdown"
        for s, h in e["hands"].items()
        if int(s) == hero
    ]
    if any(c != hero_cards for c in hero_shown):
        raise HistoryError("hero cards do not match the showdown")
    decisions = (coaching or {}).get("decisions") or []
    marks = [e["decision"] for e in events if e.get("type") == "action" and "decision" in e]
    if marks and (
        marks != list(range(len(decisions)))
        or any(
            e.get("seat") != hero for e in events if e.get("type") == "action" and "decision" in e
        )
    ):
        raise HistoryError("decision links do not match the coaching")
    return clean


# --------------------------------------------------------------------------- summary


def _position(record: Mapping[str, Any]) -> str:
    start = record["events"][0]
    if len(start["stacks"]) == 2:
        return "button" if start["button"] == record["hero"] else "big blind"
    return "button" if start["button"] == record["hero"] else "other"


def _compact(d: Mapping[str, Any], i: int) -> dict[str, Any]:
    chosen = next((o for o in d.get("options", []) if o.get("key") == d.get("chosen")), None)
    return {
        "i": i,
        "street": d.get("street"),
        "spot": d.get("spot"),
        "action": chosen.get("action") if chosen else None,
        "grade": d.get("grade"),
        "confidence": d.get("confidence"),
        "tags": list(d.get("tags") or []),
        "concept": d.get("concept"),
        "pot": d.get("pot"),
        "to_call": d.get("to_call"),
        "stack": d.get("stack"),
        "equity": d.get("equity"),
        "required": d.get("required_equity"),
        "ev_loss": d.get("ev_loss"),
    }


def summarize(record: Mapping[str, Any], frames: list[dict[str, Any]] | None = None) -> dict:
    """What the history list shows and filters on, derived from a valid record."""
    frames = frames if frames is not None else replay(record)
    start = record["events"][0]
    hero = record["hero"]
    bb = start["big_blind"]
    net = frames[-1]["stacks"][hero] - start["stacks"][hero]
    pots = [e for e in record["events"] if e["type"] == "pot_awarded"]
    pot = sum(e["amount"] for e in pots)
    split = any(len(e["winners"]) > 1 and hero in e["winners"] for e in pots)
    showdown = any(e["type"] == "showdown" for e in record["events"])
    hero_cards = record["hero_cards"]
    cls = hand_class(parse_cards("".join(hero_cards)))
    board = record["board"]
    final = evaluate(parse_cards("".join(hero_cards + board))) if len(board) >= 3 else None
    coaching = record.get("coaching") or {}
    decisions = [_compact(d, i) for i, d in enumerate(coaching.get("decisions") or [])]
    flagged = sum(1 for d in decisions if d["grade"] in ("questionable", "mistake"))
    streets = [f["street"] for f in frames if f["street"] in STREETS]
    return {
        "id": record["id"],
        "t": record["t"],
        "hand_number": record["hand_number"],
        "session": record["session"],
        "difficulty": record.get("difficulty"),
        "position": _position(record),
        "big_blind": bb,
        "hero_cards": hero_cards,
        "opponent_cards": next((c for c in record["opponent_cards"].values() if c), None),
        "board": board,
        "hand_class": cls,
        "hand_percentile": percentile(cls),
        "final_hand": final.description if final else None,
        "final_category": final.category.label if final else None,
        "net": net,
        "net_bb": net / bb,
        "pot": pot,
        "pot_bb": pot / bb,
        "result": "won" if net > 0 else "lost" if net < 0 else ("split" if split else "even"),
        "showdown": showdown,
        "last_street": streets[-1] if streets else "preflop",
        "flagged": flagged,
        "quality": coaching.get("quality"),
        "ev_loss_bb": coaching.get("ev_loss_bb"),
        "tags": sorted({t for d in decisions for t in d["tags"]}),
        "decisions": decisions,
    }
