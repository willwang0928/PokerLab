import type { GameState } from "../types";
import { Modal } from "./Modal";
import { SuitIcon } from "./SuitIcon";

interface Props {
  state: GameState;
  onPlayAgain: () => void;
  onChangeSettings: () => void;
}

export function GameOverModal({ state, onPlayAgain, onChangeSettings }: Props) {
  const winner = state.winner !== null ? state.players[state.winner] : null;
  const humanWon = Boolean(winner?.is_human);
  return (
    <Modal labelledBy="gameover-title">
      <div className="flex flex-col items-center gap-4 text-center">
        <div
          className={`grid h-16 w-16 place-items-center rounded-2xl shadow-lg ring-1 ring-white/15 ${
            humanWon
              ? "bg-gradient-to-br from-amber-200 via-amber-400 to-amber-700 text-amber-950"
              : "bg-gradient-to-br from-slate-600 to-slate-800 text-slate-200"
          }`}
          aria-hidden
        >
          {humanWon ? (
            <svg viewBox="0 0 24 24" className="h-8 w-8" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0V4Z" />
              <path d="M17 6h3v1a3 3 0 0 1-3 3M7 6H4v1a3 3 0 0 0 3 3" />
            </svg>
          ) : (
            <SuitIcon suit="s" className="h-8 w-8" />
          )}
        </div>
        <h2 id="gameover-title" className="font-display text-3xl font-bold text-amber-50">
          {humanWon ? "You won the match!" : `${winner?.name ?? "The bot"} wins the match`}
        </h2>
        <p className="text-sm text-slate-400">
          {humanWon
            ? `You took every chip in ${state.hand_number} hands.`
            : `You were busted after ${state.hand_number} hands. Shuffle up and try again.`}
        </p>
        <div className="flex w-full flex-col gap-2 pt-2 sm:flex-row">
          <button
            type="button"
            onClick={onPlayAgain}
            className="btn-gold flex-1 rounded-xl py-3 font-bold"
          >
            Play again
          </button>
          <button
            type="button"
            onClick={onChangeSettings}
            className="btn-dark flex-1 rounded-xl py-3 font-semibold"
          >
            Change settings
          </button>
        </div>
      </div>
    </Modal>
  );
}
