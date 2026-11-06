import { AnimatePresence, motion } from "motion/react";
import { type Ref, useCallback, useRef } from "react";
import { AI, HUMAN } from "../hooks/usePokerGame";
import { formatChips } from "../lib/betting";
import type { GameEvent, GameState } from "../types";
import { ChipStack } from "./ChipStack";
import { boardTiming, canTravel, DeckContext, durationScale, TIMING, useMotionPrefs } from "../lib/motion";
import { type Anchor, ChipFlights, useLanding } from "./ChipFlights";
import { CardBack, CardSlot, PlayingCard } from "./PlayingCard";
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

/** Cards that make the winning hand: the pot being awarded, or the main pot once the hand is over. */
export function winningCards(state: GameState, lastEvent: GameEvent | null): Set<string> | null {
  if (lastEvent?.type === "pot_awarded") {
    const cards = (lastEvent.cards as string[] | undefined) ?? [];
    return cards.length ? new Set(cards) : null;
  }
  if (state.status !== "in_hand" && state.result && !state.result.uncontested) {
    const cards = state.result.pots[0]?.cards ?? [];
    return cards.length ? new Set(cards) : null;
  }
  return null;
}

/** Events after which the betting round is over, so last-action tags are stale. */
const ROUND_OVER = new Set(["bets_collected", "street", "reveal", "showdown", "pot_awarded"]);

/** Seconds until newly dealt cards have turned over (so a made-hand tag doesn't spoil them). */
function revealLag(lastEvent: GameEvent | null): number {
  if (lastEvent?.type === "street") {
    const last = lastEvent.street === "flop" ? 2 : 3;
    const t = boardTiming(last);
    return t.delay + t.revealAfter + TIMING.flipDuration * 0.7;
  }
  if (lastEvent?.type === "reveal" || lastEvent?.type === "showdown") return TIMING.flipDuration;
  return 0;
}

export function Table({ state, lastEvent, thinkingSeat, humanTurn }: Props) {
  const human = state.players[HUMAN];
  const ai = state.players[AI];
  const payouts = currentPayouts(state, lastEvent);
  const awarding = lastEvent?.type === "pot_awarded";
  const winners = Object.keys(payouts).map(Number);
  const highlight = winningCards(state, lastEvent);
  const deckRef = useRef<HTMLDivElement>(null);
  const prefs = useMotionPrefs();
  const travel = canTravel(prefs);
  const flightMs = travel ? TIMING.chipFlight * durationScale(prefs) * 1000 : 0;

  // Elements chips fly between.
  const stackRefs = [useRef<HTMLDivElement>(null), useRef<HTMLDivElement>(null)];
  const betRefs = [useRef<HTMLDivElement>(null), useRef<HTMLDivElement>(null)];
  const potRef = useRef<HTMLDivElement>(null);
  const anchors = useCallback((a: Anchor): HTMLElement | null => {
    if (a === "pot") return potRef.current;
    const [kind, seat] = a.split("-");
    return (kind === "stack" ? stackRefs : betRefs)[Number(seat)]?.current ?? null;
    // Refs are stable, so this never needs to change.
  }, []);
  // Chips land in the pot a moment after they leave the bet area.
  const shownPot = useLanding(state.collected_pot, flightMs);
  const descDelay = travel ? revealLag(lastEvent) * durationScale(prefs) : 0;
  const hideActions = ROUND_OVER.has(lastEvent?.type ?? "");
  const sweeping = travel && (lastEvent?.type === "bets_collected" || lastEvent?.type === "pot_awarded");
  const betStack = (seat: number, amount: number, tagFirst: boolean) => (
    <ChipStack
      key={`bet-${seat}`}
      amount={amount}
      tagFirst={tagFirst}
      enterDelay={flightMs / 1000}
      exitDuration={sweeping ? 0.06 : undefined}
    />
  );
  // Heads-up the deal starts with the player who is not on the button.
  const dealOrder = (seat: number) => (seat === state.button ? 1 : 0);

  return (
    <DeckContext value={deckRef}>
    <div className="relative mx-auto flex w-full max-w-[min(1000px,max(340px,calc((100vh-360px)*2.1)))] flex-col items-center">
      <div className="relative z-10 -mb-[34px]">
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
          dealOrder={dealOrder(AI)}
          stackRef={stackRefs[AI]}
          hideAction={hideActions}
          descDelay={descDelay}
          actionEvent={lastEvent?.type === "action" && lastEvent.seat === AI ? lastEvent : null}
          highlight={highlight}
        />
      </div>

      <div
        className="table-rail relative aspect-[1.3/1] w-full p-[var(--rail)] [--rail:12px] min-[480px]:aspect-[1.7/1] sm:aspect-[2.1/1] sm:[--rail:18px]"
      >
        <div className="table-felt relative flex h-full w-full flex-col items-center justify-center pb-[calc(var(--card-w-lg)*0.95)] pt-[22px]">
          {/* opponent's bet */}
          <div className="absolute right-[calc(50%+var(--card-w)*0.35)] top-[calc(34px+4%)] h-9">
            <div ref={betRefs[AI]} className="absolute right-0 top-0 h-9 w-[var(--chip)]" />
            <AnimatePresence>{ai.bet > 0 && betStack(AI, ai.bet, true)}</AnimatePresence>
          </div>

          <div className="relative flex flex-col items-center gap-2 sm:gap-3">
            <div ref={potRef} className="grid min-h-9 place-items-center">
              <ResultBanner state={state} />
              <AnimatePresence>
                {shownPot > 0 && !awarding && state.status === "in_hand" && (
                  <motion.div
                    key="pot"
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, transition: { duration: travel ? 0.06 : 0.2, delay: 0 } }}
                    className="col-start-1 row-start-1 flex items-center gap-2"
                    data-testid="pot"
                  >
                    <ChipStack amount={shownPot} columns={3} hideTag />
                    <span className="amount-tag flex items-baseline gap-1.5 px-3 py-1 text-sm font-bold text-amber-50 sm:text-base">
                      <span className="eyebrow text-amber-200/70">Pot</span>
                      <span className="tabular-nums">{formatChips(shownPot)}</span>
                    </span>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>

            <div className="relative" data-testid="board">
              <div className="flex gap-[calc(var(--card-w)*0.09)]">
                {Array.from({ length: 5 }, (_, i) => (
                  <CardSlot key={i} />
                ))}
              </div>
              <AnimatePresence>
                {state.board.map((code, i) => (
                  <PlayingCard
                    key={`${state.hand_number}-${code}`}
                    className="absolute top-0"
                    style={{ left: `calc(var(--card-w) * ${i * 1.09})` }}
                    code={code}
                    {...boardTiming(i)}
                    emphasis={highlight ? (highlight.has(code) ? "win" : "muted") : undefined}
                    exitTo={{ x: 0, y: -24 }}
                  />
                ))}
              </AnimatePresence>
              <Deck ref={deckRef} />
            </div>
          </div>

          {/* player's bet */}
          <div className="absolute bottom-[calc(var(--card-w-lg)*0.7)] left-[calc(50%+var(--card-w-lg)*1.05)] h-9">
            <div ref={betRefs[HUMAN]} className="absolute left-0 top-0 h-9 w-[var(--chip)]" />
            <AnimatePresence>{human.bet > 0 && betStack(HUMAN, human.bet, false)}</AnimatePresence>
          </div>
        </div>
      </div>

      <div className="relative z-10 -mt-[calc(var(--card-w-lg)*0.98+30px)]">
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
          dealOrder={dealOrder(HUMAN)}
          stackRef={stackRefs[HUMAN]}
          hideAction={hideActions}
          descDelay={descDelay}
          actionEvent={lastEvent?.type === "action" && lastEvent.seat === HUMAN ? lastEvent : null}
          highlight={highlight}
        />
      </div>
      <ChipFlights state={state} lastEvent={lastEvent} anchors={anchors} />
    </div>
    </DeckContext>
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
          className="col-start-1 row-start-1 flex max-w-[calc(100vw-56px)] items-center gap-2 overflow-hidden whitespace-nowrap rounded-full bg-gradient-to-b from-[#fbe3a1] via-[#f0c96a] to-[#d9a23c] px-4 py-1.5 text-[#1b1405] shadow-[inset_0_1px_0_rgba(255,255,255,.6),0_10px_30px_-8px_rgba(240,201,106,0.7)]"
          role="status"
          data-testid="result-banner"
        >
          <span className="text-sm font-extrabold sm:text-base">{title}</span>
          {subtitle && <span className="truncate text-xs font-medium opacity-75 sm:text-sm">· {subtitle}</span>}
        </motion.div>
      )}
    </AnimatePresence>
  );
}

/** A short stack of face-down cards beside the board that every card is dealt from. */
function Deck({ ref }: { ref: Ref<HTMLDivElement> }) {
  const w = "calc(var(--card-w) * 0.62)";
  return (
    <div
      className="pointer-events-none absolute left-full top-1/2 ml-[calc(var(--card-w)*0.2)] -translate-y-1/2 max-[379px]:opacity-0"
      aria-hidden
      data-testid="deck"
    >
      <div ref={ref} className="relative" style={{ width: w, aspectRatio: "5 / 7", "--w": w } as React.CSSProperties}>
        {[3, 2, 1, 0].map((i) => (
          <CardBack
            key={i}
            className="absolute inset-0"
            style={{ transform: `translate(${i * 1.2}px, ${-i * 1.2}px)`, boxShadow: i ? "none" : undefined }}
          />
        ))}
      </div>
    </div>
  );
}
