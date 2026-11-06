import { AnimatePresence, motion } from "motion/react";
import { formatChips } from "../lib/betting";
import type { PlayerView } from "../types";
import { PlayingCard } from "./PlayingCard";

interface Props {
  player: PlayerView;
  isButton: boolean;
  isTurn: boolean;
  thinking: boolean;
  isWinner: boolean;
  winAmount: number;
  position: "top" | "bottom";
  handNumber: number;
  handOver: boolean;
}

function actionLabel(p: PlayerView): string | null {
  switch (p.last_action) {
    case null:
      return null;
    case "raise":
      return `Raise ${formatChips(p.bet)}`;
    case "bet":
      return `Bet ${formatChips(p.bet)}`;
    case "all-in":
      return "All-in";
    default:
      return p.last_action[0].toUpperCase() + p.last_action.slice(1);
  }
}

export function Seat({
  player,
  isButton,
  isTurn,
  thinking,
  isWinner,
  winAmount,
  position,
  handNumber,
  handOver,
}: Props) {
  const label = handOver ? null : actionLabel(player);
  const holeCount = player.hole?.length ?? 0;
  const cards = player.has_cards || holeCount > 0 ? [0, 1] : [];
  const faceUp = holeCount > 0;
  const cardsRow = (
    <div className={`flex justify-center ${player.is_human ? "-space-x-3" : "-space-x-4"}`}>
      {cards.map((i) => (
        <PlayingCard
          key={`${handNumber}-${i}`}
          code={player.hole?.[i]}
          faceUp={faceUp}
          size={player.is_human ? "lg" : "md"}
          delay={i * 0.12 + (player.is_human ? 0.06 : 0)}
          dimmed={player.folded}
        />
      ))}
    </div>
  );

  return (
    <div
      className={`flex items-center gap-2 ${position === "top" ? "flex-row" : "flex-col-reverse"}`}
      data-testid={`seat-${player.seat}`}
    >
      <div className="relative">
        <motion.div
          className={`relative flex min-w-[10.5rem] items-center gap-3 rounded-2xl bg-slate-950/80 px-3 py-2 ring-1 backdrop-blur sm:min-w-[13rem] ${
            isTurn ? "turn-glow ring-transparent" : isWinner ? "ring-amber-300/70" : "ring-white/10"
          } ${player.folded ? "opacity-60" : ""}`}
          animate={isWinner ? { scale: [1, 1.05, 1] } : { scale: 1 }}
          transition={{ duration: 0.6 }}
        >
          <div
            className={`grid h-10 w-10 shrink-0 place-items-center rounded-full text-lg font-bold sm:h-11 sm:w-11 ${
              player.is_human
                ? "bg-gradient-to-br from-sky-400 to-indigo-600 text-white"
                : "bg-gradient-to-br from-amber-300 to-orange-600 text-slate-950"
            }`}
            aria-hidden
          >
            {player.is_human ? player.name.slice(0, 1).toUpperCase() : "🤖"}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="truncate text-sm font-semibold text-slate-100">{player.name}</span>
              {player.all_in && (
                <span className="rounded bg-rose-500/90 px-1.5 text-[10px] font-bold uppercase tracking-wide text-white">
                  All-in
                </span>
              )}
            </div>
            <div className="text-base font-bold tabular-nums text-amber-200" data-testid={`stack-${player.seat}`}>
              {formatChips(player.stack)}
            </div>
          </div>
          {isButton && (
            <motion.div
              layoutId="dealer-button"
              className="absolute -right-3 -top-3 grid h-7 w-7 place-items-center rounded-full bg-gradient-to-b from-white to-slate-300 text-xs font-black text-slate-900 shadow-lg ring-2 ring-slate-500/40"
              title="Dealer button"
            >
              D
            </motion.div>
          )}
        </motion.div>

        <AnimatePresence>
          {(thinking || label) && (
            <motion.div
              key={thinking ? "thinking" : label}
              initial={{ opacity: 0, y: position === "top" ? -6 : 6, scale: 0.9 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, scale: 0.9 }}
              className={`absolute left-1/2 z-10 -translate-x-1/2 whitespace-nowrap rounded-full px-3 py-1 text-xs font-semibold shadow-lg ${
                position === "top" ? "-bottom-4" : "-top-4"
              } ${
                thinking
                  ? "bg-slate-800 text-slate-200 ring-1 ring-white/10"
                  : player.last_action === "fold"
                    ? "bg-slate-700 text-slate-300"
                    : player.last_action === "all-in" || player.last_action === "raise" || player.last_action === "bet"
                      ? "bg-amber-400 text-slate-950"
                      : "bg-emerald-500 text-emerald-950"
              }`}
            >
              {thinking ? (
                <span className="flex items-center gap-1" aria-label="Thinking">
                  Thinking
                  <span className="thinking-dot">.</span>
                  <span className="thinking-dot [animation-delay:150ms]">.</span>
                  <span className="thinking-dot [animation-delay:300ms]">.</span>
                </span>
              ) : (
                label
              )}
            </motion.div>
          )}
        </AnimatePresence>

        <AnimatePresence>
          {isWinner && winAmount > 0 && (
            <motion.div
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.5 }}
              className="pointer-events-none absolute right-full top-1/2 z-20 mr-3 -translate-y-1/2 whitespace-nowrap text-lg font-black text-amber-300 drop-shadow-[0_2px_6px_rgba(0,0,0,0.8)]"
              data-testid={`win-${player.seat}`}
            >
              +{formatChips(winAmount)}
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <div
        className={`flex flex-col items-center gap-1 ${
          player.is_human ? "min-h-[calc(var(--card-w-lg)*1.4)]" : "min-w-[calc(var(--card-w)*1.6)]"
        }`}
      >
        {cardsRow}
        <div className="flex h-5 items-center">
          {player.hand_description && !player.folded && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="whitespace-nowrap rounded-md bg-black/40 px-2 py-0.5 text-xs font-medium text-emerald-200"
              data-testid={`hand-desc-${player.seat}`}
            >
              {player.hand_description}
            </motion.div>
          )}
        </div>
      </div>
    </div>
  );
}
