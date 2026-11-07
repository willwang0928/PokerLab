import random

import pytest
from fastapi.testclient import TestClient

from pokerlab.api.app import create_app


@pytest.fixture
def client():
    return TestClient(create_app(allow_seed=True))


def new_session(client, **kw):
    r = client.post("/api/sessions", json={"seed": 7, **kw})
    assert r.status_code == 201
    return r.json()["state"]["session_id"]


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_create_validates_config(client):
    assert (
        client.post("/api/sessions", json={"small_blind": 20, "big_blind": 10}).status_code == 422
    )
    assert client.post("/api/sessions", json={"starting_stack": 10}).status_code == 422
    assert client.post("/api/sessions", json={"player_name": ""}).status_code == 422


def test_unknown_session_is_404(client):
    assert client.get("/api/sessions/nope").status_code == 404
    assert client.post("/api/sessions/nope/hands").status_code == 404
    assert client.post("/api/sessions/nope/actions", json={"action": "check"}).status_code == 404


def test_action_without_hand_is_409(client):
    sid = new_session(client)
    r = client.post(f"/api/sessions/{sid}/actions", json={"action": "check"})
    assert r.status_code == 409


def test_illegal_action_is_400_and_bad_payload_422(client):
    sid = new_session(client)
    state = client.post(f"/api/sessions/{sid}/hands").json()["state"]
    while state["status"] == "in_hand" and state["legal"]["can_check"]:
        state = client.post(f"/api/sessions/{sid}/actions", json={"action": "check"}).json()[
            "state"
        ]
    if state["status"] == "in_hand":
        r = client.post(f"/api/sessions/{sid}/actions", json={"action": "check"})
        assert r.status_code == 400
        assert "Cannot check" in r.json()["detail"]
    r = client.post(f"/api/sessions/{sid}/actions", json={"action": "dance"})
    assert r.status_code == 422


def test_steps_carry_state_snapshots(client):
    sid = new_session(client)
    body = client.post(f"/api/sessions/{sid}/hands").json()
    types = [s["event"]["type"] for s in body["steps"]]
    assert types[:4] == ["hand_start", "post_blind", "post_blind", "deal_hole"]
    for step in body["steps"]:
        assert step["state"]["players"][1]["hole"] is None or step["state"]["street"] in (
            "showdown",
            "complete",
            "flop",
            "turn",
            "river",
        )


def test_play_full_games_over_http(client):
    rng = random.Random(0)
    for game in range(3):
        sid = new_session(client, starting_stack=200, seed=game)
        state = client.get(f"/api/sessions/{sid}").json()
        hands = 0
        while state["status"] != "game_over" and hands < 300:
            r = client.post(f"/api/sessions/{sid}/hands")
            assert r.status_code == 200
            state = r.json()["state"]
            hands += 1
            while state["status"] == "in_hand":
                legal = state["legal"]
                assert legal is not None and state["to_act"] == 0
                options = []
                if legal["can_fold"]:
                    options.append({"action": "fold"})
                if legal["can_check"]:
                    options.append({"action": "check"})
                if legal["can_call"]:
                    options.append({"action": "call"})
                if legal["can_raise"]:
                    options.append({"action": "all_in"})
                    options.append(
                        {
                            "action": legal["raise_type"],
                            "amount": rng.randint(legal["min_raise_to"], legal["max_raise_to"]),
                        }
                    )
                r = client.post(f"/api/sessions/{sid}/actions", json=rng.choice(options))
                assert r.status_code == 200, r.json()
                state = r.json()["state"]
            assert sum(p["stack"] for p in state["players"]) == 400
            assert state["result"] is not None
        if state["status"] == "game_over":
            assert client.post(f"/api/sessions/{sid}/hands").status_code == 409


def test_delete_session(client):
    sid = new_session(client)
    assert client.delete(f"/api/sessions/{sid}").status_code == 204
    assert client.get(f"/api/sessions/{sid}").status_code == 404


def test_equity_endpoint_exact_and_monte_carlo(client):
    r = client.post("/api/equity", json={"players": ["AhKh", "QsQd"], "board": "Qh7h2c"})
    assert r.status_code == 200
    body = r.json()
    assert body["method"] == "exact" and body["trials"] == 990 and body["seed"] is None
    assert sum(p["equity"] for p in body["players"]) == pytest.approx(1.0)
    req = {"players": ["AhKh", "QQ+, AKs"], "iterations": 2000, "seed": 5}
    a, b = client.post("/api/equity", json=req).json(), client.post("/api/equity", json=req).json()
    assert a == b and a["method"] == "monte_carlo" and a["players"][0]["std_error"] > 0


@pytest.mark.parametrize(
    "body,status",
    [
        ({"players": ["AhKh"]}, 422),
        ({"players": ["AhKh"] * 7}, 422),
        ({"players": ["AhKh", "QQ"], "iterations": 10**6}, 422),
        ({"players": ["any"] * 6, "iterations": 100_000}, 422),  # too much total work
        ({"players": ["AhKh", "QQ"], "iterations": "500"}, 422),
        ({"players": ["AhKh", "ZZ"]}, 400),
        ({"players": ["AhKh", "AhQd"]}, 400),
        ({"players": ["AhKh", "QQ"], "board": "Qh7h"}, 400),
        ({"players": ["AA", "AA", "AA"]}, 400),
    ],
)
def test_equity_endpoint_validates_input(client, body, status):
    r = client.post("/api/equity", json=body)
    assert r.status_code == status, r.json()
