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

from ..ai.base import Observation, raise_to_pot_fraction, uncalled_excess
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
# Equity realisation: the share of its all-in equity a hand actually wins when there is
# betting still to come. It depends on position, hand type and stack depth, and PokerLab
# does not model it. A call is only judged independently of it when it breaks even at
# 70% realisation or less, and a fold only when even 110% would not be enough; this band
# is an assumption, stated as such wherever it matters.
REALIZATION_LOW = 0.70
REALIZATION_HIGH = 1.10
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
    # The same comparison against a tighter and a looser range (see RANGE_VARIANTS).
    range_check: list[dict[str, Any]] = field(default_factory=list)
    # Whether the verdict on the chosen option holds against every range in range_check.
    robust: bool | None = None
    # No more betting can follow (someone is all-in): equity is realised in full.
    betting_closed: bool = False
    # Share of its equity a call must realise to break even (calls before the river).
    realization_needed: float | None = None
    # What the coach knows about the opponent's tendencies (see spot.opponent_profile).
    opponent: dict[str, Any] | None = None
    # The explanation as steps: did / known / estimated / math / alternative / principle.
    lesson: dict[str, Any] = field(default_factory=dict)
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
    made = evaluate(list(hole) + list(board))
    name = made.description
    if len(board) == 5 and evaluate(list(board)).category == made.category:
        # The made hand is on the board: everyone has it, only kickers can differ.
        name += " (on the board)"
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
    range_check: bool = True,
    opponent: dict[str, Any] | None = None,
) -> DecisionAnalysis:
    """Analyse a decision. With ``chosen`` it is also graded (see :func:`grade_option`).

    ``range_check`` repeats the comparison against the tighter and looser ranges in
    :data:`RANGE_VARIANTS` (switch it off only for quick screening).
    """
    rng = random.Random(seed)
    villain = _villain(obs)
    preflop = obs.street == Street.PREFLOP
    # A short big blind that is already all-in leaves only call or fold: pot odds, not
    # the opening guideline.
    unopened = (
        preflop and legal.current_bet <= obs.big_blind and (legal.can_raise or legal.can_check)
    )
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
        opponent=opponent,
        betting_closed=legal.call_amount >= legal.player_stack
        or bool(obs.opponent_stacks and obs.opponent_stacks[0] == 0),
    )
    if unopened:
        _guideline_options(base, obs, legal)
    else:
        _ev_options(base, obs, legal, chosen, adjust, rng, iterations, villain, spot, range_check)
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


@dataclass(frozen=True, slots=True)
class RangeVariant:
    """A plausible alternative to the estimated range, used to test how much a verdict
    depends on the range assumption."""

    name: str  # "tighter" / "looser"
    label: str
    postflop_scale: float  # multiplies the share of the range kept by each bet/raise/call
    preflop_scale: float  # multiplies the preflop raising and calling range widths
    description: str


# Moderate alternatives, not extremes: narrowing compounds over streets, so a much tighter
# setting quickly leaves only the nuts. Each one changes the share of hands the opponent
# is assumed to bet, raise or call with on each action by about a quarter.
RANGE_VARIANTS = (
    RangeVariant(
        "tighter",
        "Tighter range",
        0.75,
        0.85,
        "they bet, raise and call only with stronger hands (about a quarter fewer)",
    ),
    RangeVariant(
        "looser",
        "Looser range",
        1.3,
        1.2,
        "they bet, raise and call with more hands, including weaker ones and bluffs "
        "(about a third more)",
    ),
)


def _scaled(adjust: Adjustments | None, v: RangeVariant) -> Adjustments:
    from ..ai.adaptation import Adjustments as _Adj

    a = adjust or _Adj()
    return replace(
        a,
        bet_range_scale=a.bet_range_scale * v.postflop_scale,
        raise_range_post_scale=a.raise_range_post_scale * v.postflop_scale,
        call_range_scale=a.call_range_scale * v.postflop_scale,
        raise_range_scale=a.raise_range_scale * v.preflop_scale,
        vpip_range_scale=a.vpip_range_scale * v.preflop_scale,
        reasons=dict(a.reasons),
    )


def _villain_range(obs: Observation, villain: int, adjust: Adjustments | None) -> HandRange:
    r = estimate_range(
        obs.actions, villain, obs.button, obs.big_blind, obs.board, known=obs.hole, adjust=adjust
    )
    if len(r) == 0:
        r = ANY_HAND.without(set(obs.hole) | set(obs.board))
    return r


@dataclass(slots=True)
class _Evaluation:
    """Every modelled option's value against one opponent range."""

    equity: float
    se: float
    method: str
    evs: dict[str, float]
    when_called: dict[str, float]


def _evaluate(
    obs: Observation,
    legal: LegalActions,
    villain_range: HandRange,
    totals: list[int],
    folds: list[float],
    pot: int,
    seed: int,
    iterations: int,
) -> _Evaluation:
    """Expected values of fold/call/check and of each modelled bet size, in chips."""
    sub = random.Random(seed)
    eq, se, method = _equity(obs.hole, villain_range, obs.board, sub.randrange(2**63), iterations)
    call = legal.call_amount
    evs: dict[str, float] = {}
    when_called: dict[str, float] = {}
    if legal.can_call:
        evs["fold"] = 0.0
        evs["call"] = eq * (pot + call) - call
    else:
        evs["check"] = eq * pot
    if totals:
        villain_bet = legal.current_bet
        villain_stack = obs.opponent_stacks[0] if obs.opponent_stacks else 0
        bets = [t - legal.player_bet for t in totals]
        if obs.board:
            defenders = defending_ranges(villain_range, obs.board, [1 - f for f in folds])
        else:
            defenders = [_preflop_defenders(villain_range, 1 - f) for f in folds]
        for total, b, fold, drange in zip(totals, bets, folds, defenders, strict=True):
            key = _raise_key(total, legal)
            v_call = min(total - villain_bet, villain_stack)
            final_pot = pot + b + v_call
            eq_d, _, _ = _equity(obs.hole, drange, obs.board, sub.randrange(2**63), iterations)
            evs[key] = fold * pot + (1 - fold) * (eq_d * final_pot - b)
            when_called[key] = eq_d
    return _Evaluation(eq, se, method, evs, when_called)


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
    range_check: bool = True,
) -> None:
    villain_range = _villain_range(obs, villain, adjust)
    a.range_size = len(villain_range)
    a.range_steps = describe_range(obs, villain, adjust)
    excess = uncalled_excess(legal)
    pot, call = obs.pot - excess, legal.call_amount
    a.pot = pot
    bb = obs.big_blind
    if excess:
        a.details.append(
            f"{excess} chips of the opponent's blind are more than you can match; they go "
            f"back to them, so the pot you can win is {pot}."
        )

    totals = _candidate_raises(obs, legal, chosen, spot)
    guideline_raise: Option | None = None
    if totals and obs.street == Street.PREFLOP:
        # How often a preflop raiser folds to a re-raise cannot be read off the fold
        # model (it would recommend re-raising junk as a bluff), so a preflop re-raise
        # is judged by a guideline on equity instead.
        total = _chosen_total(chosen, legal) if chosen is not None else None
        total = total or raise_to_pot_fraction(obs, legal, 0.75).amount or legal.min_raise_to
        key = _raise_key(total, legal)
        guideline_raise = Option(
            key,
            legal.raise_type.value,
            total,
            _bet_label(legal.raise_type.value, total, pot, key == "all-in"),
            None,
            "guideline",
            detail="preflop re-raise: judged by a guideline, not an EV",
        )
        totals = []
    modelled = [t for t in totals if _modelled(t, obs, legal)]
    folds = [fold_rate(t - legal.player_bet, pot, adjust) for t in modelled]

    seed = rng.randrange(2**63)
    base = _evaluate(obs, legal, villain_range, modelled, folds, pot, seed, iterations)
    eq, se, method = base.equity, base.se, base.method
    a.equity, a.equity_se, a.equity_method = eq, se, method
    if a.concept == "bluffing" and eq >= 0.5:
        a.concept = "value_betting"  # ahead already: betting is for value, not a semi-bluff

    # The same comparison against plausible alternative ranges.
    for v in RANGE_VARIANTS if range_check else ():
        vr = _villain_range(obs, villain, _scaled(adjust, v))
        ev = _evaluate(obs, legal, vr, modelled, folds, pot, rng.randrange(2**63), iterations)
        a.range_check.append(
            {
                "name": v.name,
                "label": v.label,
                "description": v.description,
                "range_size": len(vr),
                "equity": ev.equity,
                "evs": ev.evs,
            }
        )

    opts: list[Option] = []
    if spot == "facing_bet":
        need = call / (pot + call)
        a.required_equity = need
        opts.append(Option("fold", "fold", None, "Fold", 0.0, "pot odds"))
        ev_call = base.evs["call"]
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
        if not a.betting_closed and obs.street != Street.RIVER and eq > 0:
            a.realization_needed = need / eq
            a.details.append(
                f"Break-even realisation: {need:.1%} ÷ {eq:.1%} = {need / eq:.0%} of your "
                "equity must be won on later streets for the call to pay."
            )
    else:
        ev_check = base.evs["check"]
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
    if guideline_raise is not None:
        opts.append(guideline_raise)

    kind = legal.raise_type.value
    fold_by_total = dict(zip(modelled, folds, strict=True))
    for total in totals:
        key = _raise_key(total, legal)
        if total not in fold_by_total:
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
        b = total - legal.player_bet
        fold = fold_by_total[total]
        villain_stack = obs.opponent_stacks[0] if obs.opponent_stacks else 0
        final_pot = pot + b + min(total - legal.current_bet, villain_stack)
        eq_d, ev = base.when_called[key], base.evs[key]
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
                    f"fold {fold:.0%} × {pot} + continue {1 - fold:.0%} × "
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
    opt = next((o for o in a.options if o.key == key), None)
    a.lesson = build_lesson(a, opt, opt, 0.0)


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
        a.lesson = build_lesson(a, chosen, None, 0.0)
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

    # Does the verdict survive a different range assumption? Re-run the same comparison
    # against the tighter and the looser range: a choice flagged here must also be flagged
    # there, and a choice called good must not be a clear error there.
    flagged = grade in ("questionable", "mistake")
    flips: list[dict[str, Any]] = []
    for rc in a.range_check:
        evs = {k: v for k, v in rc["evs"].items() if k in by_key and by_key[k].ev is not None}
        if key not in evs:
            continue
        v_loss = max(evs.values()) - evs[key]
        v_flagged = v_loss > max(noise, QUESTIONABLE_AT * pot)
        if (flagged and v_loss <= noise) or (not flagged and v_flagged):
            flips.append({**rc, "best": max(evs, key=lambda k: evs[k])})
    a.robust = not flips if a.range_check else None
    if flips:
        confidence = "low"
        if grade == "mistake":
            grade = "questionable"
    elif confidence == "high" and a.robust is None:
        confidence = "medium"
    # Equity realisation: before the river the call only wins its equity if the hand is
    # played to showdown. If the verdict on call vs fold hinges on how much of it is
    # realised, it is a judgement call, not a pot-odds fact.
    r = a.realization_needed
    # A fold is always weighed against calling (its pot-odds alternative), whatever
    # scores best overall; a call only when the question is call versus fold.
    realization_matters = (
        r is not None
        and (chosen.action == "fold" or {chosen.action, top.action} <= {"fold", "call"})
        and REALIZATION_LOW < r < REALIZATION_HIGH
    )
    if realization_matters:
        confidence = "low"
        if grade == "mistake":
            grade = "questionable"
    a.grade, a.confidence = grade, confidence

    # Recurring-mistake tags (only for decisions worth flagging).
    if grade in ("questionable", "mistake"):
        a.tags = _tags(a, chosen, top)

    a.headline = GRADE_LABEL[grade]
    a.explanation = _explain(a, chosen, top, loss)
    a.lesson = build_lesson(
        a, chosen, top, loss, range_dependent=bool(flips), realization_dependent=realization_matters
    )
    if realization_matters and r is not None:
        a.explanation += (
            f" Calling needs you to win at least {r:.0%} of your equity on later streets. "
            + (
                "Out of position or with a hand that is hard to play, you usually realise "
                "less than all of it, so this is a judgement call rather than a clear verdict."
                if r < 1
                else "That is more than all of it, which only happens when you can win extra "
                "bets later (a strong draw in position, say): folding is reasonable, but "
                "not clearly better."
            )
        )
    if flips:
        rc = flips[0]
        a.explanation += (
            f" This verdict depends on the range assumption: if {rc['description']}, "
            f"your {a.hand} has about {_pct(rc['equity'])} and "
            + (
                "your choice would be fine."
                if flagged
                else f"{by_key[rc['best']].label.lower()} would score clearly better."
            )
        )


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
    a.lesson = build_lesson(a, chosen, chosen, 0.0)
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


# --------------------------------------------------------------------------- lesson

_VERB = {"fold": "folded", "check": "checked", "call": "called", "bet": "bet", "raise": "raised"}

PRINCIPLES = {
    "call_ok": "Call when your equity against the hands they are likely to have beats the "
    "price the pot offers.",
    "fold_with_odds": "Don't fold a hand that clears the price: the pot is already paying you "
    "to continue.",
    "call_without_odds": "Judge a hand against their likely range, not on its own: if it "
    "cannot beat the price, it is a fold however good it looks.",
    "fold_ok": "Folding is right when your equity is below the price; saving chips is "
    "winning chips.",
    "missed_value": "Bet when worse hands will call: checking a strong hand lets them see "
    "cards for free and gives up value.",
    "overaggressive": "Bet for value only when enough worse hands call, or as a bluff when "
    "enough better hands fold; otherwise checking keeps the pot small.",
    "sizing": "Choose the size that worse hands still call: bigger when they will pay, "
    "smaller when only better hands would continue.",
    "check_ok": "When betting would mostly be called by better hands, checking is fine: "
    "not every hand needs a bet.",
    "bet_ok": "A bet earns when worse hands call or better hands fold; this one does both "
    "often enough.",
    "depends": "When the answer changes with their range, ask what this opponent really "
    "bets and calls with: that read decides it.",
    "realization": "Before the river, equity is only worth what you can realise: position "
    "and playable hands realise more of it.",
    "raise_value": "Facing a bet with a strong hand or a strong draw, consider raising: "
    "worse hands may pay more and some better hands fold (how often is an assumption).",
    "fold_vs_raise": "Folding is not the only alternative to a bad call: a raise can win "
    "the pot at once when they fold often enough (how often is an assumption).",
    "preflop": "Preflop, hand strength and position decide: play strong hands for value, "
    "and give up the weakest ones from out of position.",
}


def _principle(a: DecisionAnalysis, chosen: Option | None, top: Option | None) -> str:
    if chosen is None or top is None or a.spot == "unopened" or chosen.basis == "guideline":
        return PRINCIPLES["preflop"]
    good = a.grade in ("good", "close")
    if a.spot == "facing_bet":
        if good:
            return PRINCIPLES[{"call": "call_ok", "fold": "fold_ok"}.get(chosen.action, "bet_ok")]
        if chosen.action == "call":
            # Graded down because folding scores more, or because raising does?
            return PRINCIPLES["call_without_odds" if top.action == "fold" else "raise_value"]
        if chosen.action == "fold":
            clears = a.equity is not None and a.required_equity is not None
            clears = clears and (a.equity or 0) >= (a.required_equity or 0)
            if top.action == "call" or clears:
                return PRINCIPLES["fold_with_odds"]
            return PRINCIPLES["fold_vs_raise"]
        return PRINCIPLES["overaggressive"]
    if chosen.action == "check":
        return PRINCIPLES["check_ok" if good else "missed_value"]
    if not good and top.action in ("bet", "raise"):
        return PRINCIPLES["sizing"]
    return PRINCIPLES["bet_ok" if good else "overaggressive"]


def build_lesson(
    a: DecisionAnalysis,
    chosen: Option | None,
    top: Option | None,
    loss: float,
    *,
    range_dependent: bool = False,
    realization_dependent: bool = False,
) -> dict[str, Any]:
    """The decision as five short steps, each marked by what kind of claim it is."""
    bb = a.big_blind
    label = chosen.label if chosen else (a.chosen or "?")
    verb = _VERB.get(chosen.action, chosen.action) if chosen else "acted"
    when = "preflop" if a.street == "preflop" else f"on the {a.street}"
    did = f"You {verb} ({label}) {when} with {a.hand}, as the {a.position}."
    facts = [f"Your cards: {' '.join(a.hole)}"]
    if a.board:
        facts.append(f"The board: {' '.join(a.board)}")
    facts.append(f"Pot {a.pot}" + (f", {a.to_call} to call" if a.to_call else ", nothing to call"))
    estimates: list[str] = []
    if a.range_size:
        estimates.append(
            f"Their range: about {a.range_size} hand combinations, estimated from their actions"
        )
    if a.opponent is not None:
        estimates.append(a.opponent["note"])
    if a.equity is None:
        estimates.append("No range to calculate against yet (the pot is unopened).")

    math: list[dict[str, str]] = []

    def step(text: str, kind: str) -> None:
        math.append({"text": text, "kind": kind})

    if a.spot == "unopened":
        if a.hand_percentile is not None:
            step(
                f"{a.hand} ranks in the top {max(1, round(a.hand_percentile * 100))}% of "
                "starting hands; the advice follows an opening guideline, not a computed EV.",
                "guideline",
            )
    else:
        eq = a.equity or 0.0
        band = [rc["equity"] for rc in a.range_check]
        spread = (
            f" ({_pct(min(band))}–{_pct(max(band))} if their range is tighter or looser)"
            if band and (max(band) - min(band)) >= 0.02
            else ""
        )
        if a.required_equity is not None:
            step(
                f"Price: you needed {_pct(a.required_equity)} equity "
                f"({a.to_call} to win {a.pot + a.to_call}).",
                "exact",
            )
        step(f"Your equity against their estimated range: about {_pct(eq)}{spread}.", "estimate")
        if a.realization_needed is not None:
            step(
                "With betting still to come, a call must realise at least "
                f"{a.realization_needed:.0%} of that equity to pay.",
                "assumption",
            )
        elif a.betting_closed:
            step("Nobody can bet again, so your equity is realised in full.", "exact")
        fold_model = (chosen is not None and chosen.basis == "fold model") or (
            top is not None and top.basis == "fold model"
        )
        if fold_model:
            step("Bet and raise values also assume how often they fold.", "model")
        if chosen is not None and chosen.basis == "guideline":
            step("Preflop re-raises follow a guideline, not a computed EV.", "guideline")

    if top is None or chosen is None or a.grade == "ungraded":
        alternative = "No option was compared for this decision."
    elif chosen.basis == "guideline" or a.spot == "unopened":
        better = [o.label for o in a.options if o.key in a.best and o.key != a.chosen]
        alternative = (
            f"The guideline suggests {better[0].lower()} instead."
            if better
            else "Your choice matches the guideline."
        )
    elif a.chosen in a.best or loss <= _noise(a):
        alternative = "No option scored clearly better by these estimates."
    else:
        basis = " (this rests on an assumed fold rate)" if top.basis == "fold model" else ""
        alternative = f"{top.label} scored about {_bb(loss, bb)} better{basis}."

    principle = _principle(a, chosen, top)
    extra = []
    if range_dependent:
        extra.append(PRINCIPLES["depends"])
    if realization_dependent:
        extra.append(PRINCIPLES["realization"])
    return {
        "did": did,
        "known": facts,
        "estimated": estimates,
        "math": math,
        "alternative": alternative,
        "principle": principle,
        "also": extra,
    }


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
        range_check=list(a.range_check),
        reads=list(a.reads),
        history=list(a.history),
    )
    return grade_option(b, key)
