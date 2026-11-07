import { useEffect, useRef, useState } from "react";
import type { GameState, StreetEquity } from "../types";

const STREET_LABEL: Record<StreetEquity["street"], string> = {
  preflop: "Preflop",
  flop: "Flop",
  turn: "Turn",
};

/**
 * A probability as a whole percentage that never rounds an uncertain outcome to a
 * certainty: 0.996 shows ">99%", not "100%".
 */
export function formatPercent(p: number): string {
  if (p <= 0) return "0%";
  if (p >= 1) return "100%";
  const rounded = Math.round(p * 100);
  if (rounded === 0) return "<1%";
  if (rounded === 100) return ">99%";
  return `${rounded}%`;
}

/**
 * Hold back each newly added row until the cards it describes have turned over, so the
 * panel never reveals how a face-down card changes the odds. Every row keeps its own
 * deadline, set when it first arrives. Rows that arrive in quick succession (an all-in
 * runout) therefore still appear one by one. Removing rows (a new hand) is immediate.
 */
export function useRevealed(rows: StreetEquity[] | null, delayMs: number): StreetEquity[] | null {
  const deadlines = useRef<number[]>([]);
  const [, setTick] = useState(0);
  const count = rows?.length ?? 0;
  if (count < deadlines.current.length) deadlines.current = deadlines.current.slice(0, count);
  const now = Date.now();
  while (deadlines.current.length < count) deadlines.current.push(now + Math.max(0, delayMs));
  const visible = deadlines.current.filter((at) => at <= now).length;
  const next = deadlines.current[visible];

  useEffect(() => {
    if (next === undefined) return;
    const t = setTimeout(() => setTick((n) => n + 1), Math.max(0, next - Date.now()));
    return () => clearTimeout(t);
  }, [next]);

  if (!rows || visible === 0) return null;
  return visible === count ? rows : rows.slice(0, visible);
}

/** The 95% margin of a Monte Carlo estimate, e.g. "±0.8%"; empty for exact results. */
export function formatMargin(row: StreetEquity, seat: number): string {
  if (row.method !== "monte_carlo") return "";
  const margin = 1.96 * (row.std_error[seat] ?? 0) * 100;
  return `±${margin < 0.1 ? "0.1" : margin.toFixed(1)}%`;
}

/**
 * Equity of face-up hands, street by street. It shows live during an all-in runout (once
 * both hands are revealed) and as a review after any showdown. It never appears while a
 * hand is hidden.
 */
export function EquityPanel({ state, revealDelayMs = 0 }: { state: GameState; revealDelayMs?: number }) {
  const live = state.status === "in_hand" ? (state.live_equity ?? null) : null;
  const incoming = live ?? (state.status !== "in_hand" ? state.result?.equity : null) ?? null;
  const rows = useRevealed(incoming, revealDelayMs);
  if (!rows?.length) return null;
  const seats = Object.keys(rows[0].equity).map(Number).sort((a, b) => a - b);
  const hero = seats.find((s) => state.players[s]?.is_human) ?? seats[0];
  const villain = seats.find((s) => s !== hero) ?? seats[1];
  const name = (seat: number) => state.players[seat]?.name ?? `Seat ${seat + 1}`;

  return (
    <section
      className="surface mb-3 rounded-2xl px-4 py-3"
      aria-labelledby="equity-title"
      data-testid="equity-panel"
    >
      <h2 id="equity-title" className="eyebrow text-slate-400">
        {live ? "All-in equity" : "Equity review"}
      </h2>
      <p className="mt-1 text-[12px] leading-snug text-slate-400">
        {live
          ? "Both hands are face up. Each hand's share of the pot as the board comes out, counting split pots as half."
          : "Each hand's share of the pot if both players had been all-in at that point, counting split pots as half."}
      </p>
      <table className="mt-2 w-full text-[13px]">
        <caption className="sr-only">
          {name(hero)} versus {name(villain)} equity by street
        </caption>
        <thead>
          <tr className="text-left text-[11px] uppercase tracking-wide text-slate-400">
            <th scope="col" className="py-1 font-medium">
              Street
            </th>
            <th scope="col" className="py-1 text-right font-medium">
              {name(hero)}
            </th>
            <th scope="col" className="py-1 text-right font-medium">
              {name(villain)}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const mine = row.equity[hero] ?? 0;
            const estimate = row.method === "monte_carlo";
            return (
              <tr key={row.street} className="border-t border-white/5">
                <th scope="row" className="py-1.5 text-left font-medium text-slate-300">
                  {STREET_LABEL[row.street]}
                  {estimate && (
                    <span className="ml-1 text-[11px] font-normal text-slate-400" title="Monte Carlo estimate with a 95% margin">
                      (est.)
                    </span>
                  )}
                  <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-slate-600/60" aria-hidden>
                    <div className="h-full rounded-full bg-emerald-400" style={{ width: `${mine * 100}%` }} />
                  </div>
                </th>
                {[hero, villain].map((seat) => (
                  <td key={seat} className="py-1.5 pl-2 text-right align-top tabular-nums text-slate-200">
                    {formatPercent(row.equity[seat] ?? 0)}
                    {estimate && <span className="block text-[11px] text-slate-400">{formatMargin(row, seat)}</span>}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}
