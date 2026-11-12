"""Hand histories (v0.4.3): records, replay, validation, storage and API.

Recurring weaknesses are tested in test_leaks.py (v0.4.4).

Scripted hands use stacked decks, blinds 5/10, and the numbers each test expects are
worked out by hand in its docstring.
"""

from __future__ import annotations

import copy
import json
import random
import sqlite3

import pytest
from fastapi.testclient import TestClient

from pokerlab import storage as S
from pokerlab.ai.base import Observation
from pokerlab.ai.random_agent import RandomAgent
from pokerlab.api.app import create_app
from pokerlab.coach.review import HandCoach
from pokerlab.engine import Action, HoldemHand
from pokerlab.history import (
    HistoryError,
    build_record,
    replay,
    summarize,
    validate_record,
)
from pokerlab.session import HeadsUpSession, SessionConfig

from .helpers import stacked_deck

T = 1_762_600_000_000.0


def play(holes, board, actions, *, button=1, stacks=(1000, 1000), coach=True, sid="sess"):
    """Play a scripted heads-up hand (hero = seat 0), coached like a real session."""
    hand = HoldemHand(list(stacks), button, 5, 10, deck=stacked_deck(holes, board, button))
    hc = HandCoach(0, 1, 5, 10, iterations=300) if coach else None
    if hc:
        hc.start_hand(1, list(stacks), button)
    for act in actions:
        if hc and hand.to_act == 0:
            hc.record(Observation.from_hand(hand, 0), hand.legal_actions(), act)
        hand.apply(act)
    assert hand.is_complete, "script did not finish the hand"
    review = None
    if hc:
        hc.finish_hand(hand.events, hand.players[0].stack - stacks[0])
        review = hc.review(1)
    rec = build_record(
        session_id=sid, hand_number=1, events=hand.events, hero=0,
        hero_cards=[c.code for c in hand.players[0].hole], names=["You", "Dealer Bot"],
        t_ms=T, difficulty="beginner", adaptive=True, review=review,
    )  # fmt: skip
    return hand, json.loads(json.dumps(rec))  # exactly what goes over the wire


def frame_after(frames, pred):
    return next(f for f in frames if pred(f))


# Villain (seat 1) is the button and small blind; hero is the big blind.
MULTI = [
    Action.raise_to(30), Action.call(),  # preflop -> pot 60
    Action.check(), Action.bet(40), Action.raise_to(120), Action.call(),  # flop -> 300
    Action.bet(200), Action.fold(),  # turn: hero bets, villain folds
]  # fmt: skip


# ----------------------------------------------------------------- replay accounting


def test_a_multi_street_hand_replays_with_exact_pots_and_stacks():
    """Preflop: SB raises to 30, BB calls 20 -> pot 60, both 970 behind.
    Flop: check, bet 40, raise to 120, call 80 -> pot 60 + 240 = 300, both 850.
    Turn: hero bets 200 (pot shows 500), villain folds, 200 is returned (pot 300) and
    hero wins 300: hero 850 + 300 = 1150, villain 850. Chips: 2000 throughout."""
    hand, rec = play(["AhKh", "QsQd"], "Kd7c2h4s9d", MULTI)
    frames = replay(rec)
    collected = [f["pot"] for f in frames if f["type"] == "bets_collected"]
    assert collected == [60, 300]
    flop = frame_after(frames, lambda f: f["type"] == "street" and f["street"] == "flop")
    assert flop["board"] == ["Kd", "7c", "2h"] and flop["stacks"] == [970, 970]
    bet = frame_after(frames, lambda f: f["type"] == "action" and f["street"] == "turn")
    assert bet["pot"] == 500 and bet["stacks"] == [650, 850] and bet["bets"] == [200, 0]
    ret = frame_after(frames, lambda f: f["type"] == "uncalled_returned")
    assert ret["pot"] == 300 and ret["stacks"] == [850, 850] and ret["text"].endswith("You")
    assert frames[-1]["stacks"] == [1150, 850] == [p.stack for p in hand.players]
    assert all(sum(f["stacks"]) + f["pot"] == 2000 for f in frames)
    assert frames[-1]["pot"] == 0
    # The raise on the flop faced a 40 bet; the call faced 80 more.
    raise_ = frame_after(frames, lambda f: f["text"] == "You raise to 120")
    assert raise_["to_call"] == 40
    assert frame_after(frames, lambda f: f["text"] == "Dealer Bot calls 80")["to_call"] == 80


def test_a_tie_splits_the_pot_and_is_summarised_as_split():
    """Both play A-K-Q-J-9: limp, check down, pot 20 split 10/10, net 0."""
    checks = [Action.call(), Action.check()] + [Action.check()] * 6
    _, rec = play(["AhKc", "AdKs"], "2s7h9cJdQs", checks)
    frames = replay(rec)
    award = frame_after(frames, lambda f: f["type"] == "pot_awarded")
    assert award["awarded"] == {"0": 10, "1": 10} and award["text"].startswith("Split pot: 20")
    s = summarize(rec)
    assert s["result"] == "split" and s["net"] == 0 and s["showdown"] and s["pot"] == 20


def test_an_all_in_call_reveals_cards_only_from_the_reveal_on():
    """Hero has 200, villain 1000. Villain (SB) can bet at most what hero can match: he
    shoves to 200 and hero calls 190 more, pot 400. Hands are turned up before the
    run-out; hero's aces hold: hero 400, villain 800."""
    acts = [Action.raise_to(200), Action.call()]
    hand, rec = play(["AhAd", "7c2d"], "Ks9s4h3c8d", acts, stacks=(200, 1000))
    frames = replay(rec)
    assert frame_after(frames, lambda f: f["type"] == "bets_collected")["pot"] == 400
    assert frames[-1]["stacks"] == [400, 800] == [p.stack for p in hand.players]
    reveal = next(i for i, f in enumerate(frames) if f["type"] == "reveal")
    assert frames[reveal]["board"] == []  # turned up before the flop is dealt
    # Opponent cards become visible at the reveal, not before.
    assert all(f["visible"] == {} for f in frames[:reveal])
    assert all(f["visible"] == {"1": ["7c", "2d"]} for f in frames[reveal:])


def test_a_short_blind_all_in_is_replayed_as_the_engine_plays_it():
    """Villain has 8 chips and is the big blind (hero is the button here): the blind is
    all-in for 8, hero completes only 3 more (no one can match 10), pot 16."""
    hand, rec = play(["AhKd", "3c3d"], "4h9s2c5c5s", [Action.call()], button=0, stacks=(1000, 8))
    frames = replay(rec)
    call = frame_after(frames, lambda f: f["type"] == "action")
    assert call["to_call"] == 3 and call["pot"] == 16
    assert frames[-1]["stacks"] == [p.stack for p in hand.players]


def test_side_pots_with_three_players_replay_exactly():
    """3 players with 50 / 200 / 1000, button seat 0 (acts first preflop). Seat 0 shoves
    50, seat 1 (small blind) shoves 200, seat 2 (big blind) calls 190 more.
    Main pot 3 x 50 = 150 (all three), side pot 2 x 150 = 300 (seats 1 and 2).
    Aces win the main pot, kings the side pot: stacks 150 / 300 / 800."""
    holes = ["AhAd", "KhKd", "QhQd"]
    deck = stacked_deck(holes, "2c7s9d3h4c", button=0)
    hand = HoldemHand([50, 200, 1000], 0, 5, 10, deck=deck)
    for act in (Action.raise_to(50), Action.raise_to(200), Action.call()):
        hand.apply(act)
    rec = build_record(
        session_id="three", hand_number=1, events=hand.events, hero=0,
        hero_cards=["Ah", "Ad"], names=["You", "B", "C"], t_ms=T,
    )  # fmt: skip
    frames = replay(json.loads(json.dumps(rec)))
    pots = [f["awarded"] for f in frames if f["type"] == "pot_awarded"]
    assert pots == [{"0": 150}, {"1": 300}]
    assert frames[-1]["stacks"] == [150, 300, 800] == [p.stack for p in hand.players]
    validate_record(json.loads(json.dumps(rec)))


@pytest.mark.parametrize("seed", range(4))
def test_random_hands_always_replay_to_the_engines_stacks(seed):
    """Fuzz: 150 random 2-6 player hands per seed with short and deep stacks."""
    for k in range(150):
        rng = random.Random(seed * 1000 + k)
        n = rng.choice([2, 2, 3, 4, 6])
        stacks = [rng.choice([3, 8, 15, 40, 200, 1000]) for _ in range(n)]
        hand = HoldemHand(stacks, rng.randrange(n), 5, 10, rng=random.Random(k))
        agent = RandomAgent(random.Random(k + 1))
        while not hand.is_complete:
            hand.apply(agent.decide(Observation.from_hand(hand, hand.to_act), hand.legal_actions()))
        rec = build_record(
            session_id=f"f{seed}-{k}", hand_number=1, events=hand.events, hero=0,
            hero_cards=[c.code for c in hand.players[0].hole],
            names=["You"] + [f"P{i}" for i in range(1, n)], t_ms=T,
        )  # fmt: skip
        clean = validate_record(json.loads(json.dumps(rec)))
        assert replay(clean)[-1]["stacks"] == [p.stack for p in hand.players]


# ----------------------------------------------------------------- hidden information


def test_a_folded_opponent_hand_never_appears_in_the_record():
    hand, rec = play(["AhKh", "QsQd"], "Kd7c2h4s9d", MULTI)
    text = json.dumps(rec)
    assert "Qs" not in text and "Qd" not in text
    assert rec["opponent_cards"] == {"1": None}
    assert all(f["visible"] == {} for f in replay(rec))


def test_shown_opponent_cards_are_kept_from_the_showdown_on():
    checks = [Action.call(), Action.check()] + [Action.check()] * 6
    _, rec = play(["AhKc", "9d9s"], "2s7h3cJdQs", checks)
    assert rec["opponent_cards"] == {"1": ["9d", "9s"]}
    frames = replay(rec)
    sd = next(i for i, f in enumerate(frames) if f["type"] == "showdown")
    assert all(f["visible"] == {} for f in frames[:sd])
    assert frames[sd]["visible"] == {"1": ["9d", "9s"]}


def test_records_that_expose_unrevealed_cards_are_refused():
    _, rec = play(["AhKh", "QsQd"], "Kd7c2h4s9d", MULTI)
    leaked = {**rec, "opponent_cards": {"1": ["Qs", "Qd"]}}
    with pytest.raises(HistoryError, match="never shown"):
        validate_record(leaked)


def test_coaching_in_the_record_does_not_depend_on_hidden_cards():
    """Same public actions, different opponent hands (folded, so never seen): the
    recorded coaching is identical."""
    a = play(["AhKh", "QsQd"], "Kd7c2h4s9d", MULTI, sid="x")[1]["coaching"]
    b = play(["AhKh", "3c2d"], "Kd7c2h4s9d", MULTI, sid="x")[1]["coaching"]
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_hero_actions_are_linked_to_their_coaching_decisions():
    _, rec = play(["AhKh", "QsQd"], "Kd7c2h4s9d", MULTI)
    decisions = rec["coaching"]["decisions"]
    hero = [e for e in rec["events"] if e["type"] == "action" and e["seat"] == 0]
    assert rec["decisions_linked"] and [e["decision"] for e in hero] == [0, 1, 2, 3]
    assert len(decisions) == 4  # call, check, raise, bet
    frames = replay(rec)
    linked = [f for f in frames if f["decision"] is not None]
    assert [decisions[f["decision"]]["street"] for f in linked] == [
        "preflop",
        "flop",
        "flop",
        "turn",
    ]
    # The board at each decision is exactly what the coach saw: no later cards.
    for f in linked:
        assert decisions[f["decision"]]["board"] == f["board"]


# ----------------------------------------------------------------- corrupted records


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        (lambda r: r["events"].pop(), "incomplete"),
        (lambda r: r["events"].insert(3, {"type": "mystery"}), "unknown type"),
        (lambda r: r["events"][-1].__setitem__("stacks", [1200, 800]), "final stacks"),
        (lambda r: next(e for e in r["events"] if e["type"] == "bets_collected")
         .__setitem__("pot", 61), "pot"),
        (lambda r: next(e for e in r["events"] if e.get("action") == "call")
         .__setitem__("amount", 25), "call"),
        (lambda r: r.__setitem__("board", ["Kd", "7c", "2h", "4s", "9d"]), "board"),
        (lambda r: r.__setitem__("hero_cards", ["Ah", "Ah"]), "twice"),
        (lambda r: r.__setitem__("v", 99), "version"),
        (lambda r: r.__setitem__("t", 12), "milliseconds"),
        (lambda r: r.__setitem__("events", []), "events"),
        (lambda r: r["coaching"]["decisions"][0].__setitem__("hole", ["2c", "3c"]), "belong"),
    ],
)  # fmt: skip
def test_corrupted_or_incomplete_records_are_refused(damage, message):
    _, rec = play(["AhKh", "QsQd"], "Kd7c2h4s9d", MULTI)
    bad = copy.deepcopy(rec)
    damage(bad)
    with pytest.raises(HistoryError, match=message):
        validate_record(bad)


def test_non_objects_are_refused():
    for junk in (None, [], "hand", 3):
        with pytest.raises(HistoryError):
            validate_record(junk)


# ----------------------------------------------------------------- summaries


def test_summary_fields_for_filtering():
    _, rec = play(["AhKh", "QsQd"], "Kd7c2h4s9d", MULTI)
    s = summarize(rec)
    assert s["result"] == "won" and s["net"] == 150 and s["net_bb"] == 15
    assert s["pot"] == 300 and s["pot_bb"] == 30 and not s["showdown"]
    assert s["hand_class"] == "AKs" and s["hand_percentile"] < 0.05
    assert s["position"] == "big blind" and s["last_street"] == "turn"
    assert s["final_hand"].startswith("Pair of Kings") and s["opponent_cards"] is None
    assert [d["action"] for d in s["decisions"]] == ["call", "check", "raise", "bet"]


# ----------------------------------------------------------------- the live session


def _play_session(session: HeadsUpSession) -> None:
    session.start_hand()
    while session.status.value == "in_hand":
        la = session.hand.legal_actions()
        session.act(Action.check() if la.can_check else Action.call())


def test_a_session_builds_a_valid_record_matching_its_review():
    s = HeadsUpSession(SessionConfig(), seed=11)
    _play_session(s)
    rec = s.hand_record(1)
    assert rec is not None
    clean = validate_record(json.loads(json.dumps(rec)))
    assert clean["coaching"] == s.coach.review(1)  # the same review the coach panel shows
    assert replay(clean)[-1]["stacks"] == s.stacks
    # Still available after the next hand has been dealt (auto-deal).
    s.start_hand()
    assert s.hand_record(1)["id"] == rec["id"] and s.hand_record(99) is None


def test_the_log_text_is_unchanged_by_the_shared_describer():
    s = HeadsUpSession(SessionConfig(), seed=11)
    _play_session(s)
    frames = replay(s.hand_record(1))
    texts = [f["text"] for f in frames if f["text"] and f["type"] != "hand_complete"]
    assert texts == s.log


# ----------------------------------------------------------------- storage and API


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "data" / "progress.sqlite3")


def client_for(path: str) -> TestClient:
    return TestClient(create_app(progress=S.ProgressStore(path), allow_seed=True))


def auth(c: TestClient) -> dict:
    r = c.post("/api/profiles", json={"name": "Ana"})
    return {"Authorization": f"Bearer {r.json()['key']}"}


def test_hands_upload_list_replay_and_survive_a_restart(db_path):
    c = client_for(db_path)
    h = auth(c)
    _, rec = play(["AhKh", "QsQd"], "Kd7c2h4s9d", MULTI)
    _, tie = play(["AhKc", "AdKs"], "2s7h9cJdQs",
                  [Action.call(), Action.check()] + [Action.check()] * 6, sid="other")  # fmt: skip
    r = c.post("/api/profile/hands", json={"hands": [rec, tie]}, headers=h)
    assert r.json() == {"added": 2, "accepted": 2, "rejected": []}
    again = c.post("/api/profile/hands", json={"hands": [rec]}, headers=h).json()
    assert again["added"] == 0 and again["accepted"] == 1  # a repeat is a no-op

    c = client_for(db_path)  # a server restart
    listed = c.get("/api/profile/hands", headers=h).json()
    assert listed["total"] == 2 and {x["id"] for x in listed["hands"]} == {rec["id"], tie["id"]}
    got = c.get(f"/api/profile/hands/{rec['id']}", headers=h).json()
    assert got["record"] == rec and got["frames"] == replay(rec)
    assert c.get("/api/profile", headers=h).json()["counts"]["hands"] == 2


def test_bad_hands_are_refused_with_reasons_and_do_not_block_good_ones(db_path):
    c = client_for(db_path)
    h = auth(c)
    _, rec = play(["AhKh", "QsQd"], "Kd7c2h4s9d", MULTI)
    bad = copy.deepcopy(rec)
    bad["id"] = "hbadbadbadbad"
    bad["events"][-1]["stacks"] = [2000, 0]
    r = c.post("/api/profile/hands", json={"hands": [bad, rec, "junk"]}, headers=h).json()
    assert r["added"] == 1 and len(r["rejected"]) == 2
    assert r["rejected"][0]["id"] == "hbadbadbadbad" and "final stacks" in r["rejected"][0]["error"]


def test_hands_are_private_to_a_profile_and_deleted_by_a_reset(db_path):
    c = client_for(db_path)
    a, b = auth(c), auth(c)
    _, rec = play(["AhKh", "QsQd"], "Kd7c2h4s9d", MULTI)
    c.post("/api/profile/hands", json={"hands": [rec]}, headers=a)
    assert c.get("/api/profile/hands", headers=b).json()["total"] == 0
    assert c.get(f"/api/profile/hands/{rec['id']}", headers=b).status_code == 404
    assert c.get("/api/profile/hands").status_code == 401
    assert c.delete("/api/profile", headers=a).status_code == 204
    con = sqlite3.connect(db_path)
    assert con.execute("SELECT COUNT(*) FROM hands").fetchone()[0] == 0


def test_a_damaged_stored_hand_is_reported_not_replayed_wrongly(db_path):
    c = client_for(db_path)
    h = auth(c)
    _, rec = play(["AhKh", "QsQd"], "Kd7c2h4s9d", MULTI)
    _, tie = play(["AhKc", "AdKs"], "2s7h9cJdQs",
                  [Action.call(), Action.check()] + [Action.check()] * 6, sid="other")  # fmt: skip
    c.post("/api/profile/hands", json={"hands": [rec, tie]}, headers=h)
    con = sqlite3.connect(db_path)
    broken = copy.deepcopy(rec)
    broken["events"][-1]["stacks"] = [0, 2000]
    con.execute("UPDATE hands SET data = ? WHERE id = ?", (json.dumps(broken), rec["id"]))
    con.execute("UPDATE hands SET summary = '{nope' WHERE id = ?", (tie["id"],))
    con.commit()
    con.close()
    r = c.get(f"/api/profile/hands/{rec['id']}", headers=h)
    assert r.status_code == 422 and "damaged" in r.json()["detail"]
    listed = c.get("/api/profile/hands", headers=h).json()
    assert [x["id"] for x in listed["hands"]] == [rec["id"]]  # unreadable summary skipped


def test_a_schema_1_database_is_migrated_and_keeps_its_records(db_path):
    """Build a v0.4.0-era database by hand, then open it with this version."""
    import os

    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    con = sqlite3.connect(db_path)
    con.executescript(S._SCHEMA.split("CREATE TABLE IF NOT EXISTS hands")[0])
    con.execute("INSERT INTO meta VALUES ('schema_version', '1')")
    con.execute(
        "INSERT INTO profiles(public_id, token_hash, name, created_at, updated_at)"
        " VALUES ('p1', ?, 'Old', 0, 0)",
        (S.hash_token("old-key"),),
    )
    data = {"id": "a1", "t": T, "concept": "pot_odds", "difficulty": "beginner", "grade": "good"}
    con.execute("INSERT INTO answers VALUES (1, 'a1', ?, ?)", (T, json.dumps(data)))
    con.commit()
    con.close()

    store = S.ProgressStore(db_path)
    assert store.available
    p = store.profile("old-key")
    assert store.records(p, 10)["answers"] == [data]
    _, rec = play(["AhKh", "QsQd"], "Kd7c2h4s9d", MULTI)
    assert store.add_hands(p, [(rec, summarize(rec))]) == 1
    version = store._conn().execute("SELECT value FROM meta WHERE key='schema_version'")
    assert version.fetchone()[0] == str(S.SCHEMA_VERSION) == "2"


def test_the_record_endpoint_over_a_real_session(db_path):
    c = client_for(db_path)
    sid = c.post("/api/sessions", json={"seed": 5}).json()["state"]["session_id"]
    c.post(f"/api/sessions/{sid}/hands")
    for _ in range(40):
        st = c.get(f"/api/sessions/{sid}").json()
        if st["status"] != "in_hand":
            break
        legal = st["legal"]
        c.post(f"/api/sessions/{sid}/actions",
               json={"action": "check" if legal["can_check"] else "call"})  # fmt: skip
    assert c.get(f"/api/sessions/{sid}/hands/2/record").status_code == 404
    rec = c.get(f"/api/sessions/{sid}/hands/1/record").json()
    assert rec["hand_number"] == 1 and rec["coaching"] is not None
    h = auth(c)
    assert c.post("/api/profile/hands", json={"hands": [rec]}, headers=h).json()["added"] == 1
