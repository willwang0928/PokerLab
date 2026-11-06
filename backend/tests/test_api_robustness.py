"""Regression tests for robustness issues found in the v0.2.4 audit."""

import threading

from fastapi.testclient import TestClient

from pokerlab.api.app import create_app
from pokerlab.api.store import SessionStore
from pokerlab.session import HeadsUpSession, SessionStatus


def test_reading_a_session_waits_for_a_move_in_progress():
    store = SessionStore()
    client = TestClient(create_app(store=store))
    sid = client.post("/api/sessions", json={}).json()["state"]["session_id"]
    session = store.get(sid)
    result = {}

    def read():
        result["status"] = client.get(f"/api/sessions/{sid}").status_code

    with session.lock:  # a move is being applied
        reader = threading.Thread(target=read)
        reader.start()
        reader.join(timeout=0.3)
        assert reader.is_alive(), "GET must not read the session mid-move"
    reader.join(timeout=5)
    assert result["status"] == 200


def test_concurrent_reads_and_moves_never_fail():
    client = TestClient(create_app())
    sid = client.post("/api/sessions", json={"starting_stack": 200}).json()["state"]["session_id"]
    errors: list[int] = []
    stop = threading.Event()

    def poll():
        while not stop.is_set():
            r = client.get(f"/api/sessions/{sid}")
            if r.status_code != 200:
                errors.append(r.status_code)

    reader = threading.Thread(target=poll)
    reader.start()
    try:
        for _ in range(40):
            state = client.get(f"/api/sessions/{sid}").json()
            if state["status"] == "game_over":
                break
            if state["status"] == "waiting":
                client.post(f"/api/sessions/{sid}/hands")
                continue
            legal = state["legal"]
            action = "check" if legal and legal["can_check"] else "call"
            client.post(f"/api/sessions/{sid}/actions", json={"action": action})
    finally:
        stop.set()
        reader.join()
    assert errors == []


def test_client_seeds_are_refused_unless_enabled():
    # A known seed would reveal the opponent's cards.
    r = TestClient(create_app()).post("/api/sessions", json={"seed": 42})
    assert r.status_code == 400
    assert "Seeded" in r.json()["detail"]
    r = TestClient(create_app(allow_seed=True)).post("/api/sessions", json={"seed": 42})
    assert r.status_code == 201


def test_seeds_can_be_enabled_by_environment(monkeypatch):
    monkeypatch.setenv("POKERLAB_ALLOW_SEED", "1")
    assert TestClient(create_app()).post("/api/sessions", json={"seed": 1}).status_code == 201


def test_amounts_must_be_real_integers():
    client = TestClient(create_app())
    sid = client.post("/api/sessions", json={}).json()["state"]["session_id"]
    client.post(f"/api/sessions/{sid}/hands")
    for bad in (True, "30", 30.5):
        r = client.post(f"/api/sessions/{sid}/actions", json={"action": "raise", "amount": bad})
        assert r.status_code == 422, bad


def test_full_store_evicts_finished_games_before_live_ones():
    store = SessionStore(max_sessions=2)
    live, finished = HeadsUpSession(), HeadsUpSession()
    finished.status = SessionStatus.GAME_OVER
    store.add(live)
    store.add(finished)
    store.add(HeadsUpSession())
    assert store.get(live.id) is live
    assert store.get(finished.id) is None


def test_idle_games_expire():
    store = SessionStore(idle_ttl=60)
    old = HeadsUpSession()
    store.add(old)
    old.last_active -= 61
    assert store.get(old.id) is None
    fresh = HeadsUpSession()
    store.add(fresh)
    assert store.get(fresh.id) is fresh
    assert len(store) == 1
