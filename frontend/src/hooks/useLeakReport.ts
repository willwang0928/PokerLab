import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, profileApi } from "../lib/api";
import type { LeakReport } from "../types";

export type LeakState =
  | { status: "none" } // no server profile yet: nothing saved to analyse
  | { status: "loading"; report: LeakReport | null }
  | { status: "ready"; report: LeakReport }
  | { status: "error"; message: string; report: LeakReport | null };

/**
 * The profile's leak report (v0.4.4), fetched once for the whole app and shared by the
 * Progress dashboard, the history and training. It is refetched when hands are saved
 * (``handsVersion``) and after each completed sync (``syncedAt``: practice answers reach
 * the server then). The server caches the report until the profile changes, so a
 * refetch with nothing new is cheap.
 */
export function useLeakReport(token: string | undefined, handsVersion: number, syncedAt: number | null) {
  const [state, setState] = useState<LeakState>(token ? { status: "loading", report: null } : { status: "none" });
  const last = useRef<LeakReport | null>(null);
  const seq = useRef(0);

  const load = useCallback(async () => {
    const mine = ++seq.current;
    if (!token) {
      last.current = null;
      setState({ status: "none" });
      return;
    }
    setState({ status: "loading", report: last.current });
    try {
      const report = await profileApi.leaks(token);
      if (mine !== seq.current) return;
      last.current = report;
      setState({ status: "ready", report });
    } catch (e) {
      if (mine !== seq.current) return;
      setState({
        status: "error",
        report: last.current,
        message:
          e instanceof ApiError && e.status === 503
            ? "The server's storage is unavailable, so your leak report cannot be updated right now."
            : e instanceof ApiError && e.status === 401
              ? "The server no longer knows this profile."
              : "Your leak report could not be loaded. Is the server running?",
      });
    }
  }, [token]);

  // A different profile (or a reset): forget the old report at once.
  useEffect(() => {
    last.current = null;
  }, [token]);

  useEffect(() => {
    void load();
  }, [load, handsVersion, syncedAt]);

  return { state, reload: load };
}

export function reportOf(s: LeakState): LeakReport | null {
  return s.status === "none" ? null : s.report;
}
