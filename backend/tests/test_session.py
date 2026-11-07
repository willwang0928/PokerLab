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


def _play_until(session, rng, predicate, max_hands=300):
    while session.status.value != "game_over" and session.hand_number < max_hands:
        session.start_hand()
        while session.status.value == "in_hand":
            legal = session.view()["legal"]
            # Never fold, so most hands reach a showdown.
            action = Action.check() if legal["can_check"] else Action.call()
            session.act(action)
        if predicate(session.result):
            return session.result
    raise AssertionError("no matching hand")


def test_showdown_result_includes_equity_by_street():
    s = HeadsUpSession(seed=21)
    result = _play_until(s, random.Random(0), lambda r: r["showdown"] is not None)
    rows = result["equity"]
    assert [row["street"] for row in rows] == ["preflop", "flop", "turn"]
    assert rows[0]["method"] == "monte_carlo" and rows[1]["method"] == "exact"
    for row in rows:
        assert set(row["equity"]) == {HUMAN, AI}
        assert sum(row["equity"].values()) == pytest.approx(1.0)
    assert all(v == 0.0 for row in rows[1:] for v in row["std_error"].values())
    assert rows[0]["std_error"][HUMAN] > 0


def test_equity_summary_is_reproducible_in_seeded_sessions():
    def first_showdown_equity(seed):
        s = HeadsUpSession(seed=seed)
        return _play_until(s, random.Random(0), lambda r: r["showdown"] is not None)["equity"]

    assert first_showdown_equity(33) == first_showdown_equity(33)


def test_uncontested_hand_has_no_equity_summary():
    s = HeadsUpSession(seed=5)
    result = None
    while result is None or not result["uncontested"]:
        s.start_hand()
        while s.status.value == "in_hand":
            legal = s.view()["legal"]
            s.act(Action.fold() if legal["can_fold"] else Action.check())
        result = s.result
    assert result["equity"] is None


def test_difficulty_selects_the_opponent_profile():
    from pokerlab.ai.difficulty import PROFILES, Difficulty

    for level in Difficulty:
        s = HeadsUpSession(config=SessionConfig(difficulty=level), seed=1)
        assert s.agent.profile is PROFILES[level]
        assert s.view()["config"]["difficulty"] == level.value
    assert HeadsUpSession(seed=1).view()["config"]["difficulty"] == "intermediate"
    with pytest.raises(ValueError):
        SessionConfig(difficulty="expert")


def _shove_until_called(session, max_hands=200):
    """Shove every hand until the opponent calls; returns that hand's steps."""
    while session.hand_number < max_hands and session.status.value != "game_over":
        steps = session.start_hand()
        while session.status.value == "in_hand" and session.view()["legal"]:
            steps = steps + session.act(Action("all_in"))
        if any(st.event["type"] == "reveal" for st in steps):
            return steps
    raise AssertionError("the opponent never called a shove")


def test_live_equity_appears_only_after_an_all_in_reveal_and_grows_by_street():
    s = HeadsUpSession(config=SessionConfig(difficulty="beginner"), seed=8)
    steps = _shove_until_called(s)
    reveal = next(i for i, st in enumerate(steps) if st.event["type"] == "reveal")
    assert all(st.state["live_equity"] is None for st in steps[:reveal])
    live = [st.state["live_equity"] for st in steps[reveal:] if st.state["status"] == "in_hand"]
    counts = [len(rows) for rows in live]
    assert counts[0] >= 1 and counts == sorted(counts) and counts[-1] == 3
    final = steps[-1].state
    assert final["live_equity"] is None and final["status"] != "in_hand"
    # The review shows exactly the numbers the live display showed (shared cache).
    assert final["result"]["equity"] == live[-1]


def test_no_live_equity_at_an_ordinary_showdown():
    s = HeadsUpSession(seed=21)
    for _ in range(100):
        steps = s.start_hand()
        while s.status.value == "in_hand":
            legal = s.view()["legal"]
            steps = steps + s.act(Action.check() if legal["can_check"] else Action.call())
        if s.result["showdown"] and not any(st.event["type"] == "reveal" for st in steps):
            break
    assert s.result["equity"] is not None
    assert all(st.state["live_equity"] is None for st in steps)
