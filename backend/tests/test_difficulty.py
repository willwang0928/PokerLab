"""Difficulty levels: real behavioural differences, no hidden information, legal play."""

import random
from dataclasses import replace

import pytest

from pokerlab.ai import Observation
from pokerlab.ai.difficulty import (
    ADVANCED,
    BEGINNER,
    INTERMEDIATE,
    PROFILES,
    Difficulty,
    make_agent,
)
from pokerlab.ai.equity_agent import EquityAgent
from pokerlab.ai.match import duplicate_match, play_hand
from pokerlab.ai.preflop import hand_class, percentile
from pokerlab.cards import Deck, parse_cards
from pokerlab.engine import Action, ActionType, HoldemHand, Street

from .helpers import stacked_deck

LEVELS = list(Difficulty)


def river_shove(hero="9c8c", ai="Ks4d", board="Kh7d2s5cJh"):
    """Seat 0 (button) raises, bets every street and shoves the river; seat 1 must act."""
    h = HoldemHand([1000, 1000], 0, 5, 10, deck=stacked_deck([hero, ai], board, 0))
    h.apply(Action.raise_to(25))
    h.apply(Action.call())
    for bet in (35, 80):
        h.apply(Action.check())
        h.apply(Action.bet(bet))
        h.apply(Action.call())
    h.apply(Action.check())
    h.apply(Action.bet(h.players[0].stack))
    return h


def checked_down_river(hero="9c8c", ai="Ks4d", board="Kh7d2s5cJh"):
    """Limped pot, checked to the river; seat 1 (out of position) acts first on the river."""
    h = HoldemHand([1000, 1000], 0, 5, 10, deck=stacked_deck([hero, ai], board, 0))
    h.apply(Action.call())
    h.apply(Action.check())
    for _ in range(2):
        h.apply(Action.check())
        h.apply(Action.check())
    assert h.street == Street.RIVER and h.to_act == 1
    return h


# ----------------------------------------------------------------- configuration


def test_make_agent_uses_the_level_profile():
    for level in LEVELS:
        agent = make_agent(level, random.Random(0))
        assert agent.profile is PROFILES[level] and agent.name == level.value
    assert make_agent("advanced").profile is ADVANCED
    with pytest.raises(ValueError):
        make_agent("expert")


def test_default_agent_is_the_v032_intermediate_opponent():
    assert EquityAgent().profile is INTERMEDIATE
    assert replace(INTERMEDIATE, name="x") == replace(EquityAgent().profile, name="x")


# ----------------------------------------------------------------- behaviour


def test_beginner_ignores_the_betting_and_pays_off_a_river_shove():
    """Same cards, same action: only the beginner believes top pair is still good."""
    h = river_shove()
    obs, legal = Observation.from_hand(h, 1), h.legal_actions()
    need = legal.call_amount / (obs.pot + legal.call_amount)
    beginner = make_agent("beginner", random.Random(1))
    assert beginner.equity(obs) > 0.8 > need  # equity against any two cards
    assert beginner.decide(obs, legal).type == ActionType.CALL
    for level in ("intermediate", "advanced"):
        agent = make_agent(level, random.Random(1))
        assert agent.equity(obs) < need  # equity against a bet-bet-shove range
        assert agent.decide(obs, legal).type == ActionType.FOLD


def test_advanced_reads_checks_as_a_capped_range():
    h = checked_down_river()
    obs = Observation.from_hand(h, 1)
    inter, adv = (
        make_agent("intermediate", random.Random(2)),
        make_agent("advanced", random.Random(2)),
    )
    r_inter, r_adv = inter.opponent_ranges(obs)[0], adv.opponent_ranges(obs)[0]
    assert set(r_adv.combos) < set(r_inter.combos)
    # The removed combos are the strong ones: kings, sets and two pairs on this board.
    removed = set(r_inter.combos) - set(r_adv.combos)
    assert tuple(parse_cards("7s7c")) in removed or tuple(parse_cards("7c7s")) in removed
    assert adv.equity(obs) > inter.equity(obs)


def facing_flop_bet(in_position: bool):
    """The agent faces a 10-chip flop bet into 20, in or out of position."""
    h = HoldemHand([1000, 1000], 0, 5, 10, deck=stacked_deck(["9c8c", "Ks4d"], "Kh7d2s5cJh", 0))
    h.apply(Action.call())
    h.apply(Action.check())
    if in_position:
        h.apply(Action.bet(10))  # big blind bets; the button (seat 0) acts last
        seat = 0
    else:
        h.apply(Action.check())
        h.apply(Action.bet(10))  # button bets; the big blind (seat 1) is out of position
        seat = 1
    return Observation.from_hand(h, seat), h.legal_actions()


def test_only_advanced_adjusts_calls_for_position():
    ip, oop = facing_flop_bet(True), facing_flop_bet(False)
    pot_odds = 10 / 40  # call 10 into a pot of 30 (20 plus the 10-chip bet)
    inter, adv = make_agent("intermediate"), make_agent("advanced")
    assert inter.required_equity(*ip) == inter.required_equity(*oop)
    assert inter.required_equity(*ip) == pytest.approx(pot_odds + INTERMEDIATE.call_margin)
    assert adv.required_equity(*ip) == pytest.approx(pot_odds)  # 0.02 margin - 0.02 position
    assert adv.required_equity(*oop) == pytest.approx(pot_odds + 0.04)


def test_all_in_calls_are_pure_pot_odds_at_every_level():
    h = river_shove()
    obs, legal = Observation.from_hand(h, 1), h.legal_actions()
    for level in LEVELS:
        assert make_agent(level).required_equity(obs, legal) == pytest.approx(
            legal.call_amount / (obs.pot + legal.call_amount)
        )


def test_beginner_limps_hands_that_stronger_levels_raise():
    hole = "Js5s"  # J5s, near the 49th percentile: a raise for the others, a limp for a beginner
    assert 0.40 < percentile(hand_class(parse_cards(hole))) < 0.60
    actions = {}
    for level in LEVELS:
        h = HoldemHand([1000, 1000], 0, 5, 10, deck=stacked_deck([hole, "2c3d"]))
        actions[level] = (
            make_agent(level, random.Random(3))
            .decide(Observation.from_hand(h, 0), h.legal_actions())
            .type
        )
    assert actions[Difficulty.BEGINNER] == ActionType.CALL
    assert actions[Difficulty.INTERMEDIATE] == ActionType.RAISE
    assert actions[Difficulty.ADVANCED] == ActionType.RAISE


# ----------------------------------------------------------------- hidden information


@pytest.mark.parametrize("level", LEVELS)
def test_decisions_never_depend_on_the_humans_hidden_cards(level):
    """Same public history, different human hole cards: identical observations and decisions."""
    seen = set()
    for human in ("AsAh", "7c2d", "QcTc"):
        h = HoldemHand([1000, 1000], 0, 5, 10, deck=stacked_deck([human, "Ks4d"], "Kh7d2s5cJh"))
        h.apply(Action.raise_to(25))
        obs = Observation.from_hand(h, 1)
        assert human[:2] not in repr(obs) and human[2:] not in repr(obs)
        action = make_agent(level, random.Random(5)).decide(obs, h.legal_actions())
        seen.add((obs, action))
    assert len(seen) == 1


# ----------------------------------------------------------------- legality and strength


@pytest.mark.parametrize("level", LEVELS)
def test_every_level_acts_legally(level):
    rng = random.Random(hash(level.value) % 1000)
    for i in range(80):
        n = rng.choice([2, 2, 3])
        agents = [
            EquityAgent(
                random.Random(rng.random()), profile=replace(PROFILES[level], iterations=150)
            )
            for _ in range(n)
        ]
        stacks = [rng.choice([3, 15, 50, 200, 1000, 5000]) for _ in range(n)]
        deck = Deck(random.Random(rng.random()))
        deck.shuffle()
        hand = play_hand(agents, stacks, i % n, deck, 5, 10)  # raises on an illegal move
        assert hand.total_chips() == sum(stacks)


def _fast(profile):
    """The level's decision logic with cheaper equity estimates, for test speed."""
    cheap = replace(profile, iterations=min(profile.iterations, 200))
    return lambda g: EquityAgent(g, profile=cheap, exact_limit=5_000)


@pytest.mark.parametrize("stronger", [INTERMEDIATE, ADVANCED])
def test_stronger_levels_beat_the_beginner(stronger):
    """A modest sample is enough for this gap (about 200 bb/100 in long runs).

    Advanced vs Intermediate is a much smaller edge. It is measured with
    ``python -m pokerlab.ai.match`` over thousands of deals, not in the unit tests.
    """
    r = duplicate_match(_fast(stronger), _fast(BEGINNER), 80, seed=31)
    assert r.bb_per_100 > 0 and r.z_score > 2.5, r


# ----------------------------------------------------------------- EV-based betting


def ev_agent(seed=0):
    return EquityAgent(random.Random(seed), profile=replace(ADVANCED, bluff_rate=0.0))


def test_ev_betting_bets_the_nuts_as_large_as_allowed():
    # Seat 1 holds the nut straight on the river; checked to it out of position.
    h = checked_down_river(hero="2c2d", ai="QsTs", board="Kh7d2sJcAh")
    obs, legal = Observation.from_hand(h, 1), h.legal_actions()
    action = ev_agent().decide(obs, legal)
    assert action.type == ActionType.BET
    # EV(bet) = P + P*B/(P+B) grows with B, so the largest candidate size wins.
    assert action.amount == ev_agent()._ev_bet(obs, legal, 1.0).amount
    assert action.amount >= round(1.0 * obs.pot) - 1


def test_ev_betting_never_pure_bluffs_and_checks_showdown_value():
    # Against a defending range at the minimum defence frequency a pure bluff is worth
    # exactly zero, so with no bluff mix the agent checks hopeless and medium hands.
    h = checked_down_river(hero="9c8c", ai="3s4d", board="Kh7d2s5cJh")  # air (busted)
    obs, legal = Observation.from_hand(h, 1), h.legal_actions()
    assert ev_agent().decide(obs, legal).type == ActionType.CHECK


def test_ev_formula_matches_the_documented_expression():
    h = checked_down_river()
    obs, legal = Observation.from_hand(h, 1), h.legal_actions()
    agent = ev_agent()
    calls = []

    def fake_equity(o, ranges=None):
        calls.append(ranges)
        return 0.9 if ranges is not None else 0.6

    agent.equity = fake_equity
    action = agent._ev_bet(obs, legal, 0.6)
    pot = obs.pot
    best = None
    for f in ADVANCED.bet_sizes:
        bet = raise_bet = max(legal.min_raise_to, round(f * pot))
        d = pot / (pot + bet)
        ev = (1 - d) * pot + d * (0.9 * (pot + 2 * bet) - bet)
        if best is None or ev > best[0]:
            best = (ev, raise_bet)
    assert best[0] > 0.6 * pot and action.amount == best[1]
    assert len(calls) == len(ADVANCED.bet_sizes)
