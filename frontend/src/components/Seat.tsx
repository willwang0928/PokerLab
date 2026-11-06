import { AnimatePresence, motion } from "motion/react";
import { formatChips } from "../lib/betting";
import type { PlayerView } from "../types";
import { Chip } from "./ChipStack";
import { TIMING } from "../lib/motion";
import { type CardEmphasis, PlayingCard } from "./PlayingCard";

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
  /** 0 if this seat receives the first card of the deal, 1 otherwise. */
  dealOrder?: number;
  /** Cards that make the winning hand, once the pot is awarded at showdown. */
  highlight?: ReadonlySet<string> | null;
}

function actionLabel(p: PlayerView): string | null {
  switch (p.last_action) {
    case null:
      return null;
    // Once bets are swept into the pot the amount is gone; show the bare verb.
    case "raise":
      return p.bet > 0 ? `Raise ${formatChips(p.bet)}` : "Raise";
    case "bet":
      return p.bet > 0 ? `Bet ${formatChips(p.bet)}` : "Bet";
    case "all-in":
      return "All-in";
    default:
      return p.last_action[0].toUpperCase() + p.last_action.slice(1);
  }
}

function actionTone(action: string | null): string {
  switch (action) {
    case "fold":
      return "bg-slate-600 text-slate-100";
    case "bet":
    case "raise":
      return "btn-gold";
    case "all-in":
      return "bg-gradient-to-b from-rose-500 to-rose-700 text-white";
    default:
      return "bg-gradient-to-b from-emerald-400 to-emerald-600 text-emerald-950";
  }
}

export function Seat({
  player,
  isButton,
  isTurn,
  thinking,
  isWinner,
  winAmount,
  handNumber,
  handOver,
  position,
  dealOrder = 0,
  highlight = null,
}: Props) {
  const label = handOver ? null : actionLabel(player);
  const holeCount = player.hole?.length ?? 0;
  const cards = player.has_cards || holeCount > 0 ? [0, 1] : [];
  const faceUp = holeCount > 0;
  const lg = player.is_human;
  const cardW = lg ? "var(--card-w-lg)" : "var(--card-w)";
  // How much of the hole cards the nameplate covers.
  const overlap = lg ? 0.3 : 0.42;

  const handDesc = player.hand_description && !player.folded ? player.hand_description : null;

  return (
    <div className="relative flex flex-col items-center" data-testid={`seat-${player.seat}`}>
      {/* hole cards, fanned */}
      <div className="relative isolate" style={{ height: `calc(${cardW} * 1.4)`, width: `calc(${cardW} * 1.68)` }}>
        <AnimatePresence>
          {cards.map((i) => {
            const code = player.hole?.[i];
            const emphasis: CardEmphasis | undefined = player.folded
              ? "folded"
              : highlight && code
                ? highlight.has(code)
                  ? "win"
                  : "muted"
                : undefined;
            return (
              <PlayingCard
                key={`${handNumber}-${i}`}
                className="absolute bottom-0"
                style={{ left: i === 1 ? `calc(${cardW} * 0.68)` : 0 }}
                code={code}
                faceUp={faceUp}
                size={lg ? "lg" : "md"}
                delay={(i * 2 + dealOrder) * TIMING.dealGap}
                revealAfter={lg ? TIMING.dealTravel + 0.04 : undefined}
                flipDelay={i * TIMING.flipGap}
                emphasis={emphasis}
                tilt={i === 0 ? -5 : 5}
                exitTo={{ x: i === 0 ? 10 : -10, y: position === "top" ? 70 : -70 }}
              />
            );
          })}
        </AnimatePresence>
      </div>

      {/* nameplate */}
      <div className="relative z-10" style={{ marginTop: `calc(${cardW} * -1.4 * ${overlap})` }}>
        <motion.div
          className={`nameplate relative flex min-w-[9.75rem] items-center gap-2.5 rounded-2xl py-2 pl-2 pr-4 sm:min-w-[12rem] ${
            isTurn ? "turn-glow" : isWinner ? "ring-2 ring-amber-300/80" : ""
          } ${player.folded ? "opacity-60 saturate-50" : ""}`}
          animate={isWinner ? { scale: [1, 1.05, 1] } : { scale: 1 }}
          transition={{ duration: 0.6 }}
        >
          <Avatar player={player} />
          <div className="min-w-0 flex-1 leading-tight">
            <div className="flex items-center gap-1.5">
              <span className="truncate text-[13px] font-medium text-slate-300">{player.name}</span>
              {player.all_in && (
                <span className="rounded-[5px] bg-rose-500 px-1.5 py-px text-[9.5px] font-bold uppercase tracking-wider text-white">
                  All-in
                </span>
              )}
            </div>
            <div className="mt-0.5 flex items-center gap-1.5">
              <Chip color="#d99a1e" className="h-3.5 w-3.5 shrink-0" />
              <span
                className="text-[17px] font-bold tabular-nums tracking-tight text-amber-100 sm:text-lg"
                data-testid={`stack-${player.seat}`}
              >
                {formatChips(player.stack)}
              </span>
            </div>
          </div>
          {isButton && (
            <motion.div
              layoutId="dealer-button"
              className="dealer-button absolute -right-2.5 -top-2.5 grid h-6 w-6 place-items-center rounded-full text-[11px] font-black text-slate-800 sm:h-7 sm:w-7 sm:text-xs"
              title="Dealer button"
            >
              D
            </motion.div>
          )}
        </motion.div>

        {/* last action / thinking and made-hand tags, tucked under the plate */}
        <div className="absolute left-1/2 top-full z-10 -mt-2 flex -translate-x-1/2 items-center gap-1 whitespace-nowrap">
          <AnimatePresence mode="popLayout">
            {(thinking || label) && (
              <motion.div
                key={thinking ? "thinking" : label}
                initial={{ opacity: 0, y: -4, scale: 0.9 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, scale: 0.9 }}
                className={`rounded-full px-3 py-[3px] text-[11px] font-bold uppercase tracking-wider shadow-lg ${
                  thinking ? "bg-slate-800 text-slate-200 ring-1 ring-white/10" : actionTone(player.last_action)
                }`}
              >
                {thinking ? (
                  <span className="flex items-center gap-0.5" aria-label="Thinking">
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
          {handDesc && (
            <motion.div
              initial={{ opacity: 0, y: -4 }}
              animate={{ opacity: 1, y: 0 }}
              className="rounded-full bg-[#0b1a14] px-2.5 py-[3px] text-[11px] font-semibold text-emerald-200 shadow-lg ring-1 ring-emerald-300/30"
              data-testid={`hand-desc-${player.seat}`}
            >
              {handDesc}
            </motion.div>
          )}
        </div>

        <AnimatePresence>
          {isWinner && winAmount > 0 && (
            <motion.div
              initial={{ opacity: 0, x: 8 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.5 }}
              className="pointer-events-none absolute right-full top-1/2 z-20 mr-3 -translate-y-1/2 whitespace-nowrap rounded-full bg-black/55 px-2.5 py-1 text-base font-extrabold tabular-nums text-amber-300 shadow-lg ring-1 ring-amber-300/40 sm:text-lg"
              data-testid={`win-${player.seat}`}
            >
              +{formatChips(winAmount)}
            </motion.div>
          )}
        </AnimatePresence>

      </div>
    </div>
  );
}

function Avatar({ player }: { player: PlayerView }) {
  return (
    <div
      className={`grid h-10 w-10 shrink-0 place-items-center rounded-full ring-2 ring-black/40 sm:h-11 sm:w-11 ${
        player.is_human
          ? "bg-gradient-to-br from-sky-400 to-indigo-600 text-white"
          : "bg-gradient-to-br from-amber-200 via-amber-400 to-orange-600 text-slate-900"
      }`}
      style={{ boxShadow: "inset 0 1px 0 rgba(255,255,255,.35), 0 2px 6px rgba(0,0,0,.4)" }}
      aria-hidden
    >
      {player.is_human ? (
        <span className="text-lg font-bold">{player.name.slice(0, 1).toUpperCase()}</span>
      ) : (
        <svg viewBox="0 0 24 24" className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <rect x="4" y="8" width="16" height="11" rx="3" />
          <path d="M12 8V5M12 4.5h.01M2 13v2M22 13v2" />
          <circle cx="9" cy="13" r="1.3" fill="currentColor" stroke="none" />
          <circle cx="15" cy="13" r="1.3" fill="currentColor" stroke="none" />
          <path d="M9.5 16.3h5" />
        </svg>
      )}
    </div>
  );
}
