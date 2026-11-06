import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../lib/api";
import type { ActionType, GameConfig, GameEvent, GameState, PlayResponse } from "../types";
import { SPEED_SCALE, type Settings } from "./useSettings";

export const HUMAN = 0;
export const AI = 1;

/**
 * Base pause (ms) after each kind of event so the table reads like a live deal. Each
 * value leaves just enough time for that event's animation to land (see lib/motion).
 */
const EVENT_DELAY: Record<string, number> = {
  hand_start: 320, // last hand's cards are collected
  post_blind: 300,
  deal_hole: 950, // four cards leave the deck one by one
  action: 650,
  uncalled_returned: 450,
  bets_collected: 480,
  reveal: 950,
  street: 850,
  showdown: 950, // opponent's cards turn over before the pot moves
  pot_awarded: 1250,
  hand_complete: 0,
};
/** The flop deals three cards and turns them over in sequence. */
const FLOP_DELAY = 1150;
const AI_THINK_MS = 700;
const AUTO_DEAL_MS = 4000;

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/**
 * What went wrong with the last request.
 * - ``rejected``: the server refused it (e.g. an illegal bet); the table is unchanged.
 * - ``resynced``: the table was out of step with the server and has been refreshed.
 * - ``network``: the server can't be reached; ``retry`` tries to reconnect.
 * - ``expired``: the server no longer has this game (e.g. it restarted).
 */
export interface GameError {
  kind: "rejected" | "resynced" | "network" | "expired";
  message: string;
}

export interface PokerGame {
  state: GameState | null;
  lastEvent: GameEvent | null;
  animating: boolean;
  pending: boolean;
  thinkingSeat: number | null;
  error: GameError | null;
  autoDealAt: number | null;
  newGame: (config: GameConfig) => Promise<void>;
  deal: () => Promise<void>;
  act: (action: ActionType, amount?: number) => Promise<void>;
  clearError: () => void;
  /** After a connection failure, fetch the current table from the server again. */
  retry: () => Promise<void>;
}

const NETWORK_MESSAGE = "Can't reach the PokerLab server. Check that it is running, then retry.";
const EXPIRED_MESSAGE = "This game is no longer on the server (it may have restarted). Start a new game to keep playing.";

/**
 * ``paused`` holds back automatic dealing, e.g. while a dialog covers the table.
 */
export function usePokerGame(settings: Settings, paused = false): PokerGame {
  const [state, setState] = useState<GameState | null>(null);
  const [lastEvent, setLastEvent] = useState<GameEvent | null>(null);
  const [animating, setAnimating] = useState(false);
  const [pending, setPending] = useState(false);
  const [thinkingSeat, setThinkingSeat] = useState<number | null>(null);
  const [error, setError] = useState<GameError | null>(null);
  const [autoDealAt, setAutoDealAt] = useState<number | null>(null);
  const playId = useRef(0);
  const sessionId = useRef<string | null>(null);
  sessionId.current = state?.session_id ?? null;
  const scaleRef = useRef(SPEED_SCALE[settings.speed]);
  scaleRef.current = SPEED_SCALE[settings.speed];

  const play = useCallback(async (resp: PlayResponse) => {
    const id = ++playId.current;
    setAnimating(true);
    for (const step of resp.steps) {
      if (playId.current !== id) return;
      const ev = step.event;
      const scale = scaleRef.current;
      if (ev.type === "action" && ev.seat === AI && scale > 0) {
        setThinkingSeat(AI);
        await sleep(AI_THINK_MS * scale);
        setThinkingSeat(null);
        if (playId.current !== id) return;
      }
      setState(step.state);
      setLastEvent(ev);
      const base =
        ev.type === "street" && ev.street === "flop" ? FLOP_DELAY : (EVENT_DELAY[ev.type] ?? 300);
      const humanAction = ev.type === "action" && ev.seat === HUMAN;
      await sleep((humanAction ? 200 : base) * scale);
    }
    if (playId.current !== id) return;
    setState(resp.state);
    setAnimating(false);
  }, []);

  /** Replace the table with the server's current view of the game. */
  const resync = useCallback(async (after: ApiError | null) => {
    const id = sessionId.current;
    if (!id) return;
    playId.current++; // drop any half-played animation
    try {
      const fresh = await api.getSession(id);
      setState(fresh);
      setLastEvent(null);
      setThinkingSeat(null);
      setAnimating(false);
      setError(
        after
          ? {
              kind: "resynced",
              message:
                after.status === 0
                  ? "The connection dropped for a moment. The table has been refreshed."
                  : `${after.message}. The table has been refreshed.`,
            }
          : null,
      );
    } catch (e) {
      const status = e instanceof ApiError ? e.status : 0;
      setError(status === 404 ? { kind: "expired", message: EXPIRED_MESSAGE } : { kind: "network", message: NETWORK_MESSAGE });
    }
  }, []);

  const run = useCallback(
    async (fn: () => Promise<PlayResponse>, opts: { resyncOnError?: boolean } = {}) => {
      setPending(true);
      setError(null);
      let resp: PlayResponse;
      try {
        resp = await fn();
      } catch (e) {
        const err = e instanceof ApiError ? e : new ApiError(String(e), 0);
        if (err.status === 400 || !opts.resyncOnError) {
          setError({ kind: err.status === 0 ? "network" : "rejected", message: err.message });
        } else {
          // The request may or may not have reached the server: ask it where things stand.
          await resync(err);
        }
        setPending(false);
        return;
      }
      setPending(false);
      await play(resp);
    },
    [play, resync],
  );

  const newGame = useCallback(
    async (config: GameConfig) => {
      playId.current++;
      setAnimating(false);
      setThinkingSeat(null);
      setLastEvent(null);
      await run(async () => {
        const created = await api.createSession(config);
        setState(created.state);
        return api.startHand(created.state.session_id);
      });
    },
    [run],
  );

  const deal = useCallback(async () => {
    if (!state) return;
    setAutoDealAt(null);
    await run(() => api.startHand(state.session_id), { resyncOnError: true });
  }, [run, state]);

  const act = useCallback(
    async (action: ActionType, amount?: number) => {
      if (!state || !state.legal || animating || pending) return;
      await run(() => api.act(state.session_id, action, amount), { resyncOnError: true });
    },
    [animating, pending, run, state],
  );

  // Automatic progression to the next hand.
  const canAutoDeal =
    settings.autoDeal && !paused && state?.status === "waiting" && !animating && !pending && !error;
  useEffect(() => {
    if (!canAutoDeal) {
      setAutoDealAt(null);
      return;
    }
    const delay = Math.max(800, AUTO_DEAL_MS * scaleRef.current);
    setAutoDealAt(Date.now() + delay);
    const t = setTimeout(() => void deal(), delay);
    return () => clearTimeout(t);
  }, [canAutoDeal, deal]);

  return {
    state,
    lastEvent,
    animating,
    pending,
    thinkingSeat,
    error,
    autoDealAt,
    newGame,
    deal,
    act,
    clearError: () => setError(null),
    retry: () => resync(null),
  };
}
