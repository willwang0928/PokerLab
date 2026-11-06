import { useEffect, useRef, useState } from "react";
import { type Settings, type Speed, THEMES } from "../hooks/useSettings";

const SPEEDS: Speed[] = ["relaxed", "normal", "fast"];

export function SettingsMenu({
  settings,
  onChange,
  onPreviewSound,
}: {
  settings: Settings;
  onChange: (patch: Partial<Settings>) => void;
  /** Play a sample so the player can hear the chosen volume. */
  onPreviewSound?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setOpen(false);
      buttonRef.current?.focus();
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        ref={buttonRef}
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-label="Settings"
        className="btn-dark grid h-9 w-9 place-items-center rounded-xl text-slate-300"
      >
        <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
          <path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z" />
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68 1.65 1.65 0 0 0 10 3.17V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9c.2.6.75 1 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z" />
        </svg>
      </button>
      {open && (
        <div className="fixed inset-x-3 top-[60px] z-40 max-h-[calc(100vh-5rem)] overflow-y-auto rounded-2xl sm:absolute sm:inset-x-auto sm:right-0 sm:top-11 sm:w-64 bg-gradient-to-b from-[#1a2330] to-[#10161f] p-4 shadow-2xl ring-1 ring-white/10">
          <label className="flex items-center justify-between gap-3 text-sm text-slate-200">
            Auto-deal next hand
            <input
              type="checkbox"
              checked={settings.autoDeal}
              onChange={(e) => onChange({ autoDeal: e.target.checked })}
              className="h-4 w-4 accent-amber-400"
            />
          </label>
          <div className="mt-4 text-sm text-slate-200" id="speed-label">Animation speed</div>
          <div className="mt-2 grid grid-cols-3 gap-1.5" role="group" aria-labelledby="speed-label">
            {SPEEDS.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => onChange({ speed: s })}
                aria-pressed={settings.speed === s}
                className={`min-h-10 rounded-lg py-1.5 text-xs font-semibold capitalize sm:min-h-0 ${
                  settings.speed === s ? "bg-amber-300 text-slate-950" : "bg-white/[0.06] text-slate-300 ring-1 ring-white/[0.06] hover:bg-white/10"
                }`}
              >
                {s}
              </button>
            ))}
          </div>

          <div className="mt-4 flex items-center justify-between gap-3 border-t border-white/[0.06] pt-4 text-sm text-slate-200">
            <label htmlFor="sound-toggle">Sound effects</label>
            <input
              id="sound-toggle"
              type="checkbox"
              checked={settings.sound}
              onChange={(e) => onChange({ sound: e.target.checked })}
              className="h-4 w-4 accent-amber-400"
            />
          </div>
          <div className="mt-3 flex items-center gap-3">
            <SpeakerIcon muted={!settings.sound || settings.volume === 0} className="h-4 w-4 shrink-0 text-slate-400" />
            <input
              type="range"
              min={0}
              max={100}
              step={5}
              value={Math.round(settings.volume * 100)}
              disabled={!settings.sound}
              aria-label="Volume"
              aria-valuetext={`${Math.round(settings.volume * 100)}%`}
              onChange={(e) => onChange({ volume: Number(e.target.value) / 100 })}
              onPointerUp={onPreviewSound}
              onKeyUp={onPreviewSound}
              className="bet-slider slider-sm min-w-0 flex-1"
              style={{ "--fill": `${settings.volume * 100}%` } as React.CSSProperties}
            />
            <span className="w-8 text-right text-xs tabular-nums text-slate-400">
              {Math.round(settings.volume * 100)}%
            </span>
          </div>

          <div className="mt-4 border-t border-white/[0.06] pt-4 text-sm text-slate-200" id="theme-label">
            Table theme
          </div>
          <div className="mt-2 grid grid-cols-3 gap-1.5" role="group" aria-labelledby="theme-label">
            {THEMES.map((t) => {
              const active = settings.theme === t.id;
              return (
                <button
                  key={t.id}
                  type="button"
                  aria-pressed={active}
                  onClick={() => onChange({ theme: t.id })}
                  className={`flex flex-col items-center gap-1.5 rounded-lg px-1 py-2 text-[11px] font-medium leading-tight ${
                    active ? "bg-white/[0.1] text-amber-100 ring-1 ring-amber-300/70" : "bg-white/[0.04] text-slate-400 ring-1 ring-white/[0.06] hover:bg-white/[0.08]"
                  }`}
                >
                  <span
                    className="h-5 w-8 rounded-full ring-1 ring-black/40"
                    style={{
                      background: `radial-gradient(ellipse at 50% 40%, color-mix(in srgb, ${t.swatch} 80%, white) 0%, ${t.swatch} 55%, color-mix(in srgb, ${t.swatch} 60%, black) 100%)`,
                      boxShadow: "0 0 0 2px #2b2018",
                    }}
                    aria-hidden
                  />
                  {t.name}
                </button>
              );
            })}
          </div>

          <p className="mt-4 border-t border-white/[0.06] pt-3 text-xs leading-relaxed text-slate-400">
            Shortcuts: <kbd className="text-slate-300">F</kbd> fold · <kbd className="text-slate-300">C</kbd>{" "}
            check/call · <kbd className="text-slate-300">R</kbd> bet/raise · <kbd className="text-slate-300">Space</kbd>{" "}
            next hand
          </p>
        </div>
      )}
    </div>
  );
}

export function SpeakerIcon({ muted, className }: { muted: boolean; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M11 5 6 9H3v6h3l5 4V5Z" fill="currentColor" fillOpacity="0.15" />
      {muted ? (
        <path d="m16 9 5 6m0-6-5 6" />
      ) : (
        <>
          <path d="M15.5 8.5a5 5 0 0 1 0 7" />
          <path d="M18.5 5.5a9 9 0 0 1 0 13" />
        </>
      )}
    </svg>
  );
}

/** Header button that turns sound effects on and off. */
export function MuteButton({ muted, onToggle }: { muted: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={muted}
      aria-label="Mute sound effects"
      title={muted ? "Unmute" : "Mute"}
      className="btn-dark grid h-9 w-9 place-items-center rounded-xl text-slate-300"
    >
      <SpeakerIcon muted={muted} className="h-5 w-5" />
    </button>
  );
}
