import { beforeEach, describe, expect, it } from "vitest";
import type { DecisionReview, Grade, HandReview } from "../types";
import {
  addAnswer,
  addReview,
  conceptStats,
  credit,
  decisionTrend,
  EMPTY_PROGRESS,
  type Progress,
  recurringLeaks,
  sanitizeProgress,
  sanitizeRecords,
} from "./progress";

function decision(grade: Grade, tags: string[] = [], concept: DecisionReview["concept"] = "pot_odds"): DecisionReview {
  return {
    street: "river",
    hole: ["As", "Kd"],
    board: [],
    hand: "AKo",
    position: "button",
    pot: 100,
    to_call: 50,
    stack: 900,
    big_blind: 10,
    history: [],
    spot: "facing_bet",
    concept,
    equity: 0.2,
    equity_se: 0,
    equity_method: "exact",
    required_equity: 0.33,
    range_size: 100,
    range_steps: [],
    reads: [],
    options: [],
    best: ["fold"],
    chosen: "call",
    grade,
    grade_label: grade,
    confidence: "high",
    ev_loss: grade === "mistake" ? 20 : 0,
    tags,
    headline: "",
    explanation: "",
    details: [],
    caveats: [],
  };
}

function review(n: number, ds: DecisionReview[]): HandReview {
  return {
    hand_number: n,
    decisions: ds,
    counts: { good: 0, close: 0, questionable: 0, mistake: 0, ungraded: 0 },
    net: 0,
    net_bb: 0,
    ev_loss_bb: 0,
    quality: "sound",
    verdict: "",
    hindsight: null,
    tags: [],
  };
}

describe("progress store", () => {
  beforeEach(() => localStorage.clear());

  it("records each hand review once and skips ungraded decisions", () => {
    let p: Progress = EMPTY_PROGRESS;
    const r = review(1, [decision("mistake", ["called_without_odds"]), decision("ungraded")]);
    p = addReview(p, "s1", r, 1);
    p = addReview(p, "s1", r, 2);
    expect(p.decisions).toHaveLength(1);
    expect(p.decisions[0]).toMatchObject({ grade: "mistake", evLossBb: 2, hand: "s1#1" });
    p = addReview(p, "s2", r, 3); // same hand number, different game
    expect(p.decisions).toHaveLength(2);
  });

  it("counts only the first answer to a training scenario", () => {
    let p = addAnswer(EMPTY_PROGRESS, { id: "x", concept: "draws", difficulty: "beginner", grade: "mistake" });
    p = addAnswer(p, { id: "x", concept: "draws", difficulty: "beginner", grade: "good" });
    expect(p.answers).toHaveLength(1);
    expect(conceptStats(p).find((s) => s.concept === "draws")).toMatchObject({ attempts: 1, accuracy: 0 });
  });

  it("keeps invalid records aside instead of dropping them silently", () => {
    const T = 1_762_600_000_000;
    const good = { id: "a1", t: T, concept: "pot_odds", difficulty: "beginner", grade: "good" };
    const out = sanitizeRecords({
      decisions: [
        { t: T, hand: "s#1", grade: "mistake", concept: "pot_odds", tags: [], evLossBb: 1 }, // v0.3: no id
        { t: T, hand: "s#1", grade: "good", concept: "pot_odds", tags: [], evLossBb: 0 },
        { id: "x", t: T, hand: "s#2", grade: "great", concept: "pot_odds", tags: [], evLossBb: 0 },
        null,
      ],
      answers: [good, good, { ...good, id: "a2", t: 5 }, { ...good, id: "a3", extra: 1 }],
    });
    expect(out.decisions.map((d) => d.id)).toEqual(["s#1:0", "s#1:1"]); // migrated ids
    expect(out.answers.map((a) => a.id)).toEqual(["a1"]); // duplicate collapsed
    expect(out.dropped.map((d) => d.reason).sort()).toEqual(["grade", "not an object", "t", "unknown field"]);
    expect(sanitizeProgress({ decisions: "nope" })).toEqual({ decisions: [], answers: [], reviews: [] });
  });

  it("finds recurring leaks only when they repeat", () => {
    let p: Progress = EMPTY_PROGRESS;
    p = addReview(p, "s", review(1, [decision("mistake", ["called_without_odds"])]));
    expect(recurringLeaks(p)).toEqual([]);
    p = addReview(p, "s", review(2, [decision("questionable", ["called_without_odds"])]));
    p = addReview(p, "s", review(3, [decision("questionable", ["missed_value"], "value_betting")]));
    const leaks = recurringLeaks(p);
    expect(leaks).toHaveLength(1);
    expect(leaks[0]).toMatchObject({ tag: "called_without_odds", count: 2, concept: "pot_odds" });
  });

  it("compares recent decisions with earlier ones", () => {
    let p: Progress = EMPTY_PROGRESS;
    for (let i = 0; i < 20; i++) p = addReview(p, "s", review(i, [decision("mistake")]));
    for (let i = 20; i < 40; i++) p = addReview(p, "s", review(i, [decision("good")]));
    const t = decisionTrend(p);
    expect(t).toMatchObject({ recent: 1, previous: 0, total: 40, recentLoss: 0, previousLoss: 2 });
    expect(credit("close")).toBe(0.5);
  });

});
