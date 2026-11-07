"""Preflop table, range model, equity agent decisions, and the duplicate-match harness."""

import random

import pytest

from pokerlab.ai import ActionRecord, EquityAgent, HeuristicAgent, Observation, RandomAgent
from pokerlab.ai.base import Agent
from pokerlab.ai.match import duplicate_match, play_hand
from pokerlab.ai.preflop import (
    PREFLOP_EQUITY,
    all_classes,
    class_combos,
    hand_class,
    percentile,
    ranked_classes,
    top_range,
)
from pokerlab.ai.range_model import MIN_COMBOS, estimate_range, preflop_fraction
from pokerlab.cards import Deck, parse_cards
from pokerlab.engine import Action, ActionType, HoldemHand, LegalActions, Street
from pokerlab.equity import calculate_equity

from .helpers import stacked_deck

PRE, FLOP, RIVER = Street.PREFLOP, Street.FLOP, Street.RIVER


def act(street, seat, action, amount=0, all_in=False):
    return ActionRecord(street, seat, action, amount, all_in)


# ----------------------------------------------------------------- preflop table


def test_table_covers_all_169_classes_and_1326_combos():
    assert set(PREFLOP_EQUITY) == set(all_classes())
    assert len(all_classes()) == 169
    assert sum(class_combos(c) for c in all_classes()) == 1326


def test_table_agrees_with_published_values_and_fresh_simulation():
    # Published equities against a random hand: AA 85.2%, 72o 34.6%, 32o 32.3%.
    assert PREFLOP_EQUITY["AA"] == pytest.approx(0.852, abs=0.006)
    assert PREFLOP_EQUITY["72o"] == pytest.approx(0.346, abs=0.006)
    assert PREFLOP_EQUITY["32o"] == pytest.approx(0.323, abs=0.006)
    r = calculate_equity(["T9s", "any"], iterations=20_000, seed=777)
    p = r.players[0]
    assert abs(p.equity - PREFLOP_EQUITY["T9s"]) < 4.5 * (p.std_error**2 + 0.0025**2) ** 0.5


def test_ranking_and_top_ranges():
    ranked = ranked_classes()
    assert ranked[0] == "AA" and ranked[-1] == "32o"
    assert percentile("AA") == 0.0 and 0.9 < percentile("72o") < 1.0
    assert hand_class(parse_cards("Kd As")) == "AKo" and hand_class(parse_cards("7h 7c")) == "77"
    assert len(top_range(1.0)) == 1326
    for fraction in (0.05, 0.15, 0.4, 0.7):
        assert abs(len(top_range(fraction)) - fraction * 1326) <= 12  # whole classes
    assert set(top_range(0.15).combos) <= set(top_range(0.4).combos)


# ----------------------------------------------------------------- range model


@pytest.mark.parametrize(
    "actions,expected",
    [
        ([], 1.0),
        ([act(PRE, 0, "raise", 25)], 0.70),  # button opens
        ([act(PRE, 0, "call", 10)], 0.85),  # button limps
        ([act(PRE, 0, "call", 10), act(PRE, 1, "raise", 40)], 0.85),  # seat 0 only limped
        ([act(PRE, 1, "raise", 25), act(PRE, 0, "raise", 80)], 0.15),  # 3-bet
        ([act(PRE, 0, "raise", 25), act(PRE, 1, "raise", 80), act(PRE, 0, "call", 80)], 0.30),
        ([act(PRE, 0, "raise", 100, all_in=True)], 0.70),  # 10bb shove opens wider than 40%
        ([act(PRE, 1, "raise", 25), act(PRE, 0, "raise", 100, all_in=True)], 0.40),
        ([act(PRE, 1, "raise", 25), act(PRE, 0, "raise", 1000, all_in=True)], 0.15),
    ],
)
def test_preflop_fraction(actions, expected):
    assert preflop_fraction(actions, seat=0, button=0, bb=10) == expected


def test_big_blind_raising_a_limp_is_tighter_than_an_open():
    actions = [act(PRE, 0, "call", 10), act(PRE, 1, "raise", 40)]
    assert preflop_fraction(actions, seat=1, button=0, bb=10) == 0.35


def test_estimated_range_removes_known_cards_and_narrows_on_aggression():
    board = parse_cards("Ah 7h 2c")
    hole = parse_cards("Kh Kd")
    pre = [act(PRE, 0, "raise", 25), act(PRE, 1, "call", 25)]
    base = estimate_range(pre, 0, 0, 10, board, known=hole)
    used = set(board) | set(hole)
    assert all(a not in used and b not in used for a, b in base)
    bet = estimate_range([*pre, act(FLOP, 0, "bet", 30)], 0, 0, 10, board, known=hole)
    raised = estimate_range(
        [*pre, act(FLOP, 1, "bet", 30), act(FLOP, 0, "raise", 90)], 0, 0, 10, board, known=hole
    )
    assert len(raised) < len(bet) < len(base)
    assert set(bet.combos) <= set(base.combos)
    # Sets and strong aces survive; flush draws are kept even though they are unmade.
    for combo in ("7s7d", "AsKs", "QhJh"):
        assert tuple(parse_cards(combo)) in raised
    assert tuple(parse_cards("4d3s")) not in raised


def test_range_never_narrows_below_minimum():
    board = parse_cards("Ah 7h 2c 9d Ks")
    actions = [act(PRE, 1, "raise", 25), act(PRE, 0, "raise", 80), act(PRE, 1, "raise", 250)]
    actions += [act(RIVER, 1, "raise", 900)] * 6
    r = estimate_range(actions, 1, 0, 10, board)
    assert len(r) >= MIN_COMBOS


# ----------------------------------------------------------------- agent decisions


class FixedEquity(EquityAgent):
    def __init__(self, equity):
        super().__init__(random.Random(0))
        self._eq = equity

    def equity(self, obs):
        return self._eq


def river_spot(stacks=(1000, 400), bet_to=None):
    """Seat 1 (to act) faces a river bet from seat 0; returns (obs, legal)."""
    h = HoldemHand(list(stacks), 0, 5, 10, rng=random.Random(4))
    h.apply(Action.call())
    h.apply(Action.check())
    for _ in range(2):  # flop and turn
        h.apply(Action.check())
        h.apply(Action.check())
    assert h.street == Street.RIVER and h.to_act == 1
    h.apply(Action.check())
    h.apply(Action.bet(bet_to or h.players[1].stack))
    return Observation.from_hand(h, 1), h.legal_actions()


def test_all_in_call_uses_exact_pot_odds():
    obs, legal = river_spot()
    assert legal.call_amount == legal.player_stack == 390
    need = 390 / (obs.pot + 390)  # pot 410 after the shove -> 390 / 800
    assert FixedEquity(need + 0.005).decide(obs, legal).type == ActionType.CALL
    assert FixedEquity(need - 0.005).decide(obs, legal).type == ActionType.FOLD


def test_smaller_calls_need_a_realisation_margin():
    obs, legal = river_spot(stacks=(1000, 1000), bet_to=20)
    assert legal.call_amount < legal.player_stack
    need = legal.call_amount / (obs.pot + legal.call_amount)
    assert FixedEquity(need + 0.01).decide(obs, legal).type == ActionType.FOLD
    assert FixedEquity(need + EquityAgent.CALL_MARGIN + 0.01).decide(obs, legal).type in (
        ActionType.CALL,
        ActionType.RAISE,
    )


def test_unopened_preflop_ranges():
    agent = EquityAgent(random.Random(0))
    h = HoldemHand([1000, 1000], 0, 5, 10, deck=stacked_deck(["AsKs", "7c2d"]))
    obs = Observation.from_hand(h, 0)
    assert obs.hole == tuple(parse_cards("As Ks"))
    assert agent.decide(obs, h.legal_actions()).type == ActionType.RAISE
    h = HoldemHand([1000, 1000], 0, 5, 10, deck=stacked_deck(["7s2d", "AcKc"]))
    obs = Observation.from_hand(h, 0)
    assert agent.decide(obs, h.legal_actions()).type == ActionType.FOLD  # 72o: bottom 5%


def test_equity_estimate_is_reproducible_from_the_agent_seed():
    obs, _ = river_spot(stacks=(1000, 1000), bet_to=200)
    a = EquityAgent(random.Random(9)).equity(obs)
    b = EquityAgent(random.Random(9)).equity(obs)
    assert a == b and 0.0 <= a <= 1.0


@pytest.mark.parametrize("seed", range(2))
def test_equity_agent_always_acts_legally(seed):
    rng = random.Random(seed)
    for i in range(150):
        n = rng.choice([2, 2, 3])
        agents = [
            EquityAgent(random.Random(rng.random()), iterations=150)
            if s % 2 == 0
            else HeuristicAgent(random.Random(rng.random()))
            for s in range(n)
        ]
        stacks = [rng.choice([3, 15, 50, 200, 1000, 5000]) for _ in range(n)]
        deck = Deck(random.Random(rng.random()))
        deck.shuffle()
        hand = play_hand(agents, stacks, i % n, deck, 5, 10)  # raises on an illegal move
        assert hand.total_chips() == sum(stacks)


# ----------------------------------------------------------------- duplicate match


class AlwaysCall(Agent):
    name = "calling station"

    def __init__(self, rng=None):
        pass

    def decide(self, obs, legal: LegalActions):
        return Action.call() if legal.can_call else Action.check()


def test_identical_deterministic_agents_break_exactly_even():
    r = duplicate_match(AlwaysCall, AlwaysCall, 200, seed=3)
    assert r.net_chips == 0 and r.bb_per_100 == 0.0 and r.std_error == 0.0


def test_equity_agent_beats_random_agent():
    r = duplicate_match(
        lambda g: EquityAgent(g, iterations=200), lambda g: RandomAgent(g), 120, seed=8
    )
    assert r.bb_per_100 > 0 and r.z_score > 3, r


def test_match_rejects_tiny_samples():
    with pytest.raises(ValueError):
        duplicate_match(AlwaysCall, AlwaysCall, 1)
