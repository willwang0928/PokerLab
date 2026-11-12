/**
 * Leaks on the client (v0.4.4): text and links around the server's leak report.
 *
 * The report itself (what is a leak, how strong the evidence is, trends) is computed on
 * the server from the saved hands (``backend/pokerlab/coach/leaks.py``); nothing here
 * re-decides it. This module turns a report item into a practice plan, finds which
 * findings a recorded decision supports, and words the comparisons.
 */
import type { Plan } from "../components/TrainingView";
import type { LeakItem, LeakReport, RateComparison } from "../types";
import type { Progress } from "./progress";
import { conceptSkills } from "./skills";

/** The principle behind each weakness (the "pot_odds_mixed" finding has its own). */
export const LEAK_PRINCIPLE: Record<string, string> = {
  called_without_odds:
    "A call is profitable only when your equity is above call ÷ (pot + call). Calls below that lose chips on average, however often they happen to win.",
  folded_with_odds:
    "Folding gives up your whole share of the pot. When your equity is above the price, folding loses more on average than calling.",
  pot_odds_mixed:
    "Both kinds of error come from the same step: estimating your equity against the hands that bet like this. Being looser or tighter overall would fix one by worsening the other.",
  missed_value:
    "With a strong hand, betting earns chips from worse hands that call. Checking lets them see free cards and puts nothing in the pot.",
  overaggressive:
    "A bet or raise without enough equity relies on folds. Against a range that defends at the minimum-defence frequency, pure bluffs only break even, so bets need equity or draws behind them.",
  bet_sizing:
    "The size decides which hands call and how much they pay. Too small undercharges worse hands; too big folds them out.",
  preflop_loose: "Weak hands lose more preflop than they win later, especially out of position and against a raise.",
  preflop_tight:
    "Heads-up, the blinds are a big share of every pot, so folding too often gives them away. Most hands have the equity a single raise asks for.",
};

/** A practice plan for a leak: its concept at your adaptive level, with its focus. */
export function leakPlan(item: LeakItem, progress: Progress): Plan {
  const concept = item.recommendation?.concept ?? item.concept;
  const level = conceptSkills(progress).find((s) => s.concept === concept)!.level;
  const focus = item.recommendation?.focus ?? null;
  return {
    concept,
    difficulty: level,
    reason: `Practising your leak: ${item.title.toLowerCase()}` + (focus ? ` (${item.recommendation?.focus_label})` : ""),
    review: false,
    focus,
    leak: item.key,
  };
}

/** Findings (leaks first, then early signals) that a recorded decision is evidence for. */
export function findingsFor(report: LeakReport | null, handId: string, decision: number): LeakItem[] {
  if (!report) return [];
  return [...report.leaks, ...report.watch].filter((i) =>
    i.hands.some((h) => h.id === handId && h.decision === decision),
  );
}

export function pct(x: number | null | undefined): string {
  return x === null || x === undefined ? "–" : `${Math.round(x * 100)}%`;
}

/** A comparison of flagged rates in plain words, never claiming more than the test does. */
export function changeText(c: RateComparison, what: { before: string; after: string }): string {
  const b = `${c.before.flagged} of ${c.before.opportunities}`;
  const a = `${c.after.flagged} of ${c.after.opportunities}`;
  switch (c.change) {
    case "improving":
      return `Improving: flagged in ${a} ${what.after} (${pct(c.after.rate)}) versus ${b} ${what.before} (${pct(c.before.rate)}). That gap is larger than chance alone would usually produce.`;
    case "worsening":
      return `More often flagged ${what.after}: ${a} (${pct(c.after.rate)}) versus ${b} ${what.before} (${pct(c.before.rate)}).`;
    case "no_clear_change":
      return `No clear change yet: ${a} ${what.after} (${pct(c.after.rate)}) versus ${b} ${what.before} (${pct(c.before.rate)}) is within normal variation.`;
    default:
      return `Not enough decisions yet to compare (${a} ${what.after}, ${b} ${what.before}; at least 8 on each side are needed).`;
  }
}
