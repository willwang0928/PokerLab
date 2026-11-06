import { useEffect, useRef } from "react";
import { canTravel, durationScale, type MotionPrefs } from "../lib/motion";
import { sound as defaultEngine, type SoundEngine, unlockOnGesture } from "../lib/sound";
import { soundCues } from "../lib/soundCues";
import type { GameEvent } from "../types";
import type { Settings } from "./useSettings";

/**
 * Plays table sounds as game events are shown. Sounds follow the same event stream as
 * the animations, so they always match what the backend reported.
 */
export function useGameSounds(
  lastEvent: GameEvent | null,
  settings: Pick<Settings, "sound" | "volume">,
  prefs: MotionPrefs,
  engine: SoundEngine = defaultEngine,
): void {
  useEffect(() => unlockOnGesture(engine), [engine]);

  useEffect(() => {
    engine.configure({ enabled: settings.sound, volume: settings.volume });
  }, [engine, settings.sound, settings.volume]);

  // StrictMode re-runs effects; play each event's sounds once.
  const handled = useRef<GameEvent | null>(null);
  useEffect(() => {
    if (!lastEvent || lastEvent === handled.current) return;
    handled.current = lastEvent;
    const cues = soundCues(lastEvent, { travel: canTravel(prefs), ds: durationScale(prefs) });
    for (const c of cues) engine.play(c.kind, c.delay);
  }, [lastEvent, prefs, engine]);
}
