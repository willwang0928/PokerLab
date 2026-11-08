import type { GameState, TrackedStat } from "../types";
import { formatPercent } from "./EquityPanel";

/** "38%" for a rate, or a dash before the first opportunity. */
function rateText(stat: TrackedStat): string {
  return stat.rate === null ? "—" : formatPercent(stat.rate);
}

/** Sample size, rounded: older hands are discounted, so counts can be fractional. */
function sampleText(stat: TrackedStat): string {
  return `${Math.round(stat.count)}/${Math.round(stat.opportunities)}`;
}

/**
 * Your observable tendencies as the opponent tracks them, how it is adjusting, and which
 * of its decisions adaptation changed in the last hand. It only ever shows statistics
 * from public actions, the same information you see in the hand history.
 */
export function TendenciesPanel({ state }: { state: GameState }) {
  const model = state.opponent_model;
  if (!model) return null;
  const notes = state.status !== "in_hand" ? (state.result?.adaptation_notes ?? null) : null;

  return (
    <details className="surface group mb-3 rounded-2xl" open data-testid="tendencies-panel">
      <summary className="eyebrow flex cursor-pointer list-none items-center justify-between px-4 py-3 text-slate-400">
        <span id="tendencies-title">Your tendencies</span>
        <span className="text-[11px] normal-case tracking-normal text-slate-400">
          {model.hands} {model.hands === 1 ? "hand" : "hands"}
          <span aria-hidden className="ml-1.5 inline-block transition group-open:rotate-180">
            ▾
          </span>
        </span>
      </summary>
      <div className="border-t border-white/5 px-4 pb-3 pt-2">
        <table className="w-full text-[13px]" aria-labelledby="tendencies-title">
          <thead>
            <tr className="text-left text-[11px] uppercase tracking-wide text-slate-400">
              <th scope="col" className="py-1 font-medium">
                Stat
              </th>
              <th scope="col" className="py-1 text-right font-medium">
                You
              </th>
              <th scope="col" className="py-1 text-right font-medium">
                Reference
              </th>
            </tr>
          </thead>
          <tbody>
            {model.stats.map((stat) => (
              <tr key={stat.key} className="border-t border-white/5" title={stat.hint}>
                <th scope="row" className="py-1.5 text-left font-medium text-slate-300">
                  {stat.label}
                </th>
                <td className="py-1.5 text-right tabular-nums text-slate-200">
                  {rateText(stat)}
                  <span className={`block text-[11px] ${stat.reliable ? "text-slate-400" : "text-amber-300/80"}`}>
                    {stat.reliable ? sampleText(stat) : `${sampleText(stat)} · needs ${model.min_opportunities}`}
                  </span>
                </td>
                <td className="py-1.5 pl-2 text-right align-top tabular-nums text-slate-400">
                  {formatPercent(stat.baseline)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        <h3 className="mt-3 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
          Opponent adjustments
        </h3>
        <p className="mt-1 text-[12px] leading-snug text-slate-400" data-testid="adaptation-policy">
          {model.policy}
        </p>
        {model.adapts && (
          <ul className="mt-1.5 space-y-1 text-[12.5px] leading-snug text-slate-200" data-testid="adjustments">
            {model.adjustments.length ? (
              model.adjustments.map((a) => (
                <li key={a} className="flex gap-1.5">
                  <span aria-hidden className="text-amber-300">
                    •
                  </span>
                  {a}
                </li>
              ))
            ) : (
              <li className="text-slate-400">None yet: your stats are close to the reference or the samples are too small.</li>
            )}
          </ul>
        )}

        {model.adapts && notes && (
          <>
            <h3 className="mt-3 text-[11px] font-semibold uppercase tracking-wide text-slate-400">Last hand</h3>
            <ul className="mt-1 space-y-1 text-[12.5px] leading-snug text-slate-200" data-testid="adaptation-notes">
              {notes.length ? (
                notes.map((n, i) => <li key={i}>{n}</li>)
              ) : (
                <li className="text-slate-400">Adaptation did not change any of its decisions.</li>
              )}
            </ul>
          </>
        )}
      </div>
    </details>
  );
}
