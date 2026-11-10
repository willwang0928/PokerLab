import type { ReactNode } from "react";
import type { CoachOption, DecisionReview, Grade, Lesson, LessonKind, OpponentInfo } from "../types";
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
  high: "High confidence: pot odds and equity settle it, and the verdict holds against a tighter and a looser range.",
  medium: "Medium confidence: later streets or an assumed fold rate could change it.",
  low: "Low confidence: the verdict depends on assumptions (what they hold, or how often they fold). Treat it as a rough guide.",
};

/** "±2%" for a Monte Carlo estimate, nothing for an exact one. */
function uncertainty(review: DecisionReview): string {
  if (review.equity_method !== "monte_carlo" || !review.equity_se) return "";
  return ` ±${Math.max(1, Math.round(1.96 * review.equity_se * 100))}%`;
}

/**
 * The same comparison against a tighter and a looser range than the estimate. Shows how
 * much the advice depends on what the opponent is assumed to hold.
 */
export function RangeCheckTable({ review }: { review: DecisionReview }) {
  const rows = review.range_check ?? [];
  if (!rows.length || review.equity === null) return null;
  const labels = Object.fromEntries(review.options.map((o) => [o.key, o.label]));
  const bestOf = (evs: Record<string, number>) => {
    const keys = Object.keys(evs).filter((k) => k in labels);
    return keys.length ? labels[keys.reduce((a, b) => (evs[b] > evs[a] ? b : a))] : "—";
  };
  const baseEvs = Object.fromEntries(review.options.filter((o) => o.ev !== null).map((o) => [o.key, o.ev as number]));
  const all = [
    { key: "estimated", label: "Estimated range", size: review.range_size ?? 0, equity: review.equity, evs: baseEvs, note: "" },
    ...rows.map((r) => ({ key: r.name, label: r.label, size: r.range_size, equity: r.equity, evs: r.evs, note: r.description })),
  ];
  return (
    <div className="mt-2" data-testid="range-check">
      <div className="text-[10.5px] uppercase tracking-wide text-slate-400">If their range is different</div>
      <table className="mt-1 w-full text-[12px]">
        <thead>
          <tr className="text-left text-[10px] uppercase tracking-wide text-slate-500">
            <th scope="col" className="py-0.5 font-medium">
              Range
            </th>
            <th scope="col" className="py-0.5 text-right font-medium">
              Your equity
            </th>
            <th scope="col" className="py-0.5 pl-2 text-right font-medium">
              Scores best
            </th>
          </tr>
        </thead>
        <tbody>
          {all.map((r) => (
            <tr key={r.key} className="border-t border-white/5" title={r.note || undefined}>
              <th scope="row" className="py-1 text-left font-medium text-slate-300">
                {r.label} <span className="font-normal text-slate-500">({r.size} combos)</span>
              </th>
              <td className="py-1 text-right tabular-nums text-slate-200">{formatPercent(r.equity)}</td>
              <td className="py-1 pl-2 text-right text-slate-300">{bestOf(r.evs)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-1 text-[11.5px] text-slate-400" data-testid="robustness">
        {review.robust === false
          ? "The verdict changes with the range assumption, so it is not a settled answer: think about which range fits this opponent."
          : review.robust
            ? "The verdict is the same for all three ranges."
            : "Tighter: they bet and call with stronger hands only. Looser: with more hands, including bluffs."}
      </p>
    </div>
  );
}

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
        <dd className="text-[15px] font-semibold tabular-nums text-slate-100">
          {formatPercent(review.equity)}
          <span className="text-[11px] font-normal text-slate-400">{uncertainty(review)}</span>
        </dd>
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

const KIND_STYLE: Record<LessonKind, string> = {
  exact: "text-emerald-300 ring-emerald-400/30",
  estimate: "text-sky-300 ring-sky-400/30",
  assumption: "text-amber-300 ring-amber-400/30",
  model: "text-amber-300 ring-amber-400/30",
  guideline: "text-slate-300 ring-slate-400/30",
};

function KindTag({ kind }: { kind: LessonKind }) {
  return (
    <span
      className={`mr-1.5 inline-block rounded px-1 text-[9.5px] font-semibold uppercase tracking-wide ring-1 ${KIND_STYLE[kind]}`}
      data-kind={kind}
    >
      {kind}
    </span>
  );
}

function hasLesson(review: DecisionReview): review is DecisionReview & { lesson: Lesson } {
  return !!review.lesson && "did" in review.lesson;
}

/**
 * The decision as steps: what you did, what you knew, what was estimated (and how sure),
 * the arithmetic with what kind of number each step is, the alternative, and the takeaway.
 */
export function LessonSteps({ lesson }: { lesson: Lesson }) {
  const row = (label: string, body: ReactNode) => (
    <div className="grid grid-cols-[5.5rem_1fr] gap-2">
      <dt className="pt-px text-[10.5px] uppercase tracking-wide text-slate-400">{label}</dt>
      <dd className="text-slate-300">{body}</dd>
    </div>
  );
  return (
    <dl className="mt-2 space-y-1.5 rounded-lg bg-white/[0.03] p-2 text-[12px]" data-testid="lesson">
      {row("You did", lesson.did)}
      {row("You knew", lesson.known.join(" · "))}
      {lesson.estimated.length > 0 &&
        row(
          "Estimated",
          <ul className="space-y-0.5">
            {lesson.estimated.map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>,
        )}
      {lesson.math.length > 0 &&
        row(
          "The maths",
          <ul className="space-y-0.5" data-testid="lesson-math">
            {lesson.math.map((m) => (
              <li key={m.text}>
                <KindTag kind={m.kind} />
                {m.text}
              </li>
            ))}
          </ul>,
        )}
      {row("Instead", lesson.alternative)}
      {row(
        "Remember",
        <div data-testid="lesson-principle">
          <p className="font-medium text-amber-100">{lesson.principle}</p>
          {lesson.also.map((t) => (
            <p key={t} className="mt-0.5 text-slate-300">
              {t}
            </p>
          ))}
        </div>,
      )}
    </dl>
  );
}

/** Observed tendencies with their sample size and a rough 80% interval. */
export function OpponentEvidence({ info }: { info: OpponentInfo }) {
  const rows = info.evidence.filter((e) => e.seen > 0);
  return (
    <div data-testid="opponent-evidence">
      <div className="text-[10.5px] uppercase tracking-wide text-slate-400">What is known about the opponent</div>
      <p className="mt-0.5 text-[11.5px] text-slate-300">{info.note}</p>
      {rows.length > 0 && (
        <table className="mt-1 w-full text-[11.5px]">
          <thead>
            <tr className="text-left text-[10px] uppercase tracking-wide text-slate-500">
              <th scope="col" className="py-0.5 font-medium">
                Tendency
              </th>
              <th scope="col" className="py-0.5 text-right font-medium">
                Estimate (80% range)
              </th>
              <th scope="col" className="py-0.5 pl-2 text-right font-medium">
                Seen
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((e) => (
              <tr key={e.key} className="border-t border-white/5">
                <th scope="row" className="py-0.5 text-left font-normal text-slate-300">
                  {e.phrase}
                  <span className="ml-1 text-slate-500">(ref. {formatPercent(e.reference)})</span>
                </th>
                <td className="py-0.5 text-right tabular-nums text-slate-200">
                  {formatPercent(e.estimate)}{" "}
                  <span className="text-slate-500">
                    ({formatPercent(e.low)}–{formatPercent(e.high)})
                  </span>
                </td>
                <td className="py-0.5 pl-2 text-right tabular-nums text-slate-400">
                  {Math.round(e.seen)}
                  {!e.reliable && <span className="ml-1 text-amber-300/80">too few</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
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
      {hasLesson(review) && <LessonSteps lesson={review.lesson} />}
      <KeyNumbers review={review} />
      {review.options.length > 0 && <OptionsTable review={review} />}
      <RangeCheckTable review={review} />
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
          <div data-testid="number-kinds">
            <div className="text-[10.5px] uppercase tracking-wide text-slate-400">What kind of numbers these are</div>
            <ul className="mt-0.5 space-y-0.5 text-[11.5px] text-slate-300">
              <li>
                <b className="font-semibold">Exact:</b> pot odds and the chips at stake (arithmetic from the betting).
              </li>
              <li>
                <b className="font-semibold">Estimate:</b> your equity, exact against the estimated range
                {review.equity_method === "monte_carlo" ? " up to sampling error (shown as ±)" : ""}, but the range itself
                is a model of what they might hold.
              </li>
              <li>
                <b className="font-semibold">Model:</b> bet and raise values, which also assume how often they fold.
              </li>
              <li>
                <b className="font-semibold">Guideline:</b> rules of thumb where no EV is computed. None of this is
                solver output.
              </li>
            </ul>
          </div>
          {review.opponent && <OpponentEvidence info={review.opponent} />}
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
