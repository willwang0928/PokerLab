import random

import pytest

from pokerlab.ai import HeuristicAgent, Observation, RandomAgent, make_legal
from pokerlab.ai.strength import chen_score, postflop_strength, preflop_strength
from pokerlab.cards import parse_cards
from pokerlab.engine import Action, HoldemHand, IllegalActionError


def play(agents, stacks, button, rng):
    h = HoldemHand(stacks, button, 5, 10, rng=rng)
    while not h.is_complete:
        seat = h.to_act
        legal = h.legal_actions()
        action = agents[seat].decide(Observation.from_hand(h, seat), legal)
        try:
            h.apply(action)
        except IllegalActionError as exc:  # pragma: no cover - this is the failure mode
            pytest.fail(f"{agents[seat].name} chose illegal {action} given {legal}: {exc}")
    return h


@pytest.mark.parametrize("seed", range(3))
def test_heuristic_agent_always_acts_legally(seed):
    rng = random.Random(seed)
    for _ in range(600):
        n = rng.choice([2, 2, 3])
        agents = [HeuristicAgent(random.Random(rng.random())) for _ in range(n)]
        stacks = [rng.choice([3, 10, 15, 50, 200, 1000, 5000]) for _ in range(n)]
        play(agents, stacks, rng.randrange(n), rng)


def test_heuristic_beats_random_over_many_hands():
    rng = random.Random(99)
    agents = [HeuristicAgent(random.Random(1)), RandomAgent(random.Random(2))]
    net = 0
    for i in range(1500):
        h = play(agents, [1000, 1000], i % 2, rng)
        net += h.players[0].stack - 1000
    assert net > 0


def test_preflop_strength_orders_hands():
    def s(c):
        return preflop_strength(parse_cards(c))

    assert chen_score(parse_cards("AsAd")) == 20
    assert chen_score(parse_cards("7c2d")) == -1
    assert s("AsAd") > s("AsKs") > s("JsTs") > s("9c4d") >= s("7c2d")


def test_postflop_strength_ranks_made_hands():
    board = parse_cards("Kh9d4c")
    top_pair = postflop_strength(parse_cards("KsQd"), board)
    set_ = postflop_strength(parse_cards("9s9c"), board)
    air = postflop_strength(parse_cards("2s3c"), board)
    flush_draw = postflop_strength(parse_cards("AhTh"), parse_cards("Kh9h4c"))
    assert set_ > top_pair > flush_draw > air


def test_board_playing_hand_is_weak():
    assert postflop_strength(parse_cards("2c3d"), parse_cards("AsKsQsJsTs")) < 0.3


def test_make_legal_clamps_and_substitutes():
    h = HoldemHand([1000, 1000], 0, 5, 10, rng=random.Random(0))
    legal = h.legal_actions()
    assert make_legal(Action.raise_to(5), legal) == Action.raise_to(20)
    assert make_legal(Action.bet(99999), legal) == Action.raise_to(1000)
    assert make_legal(Action.check(), legal) == Action.fold()
    h.apply(Action.call())
    legal = h.legal_actions()
    assert make_legal(Action.fold(), legal) == Action.check()
    assert make_legal(Action.call(), legal) == Action.check()
