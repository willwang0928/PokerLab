"""Check the coach's modelling assumptions against what the opponents actually do.

The oracle (:mod:`.oracle`) says *whether* a kind of coaching verdict is right. This
says *why not* when it is wrong, by comparing the two assumptions every bet or raise
value rests on with the truth at the same decision:

* **fold rate** — the coach's assumed chance that the opponent folds to the bet,
  against the opponent agent's real response: the bet is applied to a copy of the exact
  game state and the opponent (holding its real cards) decides ``k`` times with fresh
  randomness;
* **equity when called** — the coach's estimate against its assumed defending range,
  against the player's true equity against the opponent's real hand, weighted by how
  often the opponent actually continues.

It also records how often the opponent bets after the player checks, which the coach's
"check" value (equity × pot, as if checked down) leaves out.

Usage::

    python -m pokerlab.calibration.diagnose [--sessions 3] [--k 8]

Nothing here is used by the coach; it is a measurement tool (see docs/calibration.md).
"""

from __future__ import annotations

import argparse
import copy
import json
import random
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from ..ai.base import Observation
from ..ai.difficulty import make_agent
from ..cards import Deck
from ..coach.review import HandCoach
from ..engine import Action, ActionType, HoldemHand
from ..equity import calculate_equity
from .housekeeping import ensure_space, run_lock
from .oracle import _reseed, snapshot
from .policies import POLICY_BY_NAME, RunConfig, SimPlayer
from .run import DEFAULT_CACHE
from .simulate import HERO, VILLAIN, opponent_level

POLICIES = ("baseline", "beginner_style", "over_bluffer", "calling_station")


def _action(opt: dict[str, Any]) -> Action:
    return Action.bet(opt["amount"]) if opt["action"] == "bet" else Action.raise_to(opt["amount"])


def _responses(snap, action: Action, k: int, seed: int) -> list[ActionType] | None:
    """The opponent's real responses to ``action`` from this exact state."""
    out = []
    for j in range(k):
        hand, agents = copy.deepcopy((snap.hand, snap.agents))
        try:
            hand.apply(action)
        except Exception:
            return None
        if hand.is_complete or hand.to_act != VILLAIN:
            return None
        villain = agents[VILLAIN]
        _reseed(villain, seed + j)
        obs = Observation.from_hand(hand, VILLAIN)
        out.append(villain.decide(obs, hand.legal_actions()).type)
    return out


def diagnose_session(args: tuple[str, int, int, int]) -> list[dict[str, Any]]:
    policy, session, k, hands = args
    cfg = RunConfig()
    spec = POLICY_BY_NAME[policy]
    seed = f"{cfg.seed}:{policy}:{session}"
    rng = random.Random(seed)
    hero = SimPlayer(spec, random.Random(rng.random()))
    first = session * cfg.session_hands
    level = opponent_level(spec, first)
    villain = make_agent(level, random.Random(rng.random()))
    bb, sb = cfg.big_blind, cfg.big_blind // 2
    coach = HandCoach(HERO, VILLAIN, sb, bb, names={VILLAIN: "Opponent"})
    rows: list[dict[str, Any]] = []
    probe = random.Random(seed + ":diagnose")
    for j in range(hands):
        index = first + j
        hero.hand_index = index
        stack = rng.choice(cfg.stacks_bb) * bb
        deck = Deck(random.Random(rng.random()))
        deck.shuffle()
        hand = HoldemHand([stack, stack], index % 2, sb, bb, deck=deck)
        coach.start_hand(index + 1, [stack, stack], index % 2)
        agents = (hero, villain)
        snaps = []
        while not hand.is_complete:
            seat = hand.to_act
            legal = hand.legal_actions()
            obs = Observation.from_hand(hand, seat)
            if seat == HERO:
                snaps.append(snapshot(hand, agents, HERO))
                action = hero.decide(obs, legal)
                coach.record(obs, legal, action)
            else:
                action = villain.decide(obs, legal)
            hand.apply(action)
        for seat, agent in enumerate(agents):
            agent.observe_hand(hand.events, seat)
        coach.finish_hand(hand.events, hand.players[HERO].stack - stack)
        review = coach.review_hand(coach._finished)
        villain_cards = "".join(c.code for c in hand.players[VILLAIN].hole)
        for snap, d in zip(snaps, review["decisions"], strict=True):
            if d["street"] == "preflop" or d["spot"] == "unopened":
                continue
            hero_cards = "".join(d["hole"])
            board = "".join(d["board"])
            true_eq = calculate_equity([hero_cards, villain_cards], board).players[0].equity
            base = {
                "policy": policy,
                "level": level,
                "street": d["street"],
                "spot": d["spot"],
                "pot": d["pot"],
                "true_equity": true_eq,
                "coach_equity": d["equity"],
            }
            if d["spot"] == "unbet":  # what follows a check
                resp = _responses(snap, Action.check(), k, probe.randrange(2**31))
                if resp is not None:
                    bets = sum(t in (ActionType.BET, ActionType.RAISE) for t in resp) / len(resp)
                    rows.append({**base, "kind": "check", "villain_bets": bets})
            for opt in d["options"]:
                if opt["basis"] != "fold model" or opt["fold_rate"] is None:
                    continue
                resp = _responses(snap, _action(opt), k, probe.randrange(2**31))
                if resp is None:
                    continue
                folds = sum(t == ActionType.FOLD for t in resp) / len(resp)
                rows.append(
                    {
                        **base,
                        "kind": opt["action"],
                        "size": opt["amount"] / max(d["pot"], 1),
                        "model_fold": opt["fold_rate"],
                        "true_fold": folds,
                        "model_eq_called": opt["equity_when_called"],
                        "continue_weight": 1 - folds,
                    }
                )
    return rows


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if r["kind"] == "check":
            groups[("check", r["street"], r["level"])].append(r)
        else:
            groups[(r["kind"], r["street"], r["level"])].append(r)
    out = {}
    for key, rs in sorted(groups.items()):
        n = len(rs)
        if key[0] == "check":
            out["|".join(key)] = {"n": n, "villain_bets": sum(r["villain_bets"] for r in rs) / n}
            continue
        w = sum(r["continue_weight"] for r in rs)
        out["|".join(key)] = {
            "n": n,
            "model_fold": sum(r["model_fold"] for r in rs) / n,
            "true_fold": sum(r["true_fold"] for r in rs) / n,
            "model_eq_called": sum(r["model_eq_called"] for r in rs) / n,
            "true_eq_called": (
                sum(r["true_equity"] * r["continue_weight"] for r in rs) / w if w else None
            ),
        }
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pokerlab.calibration.diagnose")
    ap.add_argument("--sessions", type=int, default=2)
    ap.add_argument("--hands", type=int, default=100)
    ap.add_argument("--k", type=int, default=8, help="opponent responses per probe")
    ap.add_argument("--policies", default=",".join(POLICIES))
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--out", type=Path, default=None, help="write raw rows (JSON)")
    args = ap.parse_args(argv)
    tasks = [(p, s, args.k, args.hands) for p in args.policies.split(",")
             for s in range(args.sessions)]  # fmt: skip
    rows: list[dict[str, Any]] = []
    if args.out:  # raw rows are ~2 MB per 800 hands
        ensure_space(args.out.parent, need_mb=len(tasks) * args.hands * 0.003)
    with run_lock(DEFAULT_CACHE), ProcessPoolExecutor(args.workers) as pool:
        for r in pool.map(diagnose_session, tasks):
            rows.extend(r)
    if args.out:
        args.out.write_text(json.dumps(rows))
    json.dump(summarise(rows), sys.stdout, indent=1)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
