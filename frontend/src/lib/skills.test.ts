import { describe, expect, it } from "vitest";
import type { DecisionReview, Grade, HandReview } from "../types";
import { planFor } from "../components/TrainingView";
import { EMPTY_PROGRESS, type Progress } from "./progress";
import {
  adaptiveLevel,
  answerTrend,
  conceptSkills,
  dueReviews,
  estimateSkill,
  INTERVALS,
  mistakeGuides,
  nextPractice,
  type Observation,
  recordAnswer,
  recordHandReview,
  withReviews,
} from "./skills";

const rep = (n: number, y: number, b: number): Observation[] => Array.from({ length: n }, () => ({ y, b, w: 1 }));
const T0 = 1_000_000_000_000;
const MIN = 60_000;
const DAY = 1440 * MIN;

function answer(p: Progress, concept: DecisionReview["concept"], difficulty: string, grade: Grade, t: number, id = `${concept}${t}`) {
  return recordAnswer(p, { id, concept, difficulty, grade }, t);
}

function decision(o: Partial<DecisionReview> = {}): DecisionReview {
  return {
    street: "river", hole: [], board: [], hand: "", position: "button", pot: 100, to_call: 50, stack: 900,
    big_blind: 10, history: [], spot: "facing_bet", concept: "pot_odds", equity: 0.2, equity_se: 0,
    equity_method: "exact", required_equity: 0.33, range_size: 100, range_steps: [], reads: [], options: [],
    best: ["fold"], chosen: "call", grade: "mistake", grade_label: "", confidence: "high", ev_loss: 20,
    tags: ["called_without_odds"], headline: "", explanation: "", details: [], caveats: [], ...o,
  };
}

function hand(n: number, ds: DecisionReview[]): HandReview {
  return {
    hand_number: n, decisions: ds, counts: { good: 0, close: 0, questionable: 0, mistake: 0, ungraded: 0 },
    net: 0, net_bb: 0, ev_loss_bb: 0, quality: "costly", verdict: "", hindsight: null, tags: [],
  };
}

describe("skill estimate", () => {
  it("starts at the prior and needs evidence before it moves far", () => {
    const none = estimateSkill([]);
    expect(none.theta).toBe(0);
    const few = estimateSkill(rep(3, 1, -1));
    const many = estimateSkill(rep(30, 1, -1));
    expect(few.theta).toBeGreaterThan(0);
    expect(many.theta).toBeGreaterThan(few.theta);
    expect(many.sd).toBeLessThan(few.sd);
  });

  it("credits harder scenarios more and counts a close answer as half", () => {
    const easy = estimateSkill(rep(6, 1, -1));
    const hard = estimateSkill(rep(6, 1, 1));
    expect(hard.theta).toBeGreaterThan(easy.theta);
    const half = estimateSkill(rep(10, 0.5, 0));
    expect(Math.abs(half.theta)).toBeLessThan(1e-6);
  });

  it("finds the posterior mode (gradient is zero there)", () => {
    const obs = [...rep(7, 1, 0), ...rep(3, 0, 0), ...rep(4, 0.5, 1)];
    const e = estimateSkill(obs);
    const grad = obs.reduce((g, o) => g + o.w * (o.y - 1 / (1 + Math.exp(-(e.theta - o.b)))), -e.theta / 1.44);
    expect(Math.abs(grad)).toBeLessThan(1e-6);
  });
});

describe("adaptive difficulty", () => {
  it("promotes on demonstrated performance, not a fixed count", () => {
    expect(adaptiveLevel(estimateSkill([]))).toBe("beginner");
    expect(adaptiveLevel(estimateSkill(rep(5, 1, -1)))).toBe("beginner"); // too few to be sure
    expect(adaptiveLevel(estimateSkill(rep(10, 1, -1)))).toBe("intermediate");
    expect(adaptiveLevel(estimateSkill([...rep(7, 1, -1), ...rep(3, 0, -1)]))).toBe("beginner");
    expect(adaptiveLevel(estimateSkill([...rep(10, 1, -1), ...rep(10, 1, 0)]))).toBe("advanced");
  });

  it("steps back down after misses at the harder level", () => {
    expect(adaptiveLevel(estimateSkill([...rep(10, 1, -1), ...rep(5, 0, 0)]))).toBe("beginner");
  });

  it("uses table decisions only at half weight and only when the grade is trusted", () => {
    let p: Progress = EMPTY_PROGRESS;
    p = recordHandReview(p, "s", hand(1, [decision({ confidence: "low" })]), T0);
    let s = conceptSkills(p).find((c) => c.concept === "pot_odds")!;
    expect(s.estimate.evidence).toBe(0);
    expect(s.tableDecisions).toBe(1); // still shown as measured
    p = recordHandReview(p, "s", hand(2, [decision({ confidence: "high" })]), T0);
    s = conceptSkills(p).find((c) => c.concept === "pot_odds")!;
    expect(s.estimate.evidence).toBe(0.5);
    expect(s.enough).toBe(false);
  });
});

describe("spaced repetition", () => {
  it("brings a missed concept back after a delay and spaces later reviews", () => {
    let p: Progress = EMPTY_PROGRESS;
    p = answer(p, "draws", "intermediate", "mistake", T0);
    expect(p.reviews).toHaveLength(1);
    expect(dueReviews(p, T0 + 5 * MIN)).toHaveLength(0);
    expect(dueReviews(p, T0 + INTERVALS[0])).toHaveLength(1);
    // Correct before it is due: no credit for cramming.
    p = answer(p, "draws", "intermediate", "good", T0 + MIN);
    expect(p.reviews[0].box).toBe(0);
    // Correct when due: next box, a day later.
    const t1 = T0 + INTERVALS[0] + 1;
    p = answer(p, "draws", "intermediate", "good", t1);
    expect(p.reviews[0]).toMatchObject({ box: 1, due: t1 + DAY });
    // A miss sends it back to the start and counts a lapse.
    p = answer(p, "draws", "intermediate", "questionable", t1 + DAY);
    expect(p.reviews[0]).toMatchObject({ box: 0, lapses: 2 });
  });

  it("graduates a concept after the last interval", () => {
    let p: Progress = answer(EMPTY_PROGRESS, "preflop", "beginner", "mistake", T0);
    let t = T0;
    for (let i = 0; i < INTERVALS.length; i++) {
      t = p.reviews[0].due;
      p = answer(p, "preflop", "beginner", "close", t);
    }
    expect(p.reviews).toHaveLength(0);
  });

  it("ignores retries and schedules confident table mistakes at the adaptive level", () => {
    let p = answer(EMPTY_PROGRESS, "draws", "beginner", "mistake", T0, "x");
    const again = recordAnswer(p, { id: "x", concept: "draws", difficulty: "beginner", grade: "good" }, T0 + DAY);
    expect(again).toBe(p);
    p = recordHandReview(p, "s", hand(1, [decision(), decision({ confidence: "low", concept: "preflop" })]), T0);
    expect(p.reviews.map((r) => r.key).sort()).toEqual(["draws|beginner", "pot_odds|auto"]);
    const next = nextPractice(p, T0 + INTERVALS[0] + 1);
    expect(next.kind).toBe("review");
    expect(["draws", "pot_odds"]).toContain(next.concept);
  });

  it("derives the same schedule from the records as step-by-step recording", () => {
    let p: Progress = EMPTY_PROGRESS;
    let t = T0;
    const grades: Grade[] = ["mistake", "good", "questionable", "good", "good", "close", "mistake", "good"];
    for (const [i, g] of grades.entries()) {
      t += i % 3 === 0 ? INTERVALS[0] + 1 : DAY + 1;
      p = answer(p, i % 2 ? "draws" : "pot_odds", "beginner", g, t);
    }
    p = recordHandReview(p, "s", hand(9, [decision({ concept: "preflop" })]), t + 1);
    const derived = withReviews(p).reviews;
    const byKey = (xs: typeof derived) => [...xs].sort((a, b) => a.key.localeCompare(b.key));
    expect(byKey(derived)).toEqual(byKey(p.reviews));
  });
});

describe("recommendation and guides", () => {
  it("recommends untried concepts first, then the weakest, with a reason", () => {
    const first = nextPractice(EMPTY_PROGRESS, T0);
    expect(first.kind).toBe("new");
    expect(first.difficulty).toBe("beginner");
    let p: Progress = EMPTY_PROGRESS;
    // Strong everywhere except value betting.
    let t = T0;
    for (const c of ["pot_odds", "draws", "bluffing", "bet_sizing", "preflop", "reads"] as const)
      for (let i = 0; i < 8; i++) p = answer(p, c, "beginner", "good", (t += 1));
    for (let i = 0; i < 8; i++) p = answer(p, "value_betting", "beginner", i < 3 ? "good" : "close", (t += 1));
    const next = nextPractice(p, t);
    expect(next.concept).toBe("value_betting");
    expect(next.reason).toBeTruthy();
  });

  it("explains recurring mistakes with the player's own numbers", () => {
    let p: Progress = EMPTY_PROGRESS;
    p = recordHandReview(p, "s", hand(1, [decision({ equity: 0.2 })]), T0);
    p = recordHandReview(p, "s", hand(2, [decision({ equity: 0.24 })]), T0);
    const [g] = mistakeGuides(p);
    expect(g.tag).toBe("called_without_odds");
    expect(g.principle).toMatch(/call ÷ \(pot \+ call\)/);
    expect(g.evidence).toMatch(/about 22% equity on average when a call needed 33%/);
  });

  it("summarises first answers in blocks for the trend", () => {
    let p: Progress = EMPTY_PROGRESS;
    for (let i = 0; i < 25; i++) p = answer(p, "pot_odds", "beginner", i < 10 ? "mistake" : "good", T0 + i);
    expect(answerTrend(p).map((x) => x.score)).toEqual([0, 1]);
  });
});

describe("reviews with a fixed difficulty", () => {
  it("practises a due review at its own level, so answering it clears the review", () => {
    // Regression: with Beginner selected, an Intermediate review was offered at Beginner,
    // and the Beginner answers never cleared it, so it stayed due forever.
    let p: Progress = EMPTY_PROGRESS;
    p = answer(p, "pot_odds", "intermediate", "mistake", T0, "miss");
    let t = T0 + INTERVALS[0] + 1;
    const plan = planFor(p, "beginner", "recommended", 0.5);
    expect(plan).toMatchObject({ concept: "pot_odds", difficulty: "intermediate", review: true });
    p = answer(p, plan.concept, plan.difficulty, "good", t, "fix");
    t += 1;
    expect(dueReviews(p, t)).toHaveLength(0);
  });

  it("does not claim a concept is one of the lowest without comparing", () => {
    const next = nextPractice(EMPTY_PROGRESS, T0, 0.5);
    expect(next.reason).not.toMatch(/one of your lowest/);
  });
});

