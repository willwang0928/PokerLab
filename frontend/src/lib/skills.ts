/**
 * Personalised learning on top of the progress store: skill estimates, adaptive
 * difficulty, spaced repetition, the next-practice recommendation and mistake guides.
 *
 * **Measured vs estimated.** "Measured" numbers are plain counts (7 of 10 first answers
 * good). The *estimated skill* is a statistical model of those answers that accounts for
 * how hard each one was and how few there are:
 *
 *   P(good answer) = logistic(skill − difficulty)
 *
 * with difficulty −1 / 0 / +1 for Beginner / Intermediate / Advanced scenarios, a close
 * answer counted as half a success, and a Normal(0, PRIOR_SD²) prior on skill. The
 * posterior mode is found by Newton's method and its uncertainty by the usual normal
 * (Laplace) approximation. With few answers the prior dominates and the interval is
 * wide, so a lucky streak cannot produce a confident verdict.
 *
 * Decisions at the table count at half weight (they are graded with less certainty and
 * their difficulty is unknown), and only when the coach rated its confidence medium or
 * high. None of this measures poker profitability: it measures agreement with
 * PokerLab's own analysis on its own scenarios.
 */
import type { Concept, Grade, TrainingDifficulty } from "../types";
import {
  addAnswer,
  addReview,
  type AnswerRecord,
  CONCEPT_LABEL,
  CONCEPTS,
  credit,
  type DecisionRecord,
  type Progress,
  recurringLeaks,
  type ReviewItem,
  TAG_CONCEPT,
  TAG_LABEL,
} from "./progress";
import type { HandReview } from "../types";

export const LEVELS: TrainingDifficulty[] = ["beginner", "intermediate", "advanced"];
export const LEVEL_LABEL: Record<TrainingDifficulty, string> = {
  beginner: "Beginner",
  intermediate: "Intermediate",
  advanced: "Advanced",
};
export const LEVEL_DIFFICULTY: Record<TrainingDifficulty, number> = { beginner: -1, intermediate: 0, advanced: 1 };
export const PRIOR_SD = 1.2;
const TABLE_WEIGHT = 0.5;
/** Below this much evidence (first answers, table decisions at half weight) no estimate is shown. */
export const MIN_EVIDENCE = 5;
/** Move up a level only when the pessimistic estimate still expects this score there. */
export const PROMOTE_AT = 0.6;
const Z80 = 1.2816; // two-sided 80% interval
const Z_LOW = 0.8416; // one-sided 80% lower bound

const sigmoid = (x: number) => 1 / (1 + Math.exp(-x));

function levelOf(difficulty: string): TrainingDifficulty {
  return (LEVELS as string[]).includes(difficulty) ? (difficulty as TrainingDifficulty) : "intermediate";
}

// ---------------------------------------------------------------- skill estimate

export interface Observation {
  y: number; // 0, 0.5 or 1
  b: number; // difficulty
  w: number; // weight
}

export interface SkillEstimate {
  theta: number; // posterior mode of skill
  sd: number; // posterior standard deviation
  evidence: number; // total weight of observations
}

/** Posterior mode and spread of skill given graded answers (Newton's method). */
export function estimateSkill(obs: Observation[]): SkillEstimate {
  let theta = 0;
  let hess = -1 / PRIOR_SD ** 2;
  for (let i = 0; i < 50; i++) {
    let grad = -theta / PRIOR_SD ** 2;
    hess = -1 / PRIOR_SD ** 2;
    for (const o of obs) {
      const p = sigmoid(theta - o.b);
      grad += o.w * (o.y - p);
      hess -= o.w * p * (1 - p);
    }
    const step = grad / hess;
    theta -= step;
    if (Math.abs(step) < 1e-9) break;
  }
  return { theta, sd: 1 / Math.sqrt(-hess), evidence: obs.reduce((s, o) => s + o.w, 0) };
}

/** Expected first-try score at a level, with an 80% interval. */
export function expectedScore(e: SkillEstimate, level: TrainingDifficulty) {
  const b = LEVEL_DIFFICULTY[level];
  return {
    mean: sigmoid(e.theta - b),
    low: sigmoid(e.theta - Z80 * e.sd - b),
    high: sigmoid(e.theta + Z80 * e.sd - b),
  };
}

function inConcept(d: DecisionRecord, c: Concept): boolean {
  return d.concept === c || d.tags.some((t) => TAG_CONCEPT[t] === c);
}

export function observationsFor(p: Progress, concept: Concept): Observation[] {
  const obs: Observation[] = [];
  for (const a of p.answers) {
    if (a.concept !== concept || a.grade === "ungraded") continue;
    obs.push({ y: credit(a.grade), b: LEVEL_DIFFICULTY[levelOf(a.difficulty)], w: 1 });
  }
  for (const d of p.decisions) {
    if (!inConcept(d, concept) || d.grade === "ungraded") continue;
    if (d.confidence !== "medium" && d.confidence !== "high") continue;
    obs.push({ y: credit(d.grade), b: 0, w: TABLE_WEIGHT });
  }
  return obs;
}

/**
 * The level to practise next: the hardest one where even a pessimistic reading of the
 * evidence (one-sided 80% lower bound) expects a first-try score of at least
 * ``PROMOTE_AT``. A run of lucky answers moves nobody up; a run of misses moves them
 * back down.
 */
export function adaptiveLevel(e: SkillEstimate): TrainingDifficulty {
  let level: TrainingDifficulty = "beginner";
  for (const l of LEVELS) {
    if (sigmoid(e.theta - Z_LOW * e.sd - LEVEL_DIFFICULTY[l]) >= PROMOTE_AT) level = l;
  }
  return level;
}

export interface ConceptSkill {
  concept: Concept;
  label: string;
  /** Measured: first training answers. */
  attempts: number;
  good: number;
  close: number;
  /** Measured: graded table decisions in this concept (any confidence). */
  tableDecisions: number;
  tableFlagged: number;
  estimate: SkillEstimate;
  /** True once there is enough evidence to show an estimate. */
  enough: boolean;
  /** Expected first-try score on an Intermediate scenario (80% interval). */
  score: { mean: number; low: number; high: number };
  level: TrainingDifficulty;
  /** Accuracy of the last 10 first answers vs the 10 before (measured). */
  recent: number | null;
  previous: number | null;
}

export function conceptSkills(p: Progress): ConceptSkill[] {
  return CONCEPTS.map((concept) => {
    const answers = p.answers.filter((a) => a.concept === concept && a.grade !== "ungraded");
    const table = p.decisions.filter((d) => inConcept(d, concept) && d.grade !== "ungraded");
    const estimate = estimateSkill(observationsFor(p, concept));
    const mean = (xs: AnswerRecord[]) => (xs.length ? xs.reduce((s, a) => s + credit(a.grade), 0) / xs.length : null);
    return {
      concept,
      label: CONCEPT_LABEL[concept],
      attempts: answers.length,
      good: answers.filter((a) => a.grade === "good").length,
      close: answers.filter((a) => a.grade === "close").length,
      tableDecisions: table.length,
      tableFlagged: table.filter((d) => d.grade === "questionable" || d.grade === "mistake").length,
      estimate,
      enough: estimate.evidence >= MIN_EVIDENCE,
      score: expectedScore(estimate, "intermediate"),
      level: adaptiveLevel(estimate),
      recent: answers.length >= 10 ? mean(answers.slice(-10)) : null,
      previous: answers.length >= 20 ? mean(answers.slice(-20, -10)) : null,
    };
  });
}

// ---------------------------------------------------------------- spaced repetition

const MINUTE = 60_000;
const DAY = 24 * 60 * MINUTE;
/** Waiting time after each successful review (Leitner boxes). Box 0 = just missed. */
export const INTERVALS = [10 * MINUTE, 1 * DAY, 3 * DAY, 7 * DAY, 21 * DAY];

function missed(g: Grade): boolean {
  return g === "questionable" || g === "mistake";
}

/** Schedule (or reschedule) a concept after a miss. */
function lapse(reviews: ReviewItem[], key: string, concept: Concept, difficulty: string, source: ReviewItem["source"], now: number) {
  const old = reviews.find((r) => r.key === key);
  const item: ReviewItem = {
    key,
    concept,
    difficulty,
    box: 0,
    due: now + INTERVALS[0],
    lapses: (old?.lapses ?? 0) + 1,
    source,
  };
  return [...reviews.filter((r) => r.key !== key), item];
}

/**
 * Update the schedule after a training answer. A miss puts the concept (at that level)
 * back to box 0, due in ten minutes. A good or close answer to a concept that is due
 * moves it to the next box with a longer interval; after the last box it graduates.
 * Answering correctly before the review is due changes nothing (no cramming credit).
 */
export function scheduleAnswer(reviews: ReviewItem[], a: Omit<AnswerRecord, "t">, now: number): ReviewItem[] {
  const key = `${a.concept}|${a.difficulty}`;
  if (missed(a.grade)) return lapse(reviews, key, a.concept, a.difficulty, "training", now);
  if (a.grade === "ungraded") return reviews;
  const due = reviews.filter(
    (r) => r.concept === a.concept && (r.key === key || r.difficulty === "auto") && r.due <= now,
  );
  let out = reviews;
  for (const r of due) {
    const box = r.box + 1;
    out = out.filter((x) => x.key !== r.key);
    if (box < INTERVALS.length) out = [...out, { ...r, box, due: now + INTERVALS[box] }];
  }
  return out;
}

/** Table mistakes the coach is reasonably sure about schedule a review of the concept. */
export function scheduleReview(reviews: ReviewItem[], review: HandReview, now: number): ReviewItem[] {
  let out = reviews;
  for (const d of review.decisions) {
    if (!missed(d.grade) || d.confidence === "low") continue;
    out = lapse(out, `${d.concept}|auto`, d.concept, "auto", "table", now);
  }
  return out;
}

/**
 * The review schedule implied by the records, replayed in time order. It is the same
 * schedule ``recordAnswer`` and ``recordHandReview`` build step by step, but computed from
 * the log alone, so it is never stored and can never disagree with the history (after a
 * merge with another tab or the server, for example).
 */
export function deriveReviews(decisions: DecisionRecord[], answers: AnswerRecord[]): ReviewItem[] {
  type Ev = { t: number; order: number; id: string; apply: (r: ReviewItem[]) => ReviewItem[] };
  const events: Ev[] = [];
  for (const d of decisions) {
    if (!missed(d.grade) || d.confidence === "low" || d.confidence === undefined) continue;
    events.push({ t: d.t, order: 0, id: d.id, apply: (r) => lapse(r, `${d.concept}|auto`, d.concept, "auto", "table", d.t) });
  }
  for (const a of answers) events.push({ t: a.t, order: 1, id: a.id, apply: (r) => scheduleAnswer(r, a, a.t) });
  events.sort((x, y) => x.t - y.t || x.order - y.order || (x.id < y.id ? -1 : x.id > y.id ? 1 : 0));
  return events.reduce((r, e) => e.apply(r), [] as ReviewItem[]);
}

/** ``p`` with its review schedule recomputed from the records. */
export function withReviews(p: Pick<Progress, "decisions" | "answers">): Progress {
  return { decisions: p.decisions, answers: p.answers, reviews: deriveReviews(p.decisions, p.answers) };
}

export function dueReviews(p: Progress, now: number): ReviewItem[] {
  return p.reviews.filter((r) => r.due <= now).sort((a, b) => a.due - b.due);
}

/** Record a training answer and update the schedule. */
export function recordAnswer(p: Progress, a: Omit<AnswerRecord, "t">, now = Date.now()): Progress {
  const next = addAnswer(p, a, now);
  if (next === p) return p; // a retry: neither recorded nor rescheduled
  return { ...next, reviews: scheduleAnswer(next.reviews, a, now) };
}

/** Record a hand review and schedule reviews for confident mistakes. */
export function recordHandReview(p: Progress, sessionId: string, review: HandReview, now = Date.now()): Progress {
  const next = addReview(p, sessionId, review, now);
  if (next === p) return p;
  return { ...next, reviews: scheduleReview(next.reviews, review, now) };
}

// ---------------------------------------------------------------- recommendation

export interface NextPractice {
  concept: Concept;
  difficulty: TrainingDifficulty;
  kind: "review" | "leak" | "weak" | "new" | "maintain";
  reason: string;
  /** Set when this practice is a scheduled review. */
  reviewKey?: string;
}

function ago(ms: number): string {
  const m = Math.round(ms / MINUTE);
  if (m < 60) return `${Math.max(1, m)} min ago`;
  const h = Math.round(m / 60);
  if (h < 36) return `${h} h ago`;
  return `${Math.round(h / 24)} days ago`;
}

/**
 * The next thing to practise, with the reason. Scheduled reviews come first; then
 * concepts weighted by recurring table leaks, low estimated skill and missing evidence.
 * ``rand`` (0..1) picks among weighted concepts; omit it for the single top choice.
 */
export function nextPractice(p: Progress, now = Date.now(), rand?: number): NextPractice {
  const skills = conceptSkills(p);
  const byConcept = new Map(skills.map((s) => [s.concept, s]));
  const due = dueReviews(p, now);
  if (due.length) {
    const r = due[0];
    const level = r.difficulty === "auto" ? byConcept.get(r.concept)!.level : levelOf(r.difficulty);
    const when = ago(now - (r.due - INTERVALS[r.box]));
    const what = r.source === "table" ? "a decision at the table" : `a ${LEVEL_LABEL[level]} scenario`;
    return {
      concept: r.concept,
      difficulty: level,
      kind: "review",
      reviewKey: r.key,
      reason:
        r.box === 0
          ? `Review: you missed ${what} in ${CONCEPT_LABEL[r.concept].toLowerCase()} ${when}. Coming back to it after a gap helps it stick.`
          : `Review: ${CONCEPT_LABEL[r.concept].toLowerCase()} is due again (step ${r.box + 1} of ${INTERVALS.length}).`,
    };
  }
  const leaks = recurringLeaks(p);
  const weights = skills.map((s) => {
    const leak = leaks.filter((l) => l.concept === s.concept).reduce((n, l) => n + l.count, 0);
    let w = 1 + Math.min(4, leak * 0.75);
    w += s.enough ? 3 * (1 - s.score.mean) : 1.5;
    return w;
  });
  let i = weights.indexOf(Math.max(...weights));
  if (rand !== undefined) {
    const total = weights.reduce((a, b) => a + b, 0);
    let x = Math.min(Math.max(rand, 0), 0.999999) * total;
    i = 0;
    while (i < weights.length - 1 && x >= weights[i]) x -= weights[i++];
  }
  const s = skills[i];
  const leak = leaks.find((l) => l.concept === s.concept);
  if (leak)
    return {
      concept: s.concept,
      difficulty: s.level,
      kind: "leak",
      reason: `At the table, "${leak.label}" came up ${leak.count} times recently.`,
    };
  if (!s.enough)
    return {
      concept: s.concept,
      difficulty: s.level,
      kind: "new",
      reason:
        s.attempts === 0
          ? "You have not practised this yet."
          : `Only ${s.attempts} answer${s.attempts === 1 ? "" : "s"} so far: a few more give a reliable estimate.`,
    };
  if (s.score.mean < 0.7)
    return {
      concept: s.concept,
      difficulty: s.level,
      kind: "weak",
      reason: `Estimated skill here is one of your lowest (about ${Math.round(s.score.mean * 100)}% on Intermediate scenarios).`,
    };
  return { concept: s.concept, difficulty: s.level, kind: "maintain", reason: "Keeping this skill sharp." };
}

// ---------------------------------------------------------------- mistake guides

export interface MistakeGuide {
  tag: string;
  label: string;
  concept: Concept;
  count: number;
  principle: string;
  fix: string;
  /** What the records show, when there are numbers to back it. */
  evidence: string | null;
}

const GUIDES: Record<string, { principle: string; fix: string }> = {
  called_without_odds: {
    principle:
      "A call is profitable only when your equity is above call ÷ (pot + call). Calls below that lose chips on average, however often they happen to win.",
    fix: "Before calling, work out the price, then ask which hands bet like this. Against a big bet from a narrow range, fold hands that only beat bluffs.",
  },
  folded_with_odds: {
    principle:
      "Folding gives up your whole share of the pot. When your equity is above the price, folding loses more on average than calling.",
    fix: "Count your outs and compare with the price before folding draws or medium hands to small bets. Small bets need little equity to call.",
  },
  missed_value: {
    principle:
      "With a strong hand, betting earns chips from worse hands that call. Checking lets them see free cards and puts nothing in the pot.",
    fix: "When you are well ahead of the hands that would call, bet. Slow-playing is occasionally fine, but should be the exception.",
  },
  overaggressive: {
    principle:
      "A bet or raise without enough equity relies on folds. Against a range that defends at the minimum-defence frequency, pure bluffs only break even, so bets need equity or draws behind them.",
    fix: "Bet weak hands mainly when they have a real draw (a semi-bluff). With little equity and no draw, check.",
  },
  bet_sizing: {
    principle:
      "The size decides which hands call and how much they pay. A size that is too small undercharges worse hands; one that is too big folds them out.",
    fix: "Compare the sizes in the review: pick the one that worse hands still call. PokerLab's sizing model is one-street, so treat it as a guide.",
  },
  preflop_loose: {
    principle: "Weak hands lose more preflop than they win later, especially out of position and against a raise.",
    fix: "Against a raise, keep calls to hands with enough equity against the raiser's range; fold the bottom of your range.",
  },
  preflop_tight: {
    principle:
      "Heads-up, the blinds are a big share of every pot, so folding too often gives them away. Most hands are playable from the small blind.",
    fix: "Open-raise most hands from the small blind and defend the big blind with any hand that has the equity the price asks for.",
  },
};

export function mistakeGuides(p: Progress): MistakeGuide[] {
  return recurringLeaks(p).map((l) => {
    const g = GUIDES[l.tag] ?? { principle: "", fix: "" };
    const withNumbers = p.decisions
      .slice(-100)
      .filter((d) => d.tags.includes(l.tag) && d.equity !== undefined && d.required !== undefined);
    let evidence: string | null = null;
    if (withNumbers.length >= 2) {
      const eq = withNumbers.reduce((s, d) => s + (d.equity ?? 0), 0) / withNumbers.length;
      const need = withNumbers.reduce((s, d) => s + (d.required ?? 0), 0) / withNumbers.length;
      evidence = `Across ${withNumbers.length} of these spots you had about ${Math.round(eq * 100)}% equity on average when a call needed ${Math.round(need * 100)}%.`;
    }
    return {
      tag: l.tag,
      label: TAG_LABEL[l.tag] ?? l.tag,
      concept: l.concept,
      count: l.count,
      principle: g.principle,
      fix: g.fix,
      evidence,
    };
  });
}

// ---------------------------------------------------------------- trends

export interface TrendPoint {
  index: number; // position of the block's last answer
  score: number; // average credit in the block
  n: number;
  t: number; // time of the block's last answer
}

/** Average first-try score in consecutive blocks of ``block`` answers. */
export function answerTrend(p: Progress, block = 10): TrendPoint[] {
  const xs = p.answers.filter((a) => a.grade !== "ungraded");
  const out: TrendPoint[] = [];
  for (let i = 0; i + block <= xs.length; i += block) {
    const chunk = xs.slice(i, i + block);
    out.push({
      index: i + block,
      score: chunk.reduce((s, a) => s + credit(a.grade), 0) / chunk.length,
      n: chunk.length,
      t: chunk[chunk.length - 1].t,
    });
  }
  return out;
}
