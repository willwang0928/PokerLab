"""Evaluate one betting decision from the information available at the time.

**Inputs.** An :class:`~pokerlab.ai.base.Observation` for the deciding seat (its own hole
cards, the board, the public actions), the legal options, and optionally
:class:`~pokerlab.ai.adaptation.Adjustments` describing the opponent's observed
tendencies. The opponent's hole cards are not an input, so the analysis cannot use them.

**Method.**

1. *Opponent range.* The opponent's plausible hands are estimated from their public
   actions with the same model the PokerLab opponents use
   (:func:`pokerlab.ai.range_model.estimate_range`). Observed tendencies, when there are
   enough of them, widen or narrow it.
2. *Equity.* The player's all-in equity against that range: exact enumeration when the
   deal space is small, otherwise seeded Monte Carlo with a reported standard error.
3. *Expected value of each option*, in chips, measured from the moment of the decision
   (folding = 0)::

       call      equity * (pot + call) - call
       check     equity * pot                         (the hand is checked down)
       bet/raise fold * pot + defend * (equity_vs_defenders * final_pot - chips_added)

   For bets and raises the opponent is assumed to continue with the strongest part of
   their range at the minimum-defence frequency ``pot / (pot + chips_added)``, shifted
   by their observed fold-to-bet tendency when known. At that frequency a pure bluff
   breaks even, so a bet earns money only through equity when called or through extra
   folds the opponent's tendencies justify.

**What these numbers are not.** They are one-street estimates: later betting (implied
odds, being bluffed off a hand, re-raises) is ignored, and they depend on the range
model. They are not solver output and never claim to be optimal. The grade therefore
compares options by *estimated* EV, reports a confidence level, and only calls a
decision a likely mistake when it rests on pot odds and equity alone (call or fold,
check-down EV), not on an assumed fold frequency.

Unopened preflop pots (the small blind's first action, the big blind after a limp)
have no betting to estimate a range from. They are graded against a simple heads-up
opening guideline instead, and labelled as such.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field, fields, replace
from typing import TYPE_CHECKING, Any

from ..ai.base import Observation, raise_to_pot_fraction
from ..ai.preflop import hand_class, percentile
from ..ai.range_model import (
    MIN_COMBOS,
    POSTFLOP_KEEP,
    defending_ranges,
    estimate_range,
    preflop_fraction,
)
from ..ai.strength import draw_bonus
from ..cards import Card
from ..engine import Action, ActionType, LegalActions, Street
from ..equity import calculate_equity
from ..evaluator import HandCategory, evaluate
from ..ranges import HandRange

if TYPE_CHECKING:
    from ..ai.adaptation import Adjustments

GRADES = ("good", "close", "questionable", "mistake", "ungraded")
GRADE_LABEL = {
    "good": "Good",
    "close": "Close call",
    "questionable": "Questionable",
    "mistake": "Likely mistake",
    "ungraded": "Not graded",
}
# EV given up, as a share of the pot, at which each grade starts.
CLOSE_AT = 0.03
QUESTIONABLE_AT = 0.10
MISTAKE_AT = 0.25
LOW_CONFIDENCE_MISTAKE_AT = 0.50  # preflop: the model is cruder, so demand more
# Bets adding more than this many times the pot (after calling) are not modelled: how
# often an opponent calls a huge overbet cannot be read off a fold-frequency rule.
OVERBET_LIMIT = 1.5

# Heads-up opening guideline for unopened preflop pots (hand-ranking percentile, 0 = AA).
# The small blind should play most hands heads-up; the PokerLab opponents open the top
# 70% and limp to 85%.
SB_OPEN = 0.70
SB_PLAYABLE = 0.85
SB_FOLD_QUESTIONABLE = 0.50  # folding a top-half hand gives up the blind too often
BB_RAISE_LIMP = 0.30

ANALYSIS_ITERATIONS = 4000
CANDIDATE_SIZES = {"unbet": (0.33, 0.66, 1.0), "facing": (0.75,)}

ANY_HAND = HandRange.parse("any")


@dataclass(frozen=True, slots=True)
class Option:
    """One choice the player had, with its estimated value."""

    key: str  # stable id, e.g. "fold", "call", "bet-66", "raise-120", "all-in"
    action: str  # fold / check / call / bet / raise
    amount: int | None  # raise-to total for bets and raises
    label: str
    ev: float | None  # chips, from the moment of the decision; None for guideline spots
    basis: str  # "pot odds" | "fold model" | "guideline"
    equity_when_called: float | None = None
    fold_rate: float | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DecisionAnalysis:
    street: str
    hole: list[str]
    board: list[str]
    hand: str  # "AKs" preflop, the made hand afterwards
    position: str  # "button" / "big blind"
    pot: int
    to_call: int
    stack: int
    big_blind: int
    history: list[str]  # the betting so far, as short lines
    spot: str  # "facing_bet" / "unbet" / "unopened"
    concept: str
    equity: float | None
    equity_se: float | None
    equity_method: str | None
    required_equity: float | None  # pot odds, when facing a bet
    range_size: int | None
    range_steps: list[str]
    reads: list[str]
    options: list[Option]
    hand_percentile: float | None = None  # preflop hand ranking (0 = AA), unopened pots
    best: list[str] = field(default_factory=list)  # keys of options within noise of the top
    chosen: str | None = None
    grade: str = "ungraded"
    confidence: str = "low"  # how much the grade can be trusted: low / medium / high
    ev_loss: float = 0.0  # chips below the best option (estimated)
    tags: list[str] = field(default_factory=list)
    headline: str = ""
    explanation: str = ""
    details: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)

    def option(self, key: str) -> Option:
        return next(o for o in self.options if o.key == key)

    def to_dict(self) -> dict[str, Any]:
        d = {f.name: getattr(self, f.name) for f in fields(self)}
        d["options"] = [o.to_dict() for o in self.options]
        d["grade_label"] = GRADE_LABEL[self.grade]
        return d


# --------------------------------------------------------------------------- helpers


def _bb(chips: float, big_blind: int) -> str:
    v = chips / big_blind
    return f"{v:+.1f} bb"


def _pct(x: float) -> str:
    return f"{x:.0%}" if 0.005 <= x <= 0.995 or x in (0.0, 1.0) else f"{x:.1%}"


def _hand_name(hole: tuple[Card, ...], board: tuple[Card, ...]) -> str:
    if len(board) < 3:
        return hand_class(hole)
    name = evaluate(list(hole) + list(board)).description
    if len(board) < 5:
        bonus = draw_bonus(hole, board)
        if bonus >= 0.18:
            name += " with a flush draw"
        elif bonus >= 0.14:
            name += " with an open-ended straight draw"
    return name


def _villain(obs: Observation) -> int:
    if obs.opponents_in_hand:
        return obs.opponents_in_hand[0]
    return next(s for s in range(obs.num_players) if s != obs.seat)


def history_lines(obs: Observation, names: dict[int, str] | None = None) -> list[str]:
    names = names or {}
    out = []
    for a in obs.actions:
        who = names.get(a.seat, "You" if a.seat == obs.seat else "Opponent")
        verb = {
            "fold": "folds",
            "check": "checks",
            "call": f"calls {a.amount}",
            "bet": f"bets {a.amount}",
            "raise": f"raises to {a.amount}",
        }[a.action]
        if who == "You":
            verb = verb.replace("folds", "fold").replace("checks", "check")
            verb = verb.replace("calls", "call").replace("bets", "bet").replace("raises", "raise")
        out.append(f"{a.street.value.title()}: {who} {verb}" + (" (all-in)" if a.all_in else ""))
    return out


def describe_range(obs: Observation, villain: int, adjust: Adjustments | None) -> list[str]:
    """Plain-language steps of how the opponent's range was estimated."""
    raise_scale = adjust.raise_range_scale if adjust else 1.0
    call_scale = adjust.vpip_range_scale if adjust else 1.0
    frac = preflop_fraction(
        obs.actions, villain, obs.button, obs.big_blind, raise_scale, call_scale
    )
    pre = [a.action for a in obs.actions if a.seat == villain and a.street == Street.PREFLOP]
    if frac >= 0.999:
        steps = ["Preflop: no raise or call yet, so any two cards"]
    else:
        last = pre[-1] if pre else "action"
        steps = [f"Preflop: the top {frac:.0%} of hands (from their {last})"]
    scale = {
        "bet": adjust.bet_range_scale if adjust else 1.0,
        "raise": adjust.raise_range_post_scale if adjust else 1.0,
        "call": adjust.call_range_scale if adjust else 1.0,
    }
    for a in obs.actions:
        if a.seat != villain or a.street == Street.PREFLOP or a.action not in POSTFLOP_KEEP:
            continue
        keep = min(1.0, max(0.15, POSTFLOP_KEEP[a.action] * scale[a.action]))
        verb = {"bet": "bet", "raise": "raise", "call": "call"}[a.action]
        steps.append(
            f"{a.street.value.title()} {verb}: keeps their strongest {keep:.0%}, plus strong draws"
        )
    return steps


def _equity(
    hole: tuple[Card, ...], rng: HandRange, board: tuple[Card, ...], seed: int, iterations: int
) -> tuple[float, float, str]:
    r = calculate_equity([hole, rng], board=board, iterations=iterations, seed=seed)
    p = r.players[0]
    return p.equity, p.std_error, r.method


def _bet_label(kind: str, amount: int, pot: int, all_in: bool) -> str:
    if all_in:
        return f"All-in ({amount})"
    if kind == "bet":
        return f"Bet {amount} ({amount / pot:.0%} pot)" if pot else f"Bet {amount}"
    return f"Raise to {amount}"


def _candidate_raises(
    obs: Observation, legal: LegalActions, chosen: Action | None, spot: str
) -> list[int]:
    """Raise-to totals to evaluate: a few standard sizes, all-in, and the chosen size."""
    if not legal.can_raise:
        return []
    totals: list[int] = []
    for f in CANDIDATE_SIZES["unbet" if spot == "unbet" else "facing"]:
        a = raise_to_pot_fraction(obs, legal, f)
        if a.amount is not None and a.amount not in totals:
            totals.append(a.amount)
    if legal.max_raise_to not in totals and _modelled(legal.max_raise_to, obs, legal):
        totals.append(legal.max_raise_to)
    if chosen is not None:
        amt = _chosen_total(chosen, legal)
        if amt is not None and amt not in totals:
            totals.append(amt)
    return sorted(totals)


def _modelled(total: int, obs: Observation, legal: LegalActions) -> bool:
    """Whether a bet or raise is small enough for the fold model to say anything useful."""
    return total - legal.player_bet <= OVERBET_LIMIT * (obs.pot + legal.call_amount)


def _chosen_total(chosen: Action, legal: LegalActions) -> int | None:
    if chosen.type == ActionType.ALL_IN:
        if legal.can_raise:
            return legal.max_raise_to
        return None  # an all-in call
    if chosen.type in (ActionType.BET, ActionType.RAISE):
        return chosen.amount
    return None


def chosen_key(chosen: Action, legal: LegalActions) -> str:
    """Option key for an action taken at the table."""
    t = chosen.type
    if t == ActionType.ALL_IN:
        if legal.can_raise:
            return _raise_key(legal.max_raise_to, legal)
        return "call" if legal.can_call else "check"
    if t in (ActionType.BET, ActionType.RAISE):
        assert chosen.amount is not None
        return _raise_key(chosen.amount, legal)
    if t == ActionType.CALL and not legal.can_call:
        return "check"
    return t.value


def _raise_key(total: int, legal: LegalActions) -> str:
    if total >= legal.max_raise_to:
        return "all-in"
    return f"{legal.raise_type.value}-{total}"


def fold_rate(bet: int, pot: int, adjust: Adjustments | None = None) -> float:
    """Assumed chance the opponent folds to a bet of ``bet`` chips into ``pot``.

    The minimum-defence fold rate ``bet / (pot + bet)``, shifted by an observed fold-to-bet
    tendency. Bets larger than the pot are treated like a pot-sized bet: minimum defence
    would have the opponent fold ever more often to ever bigger bets, which makes huge
    overbets look free. Real opponents call big bets with their strong hands, so the
    estimate never assumes more folds than against a pot-sized bet.
    """
    capped = min(bet, pot)
    if adjust is not None:
        return adjust.fold_probability(capped, pot)
    return capped / (pot + capped) if pot + capped else 0.0


def _preflop_defenders(rng: HandRange, defend: float) -> HandRange:
    """The strongest ``defend`` share of a preflop range, by the starting-hand ranking."""
    combos = sorted(rng, key=lambda h: percentile(hand_class(h)))
    n = max(MIN_COMBOS, round(defend * len(combos)))
    return HandRange.of(combos[:n], "defending")


# --------------------------------------------------------------------------- analysis


def analyze(
    obs: Observation,
    legal: LegalActions,
    chosen: Action | None = None,
    *,
    adjust: Adjustments | None = None,
    reads: list[str] | None = None,
    seed: int | None = None,
    iterations: int = ANALYSIS_ITERATIONS,
    names: dict[int, str] | None = None,
) -> DecisionAnalysis:
    """Analyse a decision. With ``chosen`` it is also graded (see :func:`grade_option`)."""
    rng = random.Random(seed)
    villain = _villain(obs)
    preflop = obs.street == Street.PREFLOP
    unopened = preflop and legal.current_bet <= obs.big_blind
    spot = "unopened" if unopened else ("facing_bet" if legal.can_call else "unbet")
    drawing = not preflop and obs.street != Street.RIVER and draw_bonus(obs.hole, obs.board) >= 0.14
    weak = (
        not preflop and evaluate(list(obs.hole) + list(obs.board)).category <= HandCategory.ONE_PAIR
    )
    if preflop:
        concept = "preflop"
    elif spot == "facing_bet":
        concept = "draws" if drawing else "pot_odds"
    else:  # checked to: betting a draw with a weak made hand is a semi-bluff
        concept = "bluffing" if drawing and weak else "value_betting"
    base = DecisionAnalysis(
        street=obs.street.value,
        hole=[c.code for c in obs.hole],
        board=[c.code for c in obs.board],
        hand=_hand_name(obs.hole, obs.board),
        position="button" if obs.is_button else "big blind",
        pot=obs.pot,
        to_call=legal.call_amount,
        stack=obs.stack,
        big_blind=obs.big_blind,
        history=history_lines(obs, names),
        spot=spot,
        concept=concept,
        equity=None,
        equity_se=None,
        equity_method=None,
        required_equity=None,
        range_size=None,
        range_steps=[],
        reads=list(reads or []),
        options=[],
    )
    if unopened:
        _guideline_options(base, obs, legal)
    else:
        _ev_options(base, obs, legal, chosen, adjust, rng, iterations, villain, spot)
    if chosen is not None:
        grade_option(base, chosen_key(chosen, legal))
    return base


def _guideline_options(a: DecisionAnalysis, obs: Observation, legal: LegalActions) -> None:
    p = percentile(hand_class(obs.hole))
    bb = obs.big_blind
    a.confidence = "low"
    a.caveats.append(
        "Nobody has bet yet, so there is no range to calculate against. This is graded "
        "against a simple heads-up guideline, not a computed EV."
    )
    a.details.append(
        f"{hand_class(obs.hole)} ranks above {1 - p:.0%} of starting hands "
        "(by equity against a random hand)."
    )
    opts: list[Option] = []
    if legal.can_check:  # big blind after a limp
        opts.append(Option("check", "check", None, "Check", None, "guideline"))
        if legal.can_raise:
            total = max(legal.min_raise_to, min(legal.max_raise_to, 4 * bb))
            key = _raise_key(total, legal)
            opts.append(
                Option(
                    key,
                    "raise",
                    total,
                    _bet_label("raise", total, obs.pot, key == "all-in"),
                    None,
                    "guideline",
                )
            )
        a.details.append(
            f"Guideline: after a limp, raise roughly the top {BB_RAISE_LIMP:.0%} of hands for "
            "value and check the rest (checking is free)."
        )
    else:  # small blind, first to act
        opts.append(Option("fold", "fold", None, "Fold", None, "guideline"))
        opts.append(
            Option("call", "call", None, f"Call {legal.call_amount} (limp)", None, "guideline")
        )
        if legal.can_raise:
            total = max(legal.min_raise_to, min(legal.max_raise_to, int(2.5 * bb)))
            key = _raise_key(total, legal)
            opts.append(
                Option(
                    key,
                    "raise",
                    total,
                    _bet_label("raise", total, obs.pot, key == "all-in"),
                    None,
                    "guideline",
                )
            )
        a.details.append(
            f"Guideline: heads-up, the small blind can open-raise about the top "
            f"{SB_OPEN:.0%} of hands, play up to {SB_PLAYABLE:.0%}, and fold the rest. "
            "You already have half a big blind in, and you have position after the flop."
        )
    a.options = opts
    a.hand_percentile = p


def _ev_options(
    a: DecisionAnalysis,
    obs: Observation,
    legal: LegalActions,
    chosen: Action | None,
    adjust: Adjustments | None,
    rng: random.Random,
    iterations: int,
    villain: int,
    spot: str,
) -> None:
    villain_range = estimate_range(
        obs.actions, villain, obs.button, obs.big_blind, obs.board, known=obs.hole, adjust=adjust
    )
    if len(villain_range) == 0:
        villain_range = ANY_HAND.without(set(obs.hole) | set(obs.board))
    a.range_size = len(villain_range)
    a.range_steps = describe_range(obs, villain, adjust)
    eq, se, method = _equity(obs.hole, villain_range, obs.board, rng.randrange(2**63), iterations)
    a.equity, a.equity_se, a.equity_method = eq, se, method
    if a.concept == "bluffing" and eq >= 0.5:
        a.concept = "value_betting"  # ahead already: betting is for value, not a semi-bluff
    pot, call = obs.pot, legal.call_amount
    bb = obs.big_blind
    opts: list[Option] = []

    if spot == "facing_bet":
        need = call / (pot + call)
        a.required_equity = need
        opts.append(Option("fold", "fold", None, "Fold", 0.0, "pot odds"))
        ev_call = eq * (pot + call) - call
        opts.append(
            Option(
                "call",
                "call",
                None,
                f"Call {call}" + (" (all-in)" if call >= obs.stack else ""),
                ev_call,
                "pot odds",
                detail=f"{eq:.3f} × {pot + call} − {call} = {ev_call:+.1f} chips",
            )
        )
        a.details.append(
            f"Pot odds: call ÷ (pot + call) = {call} ÷ ({pot} + {call}) = {need:.1%} equity needed."
        )
    else:
        ev_check = eq * pot
        opts.append(
            Option(
                "check",
                "check",
                None,
                "Check",
                ev_check,
                "pot odds",
                detail=f"{eq:.3f} × {pot} = {ev_check:+.1f} chips (if checked down)",
            )
        )

    totals = _candidate_raises(obs, legal, chosen, spot)
    if totals and obs.street == Street.PREFLOP:
        # How often a preflop raiser folds to a re-raise cannot be read off the fold
        # model (it would recommend re-raising junk as a bluff), so a preflop re-raise
        # is judged by a guideline on equity instead.
        total = _chosen_total(chosen, legal) if chosen is not None else None
        total = total or raise_to_pot_fraction(obs, legal, 0.75).amount or legal.min_raise_to
        key = _raise_key(total, legal)
        opts.append(
            Option(
                key,
                legal.raise_type.value,
                total,
                _bet_label(legal.raise_type.value, total, pot, key == "all-in"),
                None,
                "guideline",
                detail="preflop re-raise: judged by a guideline, not an EV",
            )
        )
        totals = []
    if totals:
        kind = legal.raise_type.value
        villain_bet = legal.current_bet
        villain_stack = obs.opponent_stacks[0] if obs.opponent_stacks else 0
        bets = [t - legal.player_bet for t in totals]
        folds = [fold_rate(b, pot, adjust) for b in bets]
        if obs.board:
            defenders = defending_ranges(villain_range, obs.board, [1 - f for f in folds])
        else:
            defenders = [_preflop_defenders(villain_range, 1 - f) for f in folds]
        for total, b, fold, drange in zip(totals, bets, folds, defenders, strict=True):
            key = _raise_key(total, legal)
            if not _modelled(total, obs, legal):
                opts.append(
                    Option(
                        key,
                        kind,
                        total,
                        _bet_label(kind, total, pot, key == "all-in"),
                        None,
                        "not modelled",
                        detail="bigger than 1.5× the pot: not modelled",
                    )
                )
                continue
            v_call = min(total - villain_bet, villain_stack)
            final_pot = pot + b + v_call
            eq_d, _, _ = _equity(obs.hole, drange, obs.board, rng.randrange(2**63), iterations)
            defend = 1 - fold
            ev = fold * pot + defend * (eq_d * final_pot - b)
            opts.append(
                Option(
                    key,
                    kind,
                    total,
                    _bet_label(kind, total, pot, key == "all-in"),
                    ev,
                    "fold model",
                    equity_when_called=eq_d,
                    fold_rate=fold,
                    detail=(
                        f"fold {fold:.0%} × {pot} + continue {defend:.0%} × "
                        f"({eq_d:.3f} × {final_pot} − {b}) = {ev:+.1f} chips"
                    ),
                )
            )

    a.options = opts
    hand = a.hand
    src = "exact" if method == "exact" else f"Monte Carlo, ±{1.96 * se:.1%}"
    a.details.insert(
        0,
        f"Your {hand} has {eq:.1%} equity against the estimated range "
        f"({len(villain_range)} combos, {src}).",
    )
    for o in opts:
        if o.ev is not None:
            a.details.append(
                f"EV({o.label}) = {o.detail or '0 (nothing more invested)'} = {_bb(o.ev, bb)}"
            )
    if obs.street == Street.PREFLOP:
        a.caveats.append(
            "Preflop estimates ignore the later streets, where position and playability "
            "matter a lot. Treat the grade as a rough guide."
        )
    elif obs.street != Street.RIVER:
        a.caveats.append(
            "These are one-street estimates: they ignore betting on later streets "
            "(implied odds, or being bet off your hand)."
        )
    if any(o.basis == "fold model" for o in opts):
        a.caveats.append(
            "Bet and raise values assume the opponent continues with the strongest part of "
            "their range at the minimum-defence frequency"
            + (", adjusted by their observed fold rate." if adjust and adjust.fold_shift else ".")
        )


# --------------------------------------------------------------------------- grading


def _noise(a: DecisionAnalysis) -> float:
    """EV differences below this many chips are within the estimate's own noise."""
    se = a.equity_se or 0.0
    return max(CLOSE_AT * a.pot, 2 * se * (a.pot + a.to_call))


def grade_option(a: DecisionAnalysis, key: str) -> DecisionAnalysis:
    """Grade choosing option ``key``: fills grade, confidence, tags and the explanations.

    Re-grading the same analysis with another key is allowed (training mode compares
    alternatives this way).
    """
    a.chosen = key
    a.tags = []
    if a.spot == "unopened":
        _grade_guideline(a, key)
    else:
        _grade_ev(a, key)
    return a


def _grade_guideline(a: DecisionAnalysis, key: str) -> None:
    p = a.hand_percentile if a.hand_percentile is not None else 0.5
    hand = a.hand
    raising = key not in ("fold", "call", "check")
    a.confidence = "low"
    a.ev_loss = 0.0
    if "check" in (o.key for o in a.options):  # big blind after a limp
        a.best = [o.key for o in a.options if (o.key != "check") == (p < BB_RAISE_LIMP)]
        if raising and p >= 0.6:
            grade, why = (
                "close",
                f"{hand} is not strong enough to raise for value often; checking is free.",
            )
        elif not raising and p < 0.10:
            grade, why = "close", f"{hand} is strong: raising builds a pot you are likely to win."
        else:
            grade, why = (
                "good",
                (
                    f"Raising {hand} for value fits the guideline."
                    if raising
                    else f"Checking {hand} sees a free flop."
                ),
            )
    else:
        if p < SB_OPEN:
            a.best = [o.key for o in a.options if o.action == "raise"]
        elif p < SB_PLAYABLE:
            a.best = [o.key for o in a.options if o.key != "fold"]
        else:
            a.best = ["fold"]
        if key == "fold":
            if p < SB_FOLD_QUESTIONABLE:
                grade = "questionable"
                why = (
                    f"{hand} is in the top half of hands. Folding it from the small blind "
                    "gives up the blind too often heads-up."
                )
                a.tags.append("preflop_tight")
            elif p < SB_PLAYABLE:
                grade, why = "close", f"{hand} is usually playable from the small blind heads-up."
            else:
                grade, why = (
                    "good",
                    f"{hand} is near the bottom of the ranking; folding it is fine.",
                )
        elif key == "call":
            if p < 0.15:
                grade, why = "close", f"{hand} is strong enough to raise and build the pot."
            elif p < SB_PLAYABLE:
                grade, why = "good", f"Limping {hand} keeps the pot small; raising is also fine."
            else:
                grade, why = (
                    "close",
                    f"{hand} is weak; folding or raising as a steal are both reasonable.",
                )
        else:
            if p < SB_OPEN:
                grade, why = "good", f"Raising {hand} fits the guideline."
            elif p < SB_PLAYABLE:
                grade, why = "good", f"{hand} is a loose but reasonable raise heads-up."
            else:
                grade, why = (
                    "close",
                    f"{hand} is weak; raising it often relies on the opponent folding.",
                )
    a.grade = grade
    a.headline = GRADE_LABEL[grade] + " (guideline)"
    a.explanation = why


def _grade_ev(a: DecisionAnalysis, key: str) -> None:
    by_key = {o.key: o for o in a.options}
    chosen = by_key.get(key)
    if chosen is not None and chosen.basis == "guideline":
        _grade_reraise(a, chosen)
        return
    if chosen is None or chosen.ev is None:
        a.grade, a.confidence = "ungraded", "low"
        a.headline = GRADE_LABEL["ungraded"]
        a.explanation = (
            "A bet this large compared with the pot depends almost entirely on how often the "
            "opponent folds, which PokerLab cannot estimate reliably, so it is not graded. "
            "The other options are compared below."
            if chosen is not None and chosen.basis == "not modelled"
            else "This action could not be evaluated."
        )
        valued = [o for o in a.options if o.ev is not None]
        if valued:
            top_ev = max(o.ev or 0.0 for o in valued)
            a.best = [o.key for o in valued if top_ev - (o.ev or 0.0) <= _noise(a)]
        return
    valued = [o for o in a.options if o.ev is not None]
    best_ev = max(o.ev or 0.0 for o in valued)
    noise = _noise(a)
    # Options whose value rests on pot odds alone (fold, call, check) are preferred on
    # ties: a bet that only "breaks even" under an assumed fold rate is not shown as
    # equally good as a fold or check that is certain.
    best_po = max((o.ev or 0.0 for o in valued if o.basis == "pot odds"), default=None)
    a.best = [
        o.key
        for o in valued
        if best_ev - (o.ev or 0.0) <= noise
        and (o.basis == "pot odds" or best_po is None or (o.ev or 0.0) > best_po + noise)
    ]
    loss = max(0.0, best_ev - chosen.ev)
    a.ev_loss = loss
    pot = max(a.pot, a.big_blind)
    frac = loss / pot
    top = next(o for o in valued if o.key == a.best[0]) if a.best else valued[0]
    top = max((o for o in valued if o.key in a.best), key=lambda o: o.ev or 0.0, default=top)

    if loss <= noise:
        grade = "good"
    elif frac <= QUESTIONABLE_AT:
        grade = "close"
    elif frac <= MISTAKE_AT:
        grade = "questionable"
    else:
        grade = "mistake"
    # The pot-odds part of the comparison on its own (fold vs call, or check-down value).
    po_loss = 0.0 if best_po is None or chosen.basis != "pot odds" else best_po - (chosen.ev or 0)

    # How much the comparison can be trusted.
    uses_fold_model = chosen.basis == "fold model" or top.basis == "fold model"
    river_or_all_in = a.street == "river" or a.to_call >= a.stack
    if a.street == "preflop":
        confidence = "low"
    elif uses_fold_model:
        confidence = "low" if a.street != "river" else "medium"
    else:
        confidence = "high" if river_or_all_in else "medium"
    # Only pot-odds comparisons can be called likely mistakes; assumptions about fold
    # frequencies are too uncertain for that.
    if grade == "mistake" and uses_fold_model and po_loss / pot <= MISTAKE_AT:
        grade = "questionable"
    # A gap that exists only under an assumed fold rate needs twice the margin before it
    # counts as questionable (checking a decent hand is often fine in practice).
    if grade == "questionable" and uses_fold_model and po_loss / pot <= MISTAKE_AT:
        if frac <= 2 * QUESTIONABLE_AT:
            grade = "close"
    if po_loss / pot > MISTAKE_AT and confidence == "low" and a.street != "preflop":
        confidence = "medium"  # the pot-odds part alone already settles it
    if grade == "mistake" and confidence == "low" and po_loss / pot <= LOW_CONFIDENCE_MISTAKE_AT:
        grade = "questionable"
    a.grade, a.confidence = grade, confidence

    # Recurring-mistake tags (only for decisions worth flagging).
    if grade in ("questionable", "mistake"):
        a.tags = _tags(a, chosen, top)

    a.headline = GRADE_LABEL[grade]
    a.explanation = _explain(a, chosen, top, loss)


RERAISE_VALUE = 0.55  # equity against the raiser's range to re-raise for value


def _grade_reraise(a: DecisionAnalysis, chosen: Option) -> None:
    """A preflop re-raise, judged by equity against the raiser's estimated range."""
    eq, need = a.equity or 0.0, a.required_equity or 0.0
    valued = [o for o in a.options if o.ev is not None]
    top_ev = max((o.ev or 0.0 for o in valued), default=0.0)
    a.best = [o.key for o in valued if top_ev - (o.ev or 0.0) <= _noise(a)]
    if eq >= RERAISE_VALUE:
        a.best.append(chosen.key)
    a.confidence, a.ev_loss = "low", 0.0
    if eq >= RERAISE_VALUE:
        grade = "good"
        why = (
            f"Your {a.hand} has about {_pct(eq)} against the hands that raise like this: "
            "strong enough to re-raise for value."
        )
    elif eq >= need:
        grade = "close"
        why = (
            f"Your {a.hand} has about {_pct(eq)} against their raising range, enough to "
            f"call (you needed {_pct(need)}). Re-raising it mostly relies on them folding."
        )
    else:
        grade = "questionable"
        why = (
            f"Your {a.hand} has about {_pct(eq)} against their raising range, less than "
            f"the {_pct(need)} needed even to call. Re-raising relies on them folding."
        )
        a.tags = ["preflop_loose"]
    a.grade = grade
    a.headline = GRADE_LABEL[grade] + " (guideline)"
    a.explanation = why
    a.caveats.append(
        f"Preflop re-raises are judged by a guideline (re-raise for value with about "
        f"{RERAISE_VALUE:.0%} or more against their range), not a computed EV."
    )


def _tags(a: DecisionAnalysis, chosen: Option, top: Option) -> list[str]:
    pre = a.street == "preflop"
    aggressive = {"bet", "raise"}
    if chosen.action == "fold" and top.action != "fold":
        return ["preflop_tight" if pre else "folded_with_odds"]
    if chosen.action == "call" and top.action == "fold":
        return ["preflop_loose" if pre else "called_without_odds"]
    if chosen.action in ("check", "call") and top.action in aggressive:
        return ["missed_value"] if (a.equity or 0) >= 0.5 else []
    if chosen.action in aggressive and top.action in aggressive:
        return ["bet_sizing"]
    if chosen.action in aggressive:
        return ["preflop_loose" if pre else "overaggressive"]
    return []


def _explain(a: DecisionAnalysis, chosen: Option, top: Option, loss: float) -> str:
    bb = a.big_blind
    eq = a.equity or 0.0
    parts: list[str] = []
    if a.spot == "facing_bet":
        need = a.required_equity or 0.0
        parts.append(
            f"You needed {_pct(need)} equity to call {a.to_call} into {a.pot}. Against the "
            f"hands the opponent is likely to have here, your {a.hand} has about {_pct(eq)}."
        )
        if chosen.action == "fold":
            if top.action == "fold" or loss <= _noise(a):
                parts.append("Folding gave up little or nothing.")
            else:
                parts.append(
                    f"That is enough to continue: {top.label.lower()} was worth about "
                    f"{_bb(top.ev or 0, bb)} more than folding, by this estimate."
                )
        elif chosen.action == "call":
            ev = chosen.ev or 0.0
            parts.append(
                f"Calling was worth about {_bb(ev, bb)} by this estimate"
                + (", so folding was better." if ev < 0 and top.action == "fold" else ".")
            )
            if top.action in ("raise",) and loss > _noise(a):
                parts.append(f"{top.label} scored higher, assuming they fold or call as modelled.")
        else:
            parts.append(_raise_sentence(chosen, top, loss, a))
    else:
        if chosen.action == "check":
            parts.append(f"Your {a.hand} has about {_pct(eq)} equity against their likely hands.")
            if top.action == "check" or loss <= _noise(a):
                parts.append("Checking is reasonable: betting did not score clearly better.")
            else:
                parts.append(
                    f"{top.label} scored about {_bb(loss, bb)} more: hands that call are "
                    f"worse often enough ({_pct(top.equity_when_called or 0)} equity when "
                    "called) to bet for value."
                )
        else:
            parts.append(f"Your {a.hand} has about {_pct(eq)} equity against their likely hands.")
            parts.append(_raise_sentence(chosen, top, loss, a))
    return " ".join(parts)


def _raise_sentence(chosen: Option, top: Option, loss: float, a: DecisionAnalysis) -> str:
    bb = a.big_blind
    when_called = chosen.equity_when_called or 0.0
    s = (
        f"{chosen.label} is expected to win the pot right away {_pct(chosen.fold_rate or 0)} "
        f"of the time, and you have about {_pct(when_called)} against the hands that continue."
    )
    if loss <= _noise(a):
        return s + " It scored as well as any option, by this estimate."
    if top.action in ("bet", "raise"):
        return s + f" {top.label} scored about {_bb(loss, bb)} more."
    return (
        s + f" {top.label} scored about {_bb(loss, bb)} more: when called you are behind too often."
    )


def with_chosen(a: DecisionAnalysis, key: str) -> DecisionAnalysis:
    """A copy of ``a`` graded as if ``key`` had been chosen (for comparing alternatives)."""
    b = replace(
        a,
        options=list(a.options),
        best=list(a.best),
        tags=[],
        details=list(a.details),
        caveats=list(a.caveats),
        range_steps=list(a.range_steps),
        reads=list(a.reads),
        history=list(a.history),
    )
    return grade_option(b, key)
