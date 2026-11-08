"""Adaptive opponent: stat tracking, estimation, adjustments, decisions, leakage, legality."""

import random
from dataclasses import replace

import pytest

from pokerlab.ai import Observation
from pokerlab.ai.adaptation import (
    DEADBAND,
    NEUTRAL,
    Adjustments,
    adaptation_policy,
    compute_adjustments,
)
from pokerlab.ai.difficulty import ADVANCED, INTERMEDIATE, make_agent
from pokerlab.ai.equity_agent import EquityAgent
from pokerlab.ai.match import paired_comparison, play_hand
from pokerlab.ai.player_model import (
    BASELINES,
    DECAY,
    MIN_OPPORTUNITIES,
    PRIOR_STRENGTH,
    PlayerModel,
    Stat,
    observe_hand,
)
from pokerlab.ai.styles import STYLES, StyleAgent
from pokerlab.cards import Deck
from pokerlab.engine import Action, ActionType, HoldemHand
from pokerlab.session import AI, HUMAN, HeadsUpSession, SessionConfig

from .helpers import stacked_deck


def scripted(actions, holes=("AsAd", "KsKd"), board="2c7h9dJcQh", button=0):
    h = HoldemHand([1000, 1000], button, 5, 10, deck=stacked_deck(list(holes), board, button))
    for a in actions:
        h.apply(a)
    assert h.is_complete
    return h


def obs_of(hand, seat):
    return sorted(observe_hand(hand.events, seat))


# ----------------------------------------------------------------- stat tracking


def test_preflop_raise_counts_vpip_and_pfr_and_a_called_bet_is_not_folded():
    # Seat 0 (button) raises, seat 1 calls; flop: 1 checks, 0 bets, 1 folds.
    h = scripted(
        [Action.raise_to(30), Action.call(), Action.check(), Action.bet(40), Action.fold()]
    )
    # Both saw the flop and neither reached showdown.
    assert obs_of(h, 0) == sorted(
        [
            ("vpip", True),
            ("pfr", True),
            ("aggression", True),
            ("bet_unopposed", True),  # bet when checked to
            ("wtsd", False),
        ]
    )
    assert obs_of(h, 1) == sorted(
        [
            ("vpip", True),
            ("pfr", False),
            ("fold_to_raise_pre", False),  # called the raise
            ("bet_unopposed", False),  # checked the flop
            ("aggression", False),  # the fold counts as a non-aggressive action
            ("fold_to_bet", True),
            ("raise_vs_bet", False),
            ("wtsd", False),
        ]
    )


def test_checking_the_big_blind_is_not_vpip_and_a_walk_is_no_opportunity():
    limp_check = scripted([Action.call(), Action.check()] + [Action.check()] * 6)
    assert ("vpip", False) in obs_of(limp_check, 1)  # big blind checked its option
    assert ("vpip", True) in obs_of(limp_check, 0)  # the limp is voluntary
    walk = scripted([Action.fold()])
    assert obs_of(walk, 1) == []  # never acted
    # Folding the small blind is not "folding to a raise": nobody raised.
    assert obs_of(walk, 0) == sorted([("vpip", False), ("pfr", False)])


def test_fold_to_preflop_raise_and_conditional_postflop_stats():
    three_bet_fold = scripted([Action.raise_to(30), Action.raise_to(90), Action.fold()])
    assert ("fold_to_raise_pre", True) in obs_of(three_bet_fold, 0)
    assert ("fold_to_raise_pre", False) in obs_of(three_bet_fold, 1)  # raised the raise
    # Flop: seat 1 checks (could bet), seat 0 bets, seat 1 raises, seat 0 calls.
    h = scripted(
        [Action.call(), Action.check(), Action.check(), Action.bet(20), Action.raise_to(60)]
        + [Action.call()]
        + [Action.check()] * 4
    )
    assert ("bet_unopposed", False) in obs_of(h, 1) and ("raise_vs_bet", True) in obs_of(h, 1)
    assert ("bet_unopposed", True) in obs_of(h, 0) and ("raise_vs_bet", False) in obs_of(h, 0)


def test_checks_are_not_aggression_opportunities_and_showdown_is_tracked():
    h = scripted([Action.call(), Action.check()] + [Action.check()] * 6)
    for seat in (0, 1):
        o = dict(obs_of(h, seat))
        assert "aggression" not in o and "fold_to_bet" not in o
        assert o["bet_unopposed"] is False  # checked when it could bet
        assert o["wtsd"] is True
    assert ("wsd", True) in obs_of(h, 0)  # aces win
    assert ("wsd", False) in obs_of(h, 1)


def test_fold_to_bet_only_counts_spots_facing_a_bet():
    # Flop: seat 1 bets, seat 0 raises, seat 1 folds -> one fold-to-bet opportunity each.
    h = scripted(
        [Action.call(), Action.check(), Action.bet(20), Action.raise_to(60), Action.fold()]
    )
    assert ("fold_to_bet", False) in obs_of(h, 0)  # raised when facing the bet
    assert ("fold_to_bet", True) in obs_of(h, 1)
    assert sum(1 for k, _ in obs_of(h, 1) if k == "fold_to_bet") == 1


# ----------------------------------------------------------------- estimation


def test_estimate_is_the_documented_beta_posterior_mean():
    s = Stat(count=6, opportunities=10)
    assert s.estimate(0.4) == pytest.approx((6 + 0.4 * PRIOR_STRENGTH) / (10 + PRIOR_STRENGTH))
    assert Stat().estimate(0.4) == pytest.approx(0.4)  # no data: the baseline


def test_estimates_move_gradually_and_reliability_needs_a_minimum_sample():
    m = PlayerModel()
    h = scripted(
        [Action.call(), Action.check(), Action.bet(20), Action.fold()]
    )  # seat 0 folds to a bet
    estimates = []
    for _ in range(40):
        m.observe(h.events, 0)
        estimates.append(m.estimate("fold_to_bet"))
    assert all(a < b for a, b in zip(estimates, estimates[1:], strict=False))
    assert estimates[0] < 0.5 and estimates[-1] > 0.75  # shrunk early, convinced later
    first_reliable = next(i for i in range(40) if _opps_after(i + 1) >= MIN_OPPORTUNITIES)
    m2 = PlayerModel()
    for i in range(40):
        m2.observe(h.events, 0)
        assert m2.reliable("fold_to_bet") == (i >= first_reliable)


def _opps_after(n):
    return sum(DECAY**k for k in range(n))


def test_decay_lets_the_model_follow_a_change_of_style():
    folds = scripted([Action.call(), Action.check(), Action.bet(20), Action.fold()])
    calls = scripted(
        [Action.call(), Action.check(), Action.bet(20), Action.call()] + [Action.check()] * 4
    )
    m = PlayerModel()
    for _ in range(200):
        m.observe(folds.events, 0)
    assert m.estimate("fold_to_bet") > 0.85
    for _ in range(300):
        m.observe(calls.events, 0)
    assert m.estimate("fold_to_bet") < 0.25


# ----------------------------------------------------------------- adjustments


def trained(hand, seat, n=60):
    m = PlayerModel()
    for _ in range(n):
        m.observe(hand.events, seat)
    return m


FOLDER = scripted([Action.call(), Action.check(), Action.bet(20), Action.fold()])
CALLER = scripted(
    [Action.call(), Action.check(), Action.bet(20), Action.call()] + [Action.check()] * 4
)


def test_no_adjustment_before_the_minimum_sample_or_at_weight_zero():
    assert compute_adjustments(trained(FOLDER, 0, n=5), 1.0) == NEUTRAL
    assert compute_adjustments(trained(FOLDER, 0), 0.0) == NEUTRAL
    assert compute_adjustments(PlayerModel(), 1.0).neutral


def test_small_deviations_inside_the_deadband_are_ignored():
    m = PlayerModel()
    m.stats["fold_to_bet"] = Stat(
        count=200 * (BASELINES["fold_to_bet"] + DEADBAND / 2), opportunities=200
    )
    assert "defends" not in compute_adjustments(m, 1.0).reasons


def test_folder_and_caller_produce_opposite_bounded_adjustments():
    vs_folder = compute_adjustments(trained(FOLDER, 0), 1.0)
    vs_caller = compute_adjustments(trained(CALLER, 0), 1.0)
    # A folder's calls are read as stronger, a caller's as wider (both clamped).
    assert 0.6 <= vs_folder.call_range_scale < 1 < vs_caller.call_range_scale <= 1.3
    assert "stronger" in vs_folder.reasons["defends"]
    # Against a caller it bluffs less and bets thinner; against a folder it does NOT
    # bluff more (that direction lost in controlled matches and was removed).
    assert vs_folder.fold_shift == 0 and vs_folder.bluff_multiplier == 1
    assert -0.25 <= vs_caller.fold_shift < 0 and vs_caller.bluff_multiplier < 1
    assert vs_caller.thin_value_shift > 0 and "value" in vs_caller.reasons
    assert 0.05 <= vs_caller.fold_probability(100, 100) < 0.5


def test_half_weight_adapts_half_as_far():
    full = compute_adjustments(trained(FOLDER, 0), 1.0)
    half = compute_adjustments(trained(FOLDER, 0), 0.5)
    assert full.raise_range_scale < half.raise_range_scale < 1
    assert half.raise_range_scale == pytest.approx(full.raise_range_scale**0.5, rel=0.02) or (
        full.raise_range_scale == 0.4  # clamped
    )


def test_policies_describe_each_level():
    assert "does not track" in adaptation_policy("beginner")
    assert "half strength" in adaptation_policy("intermediate")
    assert "fully" in adaptation_policy("advanced")
    assert "switched off" in adaptation_policy("advanced", enabled=False)


# ----------------------------------------------------------------- decisions


def checked_to_river(ai="3s4d", hero="9c8c", board="Kh7d2s5cJh"):
    h = HoldemHand([1000, 1000], 0, 5, 10, deck=stacked_deck([hero, ai], board, 0))
    h.apply(Action.call())
    h.apply(Action.check())
    for _ in range(2):
        h.apply(Action.check())
        h.apply(Action.check())
    return Observation.from_hand(h, 1), h.legal_actions()


def adv(adjustments, seed=0):
    agent = EquityAgent(random.Random(seed), profile=replace(ADVANCED, bluff_rate=0.0))
    agent.adjustments = adjustments
    return agent


def test_advanced_stops_bluffing_a_caller_but_not_more_against_a_folder():
    obs, legal = checked_to_river()  # busted air on the river
    assert adv(NEUTRAL).decide(obs, legal).type == ActionType.CHECK
    folder = compute_adjustments(trained(FOLDER, 0), 1.0)
    assert adv(folder).decide(obs, legal).type == ActionType.CHECK
    caller = Adjustments(fold_shift=-0.25, reasons={"value": "x"})
    assert adv(caller).decide(obs, legal).type == ActionType.CHECK
    # The mechanism itself still works if a positive shift is ever passed in.
    assert adv(Adjustments(fold_shift=0.25, reasons={"x": "x"})).decide(obs, legal).type == (
        ActionType.BET
    )


def test_advanced_calls_wider_against_aggression_and_tighter_against_passivity():
    from .test_difficulty import river_shove

    h = river_shove()
    obs, legal = Observation.from_hand(h, 1), h.legal_actions()
    need = legal.call_amount / (obs.pot + legal.call_amount)
    base = adv(NEUTRAL).equity(obs)
    aggressive = adv(Adjustments(bet_range_scale=1.6, reasons={"calls": "x"})).equity(obs)
    passive = adv(Adjustments(bet_range_scale=0.5, reasons={"calls": "x"})).equity(obs)
    assert passive < base < need < aggressive
    assert adv(NEUTRAL).decide(obs, legal).type == ActionType.FOLD
    assert (
        adv(Adjustments(bet_range_scale=1.6, reasons={"calls": "x"})).decide(obs, legal).type
        == ActionType.CALL
    )


def test_counterfactual_decision_does_not_consume_randomness():
    obs, legal = checked_to_river()
    agent = adv(Adjustments(fold_shift=0.25, reasons={"bluff": "x"}), seed=4)
    state = agent.rng.getstate()
    agent.decide_without_adaptation(obs, legal)
    assert agent.rng.getstate() == state
    assert agent.adjustments.fold_shift == 0.25


# ----------------------------------------------------------------- information leakage


def test_stats_do_not_depend_on_hidden_or_shown_cards():
    actions = [Action.raise_to(30), Action.call()] + [Action.check()] * 6
    seen = set()
    for holes in (("AsAd", "KsKd"), ("7c2d", "8h3s"), ("QhJh", "Tc9c")):
        h = scripted(
            actions, holes=holes, board="2c7h9dJcQh" if "Jh" not in holes[0] else "2c7h9d4c5s"
        )
        seen.add(tuple(o for o in obs_of(h, 0) if o[0] != "wsd"))
    assert len(seen) == 1  # identical actions -> identical observations (besides who won)


def test_trained_agent_decisions_ignore_the_humans_hidden_cards():
    for level in ("intermediate", "advanced"):
        decisions = set()
        for human in ("AsAh", "7c2d", "QcTc"):
            agent = make_agent(level, random.Random(5))
            agent.model = trained(FOLDER, 0)
            agent.refresh_adjustments()
            h = HoldemHand([1000, 1000], 0, 5, 10, deck=stacked_deck([human, "Ks4d"], "Kh7d2s5cJh"))
            h.apply(Action.raise_to(25))
            decisions.add(agent.decide(Observation.from_hand(h, 1), h.legal_actions()))
        assert len(decisions) == 1


# ----------------------------------------------------------------- legality and harness


@pytest.mark.parametrize("style", list(STYLES))
def test_adaptive_agents_act_legally_against_every_style(style):
    rng = random.Random(len(style))
    agent = EquityAgent(random.Random(1), profile=replace(ADVANCED, iterations=150))
    opponent = StyleAgent(style, random.Random(2))
    for i in range(60):
        deck = Deck(random.Random(rng.random()))
        deck.shuffle()
        agents = [agent, opponent] if i % 2 else [opponent, agent]
        stacks = [rng.choice([20, 200, 1000]), rng.choice([20, 200, 1000])]
        hand = play_hand(agents, stacks, i % 2, deck, 5, 10)
        assert hand.total_chips() == sum(stacks)
        agent.observe_hand(hand.events, agents.index(agent))
    assert agent.model.hands == 60


def test_styles_show_their_intended_leaks():
    rates = {}
    for style in STYLES:
        m = PlayerModel()
        me = EquityAgent(random.Random(1), profile=replace(INTERMEDIATE, iterations=150))
        opp = StyleAgent(style, random.Random(2))
        rng = random.Random(3)
        for i in range(80):
            deck = Deck(random.Random(rng.random()))
            deck.shuffle()
            agents = [me, opp] if i % 2 else [opp, me]
            h = play_hand(agents, [1000, 1000], i % 2, deck, 5, 10)
            m.observe(h.events, agents.index(opp))
        rates[style] = {k: s.rate for k, s in m.stats.items()}
    assert rates["aggressive"]["aggression"] > 0.5 > rates["passive"]["aggression"]
    assert rates["folder"]["fold_to_bet"] > 0.7 > rates["passive"]["fold_to_bet"]
    assert rates["aggressive"]["pfr"] > 0.5 > rates["passive"]["pfr"]


def test_paired_comparison_of_identical_agents_is_exactly_zero():
    r = paired_comparison(
        lambda g: StyleAgent("aggressive", g),
        lambda g: StyleAgent("aggressive", g),
        lambda g: StyleAgent("passive", g),
        20,
        seed=2,
    )
    assert r.difference.net_chips == 0 and r.difference.std_error == 0
    assert r.a == r.b


# ----------------------------------------------------------------- session


def play_session(difficulty, adaptive=True, hands=35, seed=3):
    s = HeadsUpSession(
        config=SessionConfig(difficulty=difficulty, adaptive=adaptive, starting_stack=100_000),
        seed=seed,
    )
    rng = random.Random(1)
    notes = []
    for _ in range(hands):
        s.start_hand()
        while s.status.value == "in_hand":
            lg = s.view()["legal"]
            if lg["can_raise"] and rng.random() < 0.7:
                s.act(Action(lg["raise_type"], lg["min_raise_to"]))
            else:
                s.act(Action.call() if lg["can_call"] else Action.check())
        notes += s.result["adaptation_notes"]
        if s.status.value == "game_over":
            break
    return s, notes


def test_session_tracks_the_human_and_explains_changed_decisions():
    s, notes = play_session("advanced")
    model = s.view()["opponent_model"]
    assert model["adapts"] and model["hands"] == s.hand_number
    assert {st["key"] for st in model["stats"]} == set(BASELINES)
    assert model["adjustments"], "an aggressive human should trigger adjustments"
    assert notes and all("instead of" in n for n in notes)
    assert all("You " in n for n in notes)  # every change names an observed tendency


def test_beginner_and_switched_off_adaptation_never_change_decisions():
    for difficulty, adaptive in (("beginner", True), ("advanced", False)):
        s, notes = play_session(difficulty, adaptive=adaptive, hands=25)
        model = s.view()["opponent_model"]
        assert not model["adapts"] and model["adjustments"] == [] and notes == []
        assert model["hands"] == s.hand_number  # stats are still tracked for the player


def test_session_model_only_sees_the_human_seat():
    s, _ = play_session("intermediate", hands=10)
    assert s.agent.model.hands == s.hand_number
    assert HUMAN != AI
