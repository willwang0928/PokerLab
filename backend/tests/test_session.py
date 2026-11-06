import random

import pytest

from pokerlab.ai import RandomAgent
from pokerlab.engine import Action, IllegalActionError
from pokerlab.session import AI, HUMAN, HeadsUpSession, SessionConfig, SessionStateError


def random_human_action(rng, legal):
    options = []
    if legal["can_fold"]:
        options.append(Action.fold())
    if legal["can_check"]:
        options.append(Action.check())
    if legal["can_call"]:
        options.append(Action.call())
    if legal["can_raise"]:
        amount = rng.randint(legal["min_raise_to"], legal["max_raise_to"])
        options.append(Action(legal["raise_type"], amount))
    return rng.choice(options)


def test_session_alternates_button_and_conserves_chips():
    s = HeadsUpSession(seed=1)
    rng = random.Random(1)
    buttons = []
    while s.status.value != "game_over" and s.hand_number < 200:
        s.start_hand()
        buttons.append(s.hand.button)
        while s.status.value == "in_hand":
            s.act(random_human_action(rng, s.view()["legal"]))
        assert sum(s.stacks) == 2000
    assert all(a != b for a, b in zip(buttons, buttons[1:], strict=False))


def test_game_over_declares_winner_and_blocks_new_hands():
    s = HeadsUpSession(config=SessionConfig(starting_stack=20), seed=4)
    rng = random.Random(2)
    while s.status.value != "game_over":
        s.start_hand()
        while s.status.value == "in_hand":
            s.act(random_human_action(rng, s.view()["legal"]))
    view = s.view()
    assert view["winner"] in (HUMAN, AI)
    assert view["players"][view["winner"]]["stack"] == 40
    with pytest.raises(SessionStateError):
        s.start_hand()


def test_ai_cards_hidden_until_showdown():
    s = HeadsUpSession(seed=11, agent=RandomAgent(random.Random(0)))
    for _ in range(30):
        steps = s.start_hand()
        while True:
            for step in steps:
                players = step.state["players"]
                assert players[HUMAN]["hole"] is not None
                shown = (
                    step.event["type"] in ("reveal", "showdown")
                    or step.state["players"][AI]["hole"] is None
                    or any(e.event["type"] in ("reveal", "showdown") for e in steps)
                )
                assert shown
            if s.status.value != "in_hand":
                break
            legal = s.view()["legal"]
            steps = s.act(Action.call() if legal["can_call"] else Action.check())
        if s.status.value == "game_over":
            break


def test_ai_cards_never_shown_after_fold():
    s = HeadsUpSession(seed=5)
    for _ in range(20):
        s.start_hand()
        if s.status.value == "in_hand" and s.view()["legal"]["can_fold"]:
            s.act(Action.fold())
            assert s.view()["players"][AI]["hole"] is None
            assert s.result["uncontested"]
            return
        while s.status.value == "in_hand":
            legal = s.view()["legal"]
            s.act(Action.fold() if legal["can_fold"] else Action.check())
    pytest.fail("never got a chance to fold")


def test_state_errors():
    s = HeadsUpSession(seed=2)
    with pytest.raises(SessionStateError):
        s.act(Action.check())
    s.start_hand()
    if s.status.value == "in_hand":
        with pytest.raises(SessionStateError):
            s.start_hand()
        with pytest.raises(IllegalActionError):
            s.act(Action.raise_to(1))


def test_log_is_human_readable():
    s = HeadsUpSession(seed=3)
    s.start_hand()
    assert s.log[0].startswith("Hand #1")
    assert any("blind" in line for line in s.log)


def test_showdown_exposes_the_winning_cards():
    s = HeadsUpSession(seed=3, agent=RandomAgent(random.Random(1)))
    seen = 0
    for _ in range(60):
        steps = s.start_hand()
        while s.status.value == "in_hand":
            legal = s.view()["legal"]
            steps += s.act(Action.call() if legal["can_call"] else Action.check())
        showdown = next((st.event for st in steps if st.event["type"] == "showdown"), None)
        awards = [st.event for st in steps if st.event["type"] == "pot_awarded"]
        if showdown is None:
            assert all(a["cards"] == [] for a in awards)
        else:
            seen += 1
            board = set(s.view()["board"])
            for hand in showdown["hands"].values():
                assert len(hand["best"]) == 5
                assert set(hand["best"]) <= board | set(hand["cards"])
            for a in awards:
                expected = {c for w in a["winners"] for c in showdown["hands"][w]["best"]}
                assert set(a["cards"]) == expected
            assert s.result["pots"][0]["cards"] == awards[0]["cards"]
            assert s.result["showdown"][int(next(iter(showdown["hands"])))]["best"]
        if s.status.value == "game_over":
            break
    assert seen > 0
