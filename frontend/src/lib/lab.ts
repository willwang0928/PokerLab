/**
 * Display helpers for the Simulation Lab (v0.5.0). Every statistic is computed on the
 * server (backend/pokerlab/lab); this module only words and formats it.
 */
import type { LabConfig, LabEstimateStats, LabStrategy, MatchupReport } from "../types";
import { count, signed } from "./analytics";

export const HAND_PRESETS = [1_000, 5_000, 20_000, 50_000] as const;

export const FAMILY_LABEL: Record<LabStrategy["family"], string> = {
  opponent: "PokerLab opponents",
  reference: "Reference bots",
  style: "Fixed styles with leaks",
};

/** "under a second", "40 s", "3 min", "1 h 20 min". */
export function duration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 1) return "under a second";
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const m = Math.round(seconds / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  return `${h} h${m % 60 ? ` ${m % 60} min` : ""}`;
}

export function pText(p: number | null): string {
  if (p === null) return "—";
  if (p < 0.001) return "p < 0.001";
  return `p = ${p < 0.01 ? p.toFixed(3) : p.toFixed(2)}`;
}

/** "+12.3" bb/100 (or "—"). */
export function rate(v: number | null | undefined, digits = 1): string {
  return v === null || v === undefined ? "—" : signed(v, digits);
}

export function intervalText(e: Pick<LabEstimateStats, "low" | "high">): string | null {
  if (e.low === null || e.high === null) return null;
  return `${signed(e.low, 1)} to ${signed(e.high, 1)}`;
}

type Named = { a_label: string; b_label: string; estimate: LabEstimateStats };

export function headline(m: Named): string {
  switch (m.estimate.verdict) {
    case "a_better":
      return `${m.a_label} beats ${m.b_label}`;
    case "b_better":
      return `${m.b_label} beats ${m.a_label}`;
    case "unclear":
      return "No clear difference";
    default:
      return "Too few hands";
  }
}

export const VERDICT_TONE: Record<LabEstimateStats["verdict"], string> = {
  a_better: "bg-sky-500/15 text-sky-100 ring-sky-400/30",
  b_better: "bg-orange-500/15 text-orange-100 ring-orange-400/30",
  unclear: "bg-white/5 text-slate-200 ring-white/15",
  insufficient: "bg-white/5 text-slate-300 ring-white/10",
};

/** What the estimate shows and does not show, in a sentence or two. */
export function verdictSentence(m: Named & { hands: number }): string {
  const e = m.estimate;
  const level = Math.round(e.level * 100);
  const iv = intervalText(e);
  if (e.verdict === "insufficient" || iv === null) return "Not enough hands to estimate anything yet.";
  if (e.verdict === "unclear") {
    const more =
      e.hands_for_precision && e.hands_for_precision > m.hands
        ? ` About ${count(roundNice(e.hands_for_precision))} hands would narrow it to ±5 bb/100.`
        : "";
    return `${m.a_label}'s result against ${m.b_label} is somewhere from ${iv} bb/100 (${level}% interval), which includes zero: these hands cannot tell the two apart.${more}`;
  }
  const winner = e.verdict === "a_better" ? m.a_label : m.b_label;
  const loser = e.verdict === "a_better" ? m.b_label : m.a_label;
  const lo = e.verdict === "a_better" ? e.low! : -e.high!;
  return `${winner} wins by at least ${signed(lo, 1).replace("+", "")} bb/100 against ${loser} (the ${level}% interval for ${m.a_label} is ${iv}, entirely ${e.verdict === "a_better" ? "above" : "below"} zero).`;
}

/** Round to two significant figures ("2,300", not "2,287"). */
export function roundNice(n: number): number {
  if (n < 100) return Math.ceil(n);
  const p = 10 ** (Math.floor(Math.log10(n)) - 1);
  return Math.ceil(n / p) * p;
}

/** One line on how much duplicate dealing helped, or null. */
export function reductionText(m: MatchupReport): string | null {
  const v = m.variance;
  if (!v) return null;
  if (v.reduction === null) return "Every deal cancelled exactly: both strategies played the swapped cards identically.";
  if (v.reduction < 1.15)
    return "Duplicate dealing barely helped here: the result depends on the strategies' own random choices more than on the cards.";
  return `Duplicate dealing made these ${count(m.hands)} hands as precise as about ${count(roundNice(v.equivalent_hands ?? 0))} ordinary hands (variance ${v.reduction.toFixed(1)}× smaller).`;
}

/** The command that reproduces an experiment exactly. */
export function cliCommand(c: LabConfig): string {
  const parts = ["python -m pokerlab.lab", ...c.strategies, `--hands ${c.hands}`, `--seed ${c.seed}`];
  if (c.mode !== "duplicate") parts.push(`--mode ${c.mode}`);
  if (c.stack_bb !== 100) parts.push(`--stack ${c.stack_bb}`);
  if (c.session_hands !== 200) parts.push(`--session ${c.session_hands}`);
  return parts.join(" ");
}

export function newSeed(): number {
  return 1 + Math.floor(Math.random() * 999_999);
}

export function configTitle(labels: string[]): string {
  if (labels.length !== 2) return `Round robin: ${labels.join(", ")}`;
  return labels[0] === labels[1] ? `${labels[0]} vs itself` : `${labels[0]} vs ${labels[1]}`;
}

export function configLine(c: LabConfig): string {
  return `${count(c.hands)} hands${c.strategies.length > 2 ? " per matchup" : ""} · ${c.mode} · ${c.stack_bb} bb · seed ${c.seed}`;
}

/** Sessions an experiment will have per matchup (fresh agents each session). */
export function sessionCount(c: Pick<LabConfig, "hands" | "mode" | "session_hands">): number {
  const deals = c.mode === "duplicate" ? Math.floor(c.hands / 2) : c.hands;
  return c.session_hands > 0 ? Math.ceil(deals / c.session_hands) : 0;
}

