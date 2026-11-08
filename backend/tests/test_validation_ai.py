"""Regression tests for AI decision defects found in the v0.4.1 audit."""

from __future__ import annotations

import random

from pokerlab.ai import Observation
from pokerlab.ai.difficulty import PROFILES
from pokerlab.ai.equity_agent import EquityAgent
from pokerlab.ai.player_model import observe_hand
from pokerlab.engine import Action, ActionType, HoldemHand

from .helpers import stacked_deck


def test_ai_calls_a_short_all_in_big_blind_with_any_hand():
    """The big blind's post left them all-in: nothing more can be bet, and every hand has
    well over the 14-25% pot odds needed. The unopened-pot guideline used to fold it."""
    for bb_stack in (7, 10):
        h = HoldemHand(
            [1000, bb_stack], 0, 5, 10, deck=stacked_deck(["7c2d", "KsKh"], "", button=0)
        )
        legal = h.legal_actions()
        assert legal.can_call and not legal.can_raise
        obs = Observation.from_hand(h, 0)
        for prof in PROFILES.values():
            for s in range(30):
                action = EquityAgent(random.Random(s), profile=prof).decide(obs, legal)
                assert action.type != ActionType.FOLD


def test_no_unrealised_equity_margin_when_the_opponent_is_all_in():
    """Against an all-in there are no later streets: the call is pure chip EV."""
    h = HoldemHand([300, 1000], 0, 5, 10, rng=random.Random(5))
    h.apply(Action.all_in())
    legal = h.legal_actions()
    obs = Observation.from_hand(h, 1)
    need = legal.call_amount / (obs.pot + legal.call_amount)
    for prof in PROFILES.values():
        assert EquityAgent(random.Random(0), profile=prof).required_equity(obs, legal) == need


def test_margins_still_apply_when_betting_can_continue():
    h = HoldemHand([1000, 1000], 0, 5, 10, rng=random.Random(5))
    h.apply(Action.raise_to(30) if hasattr(Action, "raise_to") else Action(ActionType.RAISE, 30))
    legal = h.legal_actions()
    obs = Observation.from_hand(h, 1)
    need = legal.call_amount / (obs.pot + legal.call_amount)
    req = EquityAgent(random.Random(0), profile=PROFILES["advanced"]).required_equity(obs, legal)
    assert req > need


def test_facing_an_all_in_is_not_a_raise_opportunity():
    h = HoldemHand([1000, 200], 0, 5, 10, rng=random.Random(1))
    h.apply(Action.call())
    h.apply(Action.check())
    h.apply(Action.all_in())  # the short stack shoves the flop
    assert not h.legal_actions().can_raise
    h.apply(Action.call())
    stats = [k for k, _ in observe_hand(h.events, 0)]
    assert "raise_vs_bet" not in stats
    assert "fold_to_bet" in stats  # folding and calling were real options


def test_facing_a_normal_bet_is_a_raise_opportunity_and_all_in_raises_count():
    h = HoldemHand([1000, 1000], 0, 5, 10, rng=random.Random(1))
    h.apply(Action.call())
    h.apply(Action.check())
    h.apply(Action(ActionType.BET, 20))
    h.apply(Action.all_in())  # an all-in raise is a raise
    assert ("raise_vs_bet", True) in observe_hand(h.events, 0)
