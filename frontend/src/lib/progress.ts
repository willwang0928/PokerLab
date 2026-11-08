/**
 * Learning progress: an append-only log of immutable records.
 *
 * Two kinds of record are kept: graded decisions from post-hand reviews and first answers
 * to training scenarios, each with a unique id. Everything shown about progress (leaks,
 * trends, skill estimates, the review schedule) is derived from that log, so two copies
 * of it (two tabs, the browser and the server) merge by a plain union without conflicts.
 * Storage and syncing live in ``store.ts`` and ``sync.ts``.
 */
import type { Concept, Grade, HandReview } from "../types";

/** The v0.3.x storage key (migrated by ``store.ts``). */
export const PROGRESS_KEY = "pokerlab.progress.v1";

export const CONCEPTS: Concept[] = ["pot_odds", "draws", "value_betting", "bluffing", "bet_sizing", "preflop", "reads"];

export const CONCEPT_LABEL: Record<Concept, string> = {
  pot_odds: "Pot odds",
  draws: "Drawing hands",
  value_betting: "Value betting",
  bluffing: "Semi-bluffing",
  bet_sizing: "Bet sizing",
  preflop: "Preflop defence",
  reads: "Opponent tendencies",
};

export const TAG_LABEL: Record<string, string> = {
  called_without_odds: "Calling without enough equity",
  folded_with_odds: "Folding when the price was right",
  missed_value: "Checking or calling strong hands",
  overaggressive: "Betting or raising when behind",
  bet_sizing: "Bet sizes that cost value",
  preflop_loose: "Playing too many hands preflop",
  preflop_tight: "Folding too much preflop",
};

/** Which training concept practises each recurring mistake. */
export const TAG_CONCEPT: Record<string, Concept> = {
  called_without_odds: "pot_odds",
  folded_with_odds: "pot_odds",
  missed_value: "value_betting",
  overaggressive: "bluffing",
  bet_sizing: "bet_sizing",
  preflop_loose: "preflop",
  preflop_tight: "preflop",
};

const GRADES: Grade[] = ["good", "close", "questionable", "mistake", "ungraded"];

export interface DecisionRecord {
  /** ``<hand>:<index of the decision in its review>``. */
  id: string;
  t: number;
  hand: string; // session id + hand number, so a review is recorded only once
  grade: Grade;
  concept: Concept;
  tags: string[];
  evLossBb: number;
  /** How far the coach trusts the grade (older records lack it). */
  confidence?: "low" | "medium" | "high";
  /** Equity you had and equity a call needed, when facing a bet. */
  equity?: number;
  required?: number;
}

export interface AnswerRecord {
  t: number;
  id: string;
  concept: Concept;
  difficulty: string;
  grade: Grade;
  /** True when the scenario was a scheduled spaced-repetition review. */
  review?: boolean;
}

/** A concept due to come back (spaced repetition), at a level or "auto". */
export interface ReviewItem {
  key: string; // concept|difficulty
  concept: Concept;
  difficulty: string; // a training difficulty, or "auto" (use the adaptive level)
  box: number; // Leitner box: 0 = just missed
  due: number; // timestamp
  lapses: number;
  source: "training" | "table";
}

export interface Progress {
  decisions: DecisionRecord[];
  answers: AnswerRecord[];
  reviews: ReviewItem[];
}

export const EMPTY_PROGRESS: Progress = { decisions: [], answers: [], reviews: [] };

const isConcept = (c: unknown): c is Concept => typeof c === "string" && (CONCEPTS as string[]).includes(c);
const isGrade = (g: unknown): g is Grade => typeof g === "string" && (GRADES as string[]).includes(g);
const isUnit = (x: unknown) => typeof x === "number" && Number.isFinite(x) && x >= 0 && x <= 1;
const isTime = (t: unknown) => typeof t === "number" && Number.isFinite(t) && t >= 1e12 && t <= 4e12;
const ID_RE = /^[A-Za-z0-9._:#|=-]{1,1000}$/;
const LEVEL_NAMES = ["beginner", "intermediate", "advanced"];

/** Why a decision record is invalid, or null. Mirrors the server's validation. */
export function decisionProblem(d: unknown): string | null {
  if (!d || typeof d !== "object") return "not an object";
  const r = d as Record<string, unknown>;
  if (typeof r.id !== "string" || !ID_RE.test(r.id)) return "id";
  if (!isTime(r.t)) return "t";
  if (typeof r.hand !== "string" || !r.hand || r.hand.length > 200) return "hand";
  if (!isGrade(r.grade)) return "grade";
  if (!isConcept(r.concept)) return "concept";
  if (!Array.isArray(r.tags) || r.tags.length > 10 || !r.tags.every((t) => typeof t === "string" && t in TAG_LABEL))
    return "tags";
  if (typeof r.evLossBb !== "number" || !Number.isFinite(r.evLossBb) || r.evLossBb < 0 || r.evLossBb > 10_000)
    return "evLossBb";
  if (r.confidence !== undefined && !["low", "medium", "high"].includes(r.confidence as string)) return "confidence";
  if (r.equity !== undefined && !isUnit(r.equity)) return "equity";
  if (r.required !== undefined && !isUnit(r.required)) return "required";
  const known = new Set(["id", "t", "hand", "grade", "concept", "tags", "evLossBb", "confidence", "equity", "required"]);
  if (Object.keys(r).some((k) => !known.has(k))) return "unknown field";
  return null;
}

/** Why an answer record is invalid, or null. Mirrors the server's validation. */
export function answerProblem(a: unknown): string | null {
  if (!a || typeof a !== "object") return "not an object";
  const r = a as Record<string, unknown>;
  if (typeof r.id !== "string" || !ID_RE.test(r.id)) return "id";
  if (!isTime(r.t)) return "t";
  if (!isConcept(r.concept)) return "concept";
  if (typeof r.difficulty !== "string" || !LEVEL_NAMES.includes(r.difficulty)) return "difficulty";
  if (!isGrade(r.grade)) return "grade";
  if (r.review !== undefined && typeof r.review !== "boolean") return "review";
  const known = new Set(["id", "t", "concept", "difficulty", "grade", "review"]);
  if (Object.keys(r).some((k) => !known.has(k))) return "unknown field";
  return null;
}

export interface Dropped {
  kind: "answers" | "decisions";
  reason: string;
  record: unknown;
}

/**
 * Split stored data into valid records and the ones that are not, keeping both. Old
 * (v0.3.x) decision records without an id get ``<hand>:<n>`` in their stored order.
 * Duplicates by id keep the first copy. Nothing is dropped silently: invalid records
 * come back in ``dropped`` so the caller can keep and report them.
 */
export function sanitizeRecords(raw: unknown): { decisions: DecisionRecord[]; answers: AnswerRecord[]; dropped: Dropped[] } {
  const r = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const dropped: Dropped[] = [];
  const perHand = new Map<string, number>();
  const seenD = new Set<string>();
  const decisions: DecisionRecord[] = [];
  for (const item of Array.isArray(r.decisions) ? r.decisions : []) {
    let d = item;
    if (d && typeof d === "object" && !("id" in d) && typeof (d as DecisionRecord).hand === "string") {
      const hand = (d as DecisionRecord).hand;
      const n = perHand.get(hand) ?? 0;
      perHand.set(hand, n + 1);
      d = { id: `${hand}:${n}`, ...(d as object) }; // v0.3.x migration
    }
    const problem = decisionProblem(d);
    if (problem) dropped.push({ kind: "decisions", reason: problem, record: item });
    else if (!seenD.has((d as DecisionRecord).id)) {
      seenD.add((d as DecisionRecord).id);
      decisions.push(d as DecisionRecord);
    }
  }
  const seenA = new Set<string>();
  const answers: AnswerRecord[] = [];
  for (const a of Array.isArray(r.answers) ? r.answers : []) {
    const problem = answerProblem(a);
    if (problem) dropped.push({ kind: "answers", reason: problem, record: a });
    else if (!seenA.has((a as AnswerRecord).id)) {
      seenA.add((a as AnswerRecord).id);
      answers.push(a as AnswerRecord);
    }
  }
  const byTime = <T extends { t: number; id: string }>(x: T, y: T) => x.t - y.t || (x.id < y.id ? -1 : x.id > y.id ? 1 : 0);
  decisions.sort(byTime);
  answers.sort(byTime);
  return { decisions, answers, dropped };
}

/**
 * Valid records as a ``Progress`` (reviews are derived elsewhere; see ``withReviews`` in
 * skills.ts). Invalid records are left out here; use ``sanitizeRecords`` to keep them.
 */
export function sanitizeProgress(raw: unknown): Progress {
  const { decisions, answers } = sanitizeRecords(raw);
  return { decisions, answers, reviews: [] };
}

/** Add a hand review's graded decisions (idempotent per session and hand). */
export function addReview(p: Progress, sessionId: string, review: HandReview, now = Date.now()): Progress {
  const hand = `${sessionId}#${review.hand_number}`;
  if (p.decisions.some((d) => d.hand === hand)) return p;
  const added: DecisionRecord[] = [];
  review.decisions.forEach((d, i) => {
    if (d.grade === "ungraded") return;
    added.push({
      id: `${hand}:${i}`,
      t: now,
      hand,
      grade: d.grade,
      concept: d.concept,
      tags: d.tags,
      evLossBb: Math.max(0, d.ev_loss / d.big_blind),
      confidence: d.confidence,
      ...(d.equity !== null && d.required_equity !== null ? { equity: d.equity, required: d.required_equity } : {}),
    });
  });
  if (!added.length) return p;
  return { ...p, decisions: [...p.decisions, ...added] };
}

/** Record a training answer. Only the first attempt at a scenario counts. */
export function addAnswer(p: Progress, a: Omit<AnswerRecord, "t">, now = Date.now()): Progress {
  if (p.answers.some((x) => x.id === a.id)) return p;
  return { ...p, answers: [...p.answers, { ...a, t: now }] };
}

/** Score for one graded choice: good = 1, close = 0.5, otherwise 0. */
export function credit(g: Grade): number {
  return g === "good" ? 1 : g === "close" ? 0.5 : 0;
}

export interface Leak {
  tag: string;
  label: string;
  count: number;
  /** Share of the recent graded decisions this leak appears in. */
  rate: number;
  concept: Concept;
}

/** Recurring mistakes in the last ``window`` graded decisions (seen at least twice). */
export function recurringLeaks(p: Progress, window = 100): Leak[] {
  const recent = p.decisions.slice(-window);
  const counts = new Map<string, number>();
  for (const d of recent) for (const t of d.tags) counts.set(t, (counts.get(t) ?? 0) + 1);
  return [...counts.entries()]
    .filter(([, n]) => n >= 2)
    .map(([tag, count]) => ({
      tag,
      label: TAG_LABEL[tag] ?? tag,
      count,
      rate: count / recent.length,
      concept: TAG_CONCEPT[tag] ?? "pot_odds",
    }))
    .sort((a, b) => b.count - a.count);
}

export interface Trend {
  recent: number | null; // average credit of the latest block
  previous: number | null; // the block before it
  recentLoss: number | null; // estimated bb given up per decision, latest block
  previousLoss: number | null;
  total: number;
}

/** Compare the latest ``block`` graded decisions with the ones before. */
export function decisionTrend(p: Progress, block = 20): Trend {
  const ds = p.decisions;
  const avg = (xs: DecisionRecord[], f: (d: DecisionRecord) => number) =>
    xs.length ? xs.reduce((s, d) => s + f(d), 0) / xs.length : null;
  const recent = ds.slice(-block);
  const previous = ds.slice(-2 * block, -block);
  return {
    recent: avg(recent, (d) => credit(d.grade)),
    previous: previous.length >= Math.min(block, 10) ? avg(previous, (d) => credit(d.grade)) : null,
    recentLoss: avg(recent, (d) => d.evLossBb),
    previousLoss: previous.length >= Math.min(block, 10) ? avg(previous, (d) => d.evLossBb) : null,
    total: ds.length,
  };
}

export interface ConceptStats {
  concept: Concept;
  attempts: number;
  /** Average credit over all first attempts (null before any). */
  accuracy: number | null;
  /** Average credit over the last 10 first attempts. */
  recent: number | null;
  /** Graded decisions at the table in this concept, and how many were flagged. */
  played: number;
  flagged: number;
}

export function conceptStats(p: Progress): ConceptStats[] {
  return CONCEPTS.map((concept) => {
    const answers = p.answers.filter((a) => a.concept === concept);
    const mean = (xs: AnswerRecord[]) => (xs.length ? xs.reduce((s, a) => s + credit(a.grade), 0) / xs.length : null);
    const played = p.decisions.filter((d) => d.concept === concept || d.tags.some((t) => TAG_CONCEPT[t] === concept));
    return {
      concept,
      attempts: answers.length,
      accuracy: mean(answers),
      recent: mean(answers.slice(-10)),
      played: played.length,
      flagged: played.filter((d) => d.grade === "questionable" || d.grade === "mistake").length,
    };
  });
}
