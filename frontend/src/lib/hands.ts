/**
 * Hand histories on the client (v0.4.3).
 *
 * The server builds each finished hand's record (public information only) and stores it
 * in the profile's database. The client's part is small:
 *
 * - **Queue.** A finished hand's record is queued on this device at once, then uploaded
 *   with the next sync. If the server is unreachable, it waits here (per profile, tagged
 *   with the progress epoch so a reset discards it). Records the server refuses are moved
 *   to the quarantine, not deleted silently.
 * - **Search and filters** over the hand summaries the server returns.
 */
import { profileApi } from "./api";
import { quarantine, type StorageError } from "./store";
import type { HandRecord, HandSummary } from "../types";

export const HANDS_PREFIX = "pokerlab.hands.pending.v1.";
/** Hands kept waiting on one device; beyond this the oldest waiting hand is dropped. */
export const PENDING_LIMIT = 200;
const BATCH = 50;

interface PendingDoc {
  epoch: string;
  hands: HandRecord[];
}

function readPending(profileKey: string): PendingDoc | null {
  try {
    const raw = localStorage.getItem(HANDS_PREFIX + profileKey);
    if (!raw) return null;
    const d = JSON.parse(raw);
    if (!d || typeof d !== "object" || typeof d.epoch !== "string" || !Array.isArray(d.hands)) return null;
    const hands = d.hands.filter(
      (h: unknown): h is HandRecord =>
        !!h && typeof h === "object" && typeof (h as HandRecord).id === "string" && Array.isArray((h as HandRecord).events),
    );
    return { epoch: d.epoch, hands };
  } catch {
    return null;
  }
}

function writePending(profileKey: string, doc: PendingDoc): StorageError | null {
  try {
    if (doc.hands.length) localStorage.setItem(HANDS_PREFIX + profileKey, JSON.stringify(doc));
    else localStorage.removeItem(HANDS_PREFIX + profileKey);
    return null;
  } catch (e) {
    const name = e instanceof DOMException ? e.name : "";
    return name === "QuotaExceededError" || name === "NS_ERROR_DOM_QUOTA_REACHED" ? "quota" : "unavailable";
  }
}

/** Hands waiting to be saved on the server for this profile and epoch. */
export function pendingHands(profileKey: string, epoch: string): HandRecord[] {
  const d = readPending(profileKey);
  return d && d.epoch === epoch ? d.hands : [];
}

/** Queue a finished hand (a repeat of the same id is ignored). */
export function queueHand(profileKey: string, epoch: string, record: HandRecord): StorageError | null {
  const d = readPending(profileKey);
  const hands = d && d.epoch === epoch ? d.hands : [];
  if (hands.some((h) => h.id === record.id)) return null;
  return writePending(profileKey, { epoch, hands: [...hands, record].slice(-PENDING_LIMIT) });
}

export function clearPendingHands(profileKey: string): void {
  writePending(profileKey, { epoch: "", hands: [] });
}

/**
 * Upload waiting hands. Accepted ones (and repeats the server already has) leave the
 * queue; refused ones go to the quarantine with the server's reason. Network errors
 * propagate, leaving the queue as it was.
 */
export async function flushHands(token: string, profileKey: string, epoch: string): Promise<{ saved: number; refused: number }> {
  let saved = 0;
  let refused = 0;
  for (;;) {
    const waiting = pendingHands(profileKey, epoch);
    if (!waiting.length) break;
    const batch = waiting.slice(0, BATCH);
    const res = await profileApi.uploadHands(token, batch);
    const bad = new Map(res.rejected.filter((r) => r.id).map((r) => [r.id as string, r.error]));
    quarantine(
      batch.filter((h) => bad.has(h.id)).map((h) => ({ reason: `hand refused by the server: ${bad.get(h.id)}`, record: h })),
      Date.now(),
    );
    refused += bad.size;
    saved += batch.length - bad.size;
    // Re-read: another tab may have queued more meanwhile.
    const done = new Set(batch.map((h) => h.id));
    const rest = pendingHands(profileKey, epoch).filter((h) => !done.has(h.id));
    writePending(profileKey, { epoch, hands: rest });
    if (res.rejected.some((r) => !r.id)) break; // unidentifiable refusals: stop rather than loop
  }
  return { saved, refused };
}

// ---------------------------------------------------------------- search and filters

export const LARGE_POT_BB = 20;

export type Strength = "premium" | "strong" | "playable" | "weak";
export const STRENGTH_LABEL: Record<Strength, string> = {
  premium: "Premium (top 6%)",
  strong: "Strong (top 6–20%)",
  playable: "Playable (top 20–50%)",
  weak: "Weak (bottom half)",
};

/** Starting-hand strength by its rank among all 1,326 starting hands. */
export function strengthOf(percentile: number): Strength {
  if (percentile < 0.06) return "premium";
  if (percentile < 0.2) return "strong";
  if (percentile < 0.5) return "playable";
  return "weak";
}

export interface HistoryFilters {
  query: string;
  result: "all" | "won" | "lost" | "split";
  strength: "all" | Strength;
  largePots: boolean;
  flagged: boolean;
  showdown: "all" | "yes" | "no";
  /** Inclusive local dates, "YYYY-MM-DD" or "". */
  from: string;
  to: string;
}

export const NO_FILTERS: HistoryFilters = {
  query: "",
  result: "all",
  strength: "all",
  largePots: false,
  flagged: false,
  showdown: "all",
  from: "",
  to: "",
};

function localDay(t: number): string {
  const d = new Date(t);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/**
 * Whether a hand matches a search term: a hand number ("#12" or "12"), a starting hand
 * ("AK" matches AKs and AKo, "AKs" only suited), a card ("Kh", in your hand or on the
 * board), or words of your final hand ("flush", "two pair"). Every term must match.
 */
export function matchesQuery(h: HandSummary, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return q.split(/[\s,]+/).every((term) => {
    if (!term) return true;
    const num = term.match(/^#?(\d+)$/);
    if (num) return h.hand_number === Number(num[1]);
    const cls = h.hand_class.toLowerCase();
    if (/^[2-9tjqka]{2}[so]?$/.test(term) && (cls === term || (term.length === 2 && cls.startsWith(term)))) return true;
    const cards = [...h.hero_cards, ...h.board].map((c) => c.toLowerCase());
    if (/^[2-9tjqka][cdhs]$/.test(term)) return cards.includes(term);
    return (h.final_hand ?? "").toLowerCase().includes(term) || (h.final_category ?? "").toLowerCase().includes(term);
  });
}

export function filterHands(hands: HandSummary[], f: HistoryFilters): HandSummary[] {
  return hands.filter((h) => {
    if (f.result !== "all" && h.result !== f.result && !(f.result === "split" && h.result === "even")) return false;
    if (f.strength !== "all" && strengthOf(h.hand_percentile) !== f.strength) return false;
    if (f.largePots && h.pot_bb < LARGE_POT_BB) return false;
    if (f.flagged && h.flagged === 0) return false;
    if (f.showdown !== "all" && h.showdown !== (f.showdown === "yes")) return false;
    const day = localDay(h.t);
    if (f.from && day < f.from) return false;
    if (f.to && day > f.to) return false;
    return matchesQuery(h, f.query);
  });
}

export function activeFilterCount(f: HistoryFilters): number {
  return (Object.keys(NO_FILTERS) as (keyof HistoryFilters)[]).filter((k) => f[k] !== NO_FILTERS[k]).length;
}
