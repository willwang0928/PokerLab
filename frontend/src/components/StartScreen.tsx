import { useState } from "react";
import type { GameConfig } from "../types";
import { Modal } from "./Modal";

const STACKS = [500, 1000, 2000, 5000];
const BLINDS: [number, number][] = [
  [5, 10],
  [10, 20],
  [25, 50],
];

interface Props {
  initial: GameConfig;
  busy: boolean;
  error: string | null;
  onStart: (config: GameConfig) => void;
  onCancel?: () => void;
}

export function StartScreen({ initial, busy, error, onStart, onCancel }: Props) {
  const [name, setName] = useState(initial.player_name);
  const [stack, setStack] = useState(initial.starting_stack);
  const [blinds, setBlinds] = useState<[number, number]>([initial.small_blind, initial.big_blind]);

  return (
    <Modal labelledBy="start-title">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          onStart({
            player_name: name.trim() || "You",
            starting_stack: stack,
            small_blind: blinds[0],
            big_blind: blinds[1],
          });
        }}
        className="flex flex-col gap-5"
      >
        <div>
          <div className="mb-1 text-3xl" aria-hidden>
            ♠ ♥ ♦ ♣
          </div>
          <h1 id="start-title" className="font-display text-3xl font-bold text-amber-100">
            PokerLab
          </h1>
          <p className="mt-1 text-sm text-slate-400">
            Heads-up No-Limit Texas Hold'em against a computer opponent. Virtual chips only.
          </p>
        </div>

        <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-300">
          Your name
          <input
            value={name}
            maxLength={24}
            onChange={(e) => setName(e.target.value)}
            className="rounded-xl bg-slate-950 px-3 py-2.5 text-slate-100 ring-1 ring-white/10 focus:outline-none focus:ring-amber-300/60"
          />
        </label>

        <fieldset className="flex flex-col gap-1.5">
          <legend className="mb-1.5 text-sm font-medium text-slate-300">Starting stack</legend>
          <div className="grid grid-cols-4 gap-2">
            {STACKS.map((s) => (
              <Choice key={s} active={stack === s} onClick={() => setStack(s)}>
                {s.toLocaleString()}
              </Choice>
            ))}
          </div>
        </fieldset>

        <fieldset className="flex flex-col gap-1.5">
          <legend className="mb-1.5 text-sm font-medium text-slate-300">Blinds</legend>
          <div className="grid grid-cols-3 gap-2">
            {BLINDS.map(([sb, bb]) => (
              <Choice key={bb} active={blinds[1] === bb} onClick={() => setBlinds([sb, bb])}>
                {sb}/{bb}
              </Choice>
            ))}
          </div>
          <p className="text-xs text-slate-500">{Math.floor(stack / blinds[1])} big blinds deep</p>
        </fieldset>

        {error && (
          <p role="alert" className="rounded-xl bg-rose-500/10 px-3 py-2 text-sm text-rose-300 ring-1 ring-rose-400/30">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={busy}
          className="rounded-xl bg-gradient-to-b from-amber-300 to-amber-500 py-3 text-base font-bold text-slate-950 shadow-lg transition hover:from-amber-200 hover:to-amber-400 active:scale-[0.98] disabled:opacity-50"
        >
          {busy ? "Shuffling…" : onCancel ? "Start new game" : "Take a seat"}
        </button>
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            className="-mt-2 rounded-xl py-2 text-sm font-semibold text-slate-400 hover:text-slate-200"
          >
            Back to current game
          </button>
        )}
      </form>
    </Modal>
  );
}

function Choice({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`rounded-xl px-2 py-2 text-sm font-semibold tabular-nums transition ${
        active
          ? "bg-amber-300 text-slate-950"
          : "bg-slate-800 text-slate-200 ring-1 ring-white/5 hover:bg-slate-700"
      }`}
    >
      {children}
    </button>
  );
}
