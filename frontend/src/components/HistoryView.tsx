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
import { reportOf, type LeakState } from "../hooks/useLeakReport";
import { findingsFor, leakPlan } from "../lib/leaks";
import type { Progress } from "../lib/progress";
import type { DecisionReview, HandFrame, HandRecord, HandSummary, LeakItem } from "../types";
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
  /** The leak report (v0.4.4), shared with the Progress tab. */
  leaks?: LeakState;
  /** A hand to open (from a leak's evidence on the Progress tab); ``n`` changes per request. */
  openHand?: { id: string; decision: number | null; n: number } | null;
  onShowLeak?: (key: string) => void;
}

type ListState =
  | { status: "loading" }
  | { status: "ready"; hands: HandSummary[]; total: number }
  | { status: "error"; message: string };

/**
 * Saved hands: a searchable, filterable list and the replayer with each decision's
 * coaching. Decisions that support a recurring leak link to it; the leaks themselves,
 * with their evidence and practice, live on the Progress tab.
 */
export function HistoryView({
  store,
  progress,
  detailed,
  onToggleDetailed,
  onPractice,
  onTrain,
  leaks = { status: "none" },
  openHand = null,
  onShowLeak,
}: Props) {
  const token = store.active.token;
  const [list, setList] = useState<ListState>({ status: "loading" });
  const [filters, setFilters] = useState<HistoryFilters>(NO_FILTERS);
  const [selected, setSelected] = useState<{ id: string; decision: number | null } | null>(
    openHand ? { id: openHand.id, decision: openHand.decision } : null,
  );
  // A link from the Progress tab while this view is open.
  useEffect(() => {
    if (openHand) setSelected({ id: openHand.id, decision: openHand.decision });
  }, [openHand]);
  const report = reportOf(leaks);
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

  const train = (w: LeakItem) => onTrain(leakPlan(w, progress));

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
      {report && list.status === "ready" && list.total > 0 && <LeakSummary leaks={report.leaks} needed={report.status === "insufficient" ? report.needed : 0} onShowLeak={onShowLeak} />}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
        <section className="surface min-w-0 rounded-2xl p-4" aria-labelledby="history-title">
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
            <p className="mt-2 text-[11px] text-slate-400">Showing the most recent {list.hands.length} hands.</p>
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
              findings={(i) => findingsFor(report, hand.record.id, i)}
              onShowLeak={onShowLeak}
              onTrainLeak={train}
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
        <span className="text-slate-400"> · {h.last_street}</span>
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

/** One line about recurring leaks: the evidence and practice are on the Progress tab. */
function LeakSummary({ leaks, needed, onShowLeak }: { leaks: LeakItem[]; needed: number; onShowLeak?: (key: string) => void }) {
  return (
    <section className="surface rounded-2xl px-4 py-3 text-[12.5px] text-slate-300" data-testid="leak-summary" aria-label="Recurring leaks">
      {leaks.length ? (
        <p>
          <b className="font-semibold text-amber-200">
            {leaks.length} supported weakness{leaks.length === 1 ? "" : "es"} in your hands:
          </b>{" "}
          {leaks.map((l, i) => (
            <span key={l.key}>
              {i > 0 && ", "}
              <button type="button" className="underline decoration-amber-300/50 hover:text-white" onClick={() => onShowLeak?.(l.key)}>
                {l.title.toLowerCase()}
              </button>
            </span>
          ))}
          . Decisions that are part of one are marked in the replayer.
        </p>
      ) : needed > 0 ? (
        <p>
          Recurring leaks are looked for once there are enough graded decisions: {needed} more to go. Until then each hand's
          review stands on its own.
        </p>
      ) : (
        <p>No recurring weakness is supported by your hands so far. Strengths and early signals are on the Progress tab.</p>
      )}
    </section>
  );
}
