"""HTTP endpoints for hand reviews and training."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from pokerlab.api.app import create_app


@pytest.fixture
def client():
    return TestClient(create_app(allow_seed=True))


def play_a_hand(client, sid, policy="call"):
    state = client.post(f"/api/sessions/{sid}/hands").json()["state"]
    while state["status"] == "in_hand":
        legal = state["legal"]
        action = "check" if legal["can_check"] else policy
        state = client.post(f"/api/sessions/{sid}/actions", json={"action": action}).json()["state"]
    return state


def test_review_of_a_finished_hand(client):
    sid = client.post("/api/sessions", json={"seed": 3}).json()["state"]["session_id"]
    assert client.get(f"/api/sessions/{sid}/hands/1/review").status_code == 404
    for _ in range(6):  # some hands end before the human acts (the bot folds)
        state = play_a_hand(client, sid)
        n = state["hand_number"]
        r = client.get(f"/api/sessions/{sid}/hands/{n}/review")
        assert r.status_code == 200
        review = r.json()
        assert review["hand_number"] == n
        assert isinstance(review["verdict"], str) and review["verdict"]
        for d in review["decisions"]:
            assert d["grade"] in ("good", "close", "questionable", "mistake", "ungraded")
            assert d["spot_data"]["hero"] == "".join(d["hole"])
        if review["decisions"]:
            break
    assert review["decisions"], "expected at least one graded decision in six hands"
    # An older hand is no longer reviewable once a newer one finishes.
    play_a_hand(client, sid)
    assert client.get(f"/api/sessions/{sid}/hands/{n}/review").status_code == 404


def test_review_is_not_available_mid_hand(client):
    sid = client.post("/api/sessions", json={"seed": 5}).json()["state"]["session_id"]
    for _ in range(10):
        state = client.post(f"/api/sessions/{sid}/hands").json()["state"]
        if state["status"] == "in_hand":
            break
        # the bot ended the hand immediately; try the next one
    n = state["hand_number"]
    assert client.get(f"/api/sessions/{sid}/hands/{n}/review").status_code == 404


def test_review_never_contains_the_opponents_unshown_cards(client):
    sid = client.post("/api/sessions", json={"seed": 11}).json()["state"]["session_id"]
    for _ in range(8):
        state = play_a_hand(client, sid, policy="fold")
        n = state["hand_number"]
        review = client.get(f"/api/sessions/{sid}/hands/{n}/review").json()
        shown = state["players"][1]["hole"]
        if shown is None and review["decisions"]:
            # The opponent's cards were never shown; they must not appear anywhere.
            blob = json.dumps(review)
            hidden = _hidden_cards(client, sid)
            assert hidden and not any(f'"{c}"' in blob for c in hidden)
            return
    pytest.skip("no folded hand with a decision in this seed")


def _hidden_cards(client, sid):
    # Reach into the server's store (test only) to learn the cards the player never saw.
    session = client.app.state.sessions.get(sid)
    return [c.code for c in session.hand.players[1].hole]
