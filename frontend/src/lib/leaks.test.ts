import { describe, expect, it } from "vitest";
import { callingLeak, leakReport } from "../test/leaks";
import { changeText, findingsFor, leakPlan } from "./leaks";
import { answerProblem, EMPTY_PROGRESS } from "./progress";
import { nextPractice, recordAnswer } from "./skills";

const NOW = 2_000_000_000_000;

describe("leak plans and links", () => {
  it("practises a leak's own concept at the adaptive level, with its focus", () => {
    expect(leakPlan(callingLeak, EMPTY_PROGRESS)).toEqual({
      concept: "pot_odds",
      difficulty: "beginner",
      reason: "Practising your leak: calling bets without the odds (on the river)",
      review: false,
      focus: "river",
      leak: "called_without_odds",
    });
    const noFocus = { ...callingLeak, recommendation: { ...callingLeak.recommendation!, focus: null, focus_label: null } };
    expect(leakPlan(noFocus, EMPTY_PROGRESS)).toMatchObject({ focus: null, reason: "Practising your leak: calling bets without the odds" });
  });

  it("finds the findings a recorded decision supports, by hand and decision", () => {
    const id = callingLeak.hands[0].id;
    expect(findingsFor(leakReport, id, 2).map((f) => f.key)).toEqual(["called_without_odds"]);
    expect(findingsFor(leakReport, id, 0)).toEqual([]);
    expect(findingsFor(leakReport, "other", 2)).toEqual([]);
    expect(findingsFor(null, id, 2)).toEqual([]);
  });

  it("words each kind of change without overclaiming", () => {
    const base = { before: { flagged: 8, opportunities: 10, rate: 0.8 }, after: { flagged: 2, opportunities: 10, rate: 0.2 } };
    const w = { before: "before", after: "since" };
    expect(changeText({ ...base, change: "improving" }, w)).toMatch(/^Improving/);
    expect(changeText({ ...base, change: "worsening" }, w)).toMatch(/^More often flagged since/);
    expect(changeText({ ...base, change: "no_clear_change" }, w)).toMatch(/within normal variation/);
    expect(changeText({ ...base, change: "insufficient" }, w)).toMatch(/at least 8 on each side/);
  });
});

describe("answers aimed at a leak", () => {
  const a = { id: "v2.beginner.pot_odds.3.river", t: NOW, concept: "pot_odds", difficulty: "beginner", grade: "good" };
  it("validates the leak field like the server", () => {
    expect(answerProblem({ ...a, leak: "called_without_odds" })).toBeNull();
    expect(answerProblem({ ...a, leak: "pot_odds_mixed" })).toBeNull();
    expect(answerProblem({ ...a, leak: "nonsense" })).toBe("leak");
    expect(answerProblem({ ...a, leak: 3 })).toBe("leak");
    expect(answerProblem(a)).toBeNull(); // older answers have no leak
  });

  it("are kept when recorded", () => {
    const p = recordAnswer(EMPTY_PROGRESS, { id: a.id, concept: "pot_odds", difficulty: "beginner", grade: "good", leak: "called_without_odds" }, NOW);
    expect(p.answers[0].leak).toBe("called_without_odds");
  });
});

describe("next practice with the leak report", () => {
  it("recommends the strongest leak, and the report replaces device-local flag counts", () => {
    const rec = nextPractice(EMPTY_PROGRESS, NOW, undefined, [callingLeak]);
    expect(rec).toMatchObject({ kind: "leak", concept: "pot_odds", leak: "called_without_odds", focus: "river" });
    // With the report available but no leak, local counts do not produce a "leak" reason.
    let p = EMPTY_PROGRESS;
    for (let i = 0; i < 3; i++)
      p = {
        ...p,
        decisions: [
          ...p.decisions,
          { id: `h:${i}`, t: NOW - i, hand: `h${i}`, grade: "mistake", concept: "value_betting", tags: ["missed_value"], evLossBb: 1 },
        ],
      };
    expect(nextPractice(p, NOW).kind).toBe("leak"); // no report: the old local behaviour
    expect(nextPractice(p, NOW, undefined, []).kind).not.toBe("leak");
  });

  it("still varies the concept sometimes when picking at random", () => {
    expect(nextPractice(EMPTY_PROGRESS, NOW, 0.1, [callingLeak]).kind).toBe("leak");
    expect(nextPractice(EMPTY_PROGRESS, NOW, 0.9, [callingLeak]).kind).not.toBe("leak");
  });
});
