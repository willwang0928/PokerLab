/**
 * Learning progress, kept in the browser so it survives new games and server restarts.
 *
 * Two streams are stored: graded decisions from post-hand reviews, and training answers.
 * Everything derived from them (recurring leaks, trends, per-concept accuracy, the
 * recommended practice concept) is computed here, so it can be unit tested.
 */
import type { Concept, Grade, HandReview } from "../types";

export const PROGRESS_KEY = "pokerlab.progress.v1";
const MAX_DECISIONS = 600;
const MAX_ANSWERS = 1000;

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

/** Drop anything malformed (old versions, hand-edited storage) instead of crashing. */
export function sanitizeProgress(raw: unknown): Progress {
  if (!raw || typeof raw !== "object") return { decisions: [], answers: [], reviews: [] };
  const r = raw as Record<string, unknown>;
  const decisions = (Array.isArray(r.decisions) ? r.decisions : [])
    .filter(
      (d): d is DecisionRecord =>
        !!d &&
        typeof d === "object" &&
        typeof d.t === "number" &&
        typeof d.hand === "string" &&
        isGrade(d.grade) &&
        isConcept(d.concept) &&
        Array.isArray(d.tags) &&
        d.tags.every((t: unknown) => typeof t === "string") &&
        typeof d.evLossBb === "number" &&
        Number.isFinite(d.evLossBb) &&
        (d.confidence === undefined || ["low", "medium", "high"].includes(d.confidence)) &&
        (d.equity === undefined || (typeof d.equity === "number" && d.equity >= 0 && d.equity <= 1)) &&
        (d.required === undefined || (typeof d.required === "number" && d.required >= 0 && d.required <= 1)),
    )
    .slice(-MAX_DECISIONS);
  const answers = (Array.isArray(r.answers) ? r.answers : [])
    .filter(
      (a): a is AnswerRecord =>
        !!a &&
        typeof a === "object" &&
        typeof a.t === "number" &&
        typeof a.id === "string" &&
        isConcept(a.concept) &&
        typeof a.difficulty === "string" &&
        isGrade(a.grade) &&
        (a.review === undefined || typeof a.review === "boolean"),
    )
    .slice(-MAX_ANSWERS);
  const seen = new Set<string>();
  const reviews = (Array.isArray(r.reviews) ? r.reviews : []).filter((x): x is ReviewItem => {
    const ok =
      !!x &&
      typeof x === "object" &&
      typeof x.key === "string" &&
      isConcept(x.concept) &&
      typeof x.difficulty === "string" &&
      Number.isInteger(x.box) &&
      x.box >= 0 &&
      typeof x.due === "number" &&
      Number.isFinite(x.due) &&
      Number.isInteger(x.lapses) &&
      (x.source === "training" || x.source === "table") &&
      !seen.has(x.key);
    if (ok) seen.add(x.key);
    return ok;
  });
  return { decisions, answers, reviews };
}

export function loadProgress(): Progress {
  try {
    const raw = localStorage.getItem(PROGRESS_KEY);
    return raw ? sanitizeProgress(JSON.parse(raw)) : { ...EMPTY_PROGRESS };
  } catch {
    return { ...EMPTY_PROGRESS };
  }
}

export function saveProgress(p: Progress): void {
  try {
    localStorage.setItem(PROGRESS_KEY, JSON.stringify(p));
  } catch {
    /* storage full or blocked: progress just isn't kept */
  }
}

/** Add a hand review's graded decisions (idempotent per session and hand). */
export function addReview(p: Progress, sessionId: string, review: HandReview, now = Date.now()): Progress {
  const hand = `${sessionId}#${review.hand_number}`;
  if (p.decisions.some((d) => d.hand === hand)) return p;
  const added: DecisionRecord[] = review.decisions
    .filter((d) => d.grade !== "ungraded")
    .map((d) => ({
      t: now,
      hand,
      grade: d.grade,
      concept: d.concept,
      tags: d.tags,
      evLossBb: d.ev_loss / d.big_blind,
      confidence: d.confidence,
      ...(d.equity !== null && d.required_equity !== null ? { equity: d.equity, required: d.required_equity } : {}),
    }));
  return { ...p, decisions: [...p.decisions, ...added].slice(-MAX_DECISIONS) };
}

/** Record a training answer. Only the first attempt at a scenario counts. */
export function addAnswer(p: Progress, a: Omit<AnswerRecord, "t">, now = Date.now()): Progress {
  if (p.answers.some((x) => x.id === a.id)) return p;
  return { ...p, answers: [...p.answers, { ...a, t: now }].slice(-MAX_ANSWERS) };
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
