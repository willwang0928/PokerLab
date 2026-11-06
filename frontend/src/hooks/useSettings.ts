import { useCallback, useState } from "react";

export type Speed = "relaxed" | "normal" | "fast" | "instant";

export interface Settings {
  autoDeal: boolean;
  speed: Speed;
}

export const SPEED_SCALE: Record<Speed, number> = {
  relaxed: 1.4,
  normal: 1,
  fast: 0.55,
  instant: 0,
};

const KEY = "pokerlab.settings.v1";
const DEFAULTS: Settings = { autoDeal: true, speed: "normal" };

function load(): Settings {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return DEFAULTS;
    return { ...DEFAULTS, ...(JSON.parse(raw) as Partial<Settings>) };
  } catch {
    return DEFAULTS;
  }
}

export function useSettings() {
  const [settings, setSettings] = useState<Settings>(load);
  const update = useCallback((patch: Partial<Settings>) => {
    setSettings((prev) => {
      const next = { ...prev, ...patch };
      try {
        localStorage.setItem(KEY, JSON.stringify(next));
      } catch {
        /* storage unavailable: keep in memory only */
      }
      return next;
    });
  }, []);
  return [settings, update] as const;
}
