import { count, divergingColor, pct, signed, symmetricScale } from "../lib/analytics";
import { headline, intervalText, pText, rate, reductionText, roundNice, VERDICT_TONE, verdictSentence } from "../lib/lab";
import type { LabStyle, MatchupReport, RoundRobinReport } from "../types";
import { ConvergenceChart, TrajectoryChart } from "./LabCharts";

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div title={hint}>
      <div className="text-[11px] text-slate-400">{label}</div>
      <div className="text-sm font-semibold tabular-nums text-slate-100">{value}</div>
    </div>
  );
}

/** One matchup: verdict, interval, duplicate efficiency, charts and playing styles. */
export function MatchupDetail({ m }: { m: MatchupReport }) {
  const e = m.estimate;
  const level = Math.round(e.level * 100);
  const reduction = reductionText(m);
  return (
    <div className="flex flex-col gap-4" data-testid="matchup-detail">
      <section className="surface rounded-2xl p-4" aria-labelledby={`h-${m.a}-${m.b}`}>
        <div className="flex flex-wrap items-center gap-3">
          <h3 id={`h-${m.a}-${m.b}`} className="font-display text-[19px] font-bold text-amber-50" data-testid="matchup-headline">
            {headline(m)}
          </h3>
          <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ${VERDICT_TONE[e.verdict]}`}>
            {e.verdict === "unclear" ? "Within the noise" : e.verdict === "insufficient" ? "Too few hands" : "Clear difference"}
          </span>
          {!m.complete && (
            <span className="rounded-full bg-orange-500/15 px-2.5 py-1 text-xs font-semibold text-orange-100 ring-1 ring-orange-400/30">
              Stopped at {count(m.hands)} of {count(m.planned_hands)} hands
            </span>
          )}
        </div>
        <div className="mt-3 flex flex-wrap items-end gap-x-6 gap-y-3">
          <div>
            <div className="text-[11px] text-slate-400">{m.a_label} against {m.b_label}</div>
            <div className="font-display text-[30px] font-bold leading-none tabular-nums text-amber-50" data-testid="matchup-rate">
              {rate(e.bb_per_100)} <span className="text-[16px] font-semibold text-slate-300">bb/100</span>
            </div>
            <div className="mt-1 text-xs text-slate-400">
              {intervalText(e) ? `${level}% interval ${intervalText(e)}` : "No interval"} · {pText(e.p_value)}
            </div>
          </div>
          <Stat label="Hands" value={count(m.hands)} />
          <Stat label="Standard error" value={e.se === null ? "—" : `±${e.se.toFixed(1)}`} hint="bb/100" />
          {m.variance && (
            <Stat
              label="Variance reduction"
              value={m.variance.reduction === null ? "exact" : `${m.variance.reduction.toFixed(1)}×`}
              hint="Unpaired variance ÷ duplicate variance, from the same hands"
            />
          )}
          {e.hands_for_precision !== null && <Stat label="Hands for ±5 bb/100" value={count(roundNice(e.hands_for_precision))} />}
        </div>
        <p className="mt-3 max-w-3xl text-sm text-slate-300" data-testid="matchup-sentence">
          {verdictSentence(m)}
        </p>
        {reduction && (
          <p className="mt-1 max-w-3xl text-xs text-slate-400" data-testid="matchup-reduction">
            {reduction}
          </p>
        )}
        <ul className="mt-2 max-w-3xl list-disc space-y-1 pl-4 text-xs text-slate-400">
          {e.method === "clustered" && (
            <li>
              {m.adaptive ? "An adaptive strategy learns during a session, so" : ""} hands within a session are not independent: the
              interval uses the variation between the {count(e.clusters ?? 0)} sessions, which is wider.
            </li>
          )}
          {e.dependence_warning && (
            <li className="text-orange-200" data-testid="dependence-warning">
              Only {e.clusters} sessions: with an adaptive strategy the interval may be too narrow. Run more hands or shorter sessions
              (at least 10 sessions).
            </li>
          )}
          {!m.complete && (
            <li>
              This run was stopped early. If it was stopped because of how the result looked, the interval overstates the
              certainty.
            </li>
          )}
        </ul>
      </section>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <section className="surface rounded-2xl p-4" aria-label="Results over time">
          <h4 className="eyebrow mb-2 text-slate-400">{m.a_label}'s results (bb)</h4>
          <TrajectoryChart trajectory={m.trajectory} bandSd={m.band_sd} aLabel={m.a_label} />
          {m.variance && (
            <p className="mt-2 text-xs text-slate-400">
              The same cards are dealt at both tables with the players swapped, so the luck in one table's line is largely
              undone by the other's
              {m.variance.table_correlation !== null && ` (correlation ${m.variance.table_correlation.toFixed(2)})`}. The total is
              what remains: mostly the difference in play.
            </p>
          )}
        </section>
        <section className="surface rounded-2xl p-4" aria-label="How the estimate settled">
          <h4 className="eyebrow mb-2 text-slate-400">How the estimate settled (bb/100)</h4>
          <ConvergenceChart paired={m.convergence.paired} unpaired={m.convergence.unpaired} />
          <p className="mt-2 text-xs text-slate-400">
            Early intervals are wide and the estimate wanders inside them; read the final interval, not a point where the line
            happened to look good.
          </p>
        </section>
      </div>

      <section className="surface overflow-x-auto rounded-2xl p-4" aria-label="Playing styles" data-testid="matchup-styles">
        <h4 className="eyebrow text-slate-400">How they played</h4>
        <table className="mt-2 w-full min-w-[520px] text-xs tabular-nums [&_td+td]:pl-2 [&_th+th]:pl-2">
          <thead className="text-left text-slate-500">
            <tr>
              <th className="py-1 font-medium" />
              <th className="py-1 text-right font-medium" title="Voluntarily put chips in before the flop">VPIP</th>
              <th className="py-1 text-right font-medium" title="Raised before the flop">PFR</th>
              <th className="py-1 text-right font-medium" title="Postflop bets and raises per call">Aggression</th>
              <th className="py-1 text-right font-medium" title="Went to showdown, of hands that saw the flop">WTSD</th>
              <th className="py-1 text-right font-medium" title="Won at showdown">W$SD</th>
            </tr>
          </thead>
          <tbody className="text-slate-200">
            <StyleRow label={m.a_label} s={m.styles.a} />
            <StyleRow label={m.b_label} s={m.styles.b} />
          </tbody>
        </table>
        <p className="mt-2 text-xs text-slate-400">
          Average pot {m.table.avg_pot_bb === null ? "—" : `${m.table.avg_pot_bb.toFixed(1)} bb`} · flop seen {pct(m.table.flop)} ·
          showdown {pct(m.table.showdown)} · someone all-in {pct(m.table.allin)} · {count(m.sessions)} sessions of fresh agents ·
          every hand starts from equal stacks.
        </p>
      </section>
    </div>
  );
}

function StyleRow({ label, s }: { label: string; s: LabStyle }) {
  return (
    <tr className="border-t border-white/5">
      <td className="py-1.5">{label}</td>
      <td className="py-1.5 text-right">{pct(s.vpip)}</td>
      <td className="py-1.5 text-right">{pct(s.pfr)}</td>
      <td className="py-1.5 text-right">{s.aggression === null ? "—" : s.aggression.toFixed(2)}</td>
      <td className="py-1.5 text-right">{pct(s.wtsd)}</td>
      <td className="py-1.5 text-right">{pct(s.wsd)}</td>
    </tr>
  );
}

/** League table and results matrix for a round robin. */
export function RoundRobin({
  rr,
  labels,
  order,
  selected,
  onSelect,
}: {
  rr: RoundRobinReport;
  labels: Record<string, string>;
  order: string[];
  selected: [string, string] | null;
  onSelect: (pair: [string, string]) => void;
}) {
  const cells = new Map(rr.cells.map((c) => [`${c.row}|${c.col}`, c]));
  const scale = symmetricScale(rr.cells.map((c) => c.bb_per_100), 5);
  const span = Math.max(1, ...rr.league.map((r) => Math.max(Math.abs(r.low), Math.abs(r.high))));
  const pos = (v: number) => `${50 + (v / span) * 50}%`;
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <section className="surface rounded-2xl p-4" aria-labelledby="league-title" data-testid="league">
        <h3 id="league-title" className="eyebrow text-slate-400">
          Average against the field
        </h3>
        <table className="mt-2 w-full text-xs tabular-nums">
          <thead className="text-left text-slate-500">
            <tr>
              <th className="py-1 font-medium">#</th>
              <th className="py-1 font-medium">Strategy</th>
              <th className="py-1 text-right font-medium">bb/100</th>
              <th className="py-1 pl-3 font-medium">95% interval</th>
            </tr>
          </thead>
          <tbody className="text-slate-200">
            {rr.league.map((r) => (
              <tr key={r.strategy} className="border-t border-white/5">
                <td className="py-1.5 text-slate-400">{r.rank}</td>
                <td className="py-1.5">{r.label}</td>
                <td className="py-1.5 text-right font-semibold">{signed(r.bb_per_100)}</td>
                <td className="py-1.5 pl-3">
                  <div className="flex items-center gap-2">
                  <span className="shrink-0 text-slate-400">
                    {signed(r.low, 0)} to {signed(r.high, 0)}
                  </span>
                  <div className="relative h-3 w-full min-w-[80px] max-sm:hidden" aria-hidden="true">
                    <div className="absolute inset-y-0 left-1/2 w-px bg-white/25" />
                    <div className="absolute top-1/2 h-0.5 -translate-y-1/2 rounded bg-slate-400"
                      style={{ left: pos(r.low), right: `calc(100% - ${pos(r.high)})` }} />
                    <div className="absolute top-1/2 h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-amber-300"
                      style={{ left: pos(r.bb_per_100) }} />
                  </div>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-2 text-xs text-slate-400">
          Each strategy's mean result over its matchups. Neighbouring ranks whose intervals overlap may not really differ.
        </p>
      </section>
      <section className="surface overflow-x-auto rounded-2xl p-4" aria-labelledby="matrix-title" data-testid="matrix">
        <h3 id="matrix-title" className="eyebrow text-slate-400">
          Head to head (row's result, bb/100)
        </h3>
        <table className="mt-2 border-separate border-spacing-[2px] text-xs tabular-nums">
          <thead>
            <tr>
              <th />
              {order.map((c) => (
                <th key={c} className="max-w-[72px] px-1 pb-1 text-center align-bottom text-[10px] font-medium text-slate-400" scope="col">
                  {labels[c]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {order.map((r) => (
              <tr key={r}>
                <th scope="row" className="pr-2 text-left text-[11px] font-medium text-slate-300">
                  {labels[r]}
                </th>
                {order.map((c) => {
                  if (r === c) return <td key={c} className="h-10 w-16 rounded bg-white/[0.02]" />;
                  const cell = cells.get(`${r}|${c}`);
                  if (!cell) return <td key={c} className="h-10 w-16 rounded bg-white/[0.03] text-center text-slate-600">…</td>;
                  const pair: [string, string] = order.indexOf(r) < order.indexOf(c) ? [r, c] : [c, r];
                  const active = selected && selected[0] === pair[0] && selected[1] === pair[1];
                  return (
                    <td key={c} className="p-0">
                      <button
                        type="button"
                        onClick={() => onSelect(pair)}
                        aria-label={`${labels[r]} against ${labels[c]}: ${signed(cell.bb_per_100)} bb/100${cell.significant ? ", clear difference" : ", not clear"}`}
                        className={`flex h-10 w-16 flex-col items-center justify-center rounded text-slate-50 ${active ? "ring-2 ring-amber-200" : ""}`}
                        style={{ background: divergingColor(cell.bb_per_100, scale) }}
                      >
                        <span className={cell.significant ? "font-bold" : "text-slate-200/80"}>{signed(cell.bb_per_100, 0)}</span>
                        {cell.significant && <span className="text-[9px] leading-none">clear</span>}
                      </button>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-2 text-xs text-slate-400">
          Blue: the row strategy won; orange: it lost. <b className="font-semibold text-slate-300">Clear</b> means the difference
          holds after a Holm correction for the {rr.comparisons} comparisons, so the chance of any false "clear" in the table
          stays at 5%. Select a cell for the matchup.
        </p>
      </section>
    </div>
  );
}
