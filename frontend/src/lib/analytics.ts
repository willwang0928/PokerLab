/**
 * Display helpers for the performance analytics (v0.4.8). The statistics themselves are
 * computed on the server (backend/pokerlab/analytics.py); this module only formats them
 * and turns them into plain-language verdicts, scales and colours.
 */
import type { HandCell, TrendReport, WinRate } from "../types";

// Validated with the dataviz palette checker against the panel surface (#10161e):
// lightness band, chroma, CVD separation and contrast all pass.
export const SERIES = { actual: "#c08a18", adjusted: "#3a9fd8" } as const;
// Diverging scale for the heatmap: blue = winning, orange = losing, neutral gray midpoint.
export const DIVERGING = { pos: [58, 127, 208], neg: [216, 96, 42], mid: [42, 51, 64] } as const;

export function signed(v: number, digits = 1): string {
  const s = Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  if (Number(s.replace(/,/g, "")) === 0) return s;
  return `${v > 0 ? "+" : "−"}${s}`;
}

export function pct(v: number | null | undefined, digits = 0): string {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(digits)}%`;
}

export function count(n: number): string {
  return n.toLocaleString("en-US");
}

/** "+12.3 bb/100" or "—" without a rate. */
export function rateText(r: WinRate): string {
  return r.bb_per_100 === null ? "—" : `${signed(r.bb_per_100)} bb/100`;
}

/** "−42 to +88" (the interval), or null below the minimum sample. */
export function intervalText(r: WinRate): string | null {
  if (r.low === null || r.high === null) return null;
  return `${signed(r.low, 0)} to ${signed(r.high, 0)}`;
}

export const VERDICT_LABEL: Record<WinRate["verdict"], string> = {
  insufficient: "Too few hands",
  winning: "Winning",
  losing: "Losing",
  unclear: "Not yet clear",
};

/** One or two sentences on what a win rate does and does not show. */
export function verdictSentence(r: WinRate, minHands: number): string {
  const level = Math.round(r.level * 100);
  if (r.verdict === "insufficient") {
    const more = minHands - r.hands;
    return `With ${count(r.hands)} hand${r.hands === 1 ? "" : "s"} there is nothing to measure yet: ${count(more)} more before an interval is shown. Even then, poker results take thousands of hands to settle.`;
  }
  const iv = intervalText(r);
  if (r.verdict === "winning")
    return `You are winning beyond reasonable doubt: the ${level}% interval (${iv} bb/100) lies entirely above zero.`;
  if (r.verdict === "losing")
    return `You are losing beyond reasonable doubt: the ${level}% interval (${iv} bb/100) lies entirely below zero.`;
  const confirm =
    r.hands_to_confirm && r.bb_per_100 !== null && Math.abs(r.bb_per_100) >= 0.5
      ? ` If your true rate were ${signed(r.bb_per_100, 0)} bb/100, about ${count(roundNice(r.hands_to_confirm))} more hands would be needed to show it.`
      : "";
  return `Your results are consistent with anything from ${iv} bb/100 (${level}% interval), so a win or loss of this size could still be variance.${confirm}`;
}

/** Round a large count to two significant figures ("2,300", not "2,287"). */
export function roundNice(n: number): number {
  if (n < 100) return Math.ceil(n);
  const p = 10 ** (Math.floor(Math.log10(n)) - 1);
  return Math.ceil(n / p) * p;
}

export const TREND_TEXT: Record<Extract<TrendReport, { status: "ok" }>["verdict"], string> = {
  decisions_improved:
    "Genuine improvement: the coach's estimate of what your mistakes cost has fallen by more than chance would explain.",
  decisions_worse:
    "Your decisions have slipped: the coach's estimate of what your mistakes cost has risen by more than chance would explain.",
  results_improved: "Your results improved by more than variance alone would usually produce.",
  results_worse: "Your results fell by more than variance alone would usually produce.",
  variance:
    "No real change yet: the difference between your earlier and recent hands is within normal variance.",
};

/** Mix two RGB colours. */
function mix(a: readonly number[], b: readonly number[], t: number): string {
  const c = a.map((v, i) => Math.round(v + (b[i] - v) * t));
  return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
}

/** Diverging colour for a value against a symmetric scale ``±scale``. */
export function divergingColor(v: number, scale: number): string {
  if (!Number.isFinite(v) || scale <= 0) return mix(DIVERGING.mid, DIVERGING.mid, 0);
  const t = Math.min(1, Math.abs(v) / scale);
  // A gentle curve so small values are still visibly tinted.
  return mix(DIVERGING.mid, v >= 0 ? DIVERGING.pos : DIVERGING.neg, Math.sqrt(t));
}

/** Sequential (one hue, blue) colour for a share in [0, 1]. */
export function sequentialColor(t: number): string {
  return mix(DIVERGING.mid, DIVERGING.pos, Math.max(0, Math.min(1, t)));
}

export type HeatMetric = "shrunk" | "raw" | "dealt" | "vpip";

export const METRIC_LABEL: Record<HeatMetric, string> = {
  shrunk: "Win rate",
  raw: "Raw result",
  dealt: "Times dealt",
  vpip: "VPIP",
};

/** The value a heatmap cell shows for a metric (null = no data). */
export function cellValue(c: HandCell, metric: HeatMetric): number | null {
  switch (metric) {
    case "shrunk":
      return c.dealt ? c.shrunk : null;
    case "raw":
      return c.bb_per_hand;
    case "dealt":
      return c.dealt;
    case "vpip":
      return c.vpip;
  }
}

/** A symmetric colour scale that ignores a single extreme cell (the 95th percentile). */
export function symmetricScale(values: number[], floor = 0.5): number {
  const abs = values.filter((v) => Number.isFinite(v)).map(Math.abs).sort((a, b) => a - b);
  if (!abs.length) return floor;
  const q = abs[Math.min(abs.length - 1, Math.floor(abs.length * 0.95))];
  return Math.max(floor, q);
}

/** Text shown inside a heatmap cell for a metric. */
export function cellText(c: HandCell, metric: HeatMetric): string {
  const v = cellValue(c, metric);
  if (v === null) return "";
  if (metric === "dealt") return String(v);
  if (metric === "vpip") return `${Math.round(v * 100)}`;
  return Math.abs(v) >= 9.95 ? signed(v, 0) : signed(v, 1);
}

/** About ``n`` round tick values covering [lo, hi]. */
export function niceTicks(lo: number, hi: number, n = 5): number[] {
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [0];
  if (hi - lo < 1e-9) {
    lo -= 1;
    hi += 1;
  }
  const raw = (hi - lo) / Math.max(1, n);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? 10 * mag;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Math.round(v / step) * step + 0); // + 0 turns -0 into 0
  return out;
}

/** Index of the point nearest ``n`` (hand number) in an ascending list. */
export function nearest(points: { n: number }[], n: number): number {
  if (!points.length) return -1;
  let lo = 0;
  let hi = points.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (points[mid].n < n) lo = mid + 1;
    else hi = mid;
  }
  if (lo > 0 && Math.abs(points[lo - 1].n - n) <= Math.abs(points[lo].n - n)) return lo - 1;
  return lo;
}

/** The break-even variance band after ``n`` hands: ±z·σ·√n bb. */
export function breakEvenBand(sd: number, n: number, z = 1.96): number {
  return z * sd * Math.sqrt(Math.max(0, n));
}
