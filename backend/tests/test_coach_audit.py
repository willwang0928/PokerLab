"""Coaching correctness audit (v0.4.5): deterministic spots checked against values
derived independently of the coach.

Equity comes from ``tests/reference.py`` (plain enumeration, no PokerLab code) against
the range the coach says it used; chip accounting (pot, chips added, what the opponent
can call) is worked out from the betting by hand. The range itself is a model, so these
tests check the arithmetic and the bookkeeping, not that the range is right.
"""

from __future__ import annotations

import pytest

from pokerlab.ai.range_model import estimate_range
from pokerlab.cards import parse_cards
from pokerlab.coach.analysis import (
    CLOSE_AT,
    MISTAKE_AT,
    QUESTIONABLE_AT,
    analyze,
    fold_rate,
    with_chosen,
)
from pokerlab.coach.spot import Spot
from pokerlab.engine import Action

from . import reference as ref
from .test_coach import RIVER_LINE


def mk(hero, board, actions, button=1, stacks=(1000, 1000)) -> Spot:
    return Spot(
        hero=tuple(parse_cards(hero)),  # type: ignore[arg-type]
        board=tuple(parse_cards(board)),
        actions=tuple(actions),
        button=button,
        stacks=stacks,
        small_blind=5,
        big_blind=10,
    )


def run(s: Spot, action: Action, **kw):
    _, obs, legal = s.replay()
    return analyze(obs, legal, action, seed=s.seed(), iterations=3000, **kw), obs, legal


def reference_equity(hero: str, board: str, combos) -> float:
    """Equity against every combo of a range, weighted equally, by plain enumeration."""
    h, b = [hero[:2], hero[2:]], [board[i : i + 2] for i in range(0, len(board), 2)]
    total = 0.0
    for combo in combos:
        v = [c.code for c in combo]
        eq, _ = ref.equity([h, v], b)
        total += eq[0]
    return total / len(combos)


# --------------------------------------------------------------------------- pot odds


@pytest.mark.parametrize(
    "hero",
    ["AhQd", "7s7h", "Kc3c"],
)
def test_river_call_numbers_match_an_independent_calculation(hero):
    board = "Qs7d2c4h5s"
    a, obs, _ = run(mk(hero, board, RIVER_LINE), Action.call())
    combos = list(estimate_range(obs.actions, 1, obs.button, 10, obs.board, known=obs.hole))
    eq = reference_equity(hero, board, combos)
    # Pot before the call: blinds 25+25, flop 30+30, turn 60+60, river bet 200 = 430.
    assert (a.pot, a.to_call) == (430, 200)
    assert a.equity == pytest.approx(eq, abs=1e-9)  # exact enumeration on the river
    assert a.required_equity == pytest.approx(200 / 630)
    ev_call = eq * 630 - 200
    assert a.option("call").ev == pytest.approx(ev_call, abs=1e-6)
    # Only the pot-odds part (call versus fold) can make a "likely mistake".
    po_loss = max(0.0, -ev_call)
    if po_loss / 430 > MISTAKE_AT and a.robust:
        assert a.grade == "mistake"
    elif po_loss / 430 <= MISTAKE_AT:
        assert a.grade != "mistake"
    if a.ev_loss <= CLOSE_AT * 430:
        assert a.grade == "good"
    # A pot-odds river verdict is "high" only when it holds for the other ranges too.
    assert a.confidence != "high" or a.robust is True


def test_break_even_equity_is_the_point_where_calling_and_folding_tie():
    a, _, _ = run(mk("AhQd", "Qs7d2c4h5s", RIVER_LINE), Action.call())
    need = a.required_equity
    assert need * (a.pot + a.to_call) - a.to_call == pytest.approx(0.0, abs=1e-9)


# ------------------------------------------------------------------ bet and raise accounting


def test_raise_options_count_only_the_chips_added_and_what_they_can_call():
    # Turn: hero (big blind) bets 60, villain raises to 180. Hero has 60 in already.
    line = [("raise", 25), ("call", None), ("check", None), ("check", None),
            ("bet", 60), ("raise", 180)]  # fmt: skip
    a, obs, legal = run(mk("AsAd", "Ah7c2d9s", line), Action.call())
    assert legal.player_bet == 60 and legal.current_bet == 180
    assert a.to_call == 120 and a.pot == 50 + 60 + 180  # 290 before calling
    villain_stack = 1000 - 25 - 180
    for o in a.options:
        if o.basis != "fold model":
            continue
        added = o.amount - 60  # chips hero puts in now
        v_call = min(o.amount - 180, villain_stack)
        fold = fold_rate(added, a.pot)
        final = a.pot + added + v_call
        ev = fold * a.pot + (1 - fold) * (o.equity_when_called * final - added)
        assert o.fold_rate == pytest.approx(fold)
        assert o.ev == pytest.approx(ev, rel=1e-9)


def test_a_shove_over_a_short_stack_is_capped_at_what_they_can_call():
    # Villain has only 90 chips: 65 behind after 25/25 preflop. Checked to hero on the flop.
    line = [("raise", 25), ("call", None), ("check", None)]
    a, obs, legal = run(mk("AsAd", "Ah7c2d", line, button=0, stacks=(1000, 90)), Action.check())
    assert legal.max_raise_to == 65  # villain's remaining stack: no bet can exceed it
    shove = next(o for o in a.options if o.key == "all-in")
    assert shove.amount == 65 and shove.ev is not None
    fold = fold_rate(65, a.pot)
    final = a.pot + 65 + 65
    assert shove.ev == pytest.approx(fold * a.pot + (1 - fold) * (
        shove.equity_when_called * final - 65), rel=1e-9)  # fmt: skip


def test_an_all_in_call_closes_the_betting_so_no_realisation_is_assumed():
    # Turn: villain shoves; hero calling puts no one with chips behind.
    line = [("raise", 25), ("call", None), ("check", None), ("check", None),
            ("check", None), ("bet", 975)]  # fmt: skip
    a, _, _ = run(mk("Td9d", "8d7c2dKs", line), Action.call())
    assert a.betting_closed and a.realization_needed is None
    assert not any("one-street" in c for c in a.caveats)


def test_a_flop_call_states_how_much_equity_it_must_realise():
    line = [("raise", 25), ("call", None), ("check", None), ("bet", 40)]
    a, _, _ = run(mk("9h8h", "Th7c2h", line), Action.call())
    assert a.realization_needed == pytest.approx(a.required_equity / a.equity)
    assert any("one-street" in c for c in a.caveats)
    kinds = {m["kind"] for m in a.lesson["math"]}
    assert "assumption" in kinds  # realisation is shown as an assumption, not a fact


# --------------------------------------------------------------------------- grade thresholds


def test_grade_thresholds_follow_the_share_of_the_pot_given_up():
    a, _, _ = run(mk("3c2d", "AhKdQs7c9h", RIVER_LINE), Action.call())
    # No equity: calling 200 into 430 loses exactly the call.
    assert a.equity == 0.0 and a.ev_loss == pytest.approx(200)
    assert 200 / 430 > MISTAKE_AT and a.grade == "mistake"
    assert QUESTIONABLE_AT < MISTAKE_AT


def test_low_confidence_flags_use_hedged_labels():
    # A flop check that only "loses" under an assumed fold rate.
    s = mk("AhAd", "As7c2h", [("raise", 25), ("call", None), ("check", None)], button=0)
    a, _, _ = run(s, Action.check())
    d = a.to_dict()
    assert a.confidence == "low"
    assert d["grade_label"] in ("Close call", "Possibly questionable")
    good = with_chosen(a, a.best[0]).to_dict()
    assert good["grade_label"] != "Good" or good["confidence"] != "low"


# --------------------------------------------------------------------------- information boundary


def test_the_analysis_ignores_cards_that_are_not_dealt_yet():
    """The same decision with a different future run-out gives the same analysis."""
    from pokerlab.ai.base import Observation
    from pokerlab.engine import HoldemHand

    from .helpers import stacked_deck

    out = []
    for runout in ("2s3s", "KhQh"):
        deck = stacked_deck(["AhKd", "QcJc"], "Ts9s4d" + runout, button=1)
        h = HoldemHand([1000, 1000], 1, 5, 10, deck=deck)
        for act in (Action.raise_to(25), Action.call(), Action.check(), Action.bet(30)):
            h.apply(act)
        obs = Observation.from_hand(h, 0)
        a = analyze(obs, h.legal_actions(), Action.call(), seed=4, iterations=1500)
        out.append(a.to_dict())
    assert out[0] == out[1]


def test_a_flop_fold_is_a_confident_mistake_only_if_calling_pays_at_half_realisation():
    """v0.4.5 calibration: "should have called" verdicts at 70% realisation did not hold
    up when hands were played out, so folds need the call to pay at 50%."""
    line = [("raise", 25), ("call", None), ("check", None), ("bet", 40)]
    draw, _, _ = run(mk("9h8h", "Th7c2h", line), Action.fold())
    assert 0.5 < draw.realization_needed < 0.7
    assert draw.confidence == "low" and draw.grade != "mistake"
    top_pair, _, _ = run(mk("AhJd", "Jc8s3d", line), Action.fold())
    assert top_pair.realization_needed <= 0.5
    assert top_pair.grade == "mistake" and top_pair.confidence == "medium"
    assert top_pair.tags == ["folded_with_odds"]
