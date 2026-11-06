import { useCallback, useState } from "react";

export type Speed = "relaxed" | "normal" | "fast" | "instant";
export type ThemeId = "classic" | "midnight" | "burgundy";

export interface Settings {
  autoDeal: boolean;
  speed: Speed;
  /** Sound effects on or off. */
  sound: boolean;
  /** Sound effect volume, 0–1. */
  volume: number;
  /** Table felt theme. */
  theme: ThemeId;
}

export const SPEED_SCALE: Record<Speed, number> = {
  relaxed: 1.4,
  normal: 1,
  fast: 0.55,
  instant: 0,
};

export const THEMES: { id: ThemeId; name: string; swatch: string }[] = [
  { id: "classic", name: "Classic green", swatch: "#14694f" },
  { id: "midnight", name: "Midnight blue", swatch: "#1b3f6b" },
  { id: "burgundy", name: "Burgundy", swatch: "#6e1f2e" },
];

export const SETTINGS_KEY = "pokerlab.settings.v1";
export const DEFAULT_SETTINGS: Settings = {
  autoDeal: true,
  speed: "normal",
  sound: true,
  volume: 0.6,
  theme: "classic",
};

/**
 * Turn whatever was stored into valid settings, falling back to the default for any
 * field that is missing or invalid (older saves, hand-edited storage, removed options).
 */
export function sanitizeSettings(raw: unknown): Settings {
  const r = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const d = DEFAULT_SETTINGS;
  const volume = typeof r.volume === "number" && Number.isFinite(r.volume) ? r.volume : d.volume;
  return {
    autoDeal: typeof r.autoDeal === "boolean" ? r.autoDeal : d.autoDeal,
    speed: typeof r.speed === "string" && r.speed in SPEED_SCALE ? (r.speed as Speed) : d.speed,
    sound: typeof r.sound === "boolean" ? r.sound : d.sound,
    volume: Math.min(1, Math.max(0, volume)),
    theme: THEMES.some((t) => t.id === r.theme) ? (r.theme as ThemeId) : d.theme,
  };
}

function load(): Settings {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    return sanitizeSettings(raw ? JSON.parse(raw) : null);
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export function useSettings() {
  const [settings, setSettings] = useState<Settings>(load);
  const update = useCallback((patch: Partial<Settings>) => {
    setSettings((prev) => {
      const next = sanitizeSettings({ ...prev, ...patch });
      try {
        localStorage.setItem(SETTINGS_KEY, JSON.stringify(next));
      } catch {
        /* storage unavailable: keep in memory only */
      }
      return next;
    });
  }, []);
  return [settings, update] as const;
}
