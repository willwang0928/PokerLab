import { createContext, useContext, type RefObject } from "react";

/**
 * Shared animation preferences.
 *
 * ``scale`` follows the game-speed setting (0 = instant) and ``reduced`` mirrors the
 * user's ``prefers-reduced-motion`` setting. Components use these to decide whether a
 * travelling animation is worth playing and how long it should take.
 */
export interface MotionPrefs {
  scale: number;
  reduced: boolean;
}

export const MotionPrefsContext = createContext<MotionPrefs>({ scale: 1, reduced: false });

export function useMotionPrefs(): MotionPrefs {
  return useContext(MotionPrefsContext);
}

/** Whether things should visibly travel across the table (deals, chip flights). */
export function canTravel(p: MotionPrefs): boolean {
  return !p.reduced && p.scale > 0;
}

/** Duration multiplier: tracks the speed setting without letting motion become a flicker or a crawl. */
export function durationScale(p: MotionPrefs): number {
  if (p.scale === 0) return 0.5;
  return Math.min(1.3, Math.max(0.65, p.scale));
}

/** Where cards are dealt from (the deck on the felt). */
export const DeckContext = createContext<RefObject<HTMLElement | null> | null>(null);

/** Base timings (seconds) shared by the deal, the board and the step pacing. */
export const TIMING = {
  dealTravel: 0.38,
  dealGap: 0.13,
  flipDuration: 0.42,
  boardGap: 0.08,
  flipGap: 0.12,
  chipFlight: 0.42,
  exit: 0.3,
};

/** Centre of ``el`` relative to ``origin``'s top-left corner. */
export function centerIn(el: Element, origin: Element): { x: number; y: number } {
  const a = el.getBoundingClientRect();
  const o = origin.getBoundingClientRect();
  return { x: a.left - o.left + a.width / 2, y: a.top - o.top + a.height / 2 };
}
