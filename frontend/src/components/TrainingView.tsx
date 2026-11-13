import { useCallback, useEffect, useRef, useState } from "react";
import { coachApi } from "../lib/api";
import { CONCEPT_LABEL, CONCEPTS, type Progress } from "../lib/progress";
import { conceptSkills, LEVEL_LABEL, nextPractice, type NextPractice } from "../lib/skills";
import type { Concept, Focus, LeakItem, TrainingAnswer, TrainingDifficulty, TrainingScenario } from "../types";
import { DecisionDetail, formatBb, GradeChip } from "./DecisionDetail";
import { formatPercent } from "./EquityPanel";
import { PlayingCard } from "./PlayingCard";
import { measuredText, SkillMeter } from "./SkillMeter";

export const TRAINING_KEY = "pokerlab.training.v1";
type DifficultyChoice = TrainingDifficulty | "auto";
const DIFFICULTIES: { id: DifficultyChoice; label: string; hint: string }[] = [
  {
    id: "auto",
    label: "Auto",
    hint: "Picks the level per concept from your estimated skill: it moves up only when the evidence is solid, and back down after misses.",
  },
  { id: "beginner", label: "Beginner", hint: "Clear-cut spots, two or three choices, pot-odds hints." },
  { id: "intermediate", label: "Intermediate", hint: "Closer spots, draws, raises and bet sizes." },
  { id: "advanced", label: "Advanced", hint: "Thin margins and opponents with tendencies." },
];

type ConceptChoice = Concept | "recommended";

/** What to deal next: a concept at a level, and why. */
export interface Plan {
  concept: Concept;
  difficulty: TrainingDifficulty;
  reason: string | null;
  review: boolean;
  /** v0.4.4: fix the situation to match a leak (never the answer). */
  focus?: Focus | null;
  /** v0.4.4: the leak this practice is aimed at; recorded with the answer. */
  leak?: string | null;
}

function loadPrefs(): { difficulty: DifficultyChoice; concept: ConceptChoice; detailed: boolean } {
  const fallback = { difficulty: "auto" as DifficultyChoice, concept: "recommended" as ConceptChoice, detailed: false };
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

/** Resolve the controls into a concrete concept and level, using the learning model. */
export function planFor(
  progress: Progress,
  difficulty: DifficultyChoice,
  choice: ConceptChoice,
  rand = Math.random(),
  leaks?: LeakItem[],
): Plan {
  if (choice === "recommended") {
    const rec: NextPractice = nextPractice(progress, Date.now(), rand, leaks);
    return {
      concept: rec.concept,
      // A due review is practised at its own level: only an answer at that level clears it.
      difficulty: difficulty === "auto" || rec.kind === "review" ? rec.difficulty : difficulty,
      reason: rec.reason,
      review: rec.kind === "review",
      ...(rec.leak ? { leak: rec.leak, focus: rec.focus ?? null } : {}),
    };
  }
  const level = conceptSkills(progress).find((s) => s.concept === choice)!.level;
  return { concept: choice, difficulty: difficulty === "auto" ? level : difficulty, reason: null, review: false };
}

interface Props {
  progress: Progress;
  onAnswered: (scenario: TrainingScenario, answer: TrainingAnswer, plan: Plan | null) => void;
  /** A scenario to start with (for example, a decision from a real hand). */
  initial?: TrainingScenario | null;
  /** A plan to start with (the dashboard's "Start" button). */
  startPlan?: Plan | null;
  /** Leaks from the leak report, for "Recommended for you" (v0.4.4). */
  leaks?: LeakItem[];
}

/**
 * Interactive training: a decision point, a short list of legal choices, and immediate
 * feedback comparing every choice. Scenarios target your weakest concepts unless you pick
 * one. Retries are allowed, but only the first answer counts towards progress.
 */
export function TrainingView({ progress, onAnswered, initial = null, startPlan = null, leaks }: Props) {
  const [prefs, setPrefs] = useState(loadPrefs);
  const [scenario, setScenario] = useState<TrainingScenario | null>(initial);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [answer, setAnswer] = useState<TrainingAnswer | null>(null);
  const [attempts, setAttempts] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const progressRef = useRef(progress);
  progressRef.current = progress;
  const leaksRef = useRef(leaks);
  leaksRef.current = leaks;
  const feedbackRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try {
      localStorage.setItem(TRAINING_KEY, JSON.stringify(prefs));
    } catch {
      /* not persisted */
    }
  }, [prefs]);

  // The next scenario is fetched while the player reads the feedback, so "Next" is quick.
  const prefetched = useRef<{ key: string; plan: Plan; promise: Promise<TrainingScenario> } | null>(null);
  const request = (plan: Plan, key: string) => {
    const promise = coachApi.scenario(plan.difficulty, plan.concept, randomSeed(), plan.focus);
    promise.catch(() => {}); // a failed prefetch is retried when it is needed
    return { key, plan, promise };
  };

  // Only the most recent load may change the screen: an older one that finishes late
  // (controls changed quickly, or effects run twice in development) must not replace the
  // scenario the player is answering, or wipe their feedback.
  const loadSeq = useRef(0);
  const scenarioRef = useRef(scenario);
  scenarioRef.current = scenario;
  // A leak practice session keeps its plan for every "Next" until the controls change.
  const sticky = useRef<Plan | null>(null);
  const keyOf = (difficulty: DifficultyChoice, choice: ConceptChoice, fixed?: Plan) =>
    fixed?.leak ? `leak|${fixed.leak}|${fixed.concept}|${fixed.difficulty}|${fixed.focus ?? ""}` : `${difficulty}|${choice}`;
  const load = useCallback(async (difficulty: DifficultyChoice, choice: ConceptChoice, fixed?: Plan) => {
    const seq = ++loadSeq.current;
    sticky.current = fixed?.leak ? fixed : null;
    const key = keyOf(difficulty, choice, fixed);
    let next = (!fixed || fixed.leak) && prefetched.current?.key === key ? prefetched.current : null;
    prefetched.current = null;
    setBusy(true);
    setError(null);
    try {
      let s: TrainingScenario;
      try {
        s = await (next ?? (next = request(fixed ?? planFor(progressRef.current, difficulty, choice, Math.random(), leaksRef.current), key))).promise;
      } catch {
        next = request(fixed ?? planFor(progressRef.current, difficulty, choice, Math.random(), leaksRef.current), key);
        s = await next.promise;
      }
      if (seq !== loadSeq.current) return;
      setScenario(s);
      setPlan(next.plan);
      setAnswer(null);
      setAttempts(0);
    } catch (e) {
      if (seq === loadSeq.current) setError(e instanceof Error ? e.message : "Could not load a scenario");
    } finally {
      if (seq === loadSeq.current) setBusy(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Prefetch once the first answer is in, so the plan uses the updated progress (a miss
  // may have scheduled a review, a streak may have moved the level).
  useEffect(() => {
    if (attempts !== 1 || prefetched.current) return;
    const fixed = sticky.current ?? undefined;
    const key = keyOf(prefs.difficulty, prefs.concept, fixed);
    prefetched.current = request(fixed ?? planFor(progress, prefs.difficulty, prefs.concept, Math.random(), leaksRef.current), key);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempts, progress]);

  // The dashboard's recommendation replaces the current scenario.
  useEffect(() => {
    if (startPlan) void load(prefs.difficulty, prefs.concept, startPlan);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startPlan]);

  // A practice spot replaces the current scenario.
  useEffect(() => {
    if (initial) {
      sticky.current = null;
      setScenario(initial);
      setPlan(null);
      setAnswer(null);
      setAttempts(0);
    }
  }, [initial]);

  useEffect(() => {
    if (!initial && !startPlan) void load(prefs.difficulty, prefs.concept);
    // Load once on mount; later loads come from the controls.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const choose = async (key: string) => {
    if (!scenario || busy) return;
    setBusy(true);
    setError(null);
    try {
      const a = await coachApi.answer(scenario.id, key);
      if (attempts === 0) onAnswered(scenario, a, plan); // a real first answer: always recorded
      if (scenarioRef.current?.id !== scenario.id) return; // replaced meanwhile: don't show it
      setAnswer(a);
      setAttempts((n) => n + 1);
      requestAnimationFrame(() => feedbackRef.current?.focus());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not check your answer");
    } finally {
      setBusy(false);
    }
  };

  const skills = conceptSkills(progress);

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
            plan={plan}
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
                onClick={() => void load(prefs.difficulty, prefs.concept, sticky.current ?? undefined)}
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
            <p className="mt-2 text-[11px] leading-snug text-slate-400">
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
        <ul className="mt-2 space-y-3">
          {skills.map((sk) => (
            <li key={sk.concept} data-testid={`skill-${sk.concept}`}>
              <div className="flex items-baseline justify-between text-[12.5px]">
                <span className="font-medium text-slate-200">{sk.label}</span>
                <span className="tabular-nums text-slate-400">{measuredText(sk)}</span>
              </div>
              <SkillMeter skill={sk} />
            </li>
          ))}
        </ul>
        <p className="mt-3 text-[11px] leading-snug text-slate-400">
          Counts are your first answers (good, or close for half credit). The estimate is a model of those answers,
          weighted by difficulty, with an 80% range: few answers mean a wide range. It measures agreement with
          PokerLab's analysis, not winnings.
        </p>
      </aside>
    </div>
  );
}

function ScenarioCard({
  scenario,
  plan,
  answer,
  busy,
  onChoose,
}: {
  scenario: TrainingScenario;
  plan: Plan | null;
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
      {plan && (
        <p className="mt-1 flex flex-wrap items-center gap-x-2 text-[11.5px] text-amber-200/80" data-testid="recommendation">
          {plan.review && (
            <span className="rounded-full bg-sky-500/15 px-2 py-0.5 text-[10.5px] font-semibold text-sky-200 ring-1 ring-sky-400/30">
              Review
            </span>
          )}
          <span>{LEVEL_LABEL[plan.difficulty]}</span>
          {plan.reason && <span>· {plan.reason}</span>}
        </p>
      )}
      {plan?.leak && (
        <p className="mt-1 text-[11.5px] text-slate-400" data-testid="leak-practice-note">
          {s.focus_label ? `This scenario is set ${s.focus_label}, like the spots where your leak shows up. ` : ""}
          The situation is chosen to match your leak, not the answer: some of these are calls, some folds or bets. Your
          first answers here are tracked against this leak on the Progress tab.
        </p>
      )}

      <div className="felt mt-3 flex flex-col items-center gap-3 rounded-2xl px-3 py-4" data-testid="scenario-table">
        <div className="text-[12px] text-emerald-50">
          Opponent · stack {s.opponent_stack.toLocaleString("en-US")}
        </div>
        <div className="flex min-h-[calc(var(--card-w)*1.4)] gap-1.5" role="group" aria-label="Board">
          {s.board.length ? (
            s.board.map((c) => <PlayingCard key={c} code={c} />)
          ) : (
            <span className="self-center text-[12px] text-emerald-50">Preflop: no board yet</span>
          )}
        </div>
        <div className="rounded-full bg-black/30 px-3 py-1 text-[13px] font-semibold tabular-nums text-amber-100">
          Pot {s.pot.toLocaleString("en-US")}
          {s.to_call > 0 && <span className="text-amber-200/80"> · to call {s.to_call.toLocaleString("en-US")}</span>}
        </div>
        <div className="flex gap-1.5" role="group" aria-label="Your cards">
          {s.hero.map((c) => (
            <PlayingCard key={c} code={c} />
          ))}
        </div>
        <div className="text-[12px] text-emerald-50">
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
