"""Play simulated players through the production pipeline.

One *session* is ``RunConfig.session_hands`` hands of a :class:`~.policies.SimPlayer`
against a PokerLab opponent, in which

* the hands are played by the real engine (:class:`~pokerlab.engine.HoldemHand`),
* every decision of the simulated player is recorded and graded by the real coach
  (:class:`~pokerlab.coach.review.HandCoach`, production settings by default),
* each hand becomes a real history record and summary (:mod:`pokerlab.history`), the
  exact input of the leak detector, and
* a sample of decisions is evaluated by the independent oracle (:mod:`.oracle`).

Every hand starts from fresh, equal stacks drawn from ``RunConfig.stacks_bb`` (so stack
depth varies), with the button alternating. Sessions are seeded from (policy, index), so
a run is reproducible and sessions can run in parallel.
"""

from __future__ import annotations

import random
from typing import Any

from ..ai.base import Observation
from ..ai.difficulty import make_agent
from ..cards import Deck
from ..coach.review import HandCoach
from ..coach.training import _parse_choice
from ..engine import Action, HoldemHand
from ..history import build_record, summarize
from .oracle import compare, snapshot
from .policies import POLICY_BY_NAME, PolicySpec, RunConfig, SimPlayer

HERO, VILLAIN = 0, 1


def _key(action: Action) -> str:
    return action.type.value + (f"-{action.amount}" if action.amount is not None else "")


def _coach_alternative(d: dict[str, Any]) -> str | None:
    """The option the coach scored best among those it valued (its recommendation)."""
    valued = {o["key"]: o for o in d["options"] if o.get("ev") is not None}
    best = [k for k in d.get("best") or [] if k in valued and k != d["chosen"]]
    if best:
        return max(best, key=lambda k: valued[k]["ev"])
    others = [k for k in valued if k != d["chosen"]]
    return max(others, key=lambda k: valued[k]["ev"]) if others else None


def opponent_level(spec: PolicySpec, index: int) -> str:
    if spec.opponent_after and index >= spec.opponent_after[0]:
        return spec.opponent_after[1]
    return spec.opponent


def run_session(policy: str, session: int, cfg: RunConfig) -> dict[str, Any]:
    spec = POLICY_BY_NAME[policy]
    seed = f"{cfg.seed}:{policy}:{session}"
    rng = random.Random(seed)
    oracle_rng = random.Random(seed + ":oracle")
    hero = SimPlayer(spec, random.Random(rng.random()))
    first = session * cfg.session_hands
    level = opponent_level(spec, first)
    villain = make_agent(level, random.Random(rng.random()))
    bb, sb = cfg.big_blind, cfg.big_blind // 2
    coach = HandCoach(HERO, VILLAIN, sb, bb, names={VILLAIN: "Opponent"},
                      iterations=cfg.coach_iterations)  # fmt: skip
    summaries: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []
    candidates: dict[str, list[tuple]] = {"deviation": [], "flag": [], "control": []}

    for j in range(cfg.session_hands):
        index = first + j
        new_level = opponent_level(spec, index)
        if new_level != level:  # the opponent changes (a different player sits down)
            level = new_level
            villain = make_agent(level, random.Random(rng.random()))
            coach = HandCoach(HERO, VILLAIN, sb, bb, names={VILLAIN: "Opponent"},
                              iterations=cfg.coach_iterations)  # fmt: skip
        hero.hand_index = index
        stack = rng.choice(cfg.stacks_bb) * bb
        button = index % 2
        deck = Deck(random.Random(rng.random()))
        deck.shuffle()
        hand = HoldemHand([stack, stack], button, sb, bb, deck=deck)
        coach.start_hand(index + 1, [stack, stack], button)
        agents = (hero, villain)
        snaps = []
        start_labels = len(hero.labels)
        while not hand.is_complete:
            seat = hand.to_act
            assert seat is not None
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
        net = hand.players[HERO].stack - stack
        coach.finish_hand(hand.events, net)
        review = coach.review_hand(coach._finished)
        record = build_record(
            session_id=f"sim-{policy}-{session}",
            hand_number=index + 1,
            events=hand.events,
            hero=HERO,
            hero_cards=[c.code for c in hand.players[HERO].hole],
            names=["Sim", "Opponent"],
            t_ms=float(index),
            difficulty=level,
            review=review,
        )
        summary = summarize(record)
        summaries.append(summary)
        hand_labels = hero.labels[start_labels:]
        for i, (lab, d) in enumerate(zip(hand_labels, review["decisions"], strict=True)):
            labels.append(
                {
                    "hand": summary["id"],
                    "index": index,
                    "i": i,
                    "street": lab.street,
                    "deviation": lab.deviation,
                    "base": _key(lab.base),
                    "action": _key(lab.action),
                }
            )
            ref = (summary["id"], index, i, snaps[i], lab, d)
            if lab.deviation:
                candidates["deviation"].append(ref)
            if d["grade"] in ("questionable", "mistake"):
                candidates["flag"].append(ref)
            elif d["grade"] == "good" and d["spot"] != "unopened":
                candidates["control"].append(ref)

    oracle: list[dict[str, Any]] = []
    caps = {"deviation": cfg.oracle_per_session, "flag": cfg.oracle_per_session,
            "control": cfg.oracle_per_session // 2}  # fmt: skip
    for kind, refs in candidates.items():
        chosen_refs = refs if len(refs) <= caps[kind] else oracle_rng.sample(refs, caps[kind])
        for hid, index, i, snap, lab, d in chosen_refs:
            if kind == "deviation":
                alt: Action | None = lab.base
            else:
                k = _coach_alternative(d)
                alt = _parse_choice(k) if k else None
            if alt is None or cfg.rollouts <= 0:
                continue
            res = compare(snap, lab.action, alt, cfg.rollouts, oracle_rng.randrange(2**31))
            if res is None:
                continue
            oracle.append(
                {
                    "kind": kind,
                    "hand": hid,
                    "index": index,
                    "i": i,
                    "street": lab.street,
                    "deviation": lab.deviation,
                    "chosen": _key(lab.action),
                    "alternative": _key(alt),
                    "grade": d["grade"],
                    "confidence": d["confidence"],
                    "robust": d["robust"],
                    "tags": d["tags"],
                    "coach_ev_loss": d["ev_loss"],
                    "coach_delta": _coach_delta(d, _key(lab.action), _key(alt)),
                    "delta": res.delta,
                    "se": res.se,
                    "n": res.n,
                    "big_blind": bb,
                    "sampled_from": len(refs),
                }
            )
    return {
        "policy": policy,
        "session": session,
        "summaries": summaries,
        "labels": labels,
        "oracle": oracle,
        "candidates": {k: len(v) for k, v in candidates.items()},
    }


def _coach_delta(d: dict[str, Any], chosen: str, alt: str) -> float | None:
    """The coach's own estimate of EV(chosen) - EV(alternative), when it has both."""
    evs = {o["key"]: o.get("ev") for o in d["options"]}
    a, b = evs.get(_norm(chosen, evs)), evs.get(_norm(alt, evs))
    return None if a is None or b is None else a - b


def _norm(key: str, evs: dict[str, Any]) -> str:
    if key in evs:
        return key
    if key.startswith("all_in"):
        return "all-in"
    return key
