"""Interactive training scenarios built on the coaching analysis.

A scenario is a heads-up decision point (a :class:`~pokerlab.coach.spot.Spot`) generated
for one *concept* at one *difficulty*, with a short list of choices. The player picks
one; :func:`answer` grades it with the same analysis the post-hand review uses and
compares every alternative.

**Generation.** Cards are dealt at random and a betting line is played through the real
engine. The spot is analysed, and kept only if it teaches the concept at the requested
difficulty: the best choice must beat the alternatives by a clear margin (large for
Beginner, smaller for Advanced), and the spot must actually involve the concept (a draw
for "draws", tendencies that change the answer for "reads", and so on). Otherwise new
cards are dealt.

**Determinism.** A generated scenario's id encodes difficulty, concept and seed, and
generation and analysis are seeded from it. The same id always produces the same spot and
the same verdicts, so answers need no server-side state, and retrying a scenario shows
exactly the same analysis. Practising a decision from a real hand works the same way,
with the spot itself in the id.

There is no opponent hand in any scenario: the analysis works against the estimated
range only, so nothing hidden can leak into the feedback.
"""

from __future__ import annotations

import base64
import json
import random
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from ..ai.player_model import BASELINES, LABELS, PlayerModel, Stat
from ..ai.strength import draw_bonus
from ..cards import FULL_DECK, Card
from ..engine import Action, ActionType, HoldemHand
from ..evaluator import HandCategory, evaluate
from .analysis import DecisionAnalysis, analyze, grade_option, with_chosen
from .spot import Spot, SpotError, villain_reads

DIFFICULTIES = ("beginner", "intermediate", "advanced")
CONCEPTS = (
    "pot_odds",
    "draws",
    "value_betting",
    "bluffing",
    "bet_sizing",
    "preflop",
    "reads",
)
CONCEPT_LABEL = {
    "pot_odds": "Pot odds",
    "draws": "Drawing hands",
    "value_betting": "Value betting",
    "bluffing": "Semi-bluffing",
    "bet_sizing": "Bet sizing",
    "preflop": "Preflop defence",
    "reads": "Opponent tendencies",
}
CONCEPT_BLURB = {
    "pot_odds": "Facing a bet: compare the equity you need with the equity you have.",
    "draws": "Facing a bet with a draw: are the odds good enough to chase it?",
    "value_betting": "Checked to you: does betting earn more than checking?",
    "bluffing": "Checked to you with a draw: is a semi-bluff worth more than a free card?",
    "bet_sizing": "You want to bet: which size earns the most?",
    "preflop": "Facing a preflop raise: fold, call or re-raise?",
    "reads": "Same maths, but the opponent has shown tendencies that change their range.",
}
# Minimum EV gap (share of the pot) between the best choice and the next one.
MARGIN = {"beginner": 0.20, "intermediate": 0.08, "advanced": 0.03}
# Semi-bluffs gain modestly even when clearly right, so Beginner uses a smaller margin.
CONCEPT_MARGIN = {("bluffing", "beginner"): 0.10}


def margin(concept: str, difficulty: str) -> float:
    return CONCEPT_MARGIN.get((concept, difficulty), MARGIN[difficulty])


ITERATIONS = 2500
FILTER_ITERATIONS = 600  # quick screening of candidate spots
MAX_ATTEMPTS = 60
BIG_BLIND = 10
SMALL_BLIND = 5

# Opponent profiles for "reads" scenarios: stat -> (rate, opportunities).
READ_PROFILES: dict[str, dict[str, tuple[float, float]]] = {
    "aggressive": {
        "bet_unopposed": (0.70, 40),
        "raise_vs_bet": (0.22, 30),
        "fold_to_bet": (0.25, 40),
        "pfr": (0.70, 60),
        "vpip": (0.92, 60),
    },
    "passive": {
        "bet_unopposed": (0.12, 40),
        "raise_vs_bet": (0.02, 30),
        "fold_to_bet": (0.22, 40),
        "pfr": (0.18, 60),
        "vpip": (0.90, 60),
    },
    "tight": {
        "bet_unopposed": (0.20, 40),
        "raise_vs_bet": (0.03, 30),
        "fold_to_bet": (0.62, 40),
        "pfr": (0.25, 60),
        "vpip": (0.50, 60),
    },
}
READ_PROFILE_NAME = {
    "aggressive": "a loose, aggressive player",
    "passive": "a calling station",
    "tight": "a tight player who folds a lot",
}


class ScenarioError(ValueError):
    pass


@dataclass(slots=True)
class Scenario:
    id: str
    difficulty: str
    concept: str
    spot: Spot
    choices: list[str]  # option keys offered
    profile: str | None  # reads profile name, if any
    analysis: DecisionAnalysis  # ungraded; never sent before the answer


# --------------------------------------------------------------------------- ids


def _encode_spot(spot: Spot, difficulty: str, concept: str, chosen: str | None = None) -> str:
    raw = json.dumps(
        {"s": spot.to_dict(), "d": difficulty, "c": concept, "k": chosen}, separators=(",", ":")
    ).encode()
    return "spot." + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_spot(sid: str) -> tuple[Spot, str, str, str | None]:
    try:
        payload = sid.split(".", 1)[1]
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        d = json.loads(raw)
        chosen = d.get("k")
        return (
            Spot.from_dict(d["s"]),
            str(d["d"]),
            str(d["c"]),
            None if chosen is None else str(chosen),
        )
    except (IndexError, ValueError, KeyError, TypeError) as exc:
        raise ScenarioError("Unknown scenario") from exc


# --------------------------------------------------------------------------- building


def _apply_plan(
    hand: HoldemHand, plan: list[tuple[str, float | None]]
) -> list[tuple[str, int | None]]:
    """Apply a betting plan; bet sizes are pot fractions. Returns concrete actions."""
    out: list[tuple[str, int | None]] = []
    for kind, frac in plan:
        legal = hand.legal_actions()
        if kind in ("bet", "raise", "raise_to"):
            if not legal.can_raise:
                raise SpotError("cannot raise")
            if kind == "raise_to":  # an absolute raise-to total
                target = int(frac or 0)
            else:  # a fraction of the pot after calling
                target = legal.current_bet + round((frac or 0.5) * (hand.pot + legal.call_amount))
            target = max(legal.min_raise_to, min(legal.max_raise_to, target))
            action = Action(legal.raise_type, target)
            out.append((legal.raise_type.value, target))
        else:
            action = Action(ActionType(kind))
            out.append((kind, None))
        hand.apply(action)
    return out


def _street_plan(
    rng: random.Random, villain_first: bool, passive: bool
) -> list[tuple[str, float | None]]:
    """One completed postflop street: check-check or bet-call (either player betting)."""
    if passive or rng.random() < 0.35:
        return [("check", None), ("check", None)]
    size = rng.choice((0.33, 0.5, 0.66, 0.75))
    if villain_first or rng.random() < 0.5:
        # first player bets, second calls
        return [("bet", size), ("call", None)]
    return [("check", None), ("bet", size), ("call", None)]


def _cards_fit(concept: str, hero: tuple[Card, Card], board: tuple[Card, ...]) -> bool:
    if not board:
        return True
    bonus = draw_bonus(hero, board)
    made = evaluate(list(hero) + list(board)).category
    if concept == "draws":
        return bonus >= 0.14 and made <= HandCategory.ONE_PAIR
    if concept == "pot_odds":
        return bonus < 0.14 and made >= HandCategory.ONE_PAIR
    if concept == "bet_sizing":
        return made >= HandCategory.TWO_PAIR
    if concept == "bluffing":
        return bonus >= 0.14 and made <= HandCategory.ONE_PAIR
    return True


def _make_spot(rng: random.Random, concept: str, difficulty: str, profile: str | None) -> Spot:
    deck = list(FULL_DECK)
    rng.shuffle(deck)
    hero_cards = (deck[0], deck[1])
    button = rng.randrange(2)  # hero is seat 0; button 0 means hero is the button
    hero_button = button == 0
    stack_bb = 100 if difficulty == "beginner" else rng.choice((60, 100, 150))
    stacks = (stack_bb * BIG_BLIND, stack_bb * BIG_BLIND)

    if concept == "preflop":
        target_street = 0
        facing = True
    elif concept in ("pot_odds", "draws"):
        facing = True
        if concept == "draws":
            target_street = rng.choice((3, 4)) if difficulty != "beginner" else 3
        elif difficulty == "beginner":
            target_street = rng.choice((4, 5, 5))
        else:
            target_street = rng.choice((3, 4, 5))
    elif concept in ("value_betting", "bet_sizing"):
        facing = False
        target_street = rng.choice((3, 4, 5)) if difficulty != "beginner" else rng.choice((4, 5))
    elif concept == "bluffing":  # a draw needs cards to come
        facing = False
        target_street = rng.choice((3, 4))
    else:  # reads
        facing = rng.random() < 0.6
        target_street = rng.choice((3, 4, 5))
    board = tuple(deck[2 : 2 + target_street])
    # Cheap pre-filters: re-deal until the cards can teach the concept at all.
    for _ in range(300):
        if _cards_fit(concept, hero_cards, board):
            break
        rng.shuffle(deck)
        hero_cards, board = (deck[0], deck[1]), tuple(deck[2 : 2 + target_street])

    # Preflop line. Seat 0 = hero. The button (small blind) acts first preflop.
    plan: list[tuple[str, float | None]] = []
    if concept == "preflop":
        if hero_button:  # hero opened, villain 3-bets
            open_to = rng.choice((2.5, 3.0)) * BIG_BLIND
            plan = [("raise_to", open_to), ("raise_to", rng.choice((3.0, 3.5)) * open_to)]
        else:  # villain opens, hero defends the big blind
            plan = [("raise_to", rng.choice((2.0, 2.5, 3.0, 4.0)) * BIG_BLIND)]
    else:
        line = rng.random()
        if line < 0.6 or difficulty == "beginner":
            plan = [("raise_to", 2.5 * BIG_BLIND), ("call", None)]
        elif line < 0.8:
            plan = [("call", None), ("check", None)]
        else:
            plan = [("raise_to", 2.5 * BIG_BLIND), ("raise_to", 8.0 * BIG_BLIND), ("call", None)]

    # Postflop streets before the decision street.
    streets_before = {0: 0, 3: 0, 4: 1, 5: 2}[target_street]
    for _ in range(streets_before):
        plan += _street_plan(rng, villain_first=hero_button, passive=difficulty == "beginner")

    # Decision street.
    if target_street:
        hero_first = not hero_button  # the big blind acts first after the flop
        size = rng.choice(
            (0.33, 0.5, 0.66, 0.75, 1.0) if difficulty != "beginner" else (0.5, 0.75, 1.0)
        )
        if facing:
            plan += ([("check", None)] if hero_first else []) + [("bet", size)]
        elif not hero_first:
            plan += [("check", None)]

    spot0 = Spot(
        hero=hero_cards,
        board=board,
        actions=(),
        button=button,
        stacks=stacks,
        small_blind=SMALL_BLIND,
        big_blind=BIG_BLIND,
    )
    villain_cards = [c for c in FULL_DECK if c not in set(hero_cards) | set(board)][-2:]
    hand = HoldemHand(list(stacks), button, SMALL_BLIND, BIG_BLIND, deck=spot0._deck(villain_cards))
    actions = _apply_plan(hand, plan)
    stats: dict[str, tuple[float, float]] = {}
    if profile:
        stats = {k: (round(r * n, 2), float(n)) for k, (r, n) in READ_PROFILES[profile].items()}
    spot = Spot(
        hero=hero_cards,
        board=board,
        actions=tuple(actions),
        button=button,
        stacks=stacks,
        small_blind=SMALL_BLIND,
        big_blind=BIG_BLIND,
        villain_stats=stats,
    )
    spot.replay()  # validates: the hero is to act on the right street
    return spot


def _choices(a: DecisionAnalysis, concept: str, difficulty: str) -> list[str]:
    """The options offered: modelled ones only, fewer for Beginner."""
    keys = [o.key for o in a.options if o.ev is not None]
    raises = [o.key for o in a.options if o.ev is not None and o.action in ("bet", "raise")]
    passive = [k for k in keys if k not in raises]
    if a.street == "preflop":
        return passive  # fold or call: re-raises are judged by a guideline, not an EV
    if difficulty == "beginner" and concept == "bet_sizing":
        sized = sorted((o for o in a.options if o.key in raises), key=lambda o: o.amount or 0)
        return passive + ([sized[0].key, sized[-1].key] if len(sized) >= 2 else [])
    if difficulty == "beginner":
        if a.spot == "facing_bet":
            return passive  # fold or call
        # check, plus the size closest to two-thirds of the pot
        sized = [o for o in a.options if o.key in raises]
        best = min(sized, key=lambda o: abs((o.amount or 0) / max(a.pot, 1) - 0.66), default=None)
        return passive + ([best.key] if best else [])
    if a.spot == "facing_bet":
        # fold, call and one raise (all-in only when it is the only raise)
        return passive + raises[:1]
    return passive + raises


def _restricted(a: DecisionAnalysis, keys: list[str]) -> DecisionAnalysis:
    b = with_chosen(a, keys[0])
    b.options = [o for o in a.options if o.key in keys]
    return b


def _ev(a: DecisionAnalysis, key: str) -> float:
    return a.option(key).ev or 0.0


def _acceptable(
    a: DecisionAnalysis,
    concept: str,
    difficulty: str,
    keys: list[str],
    neutral: DecisionAnalysis | None,
) -> bool:
    if len(keys) < 2:
        return False
    pot = max(a.pot, BIG_BLIND)
    if concept in ("value_betting", "bluffing"):
        # The question is bet or check: the margin is between the best bet and checking.
        bets = [_ev(a, k) for k in keys if a.option(k).action in ("bet", "raise")]
        checks = [_ev(a, k) for k in keys if a.option(k).action == "check"]
        if not bets or not checks:
            return False
        if abs(max(bets) - max(checks)) / pot < margin(concept, difficulty):
            return False
    else:
        evs = sorted((_ev(a, k) for k in keys), reverse=True)
        if (evs[0] - evs[1]) / pot < margin(concept, difficulty):
            return False
    best = max(keys, key=lambda k: _ev(a, k))
    best_opt = a.option(best)
    if concept == "draws":
        return a.concept == "draws"
    if concept == "pot_odds":
        return a.concept == "pot_odds" and a.spot == "facing_bet"
    if concept == "bluffing":
        return a.concept == "bluffing" and a.spot == "unbet" and (a.equity or 0) < 0.5
    if concept == "bet_sizing":
        sizes = [k for k in keys if a.option(k).action in ("bet", "raise")]
        if best_opt.action not in ("bet", "raise") or len(sizes) < 2:
            return False
        bet_evs = sorted((_ev(a, k) for k in sizes), reverse=True)
        return (bet_evs[0] - bet_evs[1]) / pot >= MARGIN[difficulty] / 2
    if concept == "reads":
        if neutral is None:
            return False
        nkeys = [k for k in keys if k in {o.key for o in neutral.options}]
        if not nkeys:
            return False
        neutral_best = max(nkeys, key=lambda k: _ev(neutral, k))
        return neutral_best != best  # the tendencies change the answer
    return True


def generate(difficulty: str, concept: str, seed: int) -> Scenario:
    if difficulty not in DIFFICULTIES:
        raise ScenarioError(f"Unknown difficulty {difficulty!r}")
    if concept not in CONCEPTS:
        raise ScenarioError(f"Unknown concept {concept!r}")
    return _generate(difficulty, concept, seed)


@lru_cache(maxsize=256)
def _generate(difficulty: str, concept: str, seed: int) -> Scenario:
    rng = random.Random(f"{difficulty}:{concept}:{seed}")
    for _ in range(MAX_ATTEMPTS):
        profile = rng.choice(sorted(READ_PROFILES)) if concept == "reads" else None
        try:
            spot = _make_spot(rng, concept, difficulty, profile)
        except SpotError:
            continue
        # A cheap, low-precision pass first; the full analysis must then agree.
        if not _fits(spot, concept, difficulty, FILTER_ITERATIONS)[0]:
            continue
        ok, a, keys = _fits(spot, concept, difficulty, ITERATIONS)
        if ok:
            sid = f"v1.{difficulty}.{concept}.{seed}"
            return Scenario(sid, difficulty, concept, spot, keys, profile, a)
    raise ScenarioError("Could not generate a scenario; try another seed")


def _fits(
    spot: Spot, concept: str, difficulty: str, iterations: int
) -> tuple[bool, DecisionAnalysis, list[str]]:
    a = _analyse(spot, iterations)
    keys = _choices(a, concept, difficulty)
    neutral = None
    if concept == "reads":
        neutral = _analyse(Spot(**{**_fields(spot), "villain_stats": {}}), iterations)
    return _acceptable(a, concept, difficulty, keys, neutral), a, keys


def _fields(spot: Spot) -> dict[str, Any]:
    return {f: getattr(spot, f) for f in spot.__dataclass_fields__}


def _analyse(spot: Spot, iterations: int = ITERATIONS) -> DecisionAnalysis:
    _, obs, legal = spot.replay()
    reads = villain_reads(spot.villain_model()) if spot.villain_stats else []
    return analyze(
        obs,
        legal,
        adjust=spot.villain_adjustments(),
        reads=reads,
        seed=spot.seed(),
        iterations=iterations,
    )


def _parse_choice(key: str) -> Action | None:
    """The table action behind an option key ("fold", "raise-120", "all-in", ...)."""
    if key in ("fold", "check", "call"):
        return Action(ActionType(key))
    if key == "all-in":
        return Action.all_in()
    kind, _, amount = key.partition("-")
    if kind in ("bet", "raise") and amount.isdigit():
        return Action(ActionType(kind), int(amount))
    return None


def from_spot(spot: Spot, difficulty: str = "intermediate", chosen: str | None = None) -> Scenario:
    """A practice scenario from a decision in a real hand (any spot the review produced).

    It is analysed exactly as the post-hand review analysed it (same seed, the same
    number of samples, and the size the player chose among the options), so practising
    the decision gives the same verdict the review gave.
    """
    action = _parse_choice(chosen) if chosen else None
    _, obs, legal = spot.replay()
    reads = villain_reads(spot.villain_model()) if spot.villain_stats else []
    a = analyze(obs, legal, action, adjust=spot.villain_adjustments(), reads=reads,
                seed=spot.seed())  # fmt: skip
    keys = [o.key for o in a.options if a.spot == "unopened" or o.ev is not None]
    if len(keys) < 2:
        raise ScenarioError("This decision has nothing to compare")
    sid = _encode_spot(spot, difficulty, a.concept, chosen)
    return Scenario(sid, difficulty, a.concept, spot, keys, None, a)


def load(sid: str) -> Scenario:
    """Rebuild a scenario from its id."""
    if sid.startswith("spot."):
        return _from_spot_cached(sid)
    parts = sid.split(".")
    if len(parts) != 4 or parts[0] != "v1":
        raise ScenarioError("Unknown scenario")
    try:
        seed = int(parts[3])
    except ValueError as exc:
        raise ScenarioError("Unknown scenario") from exc
    return generate(parts[1], parts[2], seed)


@lru_cache(maxsize=128)
def _from_spot_cached(sid: str) -> Scenario:
    spot, difficulty, _, chosen = _decode_spot(sid)
    return from_spot(spot, difficulty, chosen)


# --------------------------------------------------------------------------- views


def _profile_text(profile: str | None, spot: Spot) -> dict[str, Any] | None:
    if not spot.villain_stats:
        return None
    model = PlayerModel()
    for k, (c, o) in spot.villain_stats.items():
        model.stats[k] = Stat(count=c, opportunities=o)
    stats = [
        {
            "key": k,
            "label": LABELS[k],
            "rate": c / o if o else None,
            "opportunities": round(o),
            "baseline": BASELINES[k],
        }
        for k, (c, o) in spot.villain_stats.items()
    ]
    return {
        "name": READ_PROFILE_NAME.get(profile or "", "the opponent"),
        "stats": stats,
    }


def scenario_view(s: Scenario) -> dict[str, Any]:
    """What the player sees before answering: no EVs, no verdicts."""
    a = s.analysis
    _, obs, _ = s.spot.replay()
    labels = {o.key: o.label for o in a.options}
    view = {
        "id": s.id,
        "difficulty": s.difficulty,
        "concept": s.concept,
        "concept_label": CONCEPT_LABEL.get(s.concept, s.concept),
        "concept_blurb": (
            "First to act preflop: fold, limp or raise?"
            if a.spot == "unopened"
            else CONCEPT_BLURB.get(s.concept, "")
        ),
        "hero": a.hole,
        "board": a.board,
        "street": a.street,
        "position": a.position,
        "pot": a.pot,
        "to_call": a.to_call,
        "stack": obs.stack,
        "opponent_stack": obs.opponent_stacks[0] if obs.opponent_stacks else 0,
        "big_blind": s.spot.big_blind,
        "history": a.history,
        "hand": a.hand,
        "choices": [{"key": k, "label": labels[k]} for k in s.choices],
        "opponent": _profile_text(s.profile, s.spot),
        "hints": _hints(s),
        "spot_data": s.spot.to_dict(),
    }
    return view


def _hints(s: Scenario) -> list[str]:
    """Pre-answer hints. Beginner gets the formula; nobody gets the answer."""
    a = s.analysis
    if s.difficulty != "beginner":
        return []
    if a.spot == "facing_bet":
        need = a.to_call / (a.pot + a.to_call)
        return [
            f"Pot odds: you must call {a.to_call} to win a pot of {a.pot + a.to_call}, so "
            f"you need to win at least {a.to_call} ÷ {a.pot + a.to_call} = {need:.0%} of the time.",
            "Ask: against the hands that bet like this, do I win more often than that?",
        ]
    if a.spot == "unbet":
        return [
            "Betting is good when worse hands call or better hands fold. Checking is good "
            "when only better hands would call.",
        ]
    return []


def answer(sid: str, choice: str) -> dict[str, Any]:
    s = load(sid)
    if choice not in s.choices:
        raise ScenarioError("That choice is not available in this scenario")
    a = _restricted(s.analysis, s.choices)
    graded = grade_option(a, choice)
    alternatives = []
    for k in s.choices:
        alt = with_chosen(a, k)
        o = a.option(k)
        alternatives.append(
            {
                "key": k,
                "label": o.label,
                "ev": o.ev,
                "ev_bb": None if o.ev is None else o.ev / s.spot.big_blind,
                "basis": o.basis,
                "grade": alt.grade,
                "grade_label": alt.headline,
                "explanation": alt.explanation,
                "fold_rate": o.fold_rate,
                "equity_when_called": o.equity_when_called,
                "detail": o.detail,
            }
        )
    correct = graded.grade == "good"
    return {
        "id": s.id,
        "concept": s.concept,
        "choice": choice,
        "correct": correct,
        "acceptable": graded.grade in ("good", "close"),
        "analysis": graded.to_dict(),
        "alternatives": alternatives,
    }
