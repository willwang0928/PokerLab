import { AnimatePresence, motion } from "motion/react";
import { AI, HUMAN } from "../hooks/usePokerGame";
import { formatChips } from "../lib/betting";
import type { GameEvent, GameState } from "../types";
import { ChipStack } from "./ChipStack";
import { CardSlot, PlayingCard } from "./PlayingCard";
import { Seat } from "./Seat";

interface Props {
  state: GameState;
  lastEvent: GameEvent | null;
  thinkingSeat: number | null;
  humanTurn: boolean;
}

/** Chips each seat receives right now (during the award animation or after the hand). */
function currentPayouts(state: GameState, lastEvent: GameEvent | null): Record<number, number> {
  if (lastEvent?.type === "pot_awarded") {
    const p = lastEvent.payouts as Record<string, number>;
    return Object.fromEntries(Object.entries(p).map(([k, v]) => [Number(k), v]));
  }
  if (state.status !== "in_hand" && state.result) {
    return Object.fromEntries(
      Object.entries(state.result.winnings).map(([k, v]) => [Number(k), v]),
    );
  }
  return {};
}

export function Table({ state, lastEvent, thinkingSeat, humanTurn }: Props) {
  const human = state.players[HUMAN];
  const ai = state.players[AI];
  const payouts = currentPayouts(state, lastEvent);
  const awarding = lastEvent?.type === "pot_awarded";
  const winners = Object.keys(payouts).map(Number);

  return (
    <div className="mx-auto flex w-full max-w-[min(980px,max(340px,calc((100vh-370px)*2.15)))] flex-col items-center">
      <div className="relative z-10 -mb-8">
        <Seat
          player={ai}
          position="top"
          isButton={state.button === AI}
          isTurn={thinkingSeat === AI}
          thinking={thinkingSeat === AI}
          isWinner={winners.includes(AI)}
          winAmount={payouts[AI] ?? 0}
          handNumber={state.hand_number}
          handOver={state.status !== "in_hand"}
        />
      </div>

      <div className="table-rail relative aspect-[1.55/1] w-full p-[10px] sm:aspect-[2.15/1] sm:p-[16px]">
        <div className="table-felt relative flex h-full w-full flex-col items-center justify-center pb-[calc(var(--card-w-lg)*0.55+22px)]">
          <div className="pointer-events-none absolute inset-0 grid place-items-center">
            <span className="font-display text-[clamp(1.6rem,5vw,3.6rem)] italic tracking-wide text-black/15">
              PokerLab
            </span>
          </div>

          {/* opponent's bet */}
          <div className="absolute right-[calc(50%+max(64px,var(--card-w)*1.3))] top-[14%] h-7">
            <AnimatePresence>
              {ai.bet > 0 && <ChipStack key="ai-bet" amount={ai.bet} />}
            </AnimatePresence>
          </div>

          <div className="relative flex flex-col items-center gap-2 sm:gap-3">
            <div className="grid h-8 place-items-center">
              <ResultBanner state={state} />
              <AnimatePresence mode="popLayout">
                {state.collected_pot > 0 && !awarding && (
                  <motion.div
                    key="pot"
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0 }}
                    className="flex items-center gap-2 rounded-full bg-black/45 px-3 py-1 text-sm font-semibold text-amber-100 ring-1 ring-amber-200/20"
                    data-testid="pot"
                  >
                    <span className="text-[11px] uppercase tracking-widest text-amber-200/70">Pot</span>
                    <span className="tabular-nums">{formatChips(state.collected_pot)}</span>
                  </motion.div>
                )}
                {awarding && (
                  <motion.div
                    key={`award-${String(lastEvent?.pot_index)}`}
                    initial={{ opacity: 1, y: 0 }}
                    animate={{
                      opacity: 0,
                      y: (lastEvent?.winners as number[])?.[0] === AI ? -140 : 140,
                    }}
                    transition={{ duration: 0.9, ease: "easeIn" }}
                  >
                    <ChipStack amount={Number(lastEvent?.amount ?? 0)} />
                  </motion.div>
                )}
              </AnimatePresence>
            </div>

            <div className="flex gap-1.5 sm:gap-2.5" data-testid="board">
              {Array.from({ length: 5 }, (_, i) => {
                const code = state.board[i];
                return code ? (
                  <PlayingCard key={`${state.hand_number}-${code}`} code={code} delay={(i % 3) * 0.1} />
                ) : (
                  <CardSlot key={`slot-${i}`} />
                );
              })}
            </div>

          </div>

          {/* player's bet */}
          <div className="absolute bottom-[14%] left-[calc(50%+max(64px,var(--card-w-lg)*1.15))] h-7">
            <AnimatePresence>
              {human.bet > 0 && <ChipStack key="human-bet" amount={human.bet} />}
            </AnimatePresence>
          </div>
        </div>
      </div>

      <div className="relative z-10 -mt-[calc(var(--card-w-lg)*0.9+22px)]">
        <Seat
          player={human}
          position="bottom"
          isButton={state.button === HUMAN}
          isTurn={humanTurn}
          thinking={false}
          isWinner={winners.includes(HUMAN)}
          winAmount={payouts[HUMAN] ?? 0}
          handNumber={state.hand_number}
          handOver={state.status !== "in_hand"}
        />
      </div>
    </div>
  );
}

function ResultBanner({ state }: { state: GameState }) {
  const r = state.result;
  const show = state.status !== "in_hand" && r;
  let title = "";
  let subtitle = "";
  if (r) {
    const seats = Object.keys(r.winnings).map(Number);
    const pot = Object.values(r.winnings).reduce((a, b) => a + b, 0);
    const hand = r.pots[0]?.hand;
    if (seats.length > 1) {
      title = `Split pot · ${formatChips(pot)}`;
      subtitle = hand ?? "";
    } else {
      const p = state.players[seats[0]];
      const you = p.is_human && p.name.toLowerCase() === "you";
      title = `${you ? "You win" : `${p.name} wins`} ${formatChips(pot)}`;
      subtitle = r.uncontested ? "Opponent folded" : (hand ?? "");
    }
  }
  return (
    <AnimatePresence>
      {show && (
        <motion.div
          key={`result-${state.hand_number}`}
          initial={{ opacity: 0, scale: 0.85, y: 6 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.9 }}
          transition={{ type: "spring", stiffness: 300, damping: 22 }}
          className="flex items-center gap-2 whitespace-nowrap rounded-full bg-gradient-to-r from-amber-300 via-amber-200 to-amber-400 px-4 py-1.5 text-slate-950 shadow-[0_8px_30px_-6px_rgba(242,200,107,0.6)]"
          role="status"
          data-testid="result-banner"
        >
          <span className="text-sm font-extrabold sm:text-base">{title}</span>
          {subtitle && <span className="text-xs font-medium opacity-75 sm:text-sm">· {subtitle}</span>}
        </motion.div>
      )}
    </AnimatePresence>
  );
}
