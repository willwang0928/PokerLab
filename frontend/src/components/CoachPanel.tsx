import { CONCEPT_LABEL, decisionTrend, recurringLeaks, type Progress } from "../lib/progress";
import type { DecisionReview, HandReview } from "../types";
import { Cards, DecisionDetail, GradeChip } from "./DecisionDetail";

const QUALITY_TEXT: Record<HandReview["quality"], string> = {
  sound: "Sound decisions",
  mixed: "Some questionable decisions",
  costly: "A likely mistake",
  none: "Nothing to grade",
};

function chosenLabel(d: DecisionReview): string {
  return d.options.find((o) => o.key === d.chosen)?.label ?? d.chosen ?? "";
}

function pct(x: number | null): string {
  return x === null ? "—" : `${Math.round(x * 100)}%`;
}

interface Props {
  review: HandReview | null;
  loading: boolean;
  error: string | null;
  progress: Progress;
  detailed: boolean;
  onToggleDetailed: () => void;
  /** Open the decision in training mode (when available). */
  onPractice?: (decision: DecisionReview) => void;
}

/**
 * Post-hand coaching: each of your decisions graded from what you knew at the time, the
 * result kept separate from decision quality, and your recurring leaks across hands.
 */
export function CoachPanel({ review, loading, error, progress, detailed, onToggleDetailed, onPractice }: Props) {
  const leaks = recurringLeaks(progress).slice(0, 3);
  const trend = decisionTrend(progress);

  return (
    <section className="surface mb-3 rounded-2xl" aria-labelledby="coach-title" data-testid="coach-panel">
      <div className="flex items-center justify-between px-4 pt-3">
        <h2 id="coach-title" className="eyebrow text-slate-400">
          Coach{review ? ` · hand #${review.hand_number}` : ""}
        </h2>
        <label className="flex items-center gap-1.5 text-[11px] text-slate-400">
          <input
            type="checkbox"
            checked={detailed}
            onChange={onToggleDetailed}
            className="h-3.5 w-3.5 accent-amber-300"
          />
          Show the maths
        </label>
      </div>

      <div className="px-4 pb-3 pt-2">
        {loading && <p className="text-[12.5px] text-slate-400">Reviewing your decisions…</p>}
        {error && !loading && <p className="text-[12.5px] text-rose-300">{error}</p>}
        {!review && !loading && !error && (
          <p className="text-[12.5px] text-slate-400">
            After each hand, your decisions are reviewed here using only what you could see when you made them.
          </p>
        )}

        {review && !loading && (
          <div data-testid="hand-review">
            <p className="text-[13px] font-semibold text-slate-100">{QUALITY_TEXT[review.quality]}</p>
            <p className="mt-0.5 text-[12.5px] leading-snug text-slate-300" data-testid="coach-verdict">
              {review.verdict}
            </p>
            {review.hindsight && <p className="mt-1 text-[11.5px] text-slate-400">{review.hindsight}</p>}

            <ol className="mt-2 space-y-1.5">
              {review.decisions.map((d, i) => (
                <li key={i}>
                  <details className="group rounded-xl bg-white/[0.03] ring-1 ring-white/5" data-testid="coach-decision">
                    <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2">
                      <span className="w-12 shrink-0 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                        {d.street}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-[12.5px] text-slate-200">{chosenLabel(d)}</span>
                      <GradeChip grade={d.grade} label={d.grade_label} />
                    </summary>
                    <div className="border-t border-white/5 px-3 pb-3 pt-2">
                      <div className="mb-1.5 flex flex-wrap items-center gap-1.5 text-[11.5px] text-slate-400">
                        <Cards codes={d.hole} />
                        {d.board.length > 0 && (
                          <>
                            <span>on</span>
                            <Cards codes={d.board} />
                          </>
                        )}
                        <span>
                          · pot {d.pot}
                          {d.to_call > 0 ? ` · to call ${d.to_call}` : ""}
                        </span>
                      </div>
                      <DecisionDetail review={d} detailed={detailed} />
                      {d.spot_data && onPractice && (
                        <button
                          type="button"
                          onClick={() => onPractice(d)}
                          className="btn-dark mt-2 rounded-lg px-3 py-1.5 text-[12px] font-semibold"
                        >
                          Practise this spot
                        </button>
                      )}
                    </div>
                  </details>
                </li>
              ))}
            </ol>
          </div>
        )}

        {trend.total > 0 && (
          <div className="mt-3 border-t border-white/5 pt-2" data-testid="coach-progress">
            <h3 className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Your progress</h3>
            <p className="mt-1 text-[12px] text-slate-300">
              Good or close decisions: <b className="tabular-nums text-slate-100">{pct(trend.recent)}</b> of your last{" "}
              {trend.recentCount}
              {trend.previous !== null && (
                <>
                  {" "}
                  (the {trend.previousCount} before: <span className="tabular-nums">{pct(trend.previous)}</span>)
                </>
              )}
              . {trend.total} decisions reviewed in all.
            </p>
            {leaks.length > 0 ? (
              <ul className="mt-1 space-y-0.5 text-[12px] text-slate-300" data-testid="leaks">
                {leaks.map((l) => (
                  <li key={l.tag}>
                    <span className="text-amber-300">{l.label}</span>: {l.count} times recently (practise{" "}
                    {CONCEPT_LABEL[l.concept].toLowerCase()})
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-1 text-[12px] text-slate-400">No recurring leaks so far.</p>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
