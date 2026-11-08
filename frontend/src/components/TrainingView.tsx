import { useCallback, useEffect, useRef, useState } from "react";
import { coachApi } from "../lib/api";
import {
  CONCEPT_LABEL,
  CONCEPTS,
  conceptStats,
  recommendConcept,
  type Progress,
  type Recommendation,
} from "../lib/progress";
import type { Concept, TrainingAnswer, TrainingDifficulty, TrainingScenario } from "../types";
import { DecisionDetail, formatBb, GradeChip } from "./DecisionDetail";
import { formatPercent } from "./EquityPanel";
import { PlayingCard } from "./PlayingCard";

export const TRAINING_KEY = "pokerlab.training.v1";
const DIFFICULTIES: { id: TrainingDifficulty; label: string; hint: string }[] = [
  { id: "beginner", label: "Beginner", hint: "Clear-cut spots, two or three choices, pot-odds hints." },
  { id: "intermediate", label: "Intermediate", hint: "Closer spots, draws, raises and bet sizes." },
  { id: "advanced", label: "Advanced", hint: "Thin margins and opponents with tendencies." },
];

type ConceptChoice = Concept | "recommended";

function loadPrefs(): { difficulty: TrainingDifficulty; concept: ConceptChoice; detailed: boolean } {
  const fallback = { difficulty: "beginner" as TrainingDifficulty, concept: "recommended" as ConceptChoice, detailed: false };
  try {
    const raw = JSON.parse(localStorage.getItem(TRAINING_KEY) ?? "null");
    if (!raw || typeof raw !== "object") return fallback;
    return {
      difficulty: DIFFICULTIES.some((d) => d.id === raw.difficulty) ? raw.difficulty : fallback.difficulty,
      concept: raw.concept === "recommended" || CONCEPTS.includes(raw.concept) ? raw.concept : fallback.concept,
      detailed: typeof raw.detailed === "boolean" ? raw.detailed : false,
    };
  } catch {
    return fallback;
  }
}

function randomSeed(): number {
  return Math.floor(Math.random() * 1_000_000_000);
}

interface Props {
  progress: Progress;
  onAnswered: (scenario: TrainingScenario, answer: TrainingAnswer) => void;
  /** A scenario to start with (for example, a decision from a real hand). */
  initial?: TrainingScenario | null;
}

/**
 * Interactive training: a decision point, a short list of legal choices, and immediate
 * feedback comparing every choice. Scenarios target your weakest concepts unless you pick
 * one. Retries are allowed, but only the first answer counts towards progress.
 */
export function TrainingView({ progress, onAnswered, initial = null }: Props) {
  const [prefs, setPrefs] = useState(loadPrefs);
  const [scenario, setScenario] = useState<TrainingScenario | null>(initial);
  const [recommendation, setRecommendation] = useState<Recommendation | null>(null);
  const [answer, setAnswer] = useState<TrainingAnswer | null>(null);
  const [attempts, setAttempts] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const progressRef = useRef(progress);
  progressRef.current = progress;
  const feedbackRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try {
      localStorage.setItem(TRAINING_KEY, JSON.stringify(prefs));
    } catch {
      /* not persisted */
    }
  }, [prefs]);

  // The next scenario is fetched while the player reads the feedback, so "Next" is quick.
  const prefetched = useRef<{ key: string; rec: Recommendation | null; promise: Promise<TrainingScenario> } | null>(null);
  const request = (difficulty: TrainingDifficulty, choice: ConceptChoice) => {
    const rec = choice === "recommended" ? recommendConcept(progressRef.current) : null;
    const concept = rec?.concept ?? (choice as Concept);
    const promise = coachApi.scenario(difficulty, concept, randomSeed());
    promise.catch(() => {}); // a failed prefetch is retried when it is needed
    return { key: `${difficulty}|${choice}`, rec, promise };
  };
  const prefetch = (difficulty: TrainingDifficulty, choice: ConceptChoice) => {
    prefetched.current = request(difficulty, choice);
  };

  const load = useCallback(async (difficulty: TrainingDifficulty, choice: ConceptChoice) => {
    const key = `${difficulty}|${choice}`;
    let next = prefetched.current?.key === key ? prefetched.current : null;
    prefetched.current = null;
    setBusy(true);
    setError(null);
    try {
      let s: TrainingScenario;
      try {
        s = await (next ?? (next = request(difficulty, choice))).promise;
      } catch {
        next = request(difficulty, choice);
        s = await next.promise;
      }
      const rec = next.rec;
      setScenario(s);
      setRecommendation(rec);
      setAnswer(null);
      setAttempts(0);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load a scenario");
    } finally {
      setBusy(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A practice spot replaces the current scenario.
  useEffect(() => {
    if (initial) {
      setScenario(initial);
      setRecommendation(null);
      setAnswer(null);
      setAttempts(0);
    }
  }, [initial]);

  useEffect(() => {
    if (!initial) void load(prefs.difficulty, prefs.concept);
    // Load once on mount; later loads come from the controls.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const choose = async (key: string) => {
    if (!scenario || busy) return;
    setBusy(true);
    setError(null);
    try {
      const a = await coachApi.answer(scenario.id, key);
      setAnswer(a);
      if (attempts === 0) {
        onAnswered(scenario, a);
        prefetch(prefs.difficulty, prefs.concept);
      }
      setAttempts((n) => n + 1);
      requestAnimationFrame(() => feedbackRef.current?.focus());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not check your answer");
    } finally {
      setBusy(false);
    }
  };

  const stats = conceptStats(progress);

  return (
    <div className="flex w-full flex-col gap-5 lg:flex-row lg:items-start" data-testid="training-view">
      <div className="flex min-w-0 flex-1 flex-col gap-4">
        {/* Controls */}
        <div className="surface flex flex-col gap-3 rounded-2xl p-4">
          <div className="flex flex-wrap items-center gap-2" role="radiogroup" aria-label="Training difficulty">
            {DIFFICULTIES.map((d) => (
              <button
                key={d.id}
                type="button"
                role="radio"
                aria-checked={prefs.difficulty === d.id}
                onClick={() => {
                  setPrefs((p) => ({ ...p, difficulty: d.id }));
                  void load(d.id, prefs.concept);
                }}
                className={`rounded-lg px-3 py-1.5 text-[13px] font-semibold ${
                  prefs.difficulty === d.id ? "btn-gold" : "btn-dark"
                }`}
              >
                {d.label}
              </button>
            ))}
            <label className="ml-auto flex items-center gap-2 text-[12.5px] text-slate-300">
              Practise
              <select
                value={prefs.concept}
                onChange={(e) => {
                  const concept = e.target.value as ConceptChoice;
                  setPrefs((p) => ({ ...p, concept }));
                  void load(prefs.difficulty, concept);
                }}
                className="rounded-lg bg-slate-800 px-2 py-1.5 text-[13px] text-slate-100 ring-1 ring-white/10"
              >
                <option value="recommended">Recommended for you</option>
                {CONCEPTS.map((c) => (
                  <option key={c} value={c}>
                    {CONCEPT_LABEL[c]}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <p className="text-[12px] text-slate-400">{DIFFICULTIES.find((d) => d.id === prefs.difficulty)?.hint}</p>
        </div>

        {error && (
          <div role="alert" className="rounded-xl bg-rose-600/20 px-4 py-2 text-[13px] text-rose-200 ring-1 ring-rose-400/30">
            {error}{" "}
            <button type="button" className="underline" onClick={() => void load(prefs.difficulty, prefs.concept)}>
              Try another
            </button>
          </div>
        )}

        {!scenario && busy && <div className="surface rounded-2xl p-6 text-slate-400">Dealing a scenario…</div>}

        {scenario && busy && !answer && (
          <p className="text-[12.5px] text-slate-400" role="status" data-testid="dealing">
            Dealing a new scenario…
          </p>
        )}
        {scenario && (
          <ScenarioCard
            scenario={scenario}
            recommendation={recommendation}
            answer={answer}
            busy={busy}
            onChoose={(k) => void choose(k)}
          />
        )}

        {answer && scenario && (
          <div
            ref={feedbackRef}
            tabIndex={-1}
            className="surface rounded-2xl p-4 outline-none"
            data-testid="training-feedback"
            aria-live="polite"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={`text-[15px] font-bold ${
                  answer.correct ? "text-emerald-300" : answer.acceptable ? "text-sky-300" : "text-rose-300"
                }`}
              >
                {answer.correct ? "Good decision" : answer.acceptable ? "Close" : "Not the best choice"}
              </span>
              <GradeChip grade={answer.analysis.grade} label={answer.analysis.grade_label} />
              {attempts > 1 && <span className="text-[11.5px] text-slate-400">Retry: not counted in your progress</span>}
            </div>
            <div className="mt-2">
              <DecisionDetail review={answer.analysis} detailed={prefs.detailed} />
            </div>
            <h3 className="mt-3 text-[11px] font-semibold uppercase tracking-wide text-slate-400">Compare the alternatives</h3>
            <ul className="mt-1 space-y-1.5" data-testid="alternatives">
              {answer.alternatives.map((alt) => (
                <li key={alt.key} className="rounded-lg bg-white/[0.03] px-3 py-2 ring-1 ring-white/5">
                  <div className="flex items-center gap-2">
                    <span className="flex-1 text-[13px] font-semibold text-slate-100">
                      {alt.label}
                      {alt.key === answer.choice && (
                        <span className="ml-1.5 text-[11px] font-semibold text-amber-300">your choice</span>
                      )}
                    </span>
                    <span className="tabular-nums text-[12.5px] text-slate-300">{formatBb(alt.ev, scenario.big_blind)}</span>
                    <GradeChip grade={alt.grade} label={alt.grade_label} />
                  </div>
                  <p className="mt-1 text-[12px] leading-snug text-slate-400">{alt.explanation}</p>
                </li>
              ))}
            </ul>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <button type="button" onClick={() => setAnswer(null)} className="btn-dark rounded-lg px-3 py-2 text-[13px] font-semibold">
                Try again
              </button>
              <button
                type="button"
                onClick={() => void load(prefs.difficulty, prefs.concept)}
                disabled={busy}
                className="btn-gold rounded-lg px-4 py-2 text-[13px] font-bold"
              >
                {busy ? "Dealing…" : "Next scenario"}
              </button>
              <label className="ml-auto flex items-center gap-1.5 text-[12px] text-slate-400">
                <input
                  type="checkbox"
                  checked={prefs.detailed}
                  onChange={() => setPrefs((p) => ({ ...p, detailed: !p.detailed }))}
                  className="h-3.5 w-3.5 accent-amber-300"
                />
                Show the maths
              </label>
            </div>
            <p className="mt-2 text-[11px] leading-snug text-slate-500">
              Estimates come from PokerLab's range model and one-street expected values. They are a guide to good
              reasoning, not solver-proven optimal play.
            </p>
          </div>
        )}
      </div>

      {/* Progress by concept */}
      <aside className="surface w-full rounded-2xl p-4 lg:w-80 lg:shrink-0" aria-labelledby="skills-title" data-testid="skills-panel">
        <h2 id="skills-title" className="eyebrow text-slate-400">
          Skills
        </h2>
        <ul className="mt-2 space-y-2.5">
          {stats.map((s) => (
            <li key={s.concept} data-testid={`skill-${s.concept}`}>
              <div className="flex items-baseline justify-between text-[12.5px]">
                <span className="font-medium text-slate-200">{CONCEPT_LABEL[s.concept]}</span>
                <span className="tabular-nums text-slate-400">
                  {s.accuracy === null ? "not tried" : `${Math.round(s.accuracy * 100)}% · ${s.attempts} tried`}
                </span>
              </div>
              <div
                className="mt-1 h-1.5 overflow-hidden rounded-full bg-white/10"
                role="meter"
                aria-label={`${CONCEPT_LABEL[s.concept]} accuracy`}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={s.accuracy === null ? 0 : Math.round(s.accuracy * 100)}
              >
                <div className="h-full rounded-full bg-amber-300/80" style={{ width: `${(s.accuracy ?? 0) * 100}%` }} />
              </div>
              {(s.recent !== null && s.attempts >= 10) || s.flagged > 0 ? (
                <div className="mt-0.5 text-[11px] text-slate-400">
                  {s.attempts >= 10 && s.recent !== null && <>Last 10: {Math.round(s.recent * 100)}%. </>}
                  {s.flagged > 0 && <>At the table: {s.flagged} flagged of {s.played}.</>}
                </div>
              ) : null}
            </li>
          ))}
        </ul>
        <p className="mt-3 text-[11px] leading-snug text-slate-500">
          Accuracy counts your first answer to each scenario: a good choice scores 1, a close one ½.
        </p>
      </aside>
    </div>
  );
}

function ScenarioCard({
  scenario,
  recommendation,
  answer,
  busy,
  onChoose,
}: {
  scenario: TrainingScenario;
  recommendation: Recommendation | null;
  answer: TrainingAnswer | null;
  busy: boolean;
  onChoose: (key: string) => void;
}) {
  const s = scenario;
  return (
    <div className={`surface rounded-2xl p-4 transition-opacity ${busy && !answer ? "opacity-50" : ""}`} data-testid="scenario" data-id={s.id}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="font-display text-[18px] font-bold text-amber-50">{s.concept_label}</h2>
        <span className="text-[12px] text-slate-400">{s.concept_blurb}</span>
      </div>
      {recommendation && (
        <p className="mt-1 text-[11.5px] text-amber-200/80" data-testid="recommendation">
          Recommended: {recommendation.reason}
        </p>
      )}

      <div className="felt mt-3 flex flex-col items-center gap-3 rounded-2xl px-3 py-4" data-testid="scenario-table">
        <div className="text-[12px] text-emerald-50/80">
          Opponent · stack {s.opponent_stack.toLocaleString("en-US")}
        </div>
        <div className="flex min-h-[calc(var(--card-w)*1.4)] gap-1.5" aria-label="Board">
          {s.board.length ? (
            s.board.map((c) => <PlayingCard key={c} code={c} />)
          ) : (
            <span className="self-center text-[12px] text-emerald-50/70">Preflop: no board yet</span>
          )}
        </div>
        <div className="rounded-full bg-black/30 px-3 py-1 text-[13px] font-semibold tabular-nums text-amber-100">
          Pot {s.pot.toLocaleString("en-US")}
          {s.to_call > 0 && <span className="text-amber-200/80"> · to call {s.to_call.toLocaleString("en-US")}</span>}
        </div>
        <div className="flex gap-1.5" aria-label="Your cards">
          {s.hero.map((c) => (
            <PlayingCard key={c} code={c} />
          ))}
        </div>
        <div className="text-[12px] text-emerald-50/80">
          You ({s.position}) · {s.hand} · stack {s.stack.toLocaleString("en-US")}
        </div>
      </div>

      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <div>
          <h3 className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">The hand so far</h3>
          <ol className="mt-1 space-y-0.5 text-[12.5px] text-slate-300" data-testid="scenario-history">
            <li>Blinds {s.big_blind / 2}/{s.big_blind}</li>
            {s.history.map((h, i) => (
              <li key={i}>{h}</li>
            ))}
          </ol>
        </div>
        {s.opponent && (
          <div data-testid="scenario-opponent">
            <h3 className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">
              Opponent: {s.opponent.name}
            </h3>
            <table className="mt-1 w-full text-[12px]">
              <tbody>
                {s.opponent.stats.map((st) => (
                  <tr key={st.key} className="border-t border-white/5">
                    <th scope="row" className="py-0.5 text-left font-normal text-slate-300">
                      {st.label}
                    </th>
                    <td className="py-0.5 text-right tabular-nums text-slate-100">
                      {st.rate === null ? "—" : formatPercent(st.rate)}
                    </td>
                    <td className="py-0.5 pl-2 text-right text-[11px] tabular-nums text-slate-400">
                      typical {formatPercent(st.baseline)} · {st.opportunities} spots
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {s.hints.length > 0 && !answer && (
        <ul className="mt-3 space-y-1 rounded-xl bg-sky-500/10 px-3 py-2 text-[12.5px] text-sky-100 ring-1 ring-sky-400/20" data-testid="hints">
          {s.hints.map((h) => (
            <li key={h}>{h}</li>
          ))}
        </ul>
      )}

      <div className="mt-4 flex flex-wrap gap-2" role="group" aria-label="Your decision">
        {s.choices.map((c) => {
          const picked = answer?.choice === c.key;
          return (
            <button
              key={c.key}
              type="button"
              disabled={busy || answer !== null}
              onClick={() => onChoose(c.key)}
              aria-pressed={picked}
              className={`min-h-11 rounded-xl px-4 py-2 text-[14px] font-bold disabled:opacity-60 ${
                picked ? "btn-gold" : "btn-dark"
              }`}
            >
              {c.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
