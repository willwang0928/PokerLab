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

/** The 95% margin of a Monte Carlo estimate, e.g. "±0.8%"; empty for exact results. */
export function formatMargin(row: StreetEquity, seat: number): string {
  if (row.method !== "monte_carlo") return "";
  const margin = 1.96 * (row.std_error[seat] ?? 0) * 100;
  return `±${margin < 0.1 ? "0.1" : margin.toFixed(1)}%`;
}

/** After a showdown: how often each face-up hand was winning, street by street. */
export function EquityPanel({ state }: { state: GameState }) {
  const rows = state.status !== "in_hand" ? state.result?.equity : null;
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
        Equity review
      </h2>
      <p className="mt-1 text-[12px] leading-snug text-slate-400">
        Each hand's share of the pot if both players had been all-in at that point, counting
        split pots as half.
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
