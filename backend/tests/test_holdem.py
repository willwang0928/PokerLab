import random

import pytest

from pokerlab.engine import Action, ActionType, HoldemHand, IllegalActionError, Street

from .helpers import stacked_deck


def hu(stacks=(1000, 1000), button=0, holes=("AsAd", "KsKd"), board="2c7h9dJcQh", **kw):
    deck = stacked_deck(list(holes), board, button)
    return HoldemHand(list(stacks), button, 5, 10, deck=deck, **kw)


def events(hand, kind):
    return [e.data for e in hand.events if e.type == kind]


# ----------------------------------------------------------------- dealing & blinds


def test_heads_up_button_posts_small_blind_and_acts_first_preflop():
    h = hu(button=0)
    assert [p.bet for p in h.players] == [5, 10]
    assert h.to_act == 0
    assert h.street == Street.PREFLOP
    la = h.legal_actions()
    assert (la.can_call, la.call_amount, la.min_raise_to, la.max_raise_to) == (True, 5, 20, 1000)
    assert la.raise_type == ActionType.RAISE


def test_deals_specified_cards_and_board():
    h = hu(holes=("AsAd", "KsKd"))
    assert [c.code for c in h.players[0].hole] == ["As", "Ad"]
    assert [c.code for c in h.players[1].hole] == ["Ks", "Kd"]
    h.apply(Action.call())
    h.apply(Action.check())
    assert [c.code for c in h.board] == ["2c", "7h", "9d"]


def test_random_deals_have_no_duplicate_cards():
    rng = random.Random(5)
    for _ in range(200):
        h = HoldemHand([100] * 6, 0, 1, 2, rng=rng)
        cards = [c for p in h.players for c in p.hole]
        assert len(set(cards)) == 12


def test_big_blind_gets_option_and_postflop_order():
    h = hu(button=0)
    h.apply(Action.call())
    assert h.to_act == 1
    la = h.legal_actions()
    assert la.can_check and la.can_raise and la.min_raise_to == 20
    h.apply(Action.check())
    assert h.street == Street.FLOP
    assert h.to_act == 1  # out-of-position big blind acts first after the flop
    assert h.legal_actions().raise_type == ActionType.BET
    h.apply(Action.check())
    assert h.to_act == 0
    h.apply(Action.check())
    assert h.street == Street.TURN and len(h.board) == 4
    h.apply(Action.check())
    h.apply(Action.check())
    assert h.street == Street.RIVER and len(h.board) == 5


def test_three_handed_blind_positions():
    h = HoldemHand([100, 100, 100], 0, 1, 2, rng=random.Random(1))
    assert [p.bet for p in h.players] == [0, 1, 2]
    assert h.to_act == 0  # under the gun is the button when three-handed


# ----------------------------------------------------------------- betting rules


def test_min_raise_tracks_last_raise_size():
    h = hu()
    h.apply(Action.call())
    h.apply(Action.check())
    h.apply(Action.bet(30))
    la = h.legal_actions()
    assert la.min_raise_to == 60 and la.call_amount == 30
    h.apply(Action.raise_to(100))  # raise of 70
    la = h.legal_actions()
    assert la.min_raise_to == 170
    assert la.can_raise  # full raise re-opens action


def test_minimum_bet_is_big_blind():
    h = hu()
    h.apply(Action.call())
    h.apply(Action.check())
    with pytest.raises(IllegalActionError):
        h.apply(Action.bet(5))
    h.apply(Action.bet(10))
    assert h.current_bet == 10


@pytest.mark.parametrize(
    "action,msg",
    [
        (Action.check(), "Cannot check"),
        (Action.bet(40), "did you mean raise"),
        (Action.raise_to(15), "between 20 and 1000"),
        (Action.raise_to(1001), "between 20 and 1000"),
        (Action(ActionType.RAISE, None), "integer amount"),
    ],
)
def test_illegal_actions_are_rejected_without_changing_state(action, msg):
    h = hu()
    before = [(p.stack, p.bet) for p in h.players]
    with pytest.raises(IllegalActionError, match=msg):
        h.apply(action)
    assert [(p.stack, p.bet) for p in h.players] == before
    assert h.to_act == 0


def test_cannot_fold_when_check_is_available():
    h = hu()
    h.apply(Action.call())
    with pytest.raises(IllegalActionError):
        h.apply(Action.fold())


def test_no_action_after_hand_complete():
    h = hu()
    h.apply(Action.fold())
    assert h.is_complete
    with pytest.raises(IllegalActionError):
        h.apply(Action.check())
    with pytest.raises(IllegalActionError):
        h.legal_actions()


def test_raise_is_capped_at_what_opponent_can_cover():
    h = hu(stacks=(1000, 300))
    la = h.legal_actions()
    assert la.max_raise_to == 300
    h.apply(Action.all_in())
    assert h.players[0].bet == 300 and h.players[0].stack == 700
    h.apply(Action.call())
    assert h.is_complete
    assert h.players[0].stack == 1300 and h.players[1].stack == 0


def test_incomplete_all_in_raise_does_not_reopen_betting():
    # Button 0 (UTG), seat 1 small blind, seat 2 big blind with a short stack.
    h = HoldemHand([1000, 1000, 25], 0, 5, 10, rng=random.Random(3))
    h.apply(Action.raise_to(20))
    h.apply(Action.call())
    assert h.to_act == 2
    la = h.legal_actions()
    assert la.can_raise and la.max_raise_to == 25 and la.min_raise_to == 25
    h.apply(Action.all_in())  # raise of 5 < last raise of 10
    assert h.to_act == 0
    la = h.legal_actions()
    assert la.call_amount == 5 and not la.can_raise
    h.apply(Action.call())
    assert not h.legal_actions().can_raise
    h.apply(Action.call())
    assert h.street == Street.FLOP


def test_full_raise_reopens_for_player_who_already_acted():
    h = HoldemHand([1000, 1000, 1000], 0, 5, 10, rng=random.Random(3))
    h.apply(Action.call())  # button limps
    h.apply(Action.raise_to(40))  # small blind raises
    h.apply(Action.call())  # big blind calls
    assert h.to_act == 0
    assert h.legal_actions().can_raise


# ----------------------------------------------------------------- endings & pots


def test_fold_awards_pot_and_returns_uncalled_bet():
    h = hu()
    h.apply(Action.raise_to(50))
    h.apply(Action.fold())
    assert h.is_complete
    assert [p.stack for p in h.players] == [1010, 990]
    assert events(h, "uncalled_returned") == [{"seat": 0, "amount": 40}]
    assert h.winnings == {0: 20}


def test_all_in_preflop_runs_out_board_and_best_hand_wins():
    h = hu(holes=("AsAd", "KsKd"), board="2c7h9dJcQh")
    h.apply(Action.all_in())
    h.apply(Action.call())
    assert h.is_complete
    assert len(h.board) == 5
    assert [p.stack for p in h.players] == [2000, 0]
    assert events(h, "reveal")
    assert [e.data["street"] for e in h.events if e.type == "street"] == ["flop", "turn", "river"]


def test_underdog_can_win_on_the_river():
    h = hu(holes=("AsAd", "KsKd"), board="2c7h9dJcKh")
    h.apply(Action.all_in())
    h.apply(Action.call())
    assert [p.stack for p in h.players] == [0, 2000]
    award = events(h, "pot_awarded")[0]
    assert award["winners"] == [1] and award["hand"] == "Three of a Kind, Kings"


def test_split_pot_when_board_plays():
    h = hu(holes=("2c3d", "2h4d"), board="AsKsQsJsTs")
    for a in (Action.call(), Action.check()):
        h.apply(a)
    while not h.is_complete:
        h.apply(Action.check())
    assert [p.stack for p in h.players] == [1000, 1000]
    assert events(h, "pot_awarded")[0]["winners"] == [0, 1]


def test_odd_chip_goes_to_first_winner_left_of_button():
    deck = stacked_deck(["2c3d", "7h8h", "2h3s"], "AsKsQsJsTs", button=0)
    h = HoldemHand([1000, 1000, 1000], 0, 5, 10, deck=deck)
    h.apply(Action.call())  # seat 0
    h.apply(Action.fold())  # seat 1 folds small blind
    h.apply(Action.check())  # seat 2
    while not h.is_complete:
        h.apply(Action.check())
    assert h.winnings == {0: 12, 2: 13}  # 25 chip pot; seat 2 is first left of button


def test_side_pot_three_way():
    # Seat 0 short with the best hand wins only the main pot.
    deck = stacked_deck(["AsAd", "KsKd", "QsQd"], "2c7h9d3c4h", button=2)
    h = HoldemHand([100, 300, 300], 2, 5, 10, deck=deck)
    # button 2 -> SB seat 0, BB seat 1, UTG seat 2
    h.apply(Action.raise_to(300))
    h.apply(Action.all_in())  # seat 0 all-in for 100
    h.apply(Action.call())  # seat 1 calls 300
    assert h.is_complete
    assert [p.stack for p in h.players] == [300, 400, 0]


def test_short_small_blind_all_in_runs_out_immediately():
    h = hu(stacks=(3, 1000))
    assert h.is_complete
    assert events(h, "uncalled_returned") == [{"seat": 1, "amount": 7}]
    assert sum(p.stack for p in h.players) == 1003
    assert h.players[0].stack == 6  # aces hold


def test_short_big_blind_all_in():
    h = hu(stacks=(1000, 4), holes=("7c2d", "AsAd"), board="3c8h9dJcKh")
    assert h.is_complete
    assert [p.stack for p in h.players] == [996, 8]


def test_events_record_every_action():
    h = hu()
    h.apply(Action.raise_to(30))
    h.apply(Action.call())
    acts = events(h, "action")
    assert [(a["seat"], a["action"], a["amount"]) for a in acts] == [
        (0, "raise", 30),
        (1, "call", 20),
    ]


# ----------------------------------------------------------------- fuzzing


def _random_action(rng, la):
    options = []
    if la.can_fold:
        options.append(Action.fold())
    if la.can_check:
        options.append(Action.check())
    if la.can_call:
        options.append(Action.call())
    if la.can_raise:
        options.append(Action(la.raise_type, rng.randint(la.min_raise_to, la.max_raise_to)))
        options.append(Action.all_in())
    return rng.choice(options)


@pytest.mark.parametrize("seed", range(5))
def test_fuzz_chip_conservation_and_invariants(seed):
    rng = random.Random(seed)
    for _ in range(1500):
        n = rng.choice([2, 2, 3, 4, 6])
        stacks = [rng.choice([1, 4, 5, 9, 10, 23, 100, 500]) for _ in range(n)]
        h = HoldemHand(stacks, rng.randrange(n), 5, 10, rng=rng)
        total = sum(stacks)
        while not h.is_complete:
            la = h.legal_actions()
            assert la.can_check != la.can_call
            if la.can_raise:
                assert la.min_raise_to <= la.max_raise_to
            h.apply(_random_action(rng, la))
            assert h.total_chips() == total
            assert all(p.stack >= 0 for p in h.players)
        assert sum(p.stack for p in h.players) == total
        assert sum(h.winnings.values()) > 0
