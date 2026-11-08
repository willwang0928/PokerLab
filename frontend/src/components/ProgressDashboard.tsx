import { useMemo, useState } from "react";
import { CONCEPT_LABEL, decisionTrend, type Progress } from "../lib/progress";
import {
  answerTrend,
  conceptSkills,
  INTERVALS,
  LEVEL_LABEL,
  mistakeGuides,
  nextPractice,
  type TrendPoint,
} from "../lib/skills";
import type { Grade, TrainingDifficulty } from "../types";
import { GradeChip } from "./DecisionDetail";
import { measuredText, SkillMeter } from "./SkillMeter";
import type { Plan } from "./TrainingView";

const GRADE_LABEL: Record<Grade, string> = {
  good: "Good",
  close: "Close call",
  questionable: "Questionable",
  mistake: "Likely mistake",
  ungraded: "Not graded",
};

function relative(ms: number): string {
  const m = Math.round(Math.abs(ms) / 60_000);
  const text = m < 60 ? `${Math.max(1, m)} min` : m < 36 * 60 ? `${Math.round(m / 60)} h` : `${Math.round(m / 1440)} days`;
  return ms >= 0 ? `in ${text}` : `${text} ago`;
}

interface Props {
  progress: Progress;
  onStart: (plan: Plan) => void;
  onReset: () => void;
  now?: number;
}

/**
 * The learning dashboard: what to practise next and why, estimated skill per concept
 * next to the raw counts it comes from, the trend, recurring mistakes explained, the
 * spaced-repetition schedule and the practice history.
 */
export function ProgressDashboard({ progress, onStart, onReset, now = Date.now() }: Props) {
  const skills = useMemo(() => conceptSkills(progress), [progress]);
  const next = useMemo(() => nextPractice(progress, now), [progress, now]);
  const guides = useMemo(() => mistakeGuides(progress), [progress]);
  const trend = useMemo(() => answerTrend(progress), [progress]);
  const table = decisionTrend(progress);
  const firstAnswers = progress.answers.length;
  const due = progress.reviews.filter((r) => r.due <= now).length;
  const [confirmReset, setConfirmReset] = useState(false);

  const start = (concept: Plan["concept"], difficulty: TrainingDifficulty, reason: string, review = false) =>
    onStart({ concept, difficulty, reason, review });

  const exportData = () => {
    const blob = new Blob([JSON.stringify(progress, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "pokerlab-progress.json";
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="grid w-full gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]" data-testid="progress-dashboard">
      {/* Next practice */}
      <section className="surface rounded-2xl p-4 lg:col-span-2" aria-labelledby="next-title" data-testid="next-practice">
        <h2 id="next-title" className="eyebrow text-slate-400">
          Next practice
        </h2>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <div className="min-w-0 flex-1">
            <p className="font-display text-[19px] font-bold text-amber-50">
              {CONCEPT_LABEL[next.concept]} <span className="text-[14px] font-semibold text-slate-300">· {LEVEL_LABEL[next.difficulty]}</span>
              {next.kind === "review" && (
                <span className="ml-2 rounded-full bg-sky-500/15 px-2 py-0.5 align-middle text-[11px] font-semibold text-sky-200 ring-1 ring-sky-400/30">
                  Review
                </span>
              )}
            </p>
            <p className="mt-0.5 text-[13px] text-slate-300">{next.reason}</p>
          </div>
          <button
            type="button"
            onClick={() => start(next.concept, next.difficulty, next.reason, next.kind === "review")}
            className="btn-gold rounded-xl px-5 py-2.5 text-[14px] font-bold"
          >
            Start practice
          </button>
        </div>
        <dl className="mt-3 grid grid-cols-3 gap-2 text-center">
          <Stat label="First answers" value={String(firstAnswers)} />
          <Stat label="Table decisions reviewed" value={String(table.total)} />
          <Stat label="Reviews due" value={String(due)} />
        </dl>
      </section>

      {/* Skills */}
      <section className="surface rounded-2xl p-4" aria-labelledby="skills-dash-title" data-testid="dashboard-skills">
        <h2 id="skills-dash-title" className="eyebrow text-slate-400">
          Skills
        </h2>
        <ul className="mt-2 space-y-3">
          {skills.map((s) => (
            <li key={s.concept} data-testid={`dash-skill-${s.concept}`}>
              <div className="flex flex-wrap items-baseline justify-between gap-x-2 text-[12.5px]">
                <span className="font-medium text-slate-200">{s.label}</span>
                <span className="tabular-nums text-slate-400">
                  Training: {measuredText(s)}
                  {s.tableDecisions > 0 && (
                    <>
                      {" "}
                      · Table: {s.tableFlagged} flagged of {s.tableDecisions}
                    </>
                  )}
                </span>
              </div>
              <SkillMeter skill={s} />
              {s.recent !== null && s.previous !== null && (
                <div className="mt-0.5 text-[11px] text-slate-400">
                  Last 10 answers {Math.round(s.recent * 100)}% vs the 10 before {Math.round(s.previous * 100)}%
                  {Math.abs(s.recent - s.previous) < 0.25 ? " (within normal variation for 10 answers)" : ""}
                </div>
              )}
            </li>
          ))}
        </ul>
        <p className="mt-3 text-[11px] leading-snug text-slate-500">
          <b className="font-semibold text-slate-400">Measured</b> counts are your first answers and graded table decisions.
          The <b className="font-semibold text-slate-400">estimate</b> models them (harder levels count more, table
          decisions half), shown as the expected score on an Intermediate scenario with an 80% range. It reflects agreement
          with PokerLab's one-street analysis on its scenarios, not real-money results, and nothing here is solver-verified.
        </p>
      </section>

      <div className="flex flex-col gap-4">
        {/* Trend */}
        <section className="surface rounded-2xl p-4" aria-labelledby="trend-title" data-testid="dashboard-trend">
          <h2 id="trend-title" className="eyebrow text-slate-400">
            First-answer score over time
          </h2>
          {trend.length >= 2 ? (
            <TrendChart points={trend} />
          ) : (
            <p className="mt-2 text-[12.5px] text-slate-400">
              The trend appears after 20 first answers (each point averages 10). You have {firstAnswers}.
            </p>
          )}
          {table.recent !== null && (
            <p className="mt-2 text-[12px] text-slate-400">
              At the table: {Math.round(table.recent * 100)}% of your last {Math.min(20, table.total)} graded decisions were
              good or close
              {table.previous !== null ? ` (previous 20: ${Math.round(table.previous * 100)}%)` : ""}.
            </p>
          )}
        </section>

        {/* Mistakes */}
        <section className="surface rounded-2xl p-4" aria-labelledby="mistakes-title" data-testid="dashboard-mistakes">
          <h2 id="mistakes-title" className="eyebrow text-slate-400">
            Recurring mistakes
          </h2>
          {guides.length === 0 ? (
            <p className="mt-2 text-[12.5px] text-slate-400">
              None yet. A mistake shows up here once it recurs in your reviewed hands.
            </p>
          ) : (
            <ul className="mt-2 space-y-3">
              {guides.map((g) => (
                <li key={g.tag} className="rounded-xl bg-white/[0.03] p-3 ring-1 ring-white/5" data-testid="mistake-guide">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="text-[13px] font-semibold text-amber-200">{g.label}</span>
                    <span className="text-[11.5px] text-slate-400">{g.count} times recently</span>
                  </div>
                  <p className="mt-1 text-[12.5px] leading-snug text-slate-300">{g.principle}</p>
                  {g.evidence && <p className="mt-1 text-[12px] text-slate-400">{g.evidence}</p>}
                  <p className="mt-1 text-[12.5px] leading-snug text-slate-200">
                    <b className="font-semibold">Try this:</b> {g.fix}
                  </p>
                  <button
                    type="button"
                    onClick={() =>
                      start(
                        g.concept,
                        skills.find((s) => s.concept === g.concept)!.level,
                        `Practising "${g.label.toLowerCase()}" from your hands.`,
                      )
                    }
                    className="btn-dark mt-2 rounded-lg px-3 py-1.5 text-[12px] font-semibold"
                  >
                    Practise {CONCEPT_LABEL[g.concept].toLowerCase()}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* Reviews */}
        <section className="surface rounded-2xl p-4" aria-labelledby="reviews-title" data-testid="dashboard-reviews">
          <h2 id="reviews-title" className="eyebrow text-slate-400">
            Review schedule
          </h2>
          {progress.reviews.length === 0 ? (
            <p className="mt-2 text-[12.5px] text-slate-400">
              Nothing scheduled. When you miss a concept it comes back after 10 minutes, then after 1, 3, 7 and 21 days as
              you get it right.
            </p>
          ) : (
            <ul className="mt-2 space-y-1 text-[12.5px]">
              {[...progress.reviews]
                .sort((a, b) => a.due - b.due)
                .map((r) => (
                  <li key={r.key} className="flex items-center justify-between gap-2 border-t border-white/5 py-1">
                    <span className="text-slate-200">
                      {CONCEPT_LABEL[r.concept]}
                      <span className="text-slate-400">
                        {" "}
                        · {r.difficulty === "auto" ? "your level" : LEVEL_LABEL[r.difficulty as TrainingDifficulty]} · step{" "}
                        {r.box + 1} of {INTERVALS.length}
                        {r.source === "table" ? " · from the table" : ""}
                      </span>
                    </span>
                    <span className={r.due <= now ? "font-semibold text-sky-300" : "text-slate-400"}>
                      {r.due <= now ? "due now" : relative(r.due - now)}
                    </span>
                  </li>
                ))}
            </ul>
          )}
        </section>
      </div>

      {/* History */}
      <section className="surface rounded-2xl p-4 lg:col-span-2" aria-labelledby="history-title" data-testid="dashboard-history">
        <h2 id="history-title" className="eyebrow text-slate-400">
          Practice history
        </h2>
        {progress.answers.length === 0 ? (
          <p className="mt-2 text-[12.5px] text-slate-400">No training answers yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="mt-2 w-full min-w-[420px] text-[12.5px]">
              <thead>
                <tr className="text-left text-[10.5px] uppercase tracking-wide text-slate-400">
                  <th scope="col" className="py-1 font-medium">
                    When
                  </th>
                  <th scope="col" className="py-1 font-medium">
                    Concept
                  </th>
                  <th scope="col" className="py-1 font-medium">
                    Level
                  </th>
                  <th scope="col" className="py-1 text-right font-medium">
                    First answer
                  </th>
                </tr>
              </thead>
              <tbody>
                {[...progress.answers]
                  .slice(-15)
                  .reverse()
                  .map((a) => (
                    <tr key={a.id} className="border-t border-white/5">
                      <td className="py-1.5 text-slate-400">{relative(a.t - now)}</td>
                      <td className="py-1.5 text-slate-200">
                        {CONCEPT_LABEL[a.concept]}
                        {a.review && <span className="ml-1.5 text-[10.5px] font-semibold text-sky-300">review</span>}
                      </td>
                      <td className="py-1.5 text-slate-300">{LEVEL_LABEL[a.difficulty as TrainingDifficulty] ?? a.difficulty}</td>
                      <td className="py-1.5 text-right">
                        <GradeChip grade={a.grade} label={GRADE_LABEL[a.grade]} />
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-white/5 pt-3 text-[12px] text-slate-400">
          <span className="mr-auto">Progress is saved in this browser.</span>
          <button type="button" onClick={exportData} className="btn-dark rounded-lg px-3 py-1.5 font-semibold">
            Export data
          </button>
          {confirmReset ? (
            <>
              <span className="text-rose-200">Delete all progress?</span>
              <button
                type="button"
                onClick={() => {
                  onReset();
                  setConfirmReset(false);
                }}
                className="rounded-lg bg-rose-600 px-3 py-1.5 font-semibold text-white"
              >
                Delete
              </button>
              <button type="button" onClick={() => setConfirmReset(false)} className="btn-dark rounded-lg px-3 py-1.5 font-semibold">
                Keep
              </button>
            </>
          ) : (
            <button type="button" onClick={() => setConfirmReset(true)} className="btn-dark rounded-lg px-3 py-1.5 font-semibold">
              Reset progress
            </button>
          )}
        </div>
      </section>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-white/[0.04] px-2 py-1.5">
      <dt className="text-[10px] uppercase tracking-wide text-slate-400">{label}</dt>
      <dd className="text-[16px] font-semibold tabular-nums text-slate-100">{value}</dd>
    </div>
  );
}

/** One series, so no legend: the section title names it. Hover or focus a point for its value. */
function TrendChart({ points }: { points: TrendPoint[] }) {
  const [active, setActive] = useState<number | null>(null);
  const W = 320;
  const H = 120;
  const pad = { l: 30, r: 8, t: 8, b: 20 };
  const x = (i: number) => pad.l + (i / Math.max(1, points.length - 1)) * (W - pad.l - pad.r);
  const y = (v: number) => pad.t + (1 - v) * (H - pad.t - pad.b);
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.score).toFixed(1)}`).join(" ");
  const a = active !== null ? points[active] : null;
  return (
    <div className="relative mt-2 max-w-[440px]">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="First-answer score per block of 10 answers" data-testid="trend-chart">
        {[0, 0.5, 1].map((v) => (
          <g key={v}>
            <line x1={pad.l} x2={W - pad.r} y1={y(v)} y2={y(v)} stroke="rgba(255,255,255,0.08)" strokeWidth={1} />
            <text x={pad.l - 6} y={y(v) + 3} textAnchor="end" fontSize={9} fill="rgb(148,163,184)">
              {v * 100}%
            </text>
          </g>
        ))}
        <path d={path} fill="none" stroke="rgb(252,211,77)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {points.map((p, i) => (
          <g key={i}>
            <circle cx={x(i)} cy={y(p.score)} r={active === i ? 4.5 : 3.5} fill="rgb(252,211,77)" stroke="rgb(17,24,39)" strokeWidth={2} />
            <circle
              cx={x(i)}
              cy={y(p.score)}
              r={12}
              fill="transparent"
              tabIndex={0}
              aria-label={`Answers ${p.index - p.n + 1}–${p.index}: ${Math.round(p.score * 100)}%`}
              onMouseEnter={() => setActive(i)}
              onMouseLeave={() => setActive(null)}
              onFocus={() => setActive(i)}
              onBlur={() => setActive(null)}
            />
          </g>
        ))}
        <text x={pad.l} y={H - 4} fontSize={9} fill="rgb(148,163,184)">
          answers 1–{points[0].index}
        </text>
        <text x={W - pad.r} y={H - 4} fontSize={9} fill="rgb(148,163,184)" textAnchor="end">
          {points[points.length - 1].index - points[points.length - 1].n + 1}–{points[points.length - 1].index}
        </text>
      </svg>
      {a && (
        <div
          className="pointer-events-none absolute -top-1 rounded-lg bg-slate-900/95 px-2 py-1 text-[11.5px] text-slate-100 shadow-lg ring-1 ring-white/10"
          style={{ left: `${(x(active!) / W) * 100}%`, transform: "translate(-50%, -100%)" }}
          role="status"
        >
          Answers {a.index - a.n + 1}–{a.index}: <b>{Math.round(a.score * 100)}%</b>
        </div>
      )}
      <p className="mt-1 text-[11px] text-slate-500">
        Each point averages 10 first answers; swings of up to about 20 points between blocks are normal at that size.
      </p>
    </div>
  );
}
