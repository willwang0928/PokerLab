import type { CoachOption, DecisionReview, Grade } from "../types";
import { formatPercent } from "./EquityPanel";
import { MiniCard } from "./HandLog";

export const GRADE_STYLE: Record<Grade, string> = {
  good: "bg-emerald-500/15 text-emerald-300 ring-emerald-400/30",
  close: "bg-sky-500/15 text-sky-300 ring-sky-400/30",
  questionable: "bg-amber-500/15 text-amber-300 ring-amber-400/30",
  mistake: "bg-rose-500/15 text-rose-300 ring-rose-400/30",
  ungraded: "bg-slate-500/15 text-slate-300 ring-slate-400/30",
};

export function GradeChip({ grade, label }: { grade: Grade; label: string }) {
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ${GRADE_STYLE[grade]}`}
      data-grade={grade}
    >
      {label}
    </span>
  );
}

/** "+1.2 bb" for an EV in chips, or a dash when the option is not modelled. */
export function formatBb(chips: number | null, bigBlind: number): string {
  if (chips === null) return "—";
  const v = chips / bigBlind;
  const s = Math.abs(v) < 0.05 ? "0.0" : `${v > 0 ? "+" : "−"}${Math.abs(v).toFixed(1)}`;
  return `${s} bb`;
}

export function Cards({ codes }: { codes: string[] }) {
  return (
    <span className="inline-flex flex-wrap gap-0.5">
      {codes.map((c) => (
        <MiniCard key={c} code={c} />
      ))}
    </span>
  );
}

const BASIS_NOTE: Record<CoachOption["basis"], string> = {
  "pot odds": "pot odds and equity",
  "fold model": "assumes a fold rate",
  guideline: "guideline",
  "not modelled": "not modelled",
};

const CONFIDENCE_TEXT = {
  high: "High confidence: the result rests on pot odds and exact or near-exact equity.",
  medium: "Medium confidence: later streets or an assumed fold rate could change it.",
  low: "Low confidence: treat this as a rough guide.",
};

/** The options compared side by side: estimated EV, what it rests on, and which is best. */
export function OptionsTable({ review }: { review: DecisionReview }) {
  return (
    <table className="mt-2 w-full text-[12.5px]" data-testid="options-table">
      <thead>
        <tr className="text-left text-[10.5px] uppercase tracking-wide text-slate-400">
          <th scope="col" className="py-1 font-medium">
            Option
          </th>
          <th scope="col" className="py-1 text-right font-medium">
            Est. EV
          </th>
          <th scope="col" className="py-1 pl-2 text-right font-medium">
            Basis
          </th>
        </tr>
      </thead>
      <tbody>
        {review.options.map((o) => {
          const best = review.best.includes(o.key);
          const chosen = review.chosen === o.key;
          return (
            <tr key={o.key} className={`border-t border-white/5 ${chosen ? "bg-white/[0.04]" : ""}`}>
              <th scope="row" className="py-1.5 text-left font-medium text-slate-200">
                {o.label}
                {chosen && <span className="ml-1.5 text-[10.5px] font-semibold text-amber-300">your choice</span>}
                {best && (
                  <span className="ml-1.5 text-[10.5px] font-semibold text-emerald-300">
                    {o.basis === "guideline" ? "suggested" : "best estimate"}
                  </span>
                )}
              </th>
              <td className="py-1.5 text-right tabular-nums text-slate-200">{formatBb(o.ev, review.big_blind)}</td>
              <td className="py-1.5 pl-2 text-right text-[11px] text-slate-400">{BASIS_NOTE[o.basis]}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** Key numbers in one line: equity, equity needed, range size. */
export function KeyNumbers({ review }: { review: DecisionReview }) {
  if (review.equity === null) return null;
  return (
    <dl className="mt-2 grid grid-cols-3 gap-2 text-center" data-testid="key-numbers">
      <div className="rounded-lg bg-white/[0.04] px-2 py-1.5">
        <dt className="text-[10px] uppercase tracking-wide text-slate-400">Your equity</dt>
        <dd className="text-[15px] font-semibold tabular-nums text-slate-100">{formatPercent(review.equity)}</dd>
      </div>
      <div className="rounded-lg bg-white/[0.04] px-2 py-1.5">
        <dt className="text-[10px] uppercase tracking-wide text-slate-400">Needed to call</dt>
        <dd className="text-[15px] font-semibold tabular-nums text-slate-100">
          {review.required_equity === null ? "—" : formatPercent(review.required_equity)}
        </dd>
      </div>
      <div className="rounded-lg bg-white/[0.04] px-2 py-1.5">
        <dt className="text-[10px] uppercase tracking-wide text-slate-400">Their range</dt>
        <dd className="text-[15px] font-semibold tabular-nums text-slate-100">{review.range_size ?? "—"}</dd>
      </div>
    </dl>
  );
}

/**
 * The full explanation of one decision. ``detailed`` adds the maths: equity source, EV
 * formulas, how the range was estimated, and the assumptions behind the numbers.
 */
export function DecisionDetail({ review, detailed }: { review: DecisionReview; detailed: boolean }) {
  return (
    <div className="text-[12.5px] leading-snug text-slate-300">
      <p>{review.explanation}</p>
      <KeyNumbers review={review} />
      {review.options.length > 0 && <OptionsTable review={review} />}
      {review.reads.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-[12px] text-slate-300" data-testid="reads">
          {review.reads.map((r) => (
            <li key={r}>
              <span aria-hidden className="mr-1 text-amber-300">
                •
              </span>
              {r}
            </li>
          ))}
        </ul>
      )}
      <p className="mt-2 text-[11.5px] text-slate-400">{CONFIDENCE_TEXT[review.confidence]}</p>
      {detailed && (
        <div className="mt-2 space-y-2 border-t border-white/5 pt-2" data-testid="decision-maths">
          <ul className="space-y-0.5 font-mono text-[11px] text-slate-300">
            {review.details.map((d) => (
              <li key={d}>{d}</li>
            ))}
          </ul>
          {review.range_steps.length > 0 && (
            <div>
              <div className="text-[10.5px] uppercase tracking-wide text-slate-400">How their range was estimated</div>
              <ol className="mt-0.5 list-decimal space-y-0.5 pl-4 text-[11.5px] text-slate-300">
                {review.range_steps.map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ol>
            </div>
          )}
          {review.caveats.length > 0 && (
            <ul className="space-y-0.5 text-[11.5px] text-slate-400">
              {review.caveats.map((c) => (
                <li key={c}>Note: {c}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
