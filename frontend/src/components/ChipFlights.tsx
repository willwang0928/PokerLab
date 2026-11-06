import { motion } from "motion/react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { canTravel, centerIn, durationScale, TIMING, useMotionPrefs } from "../lib/motion";
import type { GameEvent, GameState } from "../types";
import { ChipStack } from "./ChipStack";

/** Places chips can travel between. */
export type Anchor = `stack-${number}` | `bet-${number}` | "pot";

interface Flight {
  id: number;
  from: { x: number; y: number };
  to: { x: number; y: number };
  amount: number;
  duration: number;
  delay: number;
}

interface Move {
  from: Anchor;
  to: Anchor;
  amount: number;
  slow?: boolean;
  delay?: number;
}

/**
 * Chip movements implied by one game event, worked out from the event payload and the
 * state before it. Exported for tests; the result mirrors what the backend reported.
 */
export function chipMoves(event: GameEvent, prev: GameState | null, next: GameState): Move[] {
  const seats = next.players.map((p) => p.seat);
  const prevBet = (s: number) => prev?.players[s]?.bet ?? 0;
  switch (event.type) {
    case "post_blind": {
      const s = Number(event.seat);
      return [{ from: `stack-${s}`, to: `bet-${s}`, amount: Number(event.amount) }];
    }
    case "action": {
      const s = Number(event.seat);
      const added = next.players[s].bet - prevBet(s);
      return added > 0 ? [{ from: `stack-${s}`, to: `bet-${s}`, amount: added }] : [];
    }
    case "uncalled_returned": {
      const s = Number(event.seat);
      return [{ from: `bet-${s}`, to: `stack-${s}`, amount: Number(event.amount) }];
    }
    case "bets_collected":
      return seats
        .filter((s) => prevBet(s) > 0)
        .map((s) => ({ from: `bet-${s}` as Anchor, to: "pot" as Anchor, amount: prevBet(s) }));
    case "pot_awarded": {
      const payouts = Object.entries(event.payouts as Record<string, number>).map(
        ([s, amt]) => [Number(s), amt] as const,
      );
      if (event.hand === null) {
        // Uncontested: the winner takes what is in the middle plus any bets still out.
        const [w] = payouts[0];
        const moves: Move[] = seats
          .filter((s) => prevBet(s) > 0)
          .map((s) => ({ from: `bet-${s}` as Anchor, to: `stack-${w}` as Anchor, amount: prevBet(s), slow: true }));
        const middle = prev?.collected_pot ?? 0;
        if (middle > 0) moves.push({ from: "pot", to: `stack-${w}`, amount: middle, slow: true });
        return moves;
      }
      return payouts
        .filter(([, amt]) => amt > 0)
        .map(([w, amt]) => ({ from: "pot" as Anchor, to: `stack-${w}` as Anchor, amount: amt, slow: true }));
    }
    default:
      return [];
  }
}

/**
 * Renders chips flying between seats, bet areas and the pot as events play out.
 * ``anchors`` resolves an anchor name to the element whose centre the chips travel to.
 */
export function ChipFlights({
  state,
  lastEvent,
  anchors,
}: {
  state: GameState;
  lastEvent: GameEvent | null;
  anchors: (a: Anchor) => HTMLElement | null;
}) {
  const prefs = useMotionPrefs();
  const ds = durationScale(prefs);
  const travel = canTravel(prefs);
  const overlayRef = useRef<HTMLDivElement>(null);
  const prevState = useRef<GameState | null>(null);
  const handled = useRef<GameEvent | null>(null);
  const nextId = useRef(0);
  const [flights, setFlights] = useState<Flight[]>([]);

  useLayoutEffect(() => {
    // StrictMode re-runs effects; handle each event exactly once.
    if (lastEvent === handled.current) return;
    handled.current = lastEvent;
    const prev = prevState.current;
    prevState.current = state;
    const overlay = overlayRef.current;
    if (!lastEvent || !travel || !overlay) return;
    const fresh: Flight[] = [];
    for (const m of chipMoves(lastEvent, prev, state)) {
      const a = anchors(m.from);
      const b = anchors(m.to);
      if (!a || !b || m.amount <= 0) continue;
      fresh.push({
        id: nextId.current++,
        from: centerIn(a, overlay),
        to: centerIn(b, overlay),
        amount: m.amount,
        duration: (m.slow ? TIMING.chipFlight * 1.45 : TIMING.chipFlight) * ds,
        delay: (m.delay ?? 0) * ds,
      });
    }
    if (fresh.length) setFlights((f) => [...f, ...fresh]);
  }, [lastEvent, state, travel, anchors, ds]);

  // A new game starts from scratch.
  useEffect(() => {
    setFlights([]);
    prevState.current = null;
  }, [state.session_id]);

  return (
    <div ref={overlayRef} className="pointer-events-none absolute inset-0 z-30" aria-hidden>
      {flights.map((f) => (
        <motion.div
          key={f.id}
          className="absolute left-0 top-0"
          initial={{ x: f.from.x, y: f.from.y, opacity: 1, scale: 1 }}
          animate={{ x: f.to.x, y: f.to.y, opacity: [1, 1, 0], scale: [1, 1.08, 0.9] }}
          transition={{
            duration: f.duration,
            delay: f.delay,
            ease: [0.3, 0.6, 0.25, 1],
            opacity: { duration: f.duration, delay: f.delay, times: [0, 0.82, 1] },
            scale: { duration: f.duration, delay: f.delay, times: [0, 0.4, 1] },
          }}
          onAnimationComplete={() => setFlights((all) => all.filter((x) => x.id !== f.id))}
          data-testid="chip-flight"
        >
          <div className="-translate-x-1/2 -translate-y-1/2">
            <ChipStack amount={f.amount} hideTag columns={f.amount >= 500 ? 2 : 1} still />
          </div>
        </motion.div>
      ))}
    </div>
  );
}

/**
 * ``value``, except that increases show up ``delayMs`` late (so the pot grows when the
 * flying chips land rather than when they leave). Decreases apply immediately.
 */
export function useLanding(value: number, delayMs: number): number {
  const [shown, setShown] = useState(value);
  useEffect(() => {
    if (value <= shown || delayMs <= 0) {
      setShown(value);
      return;
    }
    const t = setTimeout(() => setShown(value), delayMs);
    return () => clearTimeout(t);
    // Only react to the incoming value, not to our own update.
  }, [value, delayMs]);
  return shown;
}
