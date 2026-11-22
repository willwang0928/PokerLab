import { type PointerEvent, type ReactNode, type RefObject, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { breakEvenBand, count, nearest, niceTicks, SERIES, signed } from "../lib/analytics";
import type { LabConvergencePoint, LabTrajectory } from "../types";

const DEFAULT_W = 720;
const H = 250;
const PAD = { l: 52, r: 14, t: 12, b: 28 };
const TABLE = "#94a3b8"; // slate-400: context lines, 7.5:1 on the panel
const GRID = "rgba(255,255,255,0.06)";
const ZERO = "rgba(255,255,255,0.28)";

/** The chart's rendered width, so the SVG draws at 1:1 and text keeps its real size. */
function useWidth(): [RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement | null>(null);
  const [w, setW] = useState(DEFAULT_W);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([entry]) => setW(Math.max(280, Math.round(entry.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

function Legend({ items }: { items: { label: string; color: string; dash?: boolean; band?: boolean }[] }) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-300" aria-hidden="true">
      {items.map((it) => (
        <span key={it.label} className="flex items-center gap-1.5">
          {it.band ? (
            <span className="inline-block h-2.5 w-4 rounded-sm" style={{ background: it.color }} />
          ) : (
            <svg width="18" height="6">
              <line x1="1" x2="17" y1="3" y2="3" stroke={it.color} strokeWidth="2" strokeLinecap="round"
                strokeDasharray={it.dash ? "4 3" : undefined} />
            </svg>
          )}
          {it.label}
        </span>
      ))}
    </div>
  );
}

function Tooltip({ x, w, children }: { x: number; w: number; children: ReactNode }) {
  return (
    <div
      className="pointer-events-none absolute top-1 rounded-lg bg-slate-950/95 px-2.5 py-1.5 text-[11px] text-slate-200 shadow-lg ring-1 ring-white/10"
      style={{ left: `${Math.min(w < 500 ? 52 : 68, Math.max(2, (x / w) * 100 - 12))}%` }}
      data-testid="chart-tooltip"
    >
      {children}
    </div>
  );
}

function pointerIndex(e: PointerEvent<SVGSVGElement>, w: number, toValue: (px: number) => number, xs: number[]): number {
  const r = e.currentTarget.getBoundingClientRect();
  const px = ((e.clientX - r.left) / r.width) * w;
  return nearest(xs.map((n) => ({ n })), toValue(px));
}

/**
 * Cumulative result for strategy A. For duplicate runs the total line is the sum of the
 * two tables, which swing in opposite directions on the same cards.
 */
export function TrajectoryChart({ trajectory, bandSd, aLabel }: { trajectory: LabTrajectory; bandSd: number | null; aLabel: string }) {
  const { x, total, table1, table2 } = trajectory;
  const [hover, setHover] = useState<number | null>(null);
  const [ref, W] = useWidth();
  const n = x.length ? x[x.length - 1] : 0;
  const geo = useMemo(() => {
    const band = (k: number) => (bandSd ? breakEvenBand(bandSd, k) : 0);
    let lo = Math.min(0, -band(n));
    let hi = Math.max(0, band(n));
    for (const s of [total, table1, table2]) for (const v of s ?? []) (lo = Math.min(lo, v)), (hi = Math.max(hi, v));
    const ticks = niceTicks(lo, hi, 5);
    lo = Math.min(lo, ticks[0]);
    hi = Math.max(hi, ticks[ticks.length - 1]);
    const sx = (k: number) => PAD.l + (n ? (k / n) * (W - PAD.l - PAD.r) : 0);
    const sy = (v: number) => PAD.t + ((hi - v) / (hi - lo || 1)) * (H - PAD.t - PAD.b);
    const path = (s: number[]) => `M${sx(0)},${sy(0)}` + s.map((v, i) => `L${sx(x[i]).toFixed(1)},${sy(v).toFixed(1)}`).join("");
    const steps = Array.from({ length: 49 }, (_, i) => (i / 48) * n);
    const area =
      bandSd && n
        ? `M${steps.map((k) => `${sx(k).toFixed(1)},${sy(band(k)).toFixed(1)}`).join("L")}L${[...steps]
            .reverse()
            .map((k) => `${sx(k).toFixed(1)},${sy(-band(k)).toFixed(1)}`)
            .join("L")}Z`
        : "";
    return { sx, sy, ticks, area, xticks: niceTicks(0, n, W < 500 ? 3 : 5).filter((t) => t <= n), total: path(total),
      t1: table1 ? path(table1) : null, t2: table2 ? path(table2) : null };
  }, [x, total, table1, table2, bandSd, n, W]);
  const toX = (px: number) => ((px - PAD.l) / (W - PAD.l - PAD.r)) * n;
  const i = hover;
  const dup = table1 !== null;
  return (
    <div data-testid="trajectory-chart">
      <Legend
        items={[
          { label: dup ? "Duplicate total" : aLabel, color: SERIES.actual },
          ...(dup
            ? [
                { label: "Table 1 (cards as dealt)", color: TABLE },
                { label: "Table 2 (cards swapped)", color: TABLE, dash: true },
              ]
            : []),
          ...(bandSd ? [{ label: "Break-even range (95%)", color: "rgba(255,255,255,0.12)", band: true }] : []),
        ]}
      />
      <div className="relative mt-2" ref={ref}>
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="h-auto w-full touch-none select-none"
          role="img"
          aria-label={`${aLabel}'s cumulative result over ${count(n)} ${trajectory.x_label}: ${signed(total.at(-1) ?? 0)} bb.`}
          onPointerMove={(e) => setHover(pointerIndex(e, W, toX, x))}
          onPointerLeave={() => setHover(null)}
        >
          {geo.ticks.map((t) => (
            <g key={t}>
              <line x1={PAD.l} x2={W - PAD.r} y1={geo.sy(t)} y2={geo.sy(t)} stroke={t === 0 ? ZERO : GRID} />
              <text x={PAD.l - 6} y={geo.sy(t) + 3.5} textAnchor="end" className="fill-slate-500 text-[10px] tabular-nums">
                {signed(t, 0)}
              </text>
            </g>
          ))}
          {geo.xticks.map((t) => (
            <text key={t} x={geo.sx(t)} y={H - 9} textAnchor="middle" className="fill-slate-500 text-[10px] tabular-nums">
              {count(t)}
            </text>
          ))}
          {geo.area && <path d={geo.area} fill="rgba(255,255,255,0.07)" />}
          {geo.t1 && <path d={geo.t1} fill="none" stroke={TABLE} strokeWidth={1.25} strokeOpacity={0.8} strokeLinejoin="round" />}
          {geo.t2 && (
            <path d={geo.t2} fill="none" stroke={TABLE} strokeWidth={1.25} strokeOpacity={0.8} strokeDasharray="5 4" strokeLinejoin="round" />
          )}
          <path d={geo.total} fill="none" stroke={SERIES.actual} strokeWidth={2} strokeLinejoin="round" />
          {i !== null && i >= 0 && (
            <g pointerEvents="none">
              <line x1={geo.sx(x[i])} x2={geo.sx(x[i])} y1={PAD.t} y2={H - PAD.b} stroke="rgba(255,255,255,0.3)" />
              <circle cx={geo.sx(x[i])} cy={geo.sy(total[i])} r={4} fill={SERIES.actual} stroke="#10161e" strokeWidth={2} />
            </g>
          )}
        </svg>
        {i !== null && i >= 0 && (
          <Tooltip x={geo.sx(x[i])} w={W}>
            <div className="font-semibold text-slate-100">
              {trajectory.x_label === "deals" ? "Deal" : "Hand"} {count(x[i])}
            </div>
            <div className="tabular-nums">{dup ? "Total" : aLabel} {signed(total[i])} bb</div>
            {table1 && <div className="tabular-nums">Table 1 {signed(table1[i])} bb</div>}
            {table2 && <div className="tabular-nums">Table 2 {signed(table2[i])} bb</div>}
          </Tooltip>
        )}
      </div>
      <div className="mt-1 text-center text-[11px] text-slate-500">
        {trajectory.x_label === "deals" ? "Deals (each played at both tables)" : "Hands"}
      </div>
    </div>
  );
}

/**
 * The running estimate with its 95% interval as hands accumulate, on a log scale. For
 * duplicate runs the grey band is the interval the same hands would have given unpaired.
 */
export function ConvergenceChart({ paired, unpaired }: { paired: LabConvergencePoint[]; unpaired: LabConvergencePoint[] | null }) {
  const [hover, setHover] = useState<number | null>(null);
  const [ref, W] = useWidth();
  const clipId = `clip${useId().replace(/:/g, "")}`;
  const geo = useMemo(() => {
    const all = [...paired, ...(unpaired ?? [])];
    const x0 = Math.max(1, Math.min(...all.map((p) => p.hands)));
    const x1 = Math.max(x0 * 1.01, ...all.map((p) => p.hands));
    // Ignore the widest early intervals when scaling, so the end is readable.
    const tail = all.filter((p) => p.hands >= x0 * Math.sqrt(x1 / x0) || all.length < 8);
    // The running estimate itself always stays in view.
    let lo = Math.min(0, ...tail.map((p) => p.low), ...paired.map((p) => p.bb_per_100));
    let hi = Math.max(0, ...tail.map((p) => p.high), ...paired.map((p) => p.bb_per_100));
    const ticks = niceTicks(lo, hi, 5);
    lo = Math.min(lo, ticks[0]);
    hi = Math.max(hi, ticks[ticks.length - 1]);
    const lx = (v: number) => Math.log(v);
    const sx = (v: number) => PAD.l + ((lx(v) - lx(x0)) / (lx(x1) - lx(x0))) * (W - PAD.l - PAD.r);
    // Wide early intervals leave the plot through a clip path rather than being clamped
    // to its edge (a clamped outline would look like data).
    const sy = (v: number) => PAD.t + ((hi - v) / (hi - lo || 1)) * (H - PAD.t - PAD.b);
    const band = (pts: LabConvergencePoint[]) =>
      pts.length
        ? `M${pts.map((p) => `${sx(p.hands).toFixed(1)},${sy(p.high).toFixed(1)}`).join("L")}L${[...pts]
            .reverse()
            .map((p) => `${sx(p.hands).toFixed(1)},${sy(p.low).toFixed(1)}`)
            .join("L")}Z`
        : "";
    const line = (pts: LabConvergencePoint[]) => `M${pts.map((p) => `${sx(p.hands).toFixed(1)},${sy(p.bb_per_100).toFixed(1)}`).join("L")}`;
    const xticks = (W < 500 ? [100, 1_000, 10_000, 100_000] : [100, 300, 1_000, 3_000, 10_000, 30_000, 100_000]).filter(
      (t) => t >= x0 && t <= x1,
    );
    return { sx, sy, ticks, xticks, x0, x1, pairedBand: band(paired), unpairedBand: unpaired ? band(unpaired) : "", line: line(paired) };
  }, [paired, unpaired, W]);
  if (paired.length < 2) return null;
  const toHands = (px: number) =>
    Math.exp(Math.log(geo.x0) + ((px - PAD.l) / (W - PAD.l - PAD.r)) * (Math.log(geo.x1) - Math.log(geo.x0)));
  const xs = paired.map((p) => p.hands);
  const p = hover !== null && hover >= 0 ? paired[hover] : null;
  const u = p && unpaired ? unpaired.find((q) => q.hands === p.hands) : null;
  return (
    <div data-testid="convergence-chart">
      <Legend
        items={[
          { label: "Running estimate", color: SERIES.actual },
          { label: unpaired ? "95% interval, duplicate" : "95% interval", color: "rgba(192,138,24,0.28)", band: true },
          ...(unpaired ? [{ label: "95% interval, same hands unpaired", color: TABLE, dash: true }] : []),
        ]}
      />
      <div className="relative mt-2" ref={ref}>
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="h-auto w-full touch-none select-none"
          role="img"
          aria-label="How the estimate and its interval settle as hands accumulate."
          onPointerMove={(e) => setHover(pointerIndex(e, W, toHands, xs))}
          onPointerLeave={() => setHover(null)}
        >
          {geo.ticks.map((t) => (
            <g key={t}>
              <line x1={PAD.l} x2={W - PAD.r} y1={geo.sy(t)} y2={geo.sy(t)} stroke={t === 0 ? ZERO : GRID} />
              <text x={PAD.l - 6} y={geo.sy(t) + 3.5} textAnchor="end" className="fill-slate-500 text-[10px] tabular-nums">
                {signed(t, 0)}
              </text>
            </g>
          ))}
          {geo.xticks.map((t) => (
            <text key={t} x={geo.sx(t)} y={H - 9} textAnchor="middle" className="fill-slate-500 text-[10px] tabular-nums">
              {count(t)}
            </text>
          ))}
          <defs>
            <clipPath id={clipId}>
              <rect x={PAD.l} y={PAD.t} width={W - PAD.l - PAD.r} height={H - PAD.t - PAD.b} />
            </clipPath>
          </defs>
          <g clipPath={`url(#${clipId})`}>
          {geo.unpairedBand && (
            <path d={geo.unpairedBand} fill="rgba(148,163,184,0.06)" stroke={TABLE} strokeOpacity={0.7} strokeWidth={1.25} strokeDasharray="5 4" />
          )}
          <path d={geo.pairedBand} fill="rgba(192,138,24,0.22)" />
          <path d={geo.line} fill="none" stroke={SERIES.actual} strokeWidth={2} strokeLinejoin="round" />
          </g>
          {p && (
            <g pointerEvents="none">
              <line x1={geo.sx(p.hands)} x2={geo.sx(p.hands)} y1={PAD.t} y2={H - PAD.b} stroke="rgba(255,255,255,0.3)" />
              <circle cx={geo.sx(p.hands)} cy={geo.sy(p.bb_per_100)} r={4} fill={SERIES.actual} stroke="#10161e" strokeWidth={2} />
            </g>
          )}
        </svg>
        {p && (
          <Tooltip x={geo.sx(p.hands)} w={W}>
            <div className="font-semibold text-slate-100">After {count(p.hands)} hands</div>
            <div className="tabular-nums">
              {signed(p.bb_per_100)} bb/100 ({signed(p.low, 0)} to {signed(p.high, 0)})
            </div>
            {u && (
              <div className="tabular-nums text-slate-400">
                Unpaired: {signed(u.low, 0)} to {signed(u.high, 0)}
              </div>
            )}
          </Tooltip>
        )}
      </div>
      <div className="mt-1 text-center text-[11px] text-slate-500">Hands (log scale)</div>
    </div>
  );
}
