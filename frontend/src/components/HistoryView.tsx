import { useCallback, useEffect, useMemo, useState } from "react";
import type { ProgressStore } from "../hooks/useProgressStore";
import { ApiError, profileApi } from "../lib/api";
import {
  activeFilterCount,
  filterHands,
  type HistoryFilters,
  LARGE_POT_BB,
  NO_FILTERS,
  STRENGTH_LABEL,
  type Strength,
} from "../lib/hands";
import { CONCEPT_LABEL, type Progress } from "../lib/progress";
import { adaptiveLevel, estimateSkill, observationsFor } from "../lib/skills";
import type { DecisionReview, HandFrame, HandRecord, HandSummary, Weakness, Weaknesses } from "../types";
import { formatPercent } from "./EquityPanel";
import { HandReplayer } from "./HandReplayer";
import { MiniCard } from "./HandLog";
import type { Plan } from "./TrainingView";

interface Props {
  store: ProgressStore;
  progress: Progress;
  detailed: boolean;
  onToggleDetailed: () => void;
  onPractice: (d: DecisionReview) => void;
  onTrain: (plan: Plan) => void;
}

type ListState =
  | { status: "loading" }
  | { status: "ready"; hands: HandSummary[]; total: number; weaknesses: Weaknesses }
  | { status: "error"; message: string };

/**
 * Saved hands: what to practise next (recurring weaknesses, with their evidence), a
 * searchable, filterable list, and the replayer with each decision's coaching.
 */
export function HistoryView({ store, progress, detailed, onToggleDetailed, onPractice, onTrain }: Props) {
  const token = store.active.token;
  const [list, setList] = useState<ListState>({ status: "loading" });
  const [filters, setFilters] = useState<HistoryFilters>(NO_FILTERS);
  const [selected, setSelected] = useState<{ id: string; decision: number | null } | null>(null);
  const [hand, setHand] = useState<
    { status: "loading" } | { status: "ready"; record: HandRecord; frames: HandFrame[] } | { status: "error"; message: string } | null
  >(null);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const r = await profileApi.hands(token);
      setList({ status: "ready", ...r });
    } catch (e) {
      setList({
        status: "error",
        message:
          e instanceof ApiError && e.status === 503
            ? "The server's storage is unavailable, so saved hands cannot be shown right now."
            : e instanceof ApiError && e.status === 401
              ? "The server no longer knows this profile."
              : "Saved hands could not be loaded. Is the server running?",
      });
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load, store.handsVersion]);

  // Open a hand (and optionally one decision in it).
  useEffect(() => {
    if (!selected || !token) return;
    let cancelled = false;
    setHand({ status: "loading" });
    profileApi
      .hand(token, selected.id)
      .then((r) => !cancelled && setHand({ status: "ready", ...r }))
      .catch((e) => {
        if (cancelled) return;
        setHand({
          status: "error",
          message: e instanceof ApiError && e.status === 422 ? e.message : "This hand could not be loaded.",
        });
      });
    return () => {
      cancelled = true;
    };
  }, [selected?.id, token]); // eslint-disable-line react-hooks/exhaustive-deps

  const hands = list.status === "ready" ? list.hands : [];
  const shown = useMemo(() => filterHands(hands, filters), [hands, filters]);

  const train = (w: Weakness) => {
    const level = adaptiveLevel(estimateSkill(observationsFor(progress, w.concept)));
    onTrain({ concept: w.concept, difficulty: level, reason: `From your hand history: ${w.title.toLowerCase()}`, review: false });
  };

  if (!token) {
    return (
      <div className="surface rounded-2xl p-5 text-[13px] text-slate-300" data-testid="history-view">
        <h2 className="font-display text-[18px] font-bold text-amber-50">Hand history</h2>
        <p className="mt-2">
          {store.pendingHands
            ? `${store.pendingHands} finished hand${store.pendingHands === 1 ? " is" : "s are"} waiting on this device and will be saved once the server is reachable.`
            : "Finish a hand at the table and it will be saved here, with the coaching for each of your decisions, ready to replay."}
        </p>
      </div>
    );
  }

  return (
    <div className="flex w-full flex-col gap-4" data-testid="history-view">
      <WeaknessPanel state={list} onOpen={(id, decision) => setSelected({ id, decision })} onTrain={train} />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
        <section className="surface rounded-2xl p-4" aria-labelledby="history-title">
          <div className="flex items-baseline justify-between gap-2">
            <h2 id="history-title" className="font-display text-[18px] font-bold text-amber-50">
              Hand history
            </h2>
            <span className="text-[11.5px] text-slate-400" data-testid="history-count">
              {list.status === "ready" ? `${shown.length} of ${list.total} hands` : ""}
            </span>
          </div>
          {store.pendingHands > 0 && (
            <p className="mt-1 text-[11.5px] text-amber-200/80" data-testid="history-pending">
              {store.pendingHands} finished hand{store.pendingHands === 1 ? "" : "s"} waiting to be saved on the server.
            </p>
          )}
          <Filters value={filters} onChange={setFilters} />
          {list.status === "loading" && <p className="mt-3 text-[12.5px] text-slate-400">Loading saved hands…</p>}
          {list.status === "error" && (
            <p className="mt-3 text-[12.5px] text-rose-300" role="alert">
              {list.message}{" "}
              <button type="button" className="underline" onClick={() => void load()}>
                Try again
              </button>
            </p>
          )}
          {list.status === "ready" && list.total === 0 && (
            <p className="mt-3 text-[12.5px] text-slate-400">No saved hands yet. Finished hands appear here automatically.</p>
          )}
          {list.status === "ready" && list.total > 0 && shown.length === 0 && (
            <p className="mt-3 text-[12.5px] text-slate-400">No hands match these filters.</p>
          )}
          <ul className="mt-2 max-h-[34rem] space-y-1 overflow-y-auto pr-1" data-testid="history-list">
            {shown.map((h) => (
              <li key={h.id}>
                <HandRow h={h} active={selected?.id === h.id} onOpen={() => setSelected({ id: h.id, decision: null })} />
              </li>
            ))}
          </ul>
          {list.status === "ready" && list.total > list.hands.length && (
            <p className="mt-2 text-[11px] text-slate-500">Showing the most recent {list.hands.length} hands.</p>
          )}
        </section>

        <section className="surface min-w-0 rounded-2xl p-4">
          {!selected && <p className="text-[13px] text-slate-400">Choose a hand to replay it step by step and review your decisions.</p>}
          {hand?.status === "loading" && <p className="text-[13px] text-slate-400">Loading the hand…</p>}
          {hand?.status === "error" && (
            <p className="text-[13px] text-rose-300" role="alert">
              {hand.message}
            </p>
          )}
          {hand?.status === "ready" && (
            <HandReplayer
              record={hand.record}
              frames={hand.frames}
              startDecision={selected?.decision ?? null}
              detailed={detailed}
              onToggleDetailed={onToggleDetailed}
              onPractice={onPractice}
            />
          )}
        </section>
      </div>
    </div>
  );
}

const RESULT_STYLE: Record<HandSummary["result"], string> = {
  won: "text-emerald-300",
  lost: "text-rose-300",
  split: "text-sky-300",
  even: "text-slate-300",
};

function HandRow({ h, active, onOpen }: { h: HandSummary; active: boolean; onOpen: () => void }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-current={active ? "true" : undefined}
      data-testid="history-row"
      className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[12.5px] ring-1 ${
        active ? "bg-white/10 ring-amber-300/40" : "bg-white/[0.02] ring-white/5 hover:bg-white/[0.06]"
      }`}
    >
      <span className="w-9 shrink-0 tabular-nums text-slate-400">#{h.hand_number}</span>
      <span className="shrink-0">
        {h.hero_cards.map((c) => (
          <MiniCard key={c} code={c} />
        ))}
      </span>
      <span className="min-w-0 flex-1 truncate text-slate-300">
        {h.final_hand ?? h.hand_class}
        <span className="text-slate-500"> · {h.last_street}</span>
      </span>
      {h.flagged > 0 && (
        <span className="shrink-0 rounded-full bg-amber-500/15 px-1.5 text-[10.5px] font-semibold text-amber-300 ring-1 ring-amber-400/30" title="Decisions flagged for review">
          {h.flagged} to review
        </span>
      )}
      <span className={`w-16 shrink-0 text-right font-semibold tabular-nums ${RESULT_STYLE[h.result]}`}>
        {h.net_bb > 0 ? "+" : h.net_bb < 0 ? "−" : ""}
        {Math.abs(h.net_bb).toFixed(1)} bb
      </span>
    </button>
  );
}

function Filters({ value, onChange }: { value: HistoryFilters; onChange: (f: HistoryFilters) => void }) {
  const set = <K extends keyof HistoryFilters>(k: K, v: HistoryFilters[K]) => onChange({ ...value, [k]: v });
  const field = "rounded-lg bg-white/[0.06] px-2 py-1.5 text-[12.5px] text-slate-100 ring-1 ring-white/10";
  const n = activeFilterCount(value);
  return (
    <div className="mt-2 space-y-2 text-[12px] text-slate-300" data-testid="history-filters">
      <input
        type="search"
        value={value.query}
        onChange={(e) => set("query", e.target.value)}
        placeholder="Search: AK, Kh, flush, #12"
        aria-label="Search hands"
        className={`${field} w-full`}
      />
      <div className="grid grid-cols-2 gap-2">
        <label className="flex flex-col gap-0.5">
          <span className="text-[10.5px] uppercase tracking-wide text-slate-400">Result</span>
          <select className={field} value={value.result} onChange={(e) => set("result", e.target.value as HistoryFilters["result"])}>
            <option value="all">All</option>
            <option value="won">Won</option>
            <option value="lost">Lost</option>
            <option value="split">Split or even</option>
          </select>
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[10.5px] uppercase tracking-wide text-slate-400">Starting hand</span>
          <select className={field} value={value.strength} onChange={(e) => set("strength", e.target.value as HistoryFilters["strength"])}>
            <option value="all">Any</option>
            {(Object.keys(STRENGTH_LABEL) as Strength[]).map((s) => (
              <option key={s} value={s}>
                {STRENGTH_LABEL[s]}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[10.5px] uppercase tracking-wide text-slate-400">From</span>
          <input type="date" className={field} value={value.from} onChange={(e) => set("from", e.target.value)} aria-label="From date" />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[10.5px] uppercase tracking-wide text-slate-400">To</span>
          <input type="date" className={field} value={value.to} onChange={(e) => set("to", e.target.value)} aria-label="To date" />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[10.5px] uppercase tracking-wide text-slate-400">Showdown</span>
          <select className={field} value={value.showdown} onChange={(e) => set("showdown", e.target.value as HistoryFilters["showdown"])}>
            <option value="all">Any</option>
            <option value="yes">Went to showdown</option>
            <option value="no">Ended before showdown</option>
          </select>
        </label>
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={value.largePots} onChange={(e) => set("largePots", e.target.checked)} />
          Large pots ({LARGE_POT_BB} bb+)
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={value.flagged} onChange={(e) => set("flagged", e.target.checked)} />
          Decisions flagged for review
        </label>
        {n > 0 && (
          <button type="button" className="ml-auto text-[11.5px] text-amber-200 underline" onClick={() => onChange(NO_FILTERS)}>
            Clear {n} filter{n === 1 ? "" : "s"}
          </button>
        )}
      </div>
    </div>
  );
}

function WeaknessPanel({
  state,
  onOpen,
  onTrain,
}: {
  state: ListState;
  onOpen: (id: string, decision: number | null) => void;
  onTrain: (w: Weakness) => void;
}) {
  if (state.status !== "ready" || state.total === 0) return null;
  const w = state.weaknesses;
  return (
    <section className="surface rounded-2xl p-4" aria-labelledby="weakness-title" data-testid="weakness-panel">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="weakness-title" className="font-display text-[18px] font-bold text-amber-50">
          What to practise next
        </h2>
        <span className="text-[11.5px] text-slate-400">
          From {w.decisions} graded decisions in {w.hands} hands
        </span>
      </div>
      {w.patterns.length === 0 ? (
        <p className="mt-1 text-[12.5px] text-slate-300" data-testid="no-patterns">
          No recurring weakness yet. A pattern is only reported after at least {w.rules.min_opportunities} chances to make
          the same kind of decision, {w.rules.min_flagged} confidently flagged ones, and a rate that stays above{" "}
          {formatPercent(w.rules.min_rate)} even read pessimistically.
        </p>
      ) : (
        <ul className="mt-2 grid gap-2 md:grid-cols-2" data-testid="patterns">
          {w.patterns.map((p) => (
            <li key={p.key} className="rounded-xl bg-white/[0.03] p-3 ring-1 ring-amber-400/20" data-testid="pattern">
              <div className="flex items-start justify-between gap-2">
                <h3 className="text-[13.5px] font-semibold text-slate-100">{p.title}</h3>
                <button type="button" onClick={() => onTrain(p)} className="btn-gold shrink-0 rounded-lg px-2.5 py-1 text-[12px] font-bold">
                  Practise {CONCEPT_LABEL[p.concept].toLowerCase()}
                </button>
              </div>
              <p className="mt-0.5 text-[12px] text-slate-400">{p.description}</p>
              <p className="mt-1 text-[12.5px] text-slate-200">
                {p.flagged} of {p.opportunities} such decisions flagged ({formatPercent(p.rate)}; likely between{" "}
                {formatPercent(p.low)} and {formatPercent(p.high)}).
              </p>
              {p.conditions?.map((c) => (
                <p key={c.key} className="mt-0.5 text-[12px] text-amber-200/90">
                  Mostly {c.label}: {c.flagged} of {c.opportunities} ({formatPercent(c.rate)}, versus{" "}
                  {formatPercent(c.rate_elsewhere)} otherwise).
                </p>
              ))}
              <div className="mt-1.5 flex flex-wrap items-center gap-1 text-[11.5px] text-slate-400">
                <span>Hands:</span>
                {p.hands.map((h) => (
                  <button
                    key={`${h.id}-${h.decision}`}
                    type="button"
                    onClick={() => onOpen(h.id, h.decision)}
                    className="rounded-md bg-white/[0.06] px-1.5 py-0.5 tabular-nums text-slate-200 hover:bg-white/10"
                  >
                    #{h.hand_number}
                  </button>
                ))}
              </div>
            </li>
          ))}
        </ul>
      )}
      {w.early.length > 0 && (
        <details className="mt-2 text-[12px] text-slate-400" data-testid="early-signals">
          <summary className="cursor-pointer text-slate-300">Too early to tell ({w.early.length})</summary>
          <ul className="mt-1 space-y-1">
            {w.early.map((e) => (
              <li key={e.key}>
                <b className="font-semibold text-slate-300">{e.title}</b>: {e.flagged} flagged of {e.opportunities}. Not a pattern
                yet: needs {e.missing?.join(" and ")}.{" "}
                {e.hands.slice(0, 4).map((h) => (
                  <button key={`${h.id}-${h.decision}`} type="button" className="mr-1 underline" onClick={() => onOpen(h.id, h.decision)}>
                    #{h.hand_number}
                  </button>
                ))}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
