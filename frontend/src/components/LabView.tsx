import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { count, signed } from "../lib/analytics";
import { ApiError, labApi } from "../lib/api";
import { cliCommand, configLine, configTitle, duration, FAMILY_LABEL, HAND_PRESETS, newSeed, rate, sessionCount } from "../lib/lab";
import type { LabConfig, LabEstimate, LabInfo, LabJob, LabJobSummary, LabStrategy } from "../types";
import { TrajectoryChart } from "./LabCharts";
import { MatchupDetail, RoundRobin } from "./LabResults";

type Api = Pick<typeof labApi, "info" | "estimate" | "submit" | "get" | "list" | "cancel" | "dealsUrl">;

const FORM_KEY = "pokerlab.lab.form";
const JOB_KEY = "pokerlab.lab.job";

function readStored<T>(key: string): T | null {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

function store(key: string, value: unknown) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage unavailable: a convenience only */
  }
}

function errorText(e: unknown): string {
  return e instanceof ApiError ? e.message : "Something went wrong talking to the server.";
}

const ACTIVE = (s: string | undefined) => s === "queued" || s === "running";

interface Props {
  api?: Api;
  pollMs?: number;
}

/**
 * The AI Simulation Lab (v0.5.0): pick strategies, run thousands of hands between them on
 * the server, and read the result with its uncertainty.
 */
export function LabView({ api = labApi, pollMs = 1000 }: Props) {
  const [info, setInfo] = useState<LabInfo | null>(null);
  const [infoError, setInfoError] = useState<string | null>(null);
  const [form, setForm] = useState<LabConfig | null>(null);
  const [estimate, setEstimate] = useState<LabEstimate | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(() => readStored<string>(JOB_KEY));
  const [job, setJob] = useState<LabJob | null>(null);
  const [jobError, setJobError] = useState<string | null>(null);
  const [recent, setRecent] = useState<LabJobSummary[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [pair, setPair] = useState<[string, string] | null>(null);

  const loadInfo = useCallback(async () => {
    setInfoError(null);
    try {
      const i = await api.info();
      setInfo(i);
      const saved = readStored<LabConfig>(FORM_KEY);
      const known = new Set(i.strategies.map((s) => s.id));
      setForm(
        saved && Array.isArray(saved.strategies) && saved.strategies.every((s) => known.has(s))
          ? { ...i.defaults, ...saved }
          : { ...i.defaults, seed: newSeed() },
      );
    } catch (e) {
      setInfoError(errorText(e));
    }
  }, [api]);

  const loadRecent = useCallback(async () => {
    try {
      setRecent((await api.list()).experiments);
    } catch {
      /* the list is optional */
    }
  }, [api]);

  useEffect(() => {
    void loadInfo();
    void loadRecent();
  }, [loadInfo, loadRecent]);

  useEffect(() => {
    if (form) store(FORM_KEY, form);
  }, [form]);
  useEffect(() => store(JOB_KEY, jobId), [jobId]);

  // The run's configuration: one strategy selected means a mirror match.
  const config = useMemo<LabConfig | null>(() => {
    if (!form) return null;
    const s = form.strategies.length === 1 ? [form.strategies[0], form.strategies[0]] : form.strategies;
    return { ...form, strategies: s };
  }, [form]);
  const valid = !!config && form!.strategies.length >= 1;

  // Validate and estimate on the server (debounced).
  useEffect(() => {
    if (!config || !valid) {
      setEstimate(null);
      return;
    }
    let live = true;
    const t = setTimeout(async () => {
      try {
        const est = await api.estimate(config);
        if (live) {
          setEstimate(est);
          setFormError(null);
        }
      } catch (e) {
        if (live) {
          setEstimate(null);
          setFormError(errorText(e));
        }
      }
    }, 250);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [api, config, valid]);

  // Poll the experiment being viewed while it runs.
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (!jobId) {
      setJob(null);
      return;
    }
    let live = true;
    const tick = async () => {
      try {
        const j = await api.get(jobId);
        if (!live) return;
        setJob(j);
        setJobError(null);
        if (ACTIVE(j.status)) timer.current = setTimeout(tick, pollMs);
        else void loadRecent();
      } catch (e) {
        if (!live) return;
        if (e instanceof ApiError && e.status === 404) {
          // The server restarted (experiments live in memory).
          setJobId(null);
          setJob(null);
          return;
        }
        setJobError(errorText(e));
        timer.current = setTimeout(tick, pollMs * 3);
      }
    };
    void tick();
    return () => {
      live = false;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [api, jobId, pollMs, loadRecent]);

  const run = async (c: LabConfig | null = config) => {
    if (!c) return;
    setSubmitting(true);
    setJobError(null);
    try {
      const j = await api.submit(c);
      setPair(null);
      setJob(j);
      setJobId(j.id);
      void loadRecent();
    } catch (e) {
      setFormError(errorText(e));
    } finally {
      setSubmitting(false);
    }
  };

  const cancel = async () => {
    if (!job) return;
    try {
      setJob(await api.cancel(job.id));
    } catch (e) {
      setJobError(errorText(e));
    }
  };

  if (infoError) {
    return (
      <section role="alert" className="surface flex flex-wrap items-center gap-3 rounded-2xl p-5 text-sm text-orange-100">
        <span className="flex-1">The Simulation Lab needs the PokerLab server. {infoError}</span>
        <button type="button" className="btn-dark rounded-xl px-3 py-1.5 text-xs font-semibold" onClick={() => void loadInfo()}>
          Retry
        </button>
      </section>
    );
  }
  if (!info || !form) return <div className="surface rounded-2xl p-6 text-slate-400">Loading the lab…</div>;

  return (
    <div className="flex w-full flex-col gap-4" data-testid="lab">
      <Setup
        info={info}
        form={form}
        onChange={(f) => setForm(f)}
        estimate={estimate}
        error={formError}
        busy={submitting || ACTIVE(job?.status)}
        onRun={() => void run()}
      />
      {jobError && (
        <div role="alert" className="surface rounded-2xl p-4 text-sm text-orange-100">
          {jobError}
        </div>
      )}
      {job && (
        <Experiment
          job={job}
          pair={pair}
          onPair={setPair}
          onCancel={() => void cancel()}
          onRerun={(seed) => {
            const c = { ...job.config, seed: seed ?? job.config.seed };
            setForm({ ...c, strategies: c.strategies[0] === c.strategies[1] && c.strategies.length === 2 ? [c.strategies[0]] : c.strategies });
            void run(c);
          }}
          csvUrl={api.dealsUrl(job.id)}
        />
      )}
      {recent.length > 0 && (
        <Recent recent={recent} current={job?.id ?? null} onOpen={(id) => {
          setPair(null);
          setJobId(id);
        }} />
      )}
    </div>
  );
}

// ------------------------------------------------------------------ setup

function Setup({
  info,
  form,
  onChange,
  estimate,
  error,
  busy,
  onRun,
}: {
  info: LabInfo;
  form: LabConfig;
  onChange: (f: LabConfig) => void;
  estimate: LabEstimate | null;
  error: string | null;
  busy: boolean;
  onRun: () => void;
}) {
  const families = (["opponent", "reference", "style"] as const).map((f) => ({
    family: f,
    items: info.strategies.filter((s) => s.family === f),
  }));
  const byId = new Map(info.strategies.map((s) => [s.id, s]));
  const sel = form.strategies;
  const max = info.limits.max_strategies;
  const toggle = (id: string) => {
    if (sel.includes(id)) onChange({ ...form, strategies: sel.filter((s) => s !== id) });
    else if (sel.length < max) onChange({ ...form, strategies: [...sel, id] });
  };
  const kind = sel.length === 0 ? null : sel.length === 1 ? "mirror" : sel.length === 2 ? "h2h" : "rr";
  const slowest = sel.length ? Math.max(...sel.map((s) => byId.get(s)?.cost_deep_ms ?? 0)) : 0;
  const custom = !HAND_PRESETS.includes(form.hands as (typeof HAND_PRESETS)[number]);
  const adaptive = sel.some((s) => byId.get(s)?.adaptive);
  const sessions = sessionCount(form);

  return (
    <section className="surface rounded-2xl p-4" aria-labelledby="lab-setup-title" data-testid="lab-setup">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 id="lab-setup-title" className="font-display text-[19px] font-bold text-amber-50">
          Simulation Lab
        </h2>
        <p className="text-xs text-slate-400">
          Pit PokerLab's AI strategies against each other over thousands of hands and see which is better, and how sure that is.
        </p>
      </div>

      <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-3">
        {families.map(({ family, items }) => (
          <fieldset key={family} className="min-w-0">
            <legend className="eyebrow mb-1.5 text-slate-500">{FAMILY_LABEL[family]}</legend>
            <div className="flex flex-col gap-1.5">
              {items.map((s) => (
                <StrategyChip key={s.id} s={s} index={sel.indexOf(s.id)} disabled={!sel.includes(s.id) && sel.length >= max} onToggle={() => toggle(s.id)} />
              ))}
            </div>
          </fieldset>
        ))}
      </div>

      <div className="mt-4 flex flex-wrap items-end gap-x-5 gap-y-3 text-xs">
        <div>
          <div className="mb-1 text-slate-400" id="hands-label">
            Hands{kind === "rr" ? " per matchup" : ""}
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <div className="flex rounded-xl bg-black/20 p-0.5" role="group" aria-labelledby="hands-label">
              {HAND_PRESETS.map((h) => (
                <button key={h} type="button" aria-pressed={form.hands === h} onClick={() => onChange({ ...form, hands: h })}
                  className={`rounded-[10px] px-2.5 py-1.5 font-semibold ${form.hands === h ? "bg-amber-300 text-slate-950" : "text-slate-300 hover:text-white"}`}>
                  {count(h)}
                </button>
              ))}
            </div>
            <input
              type="number"
              aria-label="Custom number of hands"
              className={`w-24 rounded-lg bg-slate-900 px-2 py-1.5 text-slate-100 ring-1 ${custom ? "ring-amber-300/60" : "ring-white/10"}`}
              min={info.limits.min_hands}
              max={info.limits.max_hands}
              step={100}
              value={form.hands}
              onChange={(e) => onChange({ ...form, hands: Math.max(0, Math.floor(Number(e.target.value) || 0)) })}
            />
          </div>
        </div>
        <div>
          <div className="mb-1 text-slate-400" id="mode-label">Dealing</div>
          <div className="flex rounded-xl bg-black/20 p-0.5" role="group" aria-labelledby="mode-label">
            {(["duplicate", "standard"] as const).map((m) => (
              <button key={m} type="button" aria-pressed={form.mode === m} onClick={() => onChange({ ...form, mode: m })}
                className={`rounded-[10px] px-2.5 py-1.5 font-semibold ${form.mode === m ? "bg-amber-300 text-slate-950" : "text-slate-300 hover:text-white"}`}>
                {m === "duplicate" ? "Duplicate" : "Standard"}
              </button>
            ))}
          </div>
        </div>
        <div>
          <div className="mb-1 text-slate-400" id="stack-label">Stacks</div>
          <div className="flex rounded-xl bg-black/20 p-0.5" role="group" aria-labelledby="stack-label">
            {info.limits.stacks_bb.map((s) => (
              <button key={s} type="button" aria-pressed={form.stack_bb === s} onClick={() => onChange({ ...form, stack_bb: s })}
                className={`rounded-[10px] px-2.5 py-1.5 font-semibold ${form.stack_bb === s ? "bg-amber-300 text-slate-950" : "text-slate-300 hover:text-white"}`}>
                {s} bb
              </button>
            ))}
          </div>
        </div>
        <label className="flex flex-col text-slate-400">
          <span className="mb-1">Seed</span>
          <span className="flex items-center gap-1.5">
            <input type="number" className="w-28 rounded-lg bg-slate-900 px-2 py-1.5 text-slate-100 ring-1 ring-white/10" min={0}
              max={info.limits.max_seed} value={form.seed}
              onChange={(e) => onChange({ ...form, seed: Math.max(0, Math.floor(Number(e.target.value) || 0)) })} />
            <button type="button" className="btn-dark rounded-lg px-2 py-1.5 font-semibold" onClick={() => onChange({ ...form, seed: newSeed() })}>
              New
            </button>
          </span>
        </label>
        <label className="flex flex-col text-slate-400">
          <span className="mb-1" title="Deals each pair of agents plays before both are recreated: adaptive strategies learn within a session">
            Session
          </span>
          <input type="number" aria-label="Session length in deals" className="w-20 rounded-lg bg-slate-900 px-2 py-1.5 text-slate-100 ring-1 ring-white/10"
            min={info.limits.session_range[0]} max={info.limits.session_range[1]} step={50} value={form.session_hands}
            onChange={(e) => onChange({ ...form, session_hands: Math.floor(Number(e.target.value) || 0) })} />
        </label>
      </div>

      <p className="mt-3 max-w-3xl text-xs text-slate-400">
        {form.mode === "duplicate"
          ? "Duplicate: every deal is played twice with the seats swapped, so both strategies get the same cards and most of the luck cancels."
          : "Standard: every deal is played once, like an ordinary match. Results are noisier than duplicate for the same number of hands."}{" "}
        The same seed always gives the same result.
      </p>
      {adaptive && sessions > 0 && sessions < 10 && (
        <p className="mt-1 max-w-3xl text-xs text-orange-200" data-testid="lab-session-hint">
          An adaptive strategy learns during a session, so its interval needs at least 10 sessions; these settings give{" "}
          {sessions}. Use more hands or shorter sessions (now {form.session_hands} deals).
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button type="button" className="btn-gold rounded-xl px-4 py-2 text-sm font-semibold disabled:opacity-50" disabled={!estimate || busy} onClick={onRun}
          data-testid="lab-run">
          {kind === "mirror" ? `Run ${byId.get(sel[0])?.label} against itself` : kind === "rr" ? `Run round robin (${(sel.length * (sel.length - 1)) / 2} matchups)` : "Run simulation"}
        </button>
        <div className="text-xs text-slate-400" data-testid="lab-estimate" aria-live="polite">
          {!kind
            ? "Choose two strategies (or one, to test it against itself)."
            : error
              ? <span className="text-orange-200">{error}</span>
              : estimate
                ? `${count(estimate.total_hands)} hands · about ${duration(estimate.estimate_seconds)} on ${estimate.workers || 1} ${estimate.workers === 1 || !estimate.workers ? "core" : "cores"}${slowest > 10 ? " (the equity-based levels think about every decision)" : ""}`
                : "Checking…"}
        </div>
        {busy && <span className="text-xs text-slate-500">An experiment is running: stop it or let it finish to start another.</span>}
      </div>
    </section>
  );
}

function StrategyChip({ s, index, disabled, onToggle }: { s: LabStrategy; index: number; disabled: boolean; onToggle: () => void }) {
  const on = index >= 0;
  // Worst case: against an opponent that takes hands deep.
  const speed = s.cost_deep_ms > 60 ? "slow" : s.cost_deep_ms > 5 ? "moderate" : "fast";
  return (
    <button
      type="button"
      aria-pressed={on}
      disabled={disabled}
      onClick={onToggle}
      title={s.description}
      className={`flex items-start gap-2 rounded-xl px-2.5 py-2 text-left ring-1 transition disabled:opacity-40 ${
        on ? "bg-amber-300/10 ring-amber-300/60" : "bg-black/15 ring-white/10 hover:ring-white/25"
      }`}
    >
      <span className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] font-bold ${on ? "bg-amber-300 text-slate-950" : "ring-1 ring-white/25"}`}>
        {on ? index + 1 : ""}
      </span>
      <span className="min-w-0">
        <span className="flex flex-wrap items-center gap-1.5 text-[13px] font-semibold text-slate-100">
          {s.label}
          {s.adaptive && <span className="rounded bg-sky-500/15 px-1 text-[10px] font-semibold text-sky-200">adapts</span>}
          <span className="text-[10px] font-medium text-slate-500">{speed}</span>
        </span>
        <span className="line-clamp-2 text-[11px] leading-snug text-slate-400">{s.description}</span>
      </span>
    </button>
  );
}

// ------------------------------------------------------------------ experiment

function Experiment({
  job,
  pair,
  onPair,
  onCancel,
  onRerun,
  csvUrl,
}: {
  job: LabJob;
  pair: [string, string] | null;
  onPair: (p: [string, string]) => void;
  onCancel: () => void;
  onRerun: (seed?: number) => void;
  csvUrl: string;
}) {
  const active = ACTIVE(job.status);
  const report = job.report;
  const rr = report?.round_robin ?? null;
  const labels: Record<string, string> = {};
  for (const m of job.matchups) {
    labels[m.a] = m.a_label;
    labels[m.b] = m.b_label;
  }
  const order = [...new Set(job.config.strategies)];
  const chosen = report
    ? rr
      ? (pair && report.matchups.find((m) => m.a === pair[0] && m.b === pair[1])) || null
      : report.matchups[0] ?? null
    : null;
  const p = job.progress;
  const running = job.matchups.find((m) => m.status === "running" && m.live?.trajectory);
  const [copied, setCopied] = useState(false);
  const cmd = cliCommand(job.config);

  return (
    <section className="flex flex-col gap-4" aria-labelledby="lab-job-title" data-testid="lab-job">
      <div className="surface rounded-2xl p-4">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <h2 id="lab-job-title" className="font-display text-[17px] font-bold text-amber-50">
            {configTitle(job.labels)}
          </h2>
          <StatusChip status={job.status} cached={job.matchups.every((m) => m.cached)} />
          <span className="text-xs text-slate-400">{configLine(job.config)}</span>
          <div className="ml-auto flex flex-wrap gap-2">
            {active ? (
              <button type="button" className="btn-dark rounded-xl px-3 py-1.5 text-xs font-semibold" onClick={onCancel}>
                Stop
              </button>
            ) : (
              <>
                <button type="button" className="btn-dark rounded-xl px-3 py-1.5 text-xs font-semibold" onClick={() => onRerun(newSeed())}
                  title="Same experiment on different cards">
                  Run with a new seed
                </button>
                {report && (
                  <a className="btn-dark rounded-xl px-3 py-1.5 text-xs font-semibold" href={csvUrl} download>
                    Deals (CSV)
                  </a>
                )}
              </>
            )}
          </div>
        </div>

        {active && (
          <div className="mt-3" data-testid="lab-progress">
            <div className="h-2 w-full overflow-hidden rounded-full bg-white/5" role="progressbar" aria-valuemin={0} aria-valuemax={100}
              aria-valuenow={Math.round(p.fraction * 100)} aria-label="Simulation progress">
              <div className="h-full rounded-full bg-amber-300 transition-[width] duration-500" style={{ width: `${Math.max(2, p.fraction * 100)}%` }} />
            </div>
            <div className="mt-1.5 text-xs tabular-nums text-slate-400">
              {job.status === "queued"
                ? "Waiting for the experiment before it to finish…"
                : `${count(p.hands_done)} of ${count(p.total_hands)} hands${p.hands_per_second ? ` · ${count(Math.round(p.hands_per_second))} hands/s` : ""} · about ${duration(p.eta_seconds)} left`}
            </div>
          </div>
        )}
        {job.status === "failed" && (
          <p role="alert" className="mt-3 text-sm text-orange-100">
            The experiment failed: {job.error}
          </p>
        )}

        {job.matchups.length > 1 && (
          <ul className="mt-3 grid grid-cols-1 gap-x-6 gap-y-1 text-xs sm:grid-cols-2 lg:grid-cols-3" data-testid="lab-matchups">
            {job.matchups.map((m) => (
              <li key={`${m.a}|${m.b}`} className="flex items-center gap-2 tabular-nums">
                <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${m.status === "done" ? "bg-amber-300" : m.status === "running" ? "animate-pulse bg-sky-300" : "bg-white/20"}`} />
                <span className="min-w-0 flex-1 truncate text-slate-300">
                  {m.a_label} vs {m.b_label}
                </span>
                <span className="text-slate-400">
                  {m.estimate
                    ? `${rate(m.estimate.bb_per_100)}`
                    : m.status === "running"
                      ? `${Math.round((m.hands_done / m.planned_hands) * 100)}%`
                      : m.cached
                        ? "cached"
                        : "waiting"}
                </span>
              </li>
            ))}
          </ul>
        )}

        {active && running?.live?.trajectory && (
          <div className="mt-4" data-testid="lab-live">
            <div className="mb-1 text-xs text-slate-400">
              Live: {running.a_label} vs {running.b_label}
              {running.live.provisional_bb_per_100 !== null && (
                <> · {signed(running.live.provisional_bb_per_100)} bb/100 so far (provisional; the interval comes at the end)</>
              )}
            </div>
            <TrajectoryChart trajectory={running.live.trajectory} bandSd={null} aLabel={running.a_label} />
          </div>
        )}

        {!active && (
          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-400">
            <span>Reproduce exactly:</span>
            <code className="rounded bg-black/30 px-2 py-1 font-mono text-[11px] text-slate-200" data-testid="lab-cli">
              {cmd}
            </code>
            <button
              type="button"
              className="text-amber-200 hover:text-amber-100"
              onClick={() => {
                void navigator.clipboard?.writeText(cmd).then(() => setCopied(true), () => undefined);
              }}
            >
              {copied ? "Copied" : "Copy"}
            </button>
            <span className="text-slate-500">(from the backend folder · PokerLab {report?.version ?? ""})</span>
          </div>
        )}
      </div>

      {rr && <RoundRobin rr={rr} labels={labels} order={order} selected={pair} onSelect={onPair} />}
      {chosen && <MatchupDetail key={`${chosen.a}|${chosen.b}`} m={chosen} />}
      {rr && !chosen && <p className="px-1 text-xs text-slate-500">Select a cell in the table to see that matchup in detail.</p>}
    </section>
  );
}

function StatusChip({ status, cached }: { status: LabJob["status"]; cached: boolean }) {
  const text = { queued: "Queued", running: "Running", done: cached ? "Done (cached)" : "Done", cancelled: "Stopped", failed: "Failed" }[status];
  const tone =
    status === "done"
      ? "bg-emerald-500/15 text-emerald-100 ring-emerald-400/30"
      : status === "running" || status === "queued"
        ? "bg-sky-500/15 text-sky-100 ring-sky-400/30"
        : "bg-orange-500/15 text-orange-100 ring-orange-400/30";
  return (
    <span className={`rounded-full px-2.5 py-0.5 text-[11px] font-semibold ring-1 ${tone}`} data-testid="lab-status">
      {text}
    </span>
  );
}

function Recent({ recent, current, onOpen }: { recent: LabJobSummary[]; current: string | null; onOpen: (id: string) => void }) {
  return (
    <section className="surface rounded-2xl p-4" aria-labelledby="lab-recent-title" data-testid="lab-recent">
      <h2 id="lab-recent-title" className="eyebrow text-slate-400">
        Recent experiments
      </h2>
      <ul className="mt-2 divide-y divide-white/5 text-xs">
        {recent.map((r) => (
          <li key={r.id}>
            <button type="button" onClick={() => onOpen(r.id)} aria-current={r.id === current}
              className={`flex w-full flex-wrap items-center gap-x-3 gap-y-0.5 py-2 text-left hover:text-white ${r.id === current ? "text-amber-100" : "text-slate-300"}`}>
              <span className="font-semibold">{configTitle(r.labels)}</span>
              <span className="text-slate-500">{configLine(r.config)}</span>
              <span className="ml-auto text-slate-500">
                {r.status === "running" ? `${Math.round(r.progress.fraction * 100)}%` : r.status}
              </span>
            </button>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-[11px] text-slate-500">Kept on the server until it restarts; any experiment can be recreated from its seed.</p>
    </section>
  );
}
