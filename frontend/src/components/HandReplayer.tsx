import { useEffect, useMemo, useRef, useState } from "react";
import { shortcutBlocked } from "../lib/keys";
import type { DecisionReview, HandFrame, HandRecord, LeakItem } from "../types";
import { DecisionDetail, GradeChip } from "./DecisionDetail";
import { PlayingCard } from "./PlayingCard";

const STREET_LABEL: Record<string, string> = {
  preflop: "Preflop",
  flop: "Flop",
  turn: "Turn",
  river: "River",
  showdown: "Showdown",
};

const PLAY_MS = 900;

const chips = (n: number) => n.toLocaleString("en-US");

interface Props {
  record: HandRecord;
  frames: HandFrame[];
  /** Open at the frame of this coaching decision (e.g. from a weakness link). */
  startDecision?: number | null;
  detailed: boolean;
  onToggleDetailed: () => void;
  onPractice?: (d: DecisionReview) => void;
  /** Leak-report findings this decision (by index) is evidence for (v0.4.4). */
  findings?: (decision: number) => LeakItem[];
  onShowLeak?: (key: string) => void;
  onTrainLeak?: (item: LeakItem) => void;
}

/**
 * Step through a saved hand. Every table state comes from the server's replay of the
 * recorded events; nothing is reconstructed or guessed here. Opponent cards appear only
 * from the moment they were shown in the hand.
 */
export function HandReplayer({
  record,
  frames,
  startDecision = null,
  detailed,
  onToggleDetailed,
  onPractice,
  findings,
  onShowLeak,
  onTrainLeak,
}: Props) {
  const decisions = record.coaching?.decisions ?? [];
  // Steps: every event except the silent collection of bets into the pot.
  const nav = useMemo(() => frames.map((f, i) => (f.type === "bets_collected" ? -1 : i)).filter((i) => i >= 0), [frames]);
  const decisionSteps = useMemo(() => nav.map((fi, s) => (frames[fi].decision !== null ? s : -1)).filter((s) => s >= 0), [nav, frames]);
  const startAt = useMemo(() => {
    if (startDecision !== null) {
      const s = nav.findIndex((fi) => frames[fi].decision === startDecision);
      if (s >= 0) return s;
    }
    return 0;
  }, [nav, frames, startDecision]);
  const [step, setStep] = useState(startAt);
  const [playing, setPlaying] = useState(false);
  useEffect(() => {
    setStep(startAt);
    setPlaying(false);
  }, [record.id, startAt]);

  const last = nav.length - 1;
  const go = (i: number) => setStep(Math.max(0, Math.min(last, i)));

  // Playback: one step per tick until the end.
  useEffect(() => {
    if (!playing) return;
    if (step >= last) {
      setPlaying(false);
      return;
    }
    const t = setTimeout(() => setStep((s) => Math.min(last, s + 1)), PLAY_MS);
    return () => clearTimeout(t);
  }, [playing, step, last]);

  // Keyboard: ← → step, Home / End jump, while the replayer is on screen.
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (shortcutBlocked(e) || !root.current?.isConnected) return;
      const map: Record<string, () => void> = {
        ArrowLeft: () => go(step - 1),
        ArrowRight: () => go(step + 1),
        Home: () => go(0),
        End: () => go(last),
      };
      const f = map[e.key];
      if (!f) return;
      e.preventDefault();
      setPlaying(false);
      f();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, last]);

  const fi = nav[step];
  const frame = frames[fi];
  // At one of your decisions the table shows what you faced: the state just before it.
  const atDecision = frame.decision !== null && fi > 0;
  const table = atDecision ? frames[fi - 1] : frame;
  const hero = record.hero;
  const opp = hero === 0 ? 1 : 0;
  const start = record.events[0] as unknown as { button: number; small_blind: number; big_blind: number; stacks: number[] };
  const bb = start.big_blind;
  const decision = frame.decision !== null ? decisions[frame.decision] : undefined;
  const prevDecision = [...decisionSteps].reverse().find((s) => s < step);
  const nextDecision = decisionSteps.find((s) => s > step);
  const net = frames[frames.length - 1].stacks[hero] - start.stacks[hero];
  const shown = table.visible[String(opp)];

  // The timeline, grouped by street (frames without text, like collections, are skipped).
  const groups = useMemo(() => {
    const out: { street: string; items: { i: number; f: HandFrame }[] }[] = [];
    nav.forEach((frameIndex, i) => {
      const f = frames[frameIndex];
      if (!f.text) return;
      const street = f.type === "street" ? f.street : out.length ? out[out.length - 1].street : "preflop";
      if (!out.length || out[out.length - 1].street !== street) out.push({ street, items: [] });
      out[out.length - 1].items.push({ i, f });
    });
    return out;
  }, [nav, frames]);

  const seat = (s: number) => {
    const isHero = s === hero;
    const cards = isHero ? record.hero_cards : shown;
    return (
      <div className={`flex flex-col items-center gap-1 ${table.folded[s] ? "opacity-50" : ""}`} data-testid={isHero ? "replay-hero" : "replay-opponent"}>
        <div className="flex gap-1" aria-label={isHero ? "Your cards" : "Opponent cards"}>
          {cards
            ? cards.map((c) => <PlayingCard key={c} code={c} />)
            : [0, 1].map((k) => <PlayingCard key={k} faceUp={false} />)}
        </div>
        <div className={`rounded-full px-2.5 py-0.5 text-[12px] ${(atDecision ? s === hero : frame.actor === s) ? "bg-amber-300/90 font-semibold text-slate-950" : "bg-black/30 text-emerald-50/90"}`}>
          {record.names[s]}
          {start.button === s ? " (button)" : ""} · {chips(table.stacks[s])}
          {table.folded[s] && " · folded"}
        </div>
        {table.bets[s] > 0 && <div className="text-[11.5px] font-semibold tabular-nums text-amber-200">bet {chips(table.bets[s])}</div>}
      </div>
    );
  };

  const btn = "btn-dark min-h-9 rounded-lg px-2.5 py-1.5 text-[12.5px] font-semibold disabled:opacity-40";

  return (
    <div ref={root} className="flex flex-col gap-3" data-testid="hand-replayer">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="font-display text-[18px] font-bold text-amber-50">Hand #{record.hand_number}</h2>
        <span className="text-[12px] text-slate-400">
          {new Date(record.t).toLocaleString()} · blinds {start.small_blind}/{bb}
          {record.difficulty ? ` · ${record.difficulty} opponent` : ""}
        </span>
        <span className={`ml-auto text-[13px] font-semibold tabular-nums ${net > 0 ? "text-emerald-300" : net < 0 ? "text-rose-300" : "text-slate-300"}`}>
          {net > 0 ? "+" : net < 0 ? "−" : ""}
          {chips(Math.abs(net))} ({(net / bb).toFixed(1)} bb)
        </span>
      </div>

      <div className="felt relative flex flex-col items-center gap-3 rounded-2xl px-3 py-4" data-testid="replay-table">
        {atDecision && (
          <div className="absolute left-3 top-3 rounded-full bg-amber-300/90 px-2.5 py-0.5 text-[11.5px] font-semibold text-slate-950" data-testid="replay-your-turn">
            Your turn: you chose to {frame.text?.replace(/^You /, "")}
          </div>
        )}
        {seat(opp)}
        <div className="flex min-h-[calc(var(--card-w)*1.4)] gap-1.5" aria-label="Board">
          {table.board.length ? (
            table.board.map((c) => <PlayingCard key={c} code={c} />)
          ) : (
            <span className="self-center text-[12px] text-emerald-50/70">Preflop: no board yet</span>
          )}
        </div>
        <div className="rounded-full bg-black/30 px-3 py-1 text-[13px] font-semibold tabular-nums text-amber-100" data-testid="replay-pot">
          Pot {chips(table.pot)}
          {atDecision && frame.to_call ? <span className="text-amber-200/80"> · to call {chips(frame.to_call)}</span> : null}
        </div>
        {seat(hero)}
      </div>

      <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Replay controls">
        <button type="button" className={btn} onClick={() => go(0)} disabled={step === 0} aria-label="First step">
          ⏮
        </button>
        <button type="button" className={btn} onClick={() => go(step - 1)} disabled={step === 0} aria-label="Previous step">
          ‹
        </button>
        <button
          type="button"
          className={btn}
          onClick={() => (step >= last ? (go(0), setPlaying(true)) : setPlaying((p) => !p))}
          aria-label={playing ? "Pause" : "Play"}
        >
          {playing ? "⏸" : "▶"}
        </button>
        <button type="button" className={btn} onClick={() => go(step + 1)} disabled={step === last} aria-label="Next step">
          ›
        </button>
        <button type="button" className={btn} onClick={() => go(last)} disabled={step === last} aria-label="Last step">
          ⏭
        </button>
        <span className="mx-1 text-[12px] tabular-nums text-slate-400" data-testid="replay-step">
          Step {step + 1} of {nav.length}
        </span>
        <span className="ml-auto flex gap-1.5">
          <button type="button" className={btn} disabled={prevDecision === undefined} onClick={() => prevDecision !== undefined && go(prevDecision)}>
            ← Your previous decision
          </button>
          <button type="button" className={btn} disabled={nextDecision === undefined} onClick={() => nextDecision !== undefined && go(nextDecision)}>
            Your next decision →
          </button>
        </span>
      </div>

      <div className="grid gap-3 lg:grid-cols-[minmax(0,15rem)_minmax(0,1fr)]">
        <ol className="max-h-[28rem] space-y-2 overflow-y-auto pr-1 text-[12.5px]" aria-label="Action timeline" data-testid="replay-timeline">
          {groups.map((g, gi) => (
            <li key={`${g.street}-${gi}`}>
              <div className="text-[10.5px] font-semibold uppercase tracking-wide text-slate-400">{STREET_LABEL[g.street] ?? g.street}</div>
              <ol className="mt-0.5 space-y-0.5">
                {g.items.map(({ i, f }) => {
                  const d = f.decision !== null ? decisions[f.decision] : undefined;
                  return (
                    <li key={i}>
                      <button
                        type="button"
                        onClick={() => {
                          setPlaying(false);
                          go(i);
                        }}
                        aria-current={i === step ? "step" : undefined}
                        className={`flex w-full items-center gap-1.5 rounded-md px-1.5 py-0.5 text-left ${
                          i === step ? "bg-white/10 text-slate-50" : "text-slate-300 hover:bg-white/5"
                        } ${d ? "font-medium" : ""}`}
                      >
                        <span className="min-w-0 flex-1">{f.text}</span>
                        {d && <GradeChip grade={d.grade} label={d.grade_label} />}
                      </button>
                    </li>
                  );
                })}
              </ol>
            </li>
          ))}
        </ol>

        <div className="min-w-0" aria-live="polite">
          {decision ? (
            <DecisionPanel
              d={decision}
              record={record}
              frame={frame}
              detailed={detailed}
              onToggleDetailed={onToggleDetailed}
              onPractice={onPractice}
              findings={findings?.(frame.decision!) ?? []}
              onShowLeak={onShowLeak}
              onTrainLeak={onTrainLeak}
            />
          ) : step === last ? (
            <ResultPanel record={record} net={net} bb={bb} />
          ) : (
            <div className="rounded-xl bg-white/[0.03] p-3 text-[12.5px] text-slate-300 ring-1 ring-white/5">
              <p>{frame.text}</p>
              <p className="mt-1 text-slate-400">
                {decisions.length
                  ? "Your decisions are marked with their grade in the timeline. Step to one to see what you knew then and how the coach judged it."
                  : record.coaching
                    ? "You made no decisions in this hand."
                    : "This hand has no coaching review saved."}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function DecisionPanel({
  d,
  record,
  frame,
  detailed,
  onToggleDetailed,
  onPractice,
  findings = [],
  onShowLeak,
  onTrainLeak,
}: {
  d: DecisionReview;
  record: HandRecord;
  frame: HandFrame;
  detailed: boolean;
  onToggleDetailed: () => void;
  onPractice?: (d: DecisionReview) => void;
  findings?: LeakItem[];
  onShowLeak?: (key: string) => void;
  onTrainLeak?: (item: LeakItem) => void;
}) {
  return (
    <section className="rounded-xl bg-white/[0.03] p-3 ring-1 ring-white/5" data-testid="replay-decision" aria-label="Your decision">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-[13.5px] font-semibold text-slate-100">Your decision: {frame.text}</h3>
        <GradeChip grade={d.grade} label={d.grade_label} />
        <label className="ml-auto flex items-center gap-1.5 text-[11.5px] text-slate-400">
          <input type="checkbox" checked={detailed} onChange={onToggleDetailed} />
          Show the maths
        </label>
      </div>
      <p className="mt-1 text-[11.5px] text-slate-400" data-testid="decision-time-note">
        Judged with only what you could see then: your cards, {d.board.length ? `the ${d.street} board, ` : ""}the pot of {d.pot}
        {d.to_call ? ` with ${d.to_call} to call` : ""} and the betting so far. Later cards
        {record.coaching?.hindsight ? " and the opponent's shown cards" : ""} were not used, and the result of the hand does not change the grade.
      </p>
      <div className="mt-2">
        <DecisionDetail review={d} detailed={detailed} />
      </div>
      {findings.map((f) => (
        <div key={f.key} className="mt-3 rounded-lg bg-amber-500/10 p-2.5 text-[12px] ring-1 ring-amber-400/25" data-testid="decision-finding">
          <p className="text-amber-100">
            {f.status === "leak" ? (
              <>
                <b className="font-semibold">Part of a supported weakness:</b> {f.title.toLowerCase()} ({f.flagged} of {f.opportunities}{" "}
                such decisions flagged).
              </>
            ) : (
              <>
                <b className="font-semibold">Early signal, not supported yet:</b> {f.title.toLowerCase()} ({f.flagged} flagged of{" "}
                {f.opportunities}).
              </>
            )}
          </p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {onShowLeak && (
              <button type="button" className="btn-dark rounded-md px-2 py-1 text-[11.5px] font-semibold" onClick={() => onShowLeak(f.key)}>
                See the evidence
              </button>
            )}
            {onTrainLeak && f.status === "leak" && (
              <button type="button" className="btn-gold rounded-md px-2 py-1 text-[11.5px] font-bold" onClick={() => onTrainLeak(f)}>
                Practise this leak
              </button>
            )}
          </div>
        </div>
      ))}
      {onPractice && d.spot_data && (
        <button type="button" onClick={() => onPractice(d)} className="btn-dark mt-3 rounded-lg px-3 py-1.5 text-[12.5px] font-semibold">
          Practise this spot
        </button>
      )}
    </section>
  );
}

function ResultPanel({ record, net, bb }: { record: HandRecord; net: number; bb: number }) {
  const c = record.coaching;
  return (
    <section className="rounded-xl bg-white/[0.03] p-3 text-[12.5px] ring-1 ring-white/5" data-testid="replay-result">
      <h3 className="text-[13.5px] font-semibold text-slate-100">Result and decisions, kept apart</h3>
      <p className="mt-1 text-slate-300">
        {net > 0 ? `You won ${net} chips` : net < 0 ? `You lost ${-net} chips` : "You broke even"} ({(net / bb).toFixed(1)} bb).
      </p>
      {c && <p className="mt-1 text-slate-300">{c.verdict}</p>}
      {c?.hindsight && <p className="mt-1 text-slate-400">{c.hindsight}</p>}
      {c && c.decisions.length > 0 && (
        <ul className="mt-2 space-y-1">
          {c.decisions.map((d, i) => (
            <li key={i} className="flex items-center gap-2">
              <GradeChip grade={d.grade} label={d.grade_label} />
              <span className="text-slate-300">
                {STREET_LABEL[d.street] ?? d.street}: {d.lesson && "principle" in d.lesson ? d.lesson.principle : d.headline}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
