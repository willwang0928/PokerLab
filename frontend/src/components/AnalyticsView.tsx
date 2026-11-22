import { type PointerEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, profileApi } from "../lib/api";
import {
  breakEvenBand,
  cellText,
  cellValue,
  count,
  divergingColor,
  type HeatMetric,
  intervalText,
  METRIC_LABEL,
  nearest,
  niceTicks,
  pct,
  rateText,
  roundNice,
  sequentialColor,
  SERIES,
  signed,
  symmetricScale,
  TREND_TEXT,
  VERDICT_LABEL,
  verdictSentence,
} from "../lib/analytics";
import type { AnalyticsReport, HandCell, Segment, WinRate } from "../types";
import { DIFFICULTY_LABEL } from "./StartScreen";

type Filters = { difficulty: string | null; last: number | null };
type Loader = (token: string, filters: Filters) => Promise<AnalyticsReport>;

type LoadState =
  | { status: "loading"; report: AnalyticsReport | null }
  | { status: "ready"; report: AnalyticsReport }
  | { status: "error"; message: string; report: AnalyticsReport | null };

const RANGES: { label: string; last: number | null }[] = [
  { label: "All hands", last: null },
  { label: "Last 1,000", last: 1000 },
  { label: "Last 500", last: 500 },
  { label: "Last 100", last: 100 },
];

const VERDICT_TONE: Record<WinRate["verdict"], string> = {
  insufficient: "bg-white/5 text-slate-300 ring-white/10",
  unclear: "bg-white/5 text-slate-200 ring-white/15",
  winning: "bg-sky-500/15 text-sky-100 ring-sky-400/30",
  losing: "bg-orange-500/15 text-orange-100 ring-orange-400/30",
};

function difficultyName(d: string): string {
  return (DIFFICULTY_LABEL as Record<string, string>)[d] ?? (d === "unknown" ? "Unknown" : d);
}

interface Props {
  /** The server profile's token; without one there are no saved hands to analyse. */
  token?: string;
  /** Increases when hands reach the server (refetch). */
  handsVersion: number;
  /** Finished hands still waiting to be saved. */
  pendingHands?: number;
  onShowHand?: (id: string) => void;
  /** Injected in tests. */
  load?: Loader;
}

/**
 * Performance analytics (v0.4.8): bankroll and win rate with honest intervals, luck from
 * all-ins, earlier versus recent, positions, opponents and the starting-hand grid. Every
 * statistic comes from the server; this view formats it and says what it does not show.
 */
export function AnalyticsView({ token, handsVersion, pendingHands = 0, onShowHand, load = profileApi.analytics }: Props) {
  const [filters, setFilters] = useState<Filters>({ difficulty: null, last: null });
  const [state, setState] = useState<LoadState>({ status: "loading", report: null });
  const seq = useRef(0);
  const last = useRef<AnalyticsReport | null>(null);

  const fetchReport = useCallback(async () => {
    if (!token) return;
    const mine = ++seq.current;
    setState({ status: "loading", report: last.current });
    try {
      const report = await load(token, filters);
      if (mine !== seq.current) return;
      last.current = report;
      setState({ status: "ready", report });
    } catch (e) {
      if (mine !== seq.current) return;
      setState({
        status: "error",
        report: last.current,
        message:
          e instanceof ApiError && e.status === 503
            ? "The server's storage is unavailable, so your statistics cannot be updated right now."
            : e instanceof ApiError && e.status === 401
              ? "The server no longer knows this profile."
              : "Your statistics could not be loaded. Is the server running?",
      });
    }
  }, [token, filters, load]);

  useEffect(() => {
    last.current = null;
  }, [token]);
  useEffect(() => {
    void fetchReport();
  }, [fetchReport, handsVersion]);

  if (!token) {
    return (
      <section className="surface rounded-2xl p-6 text-sm text-slate-300" data-testid="analytics-none">
        <h2 className="font-display text-[19px] font-bold text-amber-50">Statistics</h2>
        <p className="mt-2">
          Statistics are computed from the hands saved on the server. Play some hands while the server is running and
          they will appear here.
        </p>
      </section>
    );
  }

  const report = state.report;
  return (
    <div className="flex w-full flex-col gap-4" data-testid="analytics" aria-busy={state.status === "loading"}>
      <FilterBar report={report} filters={filters} onChange={setFilters} />
      {state.status === "error" && (
        <div role="alert" className="surface flex flex-wrap items-center gap-3 rounded-2xl p-4 text-sm text-orange-100">
          <span className="flex-1">{state.message}</span>
          <button type="button" className="btn-dark rounded-xl px-3 py-1.5 text-xs font-semibold" onClick={() => void fetchReport()}>
            Retry
          </button>
        </div>
      )}
      {!report ? (
        state.status === "loading" && <div className="surface rounded-2xl p-6 text-slate-400">Loading statistics…</div>
      ) : report.hands === 0 ? (
        <section className="surface rounded-2xl p-6 text-sm text-slate-300" data-testid="analytics-empty">
          No saved hands match these filters yet.
          {pendingHands > 0 && ` ${count(pendingHands)} finished hand${pendingHands === 1 ? " is" : "s are"} still waiting to be saved.`}
        </section>
      ) : (
        <Report report={report} pendingHands={pendingHands} onShowHand={onShowHand} />
      )}
    </div>
  );
}

function FilterBar({ report, filters, onChange }: { report: AnalyticsReport | null; filters: Filters; onChange: (f: Filters) => void }) {
  const levels = report?.available_difficulties ?? [];
  return (
    <div className="surface flex flex-wrap items-center gap-3 rounded-2xl px-4 py-3 text-xs" data-testid="analytics-filters">
      <label className="flex items-center gap-2 text-slate-400">
        Opponent
        <select
          className="rounded-lg bg-slate-900 px-2 py-1.5 text-slate-100 ring-1 ring-white/10"
          value={filters.difficulty ?? ""}
          onChange={(e) => onChange({ ...filters, difficulty: e.target.value || null })}
        >
          <option value="">All levels</option>
          {/* Keep the chosen level selectable even when it has no hands in the current range. */}
          {[...new Set([...levels, ...(filters.difficulty ? [filters.difficulty] : [])])].map((d) => (
            <option key={d} value={d}>
              {difficultyName(d)}
            </option>
          ))}
        </select>
      </label>
      <div className="flex rounded-xl bg-black/20 p-0.5" role="group" aria-label="Hands included">
        {RANGES.map((r) => (
          <button
            key={r.label}
            type="button"
            aria-pressed={filters.last === r.last}
            onClick={() => onChange({ ...filters, last: r.last })}
            className={`rounded-[10px] px-2.5 py-1.5 font-semibold ${
              filters.last === r.last ? "bg-amber-300 text-slate-950" : "text-slate-300 hover:text-white"
            }`}
          >
            {r.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function Report({ report, pendingHands, onShowHand }: { report: AnalyticsReport; pendingHands: number; onShowHand?: (id: string) => void }) {
  const { overall, settings } = report;
  const wr = overall.win_rate;
  const adj = overall.allin_adjusted;
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      {/* Headline */}
      <section className="surface rounded-2xl p-4 lg:col-span-2" aria-labelledby="wr-title" data-testid="analytics-summary">
        <h2 id="wr-title" className="eyebrow text-slate-400">
          Win rate
        </h2>
        <div className="mt-2 flex flex-wrap items-end gap-x-6 gap-y-3">
          <div>
            <div className="font-display text-[32px] font-bold leading-none tabular-nums text-amber-50" data-testid="win-rate">
              {rateText(wr)}
            </div>
            <div className="mt-1 text-xs text-slate-400">
              {intervalText(wr) ? `${Math.round(wr.level * 100)}% interval ${intervalText(wr)}` : "No interval yet"}
            </div>
          </div>
          <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ${VERDICT_TONE[wr.verdict]}`} data-testid="verdict">
            {VERDICT_LABEL[wr.verdict]}
          </span>
          <Stat label="Hands" value={count(report.hands)} />
          <Stat label="Net result" value={`${signed(wr.total_bb)} bb`} />
          <Stat label="All-in adjusted" value={rateText(adj)} />
          <Stat label="Largest drawdown" value={`${count(Math.round(report.bankroll.max_drawdown_bb))} bb`} />
        </div>
        <p className="mt-3 max-w-3xl text-sm text-slate-300" data-testid="verdict-sentence">
          {verdictSentence(wr, settings.min_hands)}
        </p>
        {wr.hands_for_precision !== null && wr.hands >= settings.min_hands && (
          <p className="mt-1 max-w-3xl text-xs text-slate-400">
            About {count(roundNice(wr.hands_for_precision))} hands in total pin a win rate down to ±{settings.precision_target}{" "}
            bb/100.
            {wr.method === "clustered" && " Hands in one session are not independent, so the interval allows for that."}
          </p>
        )}
        {(report.truncated || pendingHands > 0) && (
          <p className="mt-1 text-xs text-slate-500">
            {report.truncated && `Only the most recent ${count(report.window)} saved hands are included. `}
            {pendingHands > 0 && `${count(pendingHands)} finished hand${pendingHands === 1 ? " is" : "s are"} not saved yet.`}
          </p>
        )}
      </section>

      <BankrollChart report={report} onShowHand={onShowHand} />
      <TrendCard report={report} />
      <LuckCard report={report} />
      <SegmentTable
        title="By position"
        testid="positions"
        rows={report.positions.map((p) => ({ label: p.position === "button" ? "Button (small blind)" : "Big blind", seg: p }))}
        minHands={settings.min_hands}
      />
      <SegmentTable
        title="By opponent"
        testid="opponents"
        rows={report.opponents.map((o) => ({
          label: difficultyName(o.difficulty),
          note: o.adaptive_share === null ? undefined : o.adaptive_share >= 0.995 ? "adaptive" : o.adaptive_share <= 0.005 ? "fixed" : `${pct(o.adaptive_share)} adaptive`,
          seg: o,
        }))}
        minHands={settings.min_hands}
      />
      <HandGrid report={report} />
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[11px] text-slate-400">{label}</div>
      <div className="text-sm font-semibold tabular-nums text-slate-100">{value}</div>
    </div>
  );
}

// ------------------------------------------------------------------ bankroll

const W = 720;
const H = 260;
const PAD = { l: 48, r: 12, t: 12, b: 26 };

function BankrollChart({ report, onShowHand }: { report: AnalyticsReport; onShowHand?: (id: string) => void }) {
  const { points, sd_bb: sd, thinned } = report.bankroll;
  const [hover, setHover] = useState<number | null>(null);
  const n = points.length ? points[points.length - 1].n : 0;

  const geo = useMemo(() => {
    const bandAt = (k: number) => (sd ? breakEvenBand(sd, k) : 0);
    let lo = 0;
    let hi = 0;
    for (const p of points) {
      lo = Math.min(lo, p.cum, p.ev);
      hi = Math.max(hi, p.cum, p.ev);
    }
    lo = Math.min(lo, -bandAt(n));
    hi = Math.max(hi, bandAt(n));
    const ticks = niceTicks(lo, hi, 5);
    lo = Math.min(lo, ticks[0]);
    hi = Math.max(hi, ticks[ticks.length - 1]);
    const x = (k: number) => PAD.l + (n ? (k / n) * (W - PAD.l - PAD.r) : 0);
    const y = (v: number) => PAD.t + ((hi - v) / (hi - lo || 1)) * (H - PAD.t - PAD.b);
    const line = (key: "cum" | "ev") =>
      `M${x(0)},${y(0)}` + points.map((p) => `L${x(p.n).toFixed(1)},${y(p[key]).toFixed(1)}`).join("");
    const steps = 48;
    const upper = Array.from({ length: steps + 1 }, (_, i) => (i / steps) * n);
    const band =
      sd && n
        ? `M${upper.map((k) => `${x(k).toFixed(1)},${y(bandAt(k)).toFixed(1)}`).join("L")}` +
          `L${[...upper].reverse().map((k) => `${x(k).toFixed(1)},${y(-bandAt(k)).toFixed(1)}`).join("L")}Z`
        : "";
    return { x, y, ticks, actual: line("cum"), adjusted: line("ev"), band, xticks: niceTicks(0, n, 5).filter((t) => t <= n) };
  }, [points, sd, n]);

  const onMove = (e: PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    const k = ((px - PAD.l) / (W - PAD.l - PAD.r)) * n;
    setHover(nearest(points, k));
  };
  const hp = hover !== null && hover >= 0 ? points[hover] : null;
  const recent = points.slice(-20).reverse();

  return (
    <section className="surface rounded-2xl p-4 lg:col-span-2" aria-labelledby="bankroll-title" data-testid="bankroll">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <h2 id="bankroll-title" className="eyebrow text-slate-400">
          Results over time (bb)
        </h2>
        <div className="flex flex-wrap gap-4 text-xs text-slate-300" aria-hidden="true">
          <LegendKey color={SERIES.actual} label="Actual" />
          <LegendKey color={SERIES.adjusted} label="All-in adjusted" dashed />
          {sd ? (
            <span className="flex items-center gap-1.5">
              <span className="inline-block h-2.5 w-4 rounded-sm bg-white/10" /> Break-even range (95%)
            </span>
          ) : null}
        </div>
      </div>
      <div className="relative mt-2">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="h-auto w-full touch-none select-none"
          role="img"
          aria-label={`Cumulative result over ${count(n)} hands: ${signed(points.at(-1)?.cum ?? 0)} bb actual, ${signed(points.at(-1)?.ev ?? 0)} bb all-in adjusted.`}
          onPointerMove={onMove}
          onPointerLeave={() => setHover(null)}
          onClick={() => hp && onShowHand?.(hp.id)}
        >
          {geo.ticks.map((t) => (
            <g key={t}>
              <line x1={PAD.l} x2={W - PAD.r} y1={geo.y(t)} y2={geo.y(t)} stroke={t === 0 ? "rgba(255,255,255,0.28)" : "rgba(255,255,255,0.06)"} />
              <text x={PAD.l - 6} y={geo.y(t) + 3.5} textAnchor="end" className="fill-slate-500 text-[10px] tabular-nums">
                {signed(t, 0)}
              </text>
            </g>
          ))}
          {geo.xticks.map((t) => (
            <text key={t} x={geo.x(t)} y={H - 8} textAnchor="middle" className="fill-slate-500 text-[10px] tabular-nums">
              {count(t)}
            </text>
          ))}
          {geo.band && <path d={geo.band} fill="rgba(255,255,255,0.07)" />}
          <path d={geo.adjusted} fill="none" stroke={SERIES.adjusted} strokeWidth={2} strokeDasharray="5 4" strokeLinejoin="round" />
          <path d={geo.actual} fill="none" stroke={SERIES.actual} strokeWidth={2} strokeLinejoin="round" />
          {hp && (
            <g pointerEvents="none">
              <line x1={geo.x(hp.n)} x2={geo.x(hp.n)} y1={PAD.t} y2={H - PAD.b} stroke="rgba(255,255,255,0.3)" />
              <circle cx={geo.x(hp.n)} cy={geo.y(hp.ev)} r={4} fill={SERIES.adjusted} stroke="#10161e" strokeWidth={2} />
              <circle cx={geo.x(hp.n)} cy={geo.y(hp.cum)} r={4} fill={SERIES.actual} stroke="#10161e" strokeWidth={2} />
            </g>
          )}
        </svg>
        {hp && (
          <div
            className="pointer-events-none absolute top-1 rounded-lg bg-slate-950/95 px-2.5 py-1.5 text-[11px] text-slate-200 shadow-lg ring-1 ring-white/10"
            style={{ left: `${Math.min(70, Math.max(2, (geo.x(hp.n) / W) * 100 - 12))}%` }}
            data-testid="bankroll-tooltip"
          >
            <div className="font-semibold text-slate-100">Hand {count(hp.n)}</div>
            <div className="tabular-nums">This hand {signed(hp.net)} bb</div>
            <div className="tabular-nums">Actual {signed(hp.cum)} bb</div>
            <div className="tabular-nums">Adjusted {signed(hp.ev)} bb</div>
            {onShowHand && <div className="text-slate-500">Click to open the hand</div>}
          </div>
        )}
      </div>
      <p className="mt-2 max-w-3xl text-xs text-slate-400">
        The all-in adjusted line replaces the outcome of each hand that was all-in before the river with its expected value, so
        it removes that luck. A line that stays inside the shaded range is what a break-even player would produce about 95% of
        the time.{thinned && " Long histories are thinned for drawing; peaks and troughs are kept."}
      </p>
      <details className="mt-2 text-xs text-slate-300">
        <summary className="cursor-pointer text-slate-400">Recent hands as a table</summary>
        <table className="mt-2 w-full max-w-md tabular-nums">
          <thead className="text-left text-slate-500">
            <tr>
              <th className="py-1 font-medium">Hand</th>
              <th className="py-1 text-right font-medium">Result</th>
              <th className="py-1 text-right font-medium">Cumulative</th>
              <th className="py-1 text-right font-medium">Adjusted</th>
            </tr>
          </thead>
          <tbody>
            {recent.map((p) => (
              <tr key={p.n} className="border-t border-white/5">
                <td className="py-1">{count(p.n)}</td>
                <td className="py-1 text-right">{signed(p.net)}</td>
                <td className="py-1 text-right">{signed(p.cum)}</td>
                <td className="py-1 text-right">{signed(p.ev)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </section>
  );
}

function LegendKey({ color, label, dashed = false }: { color: string; label: string; dashed?: boolean }) {
  return (
    <span className="flex items-center gap-1.5">
      <svg width="18" height="6" aria-hidden="true">
        <line x1="1" x2="17" y1="3" y2="3" stroke={color} strokeWidth="2" strokeDasharray={dashed ? "4 3" : undefined} strokeLinecap="round" />
      </svg>
      {label}
    </span>
  );
}

// ------------------------------------------------------------------ trend and luck

function TrendCard({ report }: { report: AnalyticsReport }) {
  const t = report.trend;
  return (
    <section className="surface rounded-2xl p-4" aria-labelledby="trend-title" data-testid="trend">
      <h2 id="trend-title" className="eyebrow text-slate-400">
        Earlier versus recent
      </h2>
      {t.status === "insufficient" ? (
        <p className="mt-2 text-sm text-slate-300">
          Comparing needs at least {count(t.needed)} hands in each half; there are {count(t.hands)} in total so far.
        </p>
      ) : (
        <>
          <p className="mt-2 text-sm text-slate-200" data-testid="trend-verdict">
            {TREND_TEXT[t.verdict]}
          </p>
          <table className="mt-3 w-full text-xs tabular-nums">
            <thead className="text-left text-slate-500">
              <tr>
                <th className="py-1 font-medium" />
                <th className="py-1 text-right font-medium">Earlier {count(t.early_hands)}</th>
                <th className="py-1 text-right font-medium">Recent {count(t.late_hands)}</th>
                <th className="py-1 text-right font-medium">Change (95%)</th>
              </tr>
            </thead>
            <tbody className="text-slate-200">
              <tr className="border-t border-white/5">
                <td className="py-1.5 text-slate-400">Win rate, bb/100</td>
                <td className="py-1.5 text-right">{rateText(t.early).replace(" bb/100", "")}</td>
                <td className="py-1.5 text-right">{rateText(t.late).replace(" bb/100", "")}</td>
                <td className="py-1.5 text-right">{changeText(t.results.diff, t.results.low, t.results.high)}</td>
              </tr>
              <tr className="border-t border-white/5">
                <td className="py-1.5 text-slate-400">All-in adjusted</td>
                <td className="py-1.5 text-right" colSpan={2} />
                <td className="py-1.5 text-right">{changeText(t.adjusted.diff, t.adjusted.low, t.adjusted.high)}</td>
              </tr>
              {t.quality && (
                <tr className="border-t border-white/5" data-testid="trend-quality">
                  <td className="py-1.5 text-slate-400">Mistakes cost, bb/100</td>
                  <td className="py-1.5 text-right">{t.quality.early_loss_per_100.toFixed(1)}</td>
                  <td className="py-1.5 text-right">{t.quality.late_loss_per_100.toFixed(1)}</td>
                  <td className="py-1.5 text-right">{changeText(t.quality.diff_per_100, t.quality.low, t.quality.high)}</td>
                </tr>
              )}
            </tbody>
          </table>
          <p className="mt-2 text-xs text-slate-400">
            Results swing far more than decisions do, so the coach's estimate of what your mistakes cost is the better guide to
            whether you are improving. It measures agreement with PokerLab's analysis, not perfect play.
          </p>
        </>
      )}
    </section>
  );
}

function changeText(d: number, lo: number, hi: number): string {
  return `${signed(d)} (${signed(lo, 0)} to ${signed(hi, 0)})`;
}

function LuckCard({ report }: { report: AnalyticsReport }) {
  const l = report.luck;
  const text =
    l.verdict === "none"
      ? "None of these hands went all-in before the river, so there is no all-in luck to measure."
      : l.verdict === "lucky"
        ? "You have run above expectation in all-ins by more than chance usually allows. Expect your results to come back down."
        : l.verdict === "unlucky"
          ? "You have run below expectation in all-ins by more than chance usually allows. That is bad luck, not bad play."
          : "Your all-in results are within the normal range of luck.";
  return (
    <section className="surface rounded-2xl p-4" aria-labelledby="luck-title" data-testid="luck">
      <h2 id="luck-title" className="eyebrow text-slate-400">
        All-in luck
      </h2>
      {l.verdict !== "none" && (
        <div className="mt-2 flex flex-wrap gap-x-6 gap-y-2">
          <Stat label="All-in hands" value={count(l.spots)} />
          <Stat label="Above or below expectation" value={`${signed(l.luck_bb)} bb`} />
          <Stat label="Typical spread (1 SD)" value={`±${l.sd_bb.toFixed(1)} bb`} />
          {l.mean_equity !== undefined && <Stat label="Average equity when all-in" value={pct(l.mean_equity)} />}
        </div>
      )}
      <p className="mt-2 text-sm text-slate-300" data-testid="luck-verdict">
        {text}
      </p>
    </section>
  );
}

// ------------------------------------------------------------------ segments

function SegmentTable({
  title,
  testid,
  rows,
  minHands,
}: {
  title: string;
  testid: string;
  rows: { label: string; note?: string; seg: Segment }[];
  minHands: number;
}) {
  return (
    <section className="surface overflow-x-auto rounded-2xl p-4" aria-label={title} data-testid={testid}>
      <h2 className="eyebrow text-slate-400">{title}</h2>
      <table className="mt-2 w-full min-w-[480px] text-xs tabular-nums [&_td+td]:pl-2 [&_th+th]:pl-2">
        <thead className="text-left text-slate-500">
          <tr>
            <th className="py-1 font-medium" />
            <th className="py-1 text-right font-medium">Hands</th>
            <th className="py-1 text-right font-medium">bb/100</th>
            <th className="py-1 text-right font-medium">95% interval</th>
            <th className="py-1 text-right font-medium" title="Voluntarily put money in the pot">
              VPIP
            </th>
            <th className="py-1 text-right font-medium" title="Raised preflop">
              PFR
            </th>
            <th className="py-1 text-right font-medium" title="Went to showdown, of hands that saw the flop">
              WTSD
            </th>
            <th className="py-1 text-right font-medium" title="Won at showdown">
              W$SD
            </th>
          </tr>
        </thead>
        <tbody className="text-slate-200">
          {rows.map(({ label, note, seg }) => (
            <tr key={label} className="border-t border-white/5">
              <td className="py-1.5">
                {label}
                {note && <span className="ml-1 text-slate-500">({note})</span>}
              </td>
              <td className="py-1.5 text-right">{count(seg.hands)}</td>
              <td className="py-1.5 text-right">{seg.hands ? rateText(seg.win_rate).replace(" bb/100", "") : "—"}</td>
              <td className="py-1.5 text-right text-slate-400">
                {intervalText(seg.win_rate) ?? (seg.hands ? `needs ${minHands}` : "—")}
              </td>
              <td className="py-1.5 text-right">{pct(seg.vpip)}</td>
              <td className="py-1.5 text-right">{pct(seg.pfr)}</td>
              <td className="py-1.5 text-right">{pct(seg.wtsd)}</td>
              <td className="py-1.5 text-right">{pct(seg.wsd)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

// ------------------------------------------------------------------ starting hands

const METRICS: HeatMetric[] = ["shrunk", "raw", "dealt", "vpip"];

function HandGrid({ report }: { report: AnalyticsReport }) {
  const sh = report.starting_hands;
  const [metric, setMetric] = useState<HeatMetric>("shrunk");
  const [picked, setPicked] = useState<string | null>(null);
  const cells = sh.cells;
  // The shrunk estimates all sit near the overall rate by design, so colouring them against
  // zero paints a winning player's whole grid one colour. They are coloured against the
  // player's own average instead: which hands do better or worse than usual.
  const center = metric === "shrunk" ? (sh.overall_bb_per_hand ?? 0) : 0;
  const dealtCells = metric === "shrunk" ? cells.filter((c) => c.dealt) : cells;
  const values = dealtCells.map((c) => cellValue(c, metric)).filter((v): v is number => v !== null);
  const scale =
    metric === "shrunk"
      ? // Never finer than the prior spread: shrunk estimates differ from the average only
        // as far as the evidence allows, and the colours should not magnify that.
        symmetricScale(values.map((v) => v - center), sh.prior_sd)
      : metric === "raw"
        ? symmetricScale(values)
        : Math.max(1, ...values);
  const color = (c: HandCell) => {
    const v = cellValue(c, metric);
    if (v === null) return "rgba(255,255,255,0.03)";
    if (metric === "dealt") return sequentialColor(v / scale);
    if (metric === "vpip") return sequentialColor(v);
    return divergingColor(v - center, scale);
  };
  const cell = picked ? cells.find((c) => c.hand === picked) ?? null : null;

  return (
    <section className="surface rounded-2xl p-4 lg:col-span-2" aria-labelledby="hands-title" data-testid="hand-grid">
      <div className="flex flex-wrap items-center gap-3">
        <h2 id="hands-title" className="eyebrow flex-1 text-slate-400">
          Starting hands
        </h2>
        <div className="flex rounded-xl bg-black/20 p-0.5 text-xs" role="group" aria-label="Grid shows">
          {METRICS.map((m) => (
            <button
              key={m}
              type="button"
              aria-pressed={metric === m}
              onClick={() => setMetric(m)}
              className={`rounded-[10px] px-2.5 py-1.5 font-semibold ${
                metric === m ? "bg-amber-300 text-slate-950" : "text-slate-300 hover:text-white"
              }`}
            >
              {METRIC_LABEL[m]}
            </button>
          ))}
        </div>
      </div>
      <div className="mt-3 flex flex-col gap-4 lg:flex-row">
        <div className="grid w-full max-w-[560px] gap-[2px]" style={{ gridTemplateColumns: "repeat(13, minmax(0, 1fr))" }}>
          {cells.map((c) => (
            <button
              key={c.hand}
              type="button"
              onClick={() => setPicked(c.hand === picked ? null : c.hand)}
              onMouseEnter={() => setPicked(c.hand)}
              aria-label={`${c.hand}: dealt ${c.dealt} times${c.bb_per_hand !== null ? `, ${signed(c.bb_per_hand)} bb per hand` : ""}`}
              aria-pressed={picked === c.hand}
              className={`flex aspect-square flex-col items-center justify-center rounded-[3px] text-[9px] leading-tight sm:text-[10px] ${
                picked === c.hand ? "ring-2 ring-amber-200" : ""
              }`}
              style={{ background: color(c) }}
            >
              <span className={`font-semibold ${c.dealt ? "text-slate-50" : "text-slate-600"}`}>{c.hand}</span>
              <span className="max-sm:hidden tabular-nums text-slate-200/80">{cellText(c, metric)}</span>
            </button>
          ))}
        </div>
        <div className="min-w-0 flex-1 text-xs text-slate-300">
          <GridLegend metric={metric} scale={scale} center={center} />
          {cell ? <CellDetail cell={cell} /> : <p className="mt-3 text-slate-400">Point at or select a hand for its numbers.</p>}
          <p className="mt-3 text-slate-400">
            {count(sh.classes_dealt)} of 169 starting hands dealt. <b className="font-semibold text-slate-300">Win rate</b> is coloured against your average and pulled
            toward your overall result until a hand has been dealt many times (half its own weight after about{" "}
            {count(Math.round(sh.shrink_hands))} hands), so a hand dealt three times cannot top the grid by luck. Raw result is
            the plain average and is shown for comparison.
          </p>
        </div>
      </div>
    </section>
  );
}

function GridLegend({ metric, scale, center }: { metric: HeatMetric; scale: number; center: number }) {
  const diverging = metric === "shrunk" || metric === "raw";
  const stops = diverging
    ? [-1, -0.5, 0, 0.5, 1].map((t) => divergingColor(t * scale, scale))
    : [0, 0.25, 0.5, 0.75, 1].map((t) => sequentialColor(t));
  const digits = scale < 0.5 ? 2 : 1;
  const [lo, mid, hi] = diverging
    ? [`${signed(center - scale, digits)} bb/hand`, metric === "shrunk" ? `your average ${signed(center, 2)}` : "0", `${signed(center + scale, digits)}`]
    : metric === "vpip"
      ? ["0%", "", "100%"]
      : ["0", "", `${count(Math.round(scale))} times`];
  return (
    <div data-testid="grid-legend">
      <div className="h-2.5 w-full max-w-[280px] rounded-sm" style={{ background: `linear-gradient(90deg, ${stops.join(",")})` }} />
      <div className="mt-1 flex max-w-[280px] justify-between gap-2 text-[10px] text-slate-500">
        <span>{lo}</span>
        {mid && <span>{mid}</span>}
        <span>{hi}</span>
      </div>
    </div>
  );
}

function CellDetail({ cell }: { cell: HandCell }) {
  return (
    <dl className="mt-3 grid max-w-[280px] grid-cols-[auto_1fr] gap-x-4 gap-y-1 tabular-nums" data-testid="cell-detail">
      <dt className="col-span-2 font-display text-[15px] font-bold text-amber-50">{cell.hand}</dt>
      <dt className="text-slate-400">Dealt</dt>
      <dd>
        {count(cell.dealt)} <span className="text-slate-500">(expected {cell.expected.toFixed(1)})</span>
      </dd>
      <dt className="text-slate-400">Net</dt>
      <dd>{signed(cell.net_bb)} bb</dd>
      <dt className="text-slate-400">Raw per hand</dt>
      <dd>{cell.bb_per_hand === null ? "—" : `${signed(cell.bb_per_hand, 2)} bb`}</dd>
      <dt className="text-slate-400">Estimated per hand</dt>
      <dd>
        {signed(cell.shrunk, 2)} bb <span className="text-slate-500">({pct(cell.weight)} own data)</span>
      </dd>
      {cell.low !== null && cell.high !== null && (
        <>
          <dt className="text-slate-400">95% interval</dt>
          <dd>
            {signed(cell.low, 1)} to {signed(cell.high, 1)} bb
          </dd>
        </>
      )}
      <dt className="text-slate-400">VPIP / PFR</dt>
      <dd>
        {pct(cell.vpip)} / {pct(cell.pfr)}
      </dd>
    </dl>
  );
}
