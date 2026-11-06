import type { LegalView } from "../types";

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/**
 * "Raise to" amount for a pot-fraction sizing. A pot-sized raise is the current bet plus
 * the pot after calling; a pot-sized bet (no bet yet) is simply the pot.
 */
export function potSizedRaiseTo(
  legal: LegalView,
  pot: number,
  currentBet: number,
  fraction: number,
): number {
  const potAfterCall = pot + legal.call_amount;
  const target = Math.round(currentBet + fraction * potAfterCall);
  return clamp(target, legal.min_raise_to, legal.max_raise_to);
}

export function formatChips(n: number): string {
  return n.toLocaleString("en-US");
}
