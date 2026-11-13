"""Turn a :class:`PlayerModel` into bounded, explainable adjustments to the opponent.

Each adjustment comes from one observable tendency and changes one part of the agent:

==========================  ===========================================================
Tendency (stat)             What changes
==========================  ===========================================================
Fold to bet                 *Calling ranges and value betting.* The share of the
                            player's range assumed to call a bet narrows for a frequent
                            folder and widens for a player who rarely folds. Against a
                            player who rarely folds it also bluffs less, bets thinner
                            for value and expects fewer folds. (The mirror image, more
                            bluffs against frequent folders, lost in controlled
                            matches and was removed; see ``compute_adjustments``.)
Bet when checked to /       *Calling and folding.* The share of the player's range that
raise vs a bet              the model assumes bets (or raises) scales with how often
                            they bet when they could (or raise when facing a bet). Bets
                            from a frequent bettor are read as weaker (more calls), and
                            bets from a rare bettor as stronger. These conditional
                            frequencies are used rather than overall aggression (AFq),
                            whose denominator includes folds and would make a player
                            who folds a lot look passive.
Preflop raise / VPIP        *Preflop reads.* "Top X%" raising and calling ranges scale
                            with the observed preflop frequencies.
Fold to preflop raise       *Preflop steals.* The button opens more hands against a
                            player who folds to raises often, and fewer against one who
                            rarely does.
==========================  ===========================================================

Safeguards against overreacting and against being exploited:

* A stat changes nothing until it has ``MIN_OPPORTUNITIES`` (see
  :mod:`pokerlab.ai.player_model`), and its estimate is shrunk towards the baseline.
* Deviations smaller than ``DEADBAND`` are ignored as noise.
* Every adjustment is clamped. The opponent never stops bluffing, never calls
  everything, and never folds everything, whatever the statistics say.
* The model forgets old hands, so a player who changes style is followed.
* The difficulty's ``adapt`` weight scales every deviation (0 = no adaptation).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .player_model import BASELINES, MIN_OPPORTUNITIES, PlayerModel

if TYPE_CHECKING:
    from ..engine import Action, LegalActions
    from .base import Observation

DEADBAND = 0.03
MAX_FOLD_SHIFT = 0.25


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


@dataclass(frozen=True, slots=True)
class Adjustments:
    """How the opponent currently deviates from its default play, and why."""

    fold_shift: float = 0.0  # estimated fold-to-bet minus the reference (weighted)
    bet_range_scale: float = 1.0  # share of range the player bets with postflop, x
    raise_range_post_scale: float = 1.0  # share of range the player raises with postflop, x
    open_shift: float = 0.0  # change to the button's opening range (share of hands)
    call_range_scale: float = 1.0  # share of range the player calls with postflop, x
    raise_range_scale: float = 1.0  # preflop raising range width, x
    vpip_range_scale: float = 1.0  # preflop calling range width, x
    reasons: dict[str, str] = field(default_factory=dict)  # area -> explanation

    @property
    def neutral(self) -> bool:
        return not self.reasons

    @property
    def bluff_multiplier(self) -> float:
        """Scale on bluff and semi-bluff frequency (threshold betting)."""
        return _clamp(1.0 + 2.5 * self.fold_shift, 0.4, 1.75)

    @property
    def thin_value_shift(self) -> float:
        """How much lower the equity bar for thin value bets is against a caller."""
        return max(0.0, -self.fold_shift) * 0.5

    def fold_probability(self, bet: int, pot: int) -> float:
        """Estimated chance the player folds to a bet of ``bet`` into ``pot``.

        The default is the minimum-defence fold rate ``bet / (pot + bet)``. The observed
        deviation in fold-to-bet shifts it, within bounds.
        """
        return _clamp(bet / (pot + bet) + self.fold_shift, 0.05, 0.90)


NEUTRAL = Adjustments()


def _pct(x: float) -> str:
    return f"{x:.0%}"


def _seen(model: PlayerModel, key: str) -> str:
    """The raw count behind an estimate, as the tendencies table shows it.

    Estimates are shrunk towards the reference (see ``player_model``), so the estimate
    and the raw rate differ on small samples; quoting both avoids an apparent
    contradiction such as "you bet 13%" next to "0 of 18".
    """
    s = model.stats[key]
    return f"seen {s.count:.0f} of {s.opportunities:.0f}"


def compute_adjustments(model: PlayerModel, weight: float) -> Adjustments:
    """Adjustments for a model, scaled by the difficulty's adaptation ``weight`` (0–1)."""
    if weight <= 0:
        return NEUTRAL
    reasons: dict[str, str] = {}
    fold_shift = open_shift = 0.0
    bet_scale = raise_post_scale = call_scale = raise_scale = vpip_scale = 1.0

    if model.reliable("fold_to_bet"):
        est, ref = model.estimate("fold_to_bet"), BASELINES["fold_to_bet"]
        obs = f"{_seen(model, 'fold_to_bet')}; reference {_pct(ref)}"
        dev = est - ref
        if abs(dev) >= DEADBAND:
            # The calling range always follows the observed fold rate. The assumed fold
            # rate itself is only shifted *down* (bluff less, value-bet thinner against
            # a player who rarely folds). Shifting it up against frequent folders (bluff
            # more, expect more folds) was measured and removed: against the v0.1 bot it
            # cost 66 ± 17 bb/100 and against the "folder" style it gained nothing,
            # while the downward shift added about +90 bb/100 against a calling station
            # (2026-11-08 controlled matches; see README).
            call_scale = _clamp(((1 - est) / (1 - ref)) ** weight, 0.6, 1.3)
            if dev < 0:
                fold_shift = weight * max(dev, -MAX_FOLD_SHIFT)
                reasons["value"] = (
                    f"It estimates you fold to only {_pct(est)} of bets ({obs}), so it "
                    "bluffs less, value-bets thinner and reads your calls as wider."
                )
            else:
                reasons["defends"] = (
                    f"It estimates you fold to {_pct(est)} of bets ({obs}), so it reads "
                    "the hands you call with as stronger."
                )

    if model.reliable("bet_unopposed"):
        est, ref = model.estimate("bet_unopposed"), BASELINES["bet_unopposed"]
        obs = f"{_seen(model, 'bet_unopposed')}; reference {_pct(ref)}"
        if abs(est - ref) >= DEADBAND:
            bet_scale = _clamp((est / ref) ** weight, 0.5, 1.6)
            reasons["calls"] = (
                f"It estimates you bet {_pct(est)} of the time when checked to ({obs}), "
                + (
                    "so it reads your bets as weaker and calls more."
                    if est > ref
                    else "so it gives your bets more credit and folds more."
                )
            )
    if model.reliable("raise_vs_bet"):
        est, ref = model.estimate("raise_vs_bet"), BASELINES["raise_vs_bet"]
        obs = f"{_seen(model, 'raise_vs_bet')}; reference {_pct(ref)}"
        if abs(est - ref) >= DEADBAND:
            raise_post_scale = _clamp((est / ref) ** weight, 0.5, 1.6)
            reasons["raises"] = f"It estimates you raise {_pct(est)} of bets ({obs}), " + (
                "so it reads your raises as weaker."
                if est > ref
                else "so it reads your raises as stronger."
            )
    if model.reliable("fold_to_raise_pre"):
        est, ref = model.estimate("fold_to_raise_pre"), BASELINES["fold_to_raise_pre"]
        obs = f"{_seen(model, 'fold_to_raise_pre')}; reference {_pct(ref)}"
        if abs(est - ref) >= DEADBAND:
            open_shift = weight * _clamp(est - ref, -0.15, 0.15)
            reasons["steal"] = (
                f"It estimates you fold to {_pct(est)} of preflop raises ({obs}), "
                + (
                    "so it raises more hands from the button."
                    if est > ref
                    else "so it raises fewer hands from the button."
                )
            )

    if model.reliable("pfr"):
        est, ref = model.estimate("pfr"), BASELINES["pfr"]
        obs = f"{_seen(model, 'pfr')}; reference {_pct(ref)}"
        if abs(est - ref) >= DEADBAND:
            raise_scale = _clamp((est / ref) ** weight, 0.4, 2.0)
            reasons["preflop"] = (
                f"It estimates you raise {_pct(est)} of hands preflop ({obs}), "
                + (
                    "so it reads your raises as wider."
                    if est > ref
                    else "so it reads your raises as stronger."
                )
            )
    if model.reliable("vpip"):
        est, ref = model.estimate("vpip"), BASELINES["vpip"]
        obs = f"{_seen(model, 'vpip')}; reference {_pct(ref)}"
        if abs(est - ref) >= DEADBAND:
            vpip_scale = _clamp((est / ref) ** weight, 0.5, 1.25)
            reasons["vpip"] = f"It estimates you play {_pct(est)} of hands ({obs}), " + (
                "so it reads your preflop calls as wider."
                if est > ref
                else "so it reads your preflop calls as stronger."
            )

    return Adjustments(
        fold_shift=fold_shift,
        bet_range_scale=bet_scale,
        raise_range_post_scale=raise_post_scale,
        open_shift=open_shift,
        call_range_scale=call_scale,
        raise_range_scale=raise_scale,
        vpip_range_scale=vpip_scale,
        reasons=reasons,
    )


# Which tendencies bear on which kind of decision, most relevant first.
_RELEVANT = {
    "facing_bet": ("calls", "raises", "preflop", "vpip", "value", "defends"),
    "unbet": ("value", "defends", "calls", "raises", "preflop", "vpip"),
    "open": ("steal",),
}


def _describe(action: Action, legal: LegalActions, gerund: bool = False) -> str:
    kind = action.type.value if hasattr(action.type, "value") else str(action.type)
    words = {
        "fold": ("fold", "folding"),
        "check": ("check", "checking"),
        "call": (f"call {legal.call_amount}", f"calling {legal.call_amount}"),
        "all_in": ("go all-in", "going all-in"),
        "bet": (f"bet {action.amount}", f"betting {action.amount}"),
        "raise": (f"raise to {action.amount}", f"raising to {action.amount}"),
    }[kind]
    return words[1] if gerund else words[0]


def explain_change(
    obs: Observation, legal: LegalActions, action: Action, neutral: Action, adj: Adjustments
) -> str:
    """One line on a decision adaptation changed: what it did, what it would have done,
    and the observed tendency behind it."""
    from ..engine import Street

    unopened = obs.street == Street.PREFLOP and legal.current_bet <= obs.big_blind
    context = "open" if unopened else ("facing_bet" if legal.can_call else "unbet")
    reason = next((adj.reasons[k] for k in _RELEVANT[context] if k in adj.reasons), "")
    street = obs.street.value.title()
    did, would = _describe(action, legal), _describe(neutral, legal, gerund=True)
    return f"{street}: chose to {did} instead of {would}. {reason}".strip()


def adaptation_policy(difficulty: str, enabled: bool = True) -> str:
    """How a difficulty level uses the player model, in one sentence."""
    if difficulty == "beginner":
        return "Beginner does not track your play. Your stats are shown for your own review."
    if not enabled:
        return "Adaptation is switched off for this game. Your stats are still tracked."
    n = int(MIN_OPPORTUNITIES)
    if difficulty == "intermediate":
        return (
            f"Intermediate adapts at half strength, using each stat once it has {n} or more "
            "opportunities."
        )
    return (
        f"Advanced adapts fully, using each stat once it has {n} or more opportunities, "
        "within fixed limits."
    )
