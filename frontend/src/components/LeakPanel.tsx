import { useEffect, useRef } from "react";
import type { LeakState } from "../hooks/useLeakReport";
import { changeText, LEAK_PRINCIPLE, leakPlan, pct } from "../lib/leaks";
import { CONCEPT_LABEL, type Progress } from "../lib/progress";
import { LEVEL_LABEL } from "../lib/skills";
import type { LeakHand, LeakItem, LeakReport } from "../types";
import { MiniCard } from "./HandLog";
import type { Plan } from "./TrainingView";

interface Props {
  state: LeakState;
  progress: Progress;
  onStart: (plan: Plan) => void;
  onShowHand?: (id: string, decision: number | null) => void;
  onRetry?: () => void;
  /** Scroll to and highlight this leak (a link from the history); ``n`` changes per request. */
  focus?: { key: string; n: number } | null;
}

const ACTION_VERB: Record<string, string> = { call: "called", fold: "folded", check: "checked", bet: "bet", raise: "raised" };

/**
 * Strengths and leaks from the saved hands (v0.4.4). Every number comes from the
 * server's leak report; this only lays it out: the pattern and its evidence, why it
 * matters, the hands behind it, what to practise, and whether it is changing.
 */
export function LeakPanel({ state, progress, onStart, onShowHand, onRetry, focus = null }: Props) {
  const report = state.status === "none" ? null : state.report;
  const cards = useRef(new Map<string, HTMLElement>());
  useEffect(() => {
    if (!focus || !report) return;
    const el = cards.current.get(focus.key);
    el?.scrollIntoView?.({ block: "center", behavior: "smooth" });
    el?.focus?.({ preventScroll: true });
  }, [focus, report]);

  return (
    <section className="surface min-w-0 rounded-2xl p-4 lg:col-span-2" aria-labelledby="leaks-title" data-testid="leak-panel">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="leaks-title" className="eyebrow text-slate-400">
          Strengths and leaks
        </h2>
        {report && (
          <span className="text-[11.5px] text-slate-400" data-testid="leak-sample">
            From {report.decisions} graded decision{report.decisions === 1 ? "" : "s"} in {report.hands} saved hand
            {report.hands === 1 ? "" : "s"}
          </span>
        )}
      </div>

      {state.status === "error" && (
        <p className="mt-2 text-[12.5px] text-rose-300" role="alert">
          {state.message}{" "}
          {onRetry && (
            <button type="button" className="underline" onClick={onRetry}>
              Try again
            </button>
          )}
        </p>
      )}
      {state.status === "loading" && !report && <p className="mt-2 text-[12.5px] text-slate-400">Looking through your hands…</p>}
      {report && <Report report={report} progress={progress} onStart={onStart} onShowHand={onShowHand} focus={focus} cards={cards.current} />}

      <p className="mt-3 text-[11px] leading-snug text-slate-500">
        A leak is reported only after at least {report?.rules.min_opportunities ?? 10} chances at the same kind of decision,{" "}
        {report?.rules.min_flagged ?? 3} confidently flagged ones, and a flagged rate that stays above{" "}
        {pct(report?.rules.min_rate ?? 0.15)} even read pessimistically (90% interval). Grades come from the coach's
        analysis at the moment of each decision: results, the opponent's cards and later streets are never used, so a lost
        pot is not counted as a mistake. Grades that depended on an assumption (the range, equity realisation, a preflop
        guideline) never make a leak on their own.
      </p>
    </section>
  );
}

function Report({
  report,
  progress,
  onStart,
  onShowHand,
  focus,
  cards,
}: {
  report: LeakReport;
  progress: Progress;
  onStart: (plan: Plan) => void;
  onShowHand?: (id: string, decision: number | null) => void;
  focus: { key: string; n: number } | null;
  cards: Map<string, HTMLElement>;
}) {
  const insufficient = report.other.filter((i) => i.status === "insufficient");
  return (
    <div className="mt-2 space-y-3">
      {report.status === "insufficient" && (
        <p className="text-[12.5px] text-slate-300" data-testid="leaks-insufficient">
          Not enough hands to look for recurring leaks yet: {report.needed} more graded decision{report.needed === 1 ? "" : "s"}{" "}
          needed. Until then, each hand's review stands on its own.
        </p>
      )}
      {report.status === "ready" && report.leaks.length === 0 && (
        <p className="text-[12.5px] text-slate-300" data-testid="no-leaks">
          No recurring leak is supported by your hands so far.
        </p>
      )}

      {report.leaks.length > 0 && (
        <ul className="grid grid-cols-1 gap-3 xl:grid-cols-2" data-testid="leaks">
          {report.leaks.map((l) => (
            <li
              key={l.key}
              ref={(el) => {
                if (el) cards.set(l.key, el);
                else cards.delete(l.key);
              }}
              tabIndex={-1}
              data-testid="leak"
              data-key={l.key}
              className={`min-w-0 rounded-xl bg-white/[0.03] p-3 outline-none ring-1 ${
                focus?.key === l.key ? "ring-amber-300/70" : "ring-amber-400/20"
              }`}
            >
              <LeakCard l={l} progress={progress} onStart={onStart} onShowHand={onShowHand} />
            </li>
          ))}
        </ul>
      )}

      {report.strengths.length > 0 && (
        <div data-testid="strengths">
          <h3 className="text-[12.5px] font-semibold text-emerald-200">Relative strengths</h3>
          <ul className="mt-1 space-y-0.5 text-[12px] text-slate-300">
            {report.strengths.map((s) => (
              <li key={s.key}>
                <b className="font-semibold text-slate-200">{strengthTitle(s)}</b>: flagged in {s.flagged + s.uncertain} of{" "}
                {s.judged} confidently graded decisions (likely under {pct(s.high)}).
              </li>
            ))}
          </ul>
        </div>
      )}

      {report.watch.length > 0 && (
        <details className="text-[12px] text-slate-400" data-testid="watch">
          <summary className="cursor-pointer text-slate-300">Too early to tell ({report.watch.length})</summary>
          <ul className="mt-1 space-y-1">
            {report.watch.map((w) => (
              <li key={w.key}>
                <b className="font-semibold text-slate-300">{w.title}</b>: {w.flagged} confidently flagged
                {w.uncertain ? ` and ${w.uncertain} uncertain` : ""} of {w.opportunities}.{" "}
                {w.status === "uncertain" ? "The coach is not sure: " : "Not a leak yet: needs "}
                {w.missing?.join(" and ")}.
              </li>
            ))}
          </ul>
        </details>
      )}

      {insufficient.length > 0 && report.status === "ready" && (
        <p className="text-[11.5px] text-slate-500" data-testid="leaks-no-data">
          Not enough of these decisions yet to say either way: {insufficient.map((i) => i.title.toLowerCase()).join("; ")}.
        </p>
      )}
    </div>
  );
}

function strengthTitle(s: LeakItem): string {
  const t: Record<string, string> = {
    called_without_odds: "Calls facing bets",
    folded_with_odds: "Folds facing bets",
    missed_value: "Playing strong hands",
    overaggressive: "Choosing when to bet or raise",
    bet_sizing: "Bet sizes",
    preflop_loose: "Preflop calls and raises",
    preflop_tight: "Preflop folds",
  };
  return t[s.key] ?? s.title;
}

/** Hand numbers restart with each game, so the time tells hands apart. */
function when(t: number): string {
  const d = new Date(t);
  return `${d.toLocaleDateString(undefined, { month: "short", day: "numeric" })} ${d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}`;
}

function handLabel(h: LeakHand): string {
  const verb = (h.action && ACTION_VERB[h.action]) ?? h.action ?? "acted";
  const numbers =
    h.equity !== null && h.required !== null && h.to_call > 0
      ? `: about ${pct(h.equity)} equity, ${pct(h.required)} needed`
      : h.equity !== null
        ? `: about ${pct(h.equity)} equity`
        : "";
  return `${h.street} ${verb}${numbers}`;
}

function LeakCard({
  l,
  progress,
  onStart,
  onShowHand,
}: {
  l: LeakItem;
  progress: Progress;
  onStart: (plan: Plan) => void;
  onShowHand?: (id: string, decision: number | null) => void;
}) {
  const plan = leakPlan(l, progress);
  const evidence = l.hands.filter((h) => h.kind === "flagged");
  const p = l.practice;
  return (
    <>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 className="text-[14px] font-semibold text-amber-100">{l.title}</h3>
        <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[10.5px] font-semibold text-amber-200 ring-1 ring-amber-400/30">
          {CONCEPT_LABEL[l.concept]}
        </span>
      </div>
      <p className="mt-0.5 text-[12px] text-slate-400">{l.description}</p>

      <p className="mt-1.5 text-[12.5px] text-slate-200" data-testid="leak-evidence">
        <b className="font-semibold">Pattern:</b> {l.flagged} of {l.opportunities} such decisions flagged ({pct(l.rate)}; likely
        between {pct(l.low)} and {pct(l.high)}).
        {l.split && ` ${l.split.calls} were calls without the odds and ${l.split.folds} folds with them.`}
        {l.uncertain > 0 && ` ${l.uncertain} more were flagged with low confidence and are not counted.`}
      </p>
      {l.conditions?.map((c) => (
        <p key={c.key} className="mt-0.5 text-[12px] text-amber-200/90">
          Mostly {c.label}: {c.flagged} of {c.opportunities} ({pct(c.rate)}), versus {pct(c.rate_elsewhere)} in your other{" "}
          {c.opportunities_elsewhere}.
        </p>
      ))}

      <p className="mt-1.5 text-[12.5px] leading-snug text-slate-300">
        <b className="font-semibold text-slate-200">Why it matters:</b> {LEAK_PRINCIPLE[l.key] ?? l.description}
      </p>

      {evidence.length > 0 && (
        <div className="mt-1.5 text-[12px]">
          <span className="text-slate-400">Your hands:</span>
          <ul className="mt-0.5 space-y-0.5" data-testid="leak-hands">
            {evidence.slice(0, 5).map((h) => (
              <li key={`${h.id}-${h.decision}`}>
                <button
                  type="button"
                  disabled={!onShowHand}
                  onClick={() => onShowHand?.(h.id, h.decision)}
                  className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 rounded-md px-1 py-0.5 text-left text-slate-200 hover:bg-white/[0.06]"
                >
                  <span className="tabular-nums text-slate-400" title={new Date(h.t).toLocaleString()}>
                    #{h.hand_number}
                  </span>
                  <span className="hidden text-[11px] tabular-nums text-slate-500 sm:inline">{when(h.t)}</span>
                  <span className="whitespace-nowrap">
                    {h.hero_cards.map((c) => (
                      <MiniCard key={c} code={c} />
                    ))}
                  </span>
                  <span>{handLabel(h)}</span>
                </button>
              </li>
            ))}
          </ul>
          {l.flagged > Math.min(5, evidence.length) && (
            <p className="text-[11px] text-slate-500" data-testid="leak-hands-more">
              The {Math.min(5, evidence.length)} most recent of {l.flagged} flagged decisions.
            </p>
          )}
        </div>
      )}

      <div className="mt-2 rounded-lg bg-white/[0.03] p-2 text-[12px] ring-1 ring-white/5">
        <p className="text-slate-300">
          <b className="font-semibold text-slate-200">Practice:</b> {l.recommendation?.text}
          {l.recommendation?.focus_label ? ` Scenarios are set ${l.recommendation.focus_label}, where this shows up most.` : ""}
        </p>
        <button
          type="button"
          onClick={() => onStart(plan)}
          className="btn-gold mt-1.5 rounded-lg px-3 py-1.5 text-[12px] font-bold"
          data-testid="leak-practice"
        >
          Practise {CONCEPT_LABEL[plan.concept].toLowerCase()} · {LEVEL_LABEL[plan.difficulty]}
        </button>
      </div>

      <div className="mt-2 space-y-0.5 text-[11.5px] text-slate-400" data-testid="leak-progress">
        {p && p.answers > 0 ? (
          <p>
            {p.answers} practice answer{p.answers === 1 ? "" : "s"} aimed at this leak, {pct(p.score)} good or close
            {p.answers > 10 ? ` (last 10: ${pct(p.recent_score)})` : ""}.
          </p>
        ) : (
          <p>No practice aimed at this leak yet.</p>
        )}
        {p?.since && <p>At the table since you started practising it: {changeText(p.since, { before: "before", after: "since" })}</p>}
        {!p?.since && l.trend && (
          <p>Over time: {changeText(l.trend, { before: "in your earlier half", after: "in your recent half" })}</p>
        )}
      </div>
    </>
  );
}
