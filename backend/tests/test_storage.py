"""Progress storage: durability, isolation, validation and failure handling."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from pokerlab import storage as S
from pokerlab.api.app import create_app

T = 1_762_600_000_000.0  # a timestamp in milliseconds


def answer(i: int, **kw):
    return {"id": f"a{i}", "t": T + i, "concept": "pot_odds", "difficulty": "beginner",
            "grade": "good", **kw}  # fmt: skip


def decision(i: int, **kw):
    return {"id": f"s1#1:{i}", "t": T + i, "hand": "s1#1", "grade": "mistake",
            "concept": "pot_odds", "tags": ["called_without_odds"], "evLossBb": 2.5,
            "confidence": "high", "equity": 0.2, "required": 0.33, **kw}  # fmt: skip


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "data" / "progress.sqlite3")


def client_for(path: str) -> TestClient:
    return TestClient(create_app(progress=S.ProgressStore(path)))


def new_profile(c: TestClient, name="Ana"):
    r = c.post("/api/profiles", json={"name": name})
    assert r.status_code == 201
    body = r.json()
    return {"Authorization": f"Bearer {body['key']}"}, body


# ----------------------------------------------------------------- store


def test_records_survive_a_restart(db_path):
    store = S.ProgressStore(db_path)
    key, profile = store.create_profile("Ana")
    store.add(profile, {"answers": [answer(1), answer(2)], "decisions": [decision(0)]})
    store.close()

    reopened = S.ProgressStore(db_path)
    p = reopened.profile(key)
    assert p.public_id == profile.public_id and p.name == "Ana"
    got = reopened.records(p, 100)
    assert [a["id"] for a in got["answers"]] == ["a1", "a2"]
    assert got["decisions"][0]["tags"] == ["called_without_odds"]


def test_only_a_hash_of_the_key_is_stored(db_path):
    store = S.ProgressStore(db_path)
    key, _ = store.create_profile("Ana")
    store.close()
    import glob

    raw = b"".join(open(f, "rb").read() for f in glob.glob(db_path + "*"))
    assert key.encode() not in raw
    rows = sqlite3.connect(db_path).execute("SELECT token_hash FROM profiles").fetchall()
    assert rows == [(S.hash_token(key),)]


def test_uploads_are_idempotent_unions(db_path):
    store = S.ProgressStore(db_path)
    _, p = store.create_profile("Ana")
    assert store.add(p, {"answers": [answer(1), answer(2)]})["answers"] == 2
    # The same records again (a retry) plus one new one: only the new one is added.
    assert store.add(p, {"answers": [answer(1), answer(2), answer(3)]})["answers"] == 1
    assert store.counts(p) == {"answers": 3, "decisions": 0}


def test_an_interrupted_batch_writes_nothing(db_path, monkeypatch):
    store = S.ProgressStore(db_path)
    _, p = store.create_profile("Ana")

    def rows(pid, records):
        for i, r in enumerate(records):
            if i == 2:
                raise RuntimeError("power cut")  # simulated crash part-way through
            yield (pid, r["id"], r["t"], "{}")

    monkeypatch.setattr(S.ProgressStore, "_rows", staticmethod(rows))
    with pytest.raises(RuntimeError):
        store.add(p, {"answers": [answer(i) for i in range(5)]})
    monkeypatch.undo()
    assert store.counts(p)["answers"] == 0  # all or nothing
    assert store.add(p, {"answers": [answer(i) for i in range(5)]})["answers"] == 5  # retry


def test_profiles_are_isolated(db_path):
    store = S.ProgressStore(db_path)
    _, a = store.create_profile("A")
    _, b = store.create_profile("B")
    store.add(a, {"answers": [answer(1)]})
    assert store.records(b, 10)["answers"] == []
    store.delete_profile(a)
    assert store.counts(b) == {"answers": 0, "decisions": 0}
    with pytest.raises(S.ProfileNotFound):
        store.profile("not-a-key")


def test_records_returns_the_most_recent_in_time_order(db_path):
    store = S.ProgressStore(db_path)
    _, p = store.create_profile("A")
    store.add(p, {"answers": [answer(i) for i in (5, 1, 3, 2, 4)]})
    assert [a["id"] for a in store.records(p, 3)["answers"]] == ["a3", "a4", "a5"]


def test_quota_limits_one_profile(db_path, monkeypatch):
    monkeypatch.setattr(S, "MAX_RECORDS_PER_PROFILE", 3)
    store = S.ProgressStore(db_path)
    _, p = store.create_profile("A")
    store.add(p, {"answers": [answer(1), answer(2)]})
    with pytest.raises(S.QuotaExceeded):
        store.add(p, {"answers": [answer(3), answer(4)]})
    assert store.counts(p)["answers"] == 2


def test_a_corrupt_file_is_reported_and_left_untouched(tmp_path):
    path = tmp_path / "progress.sqlite3"
    path.write_bytes(b"this is not a database" * 100)
    before = path.read_bytes()
    store = S.ProgressStore(str(path))
    assert not store.available and "DatabaseError" in (store.error or "")
    with pytest.raises(S.StorageUnavailable):
        store.create_profile("A")
    assert path.read_bytes() == before  # nothing overwritten: recoverable by hand


def test_an_unwritable_location_is_reported(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    store = S.ProgressStore(str(blocker / "sub" / "db.sqlite3"))
    assert not store.available


def test_a_newer_schema_is_refused(db_path):
    S.ProgressStore(db_path).close()
    con = sqlite3.connect(db_path)
    con.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
    con.commit()
    con.close()
    store = S.ProgressStore(db_path)
    assert not store.available and "newer" in (store.error or "")


# ----------------------------------------------------------------- API


def test_profile_lifecycle_over_http(db_path):
    c = client_for(db_path)
    auth, body = new_profile(c, "  Ana  ")
    assert body["profile"]["name"] == "Ana" and len(body["key"]) >= 40
    assert c.get("/api/profile", headers=auth).json()["counts"] == {"answers": 0, "decisions": 0}
    assert c.patch("/api/profile", json={"name": "Bea"}, headers=auth).json()["name"] == "Bea"
    assert c.delete("/api/profile", headers=auth).status_code == 204
    assert c.get("/api/profile", headers=auth).status_code == 401


def test_upload_validates_each_record_and_keeps_the_good_ones(db_path):
    c = client_for(db_path)
    auth, _ = new_profile(c)
    bad = [
        answer(2, grade="perfect"),
        answer(3, t=1700000000),  # seconds, not milliseconds
        answer(4, concept="luck"),
        {**answer(5), "synced": True},  # unknown field
        "not a record",
        decision(1, equity=1.5),
        decision(2, tags=["made_up"]),
        decision(3, evLossBb=-1),
        answer(6, id="has spaces"),
    ]
    bad_decisions = [b for b in bad if isinstance(b, dict) and "hand" in b]
    bad_answers = [b for b in bad if b not in bad_decisions]
    r = c.post(
        "/api/profile/records",
        json={"answers": [answer(1), *bad_answers], "decisions": [decision(0), *bad_decisions]},
        headers=auth,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["added"] == {"answers": 1, "decisions": 1}
    assert len(body["rejected"]) == len(bad)
    assert {x["id"] for x in body["rejected"] if x["id"]} >= {"a2", "a3", "s1#1:1"}
    got = c.get("/api/profile/records", headers=auth).json()
    assert [a["id"] for a in got["answers"]] == ["a1"] and got["truncated"] is False


def test_records_round_trip_exactly_and_retries_are_safe(db_path):
    c = client_for(db_path)
    auth, _ = new_profile(c)
    payload = {"answers": [answer(1, review=True)], "decisions": [decision(0)]}
    assert c.post("/api/profile/records", json=payload, headers=auth).json()["added"] == {
        "answers": 1, "decisions": 1}  # fmt: skip
    again = c.post("/api/profile/records", json=payload, headers=auth).json()
    assert again["added"] == {"answers": 0, "decisions": 0}
    got = c.get("/api/profile/records", headers=auth).json()
    assert got["answers"] == payload["answers"] and got["decisions"] == payload["decisions"]


def test_progress_survives_a_server_restart_over_http(db_path):
    c1 = client_for(db_path)
    auth, _ = new_profile(c1)
    c1.post("/api/profile/records", json={"answers": [answer(1)]}, headers=auth)
    c1.app.state.progress.close()
    c2 = client_for(db_path)  # a new server process on the same file
    assert [a["id"] for a in c2.get("/api/profile/records", headers=auth).json()["answers"]] == [
        "a1"
    ]


def test_auth_failures_and_isolation_over_http(db_path):
    c = client_for(db_path)
    a, _ = new_profile(c, "A")
    b, _ = new_profile(c, "B")
    c.post("/api/profile/records", json={"answers": [answer(1)]}, headers=a)
    assert c.get("/api/profile/records", headers=b).json()["answers"] == []
    assert c.get("/api/profile/records").status_code == 401
    assert c.get("/api/profile/records", headers={"Authorization": "Basic x"}).status_code == 401
    assert c.get("/api/profile", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_upload_and_name_limits(db_path):
    c = client_for(db_path)
    auth, _ = new_profile(c)
    too_many = {"answers": [answer(i) for i in range(2001)]}
    assert c.post("/api/profile/records", json=too_many, headers=auth).status_code == 422
    assert c.post("/api/profiles", json={"name": "x" * 41}).status_code == 422
    assert c.get("/api/profile/records?limit=0", headers=auth).status_code == 422


def test_unavailable_storage_returns_503_but_games_still_work(tmp_path):
    path = tmp_path / "broken.sqlite3"
    path.write_bytes(b"garbage" * 200)
    c = client_for(str(path))
    assert c.get("/api/health").json()["storage"] == "unavailable"
    assert c.post("/api/profiles", json={}).status_code == 503
    assert c.get("/api/profile", headers={"Authorization": "Bearer x"}).status_code == 503
    assert c.post("/api/sessions", json={}).status_code == 201  # play is unaffected


def test_real_record_ids_are_accepted(db_path):
    """Scenario ids, practice-spot ids (long base64url with '_' and '-') and decision ids."""
    c = client_for(db_path)
    auth, _ = new_profile(c)
    spot_id = "spot." + "eyJzIjp7Imhlcm8iOiI5aDhoIn0sImQiOiJpbnRlcm1lZGlhdGUi_-" * 8
    ids = ["v1.beginner.pot_odds.980855924", spot_id]
    r = c.post(
        "/api/profile/records",
        json={"answers": [answer(i, id=x) for i, x in enumerate(ids)],
              "decisions": [decision(0, id="0f3c9a#12:3", hand="0f3c9a#12")]},
        headers=auth,
    )  # fmt: skip
    assert r.json()["rejected"] == [] and r.json()["added"] == {"answers": 2, "decisions": 1}


def test_database_errors_after_startup_return_503(db_path, monkeypatch):
    """Regression: an I/O error or a locked database mid-request was an unhandled 500."""
    c = client_for(db_path)
    auth, _ = new_profile(c)

    def broken(*_a, **_k):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(S.ProgressStore, "add", broken)
    monkeypatch.setattr(S.ProgressStore, "records", broken)
    r = c.post("/api/profile/records", json={"answers": [answer(1)]}, headers=auth)
    assert r.status_code == 503
    assert c.get("/api/profile/records", headers=auth).status_code == 503
    assert c.post("/api/sessions", json={}).status_code == 201  # play is unaffected
