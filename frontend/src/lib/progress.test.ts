import { beforeEach, describe, expect, it } from "vitest";
import type { DecisionReview, Grade, HandReview } from "../types";
import {
  addAnswer,
  addReview,
  conceptStats,
  credit,
  decisionTrend,
  EMPTY_PROGRESS,
  loadProgress,
  PROGRESS_KEY,
  type Progress,
  recommendConcept,
  recurringLeaks,
  sanitizeProgress,
  saveProgress,
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

  it("survives malformed storage and round-trips valid data", () => {
    localStorage.setItem(PROGRESS_KEY, "{not json");
    expect(loadProgress()).toEqual({ decisions: [], answers: [] });
    const bad = sanitizeProgress({
      decisions: [{ t: 1, hand: "a", grade: "great", concept: "pot_odds", tags: [], evLossBb: 0 }, null],
      answers: [{ t: 1, id: "a", concept: "nope", difficulty: "x", grade: "good" }],
    });
    expect(bad).toEqual({ decisions: [], answers: [] });
    const p = addReview(EMPTY_PROGRESS, "s", review(1, [decision("good")]), 5);
    saveProgress(p);
    expect(loadProgress()).toEqual(p);
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

  it("recommends practice for recurring leaks and untried or weak concepts", () => {
    let p: Progress = EMPTY_PROGRESS;
    for (let i = 0; i < 6; i++) p = addReview(p, "s", review(i, [decision("mistake", ["preflop_tight"], "preflop")]));
    // Count how often each concept is picked across the whole random range.
    const picks: Record<string, number> = {};
    for (let i = 0; i < 100; i++) {
      const r = recommendConcept(p, i / 100);
      picks[r.concept] = (picks[r.concept] ?? 0) + 1;
    }
    const top = Object.entries(picks).sort((a, b) => b[1] - a[1])[0][0];
    expect(top).toBe("preflop");
    expect(recommendConcept(p, 0.5).reason).toBeTruthy();
    const leakPick = Object.keys(picks).length;
    expect(leakPick).toBe(6); // every concept still gets some practice
    const r = recommendConcept(EMPTY_PROGRESS, 0);
    expect(r.reason).toMatch(/not practised/);
  });
});
