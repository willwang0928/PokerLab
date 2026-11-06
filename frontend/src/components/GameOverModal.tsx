import type { GameState } from "../types";
import { Modal } from "./Modal";

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
        <div className="text-5xl" aria-hidden>
          {humanWon ? "🏆" : "🤖"}
        </div>
        <h2 id="gameover-title" className="font-display text-3xl font-bold text-amber-100">
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
            className="flex-1 rounded-xl bg-gradient-to-b from-amber-300 to-amber-500 py-3 font-bold text-slate-950 shadow-lg hover:from-amber-200 hover:to-amber-400"
          >
            Play again
          </button>
          <button
            type="button"
            onClick={onChangeSettings}
            className="flex-1 rounded-xl bg-slate-800 py-3 font-semibold text-slate-200 hover:bg-slate-700"
          >
            Change settings
          </button>
        </div>
      </div>
    </Modal>
  );
}
