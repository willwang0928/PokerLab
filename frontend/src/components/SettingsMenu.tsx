import { useEffect, useRef, useState } from "react";
import type { Settings, Speed } from "../hooks/useSettings";

const SPEEDS: Speed[] = ["relaxed", "normal", "fast"];

export function SettingsMenu({
  settings,
  onChange,
}: {
  settings: Settings;
  onChange: (patch: Partial<Settings>) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
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
        <div className="absolute right-0 top-11 z-40 w-64 rounded-2xl bg-gradient-to-b from-[#1a2330] to-[#10161f] p-4 shadow-2xl ring-1 ring-white/10">
          <label className="flex items-center justify-between gap-3 text-sm text-slate-200">
            Auto-deal next hand
            <input
              type="checkbox"
              checked={settings.autoDeal}
              onChange={(e) => onChange({ autoDeal: e.target.checked })}
              className="h-4 w-4 accent-amber-400"
            />
          </label>
          <div className="mt-4 text-sm text-slate-200">Animation speed</div>
          <div className="mt-2 grid grid-cols-3 gap-1.5">
            {SPEEDS.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => onChange({ speed: s })}
                aria-pressed={settings.speed === s}
                className={`rounded-lg py-1.5 text-xs font-semibold capitalize ${
                  settings.speed === s ? "bg-amber-300 text-slate-950" : "bg-white/[0.06] text-slate-300 ring-1 ring-white/[0.06] hover:bg-white/10"
                }`}
              >
                {s}
              </button>
            ))}
          </div>
          <p className="mt-4 text-xs leading-relaxed text-slate-500">
            Shortcuts: <kbd className="text-slate-300">F</kbd> fold · <kbd className="text-slate-300">C</kbd>{" "}
            check/call · <kbd className="text-slate-300">R</kbd> bet/raise · <kbd className="text-slate-300">Space</kbd>{" "}
            next hand
          </p>
        </div>
      )}
    </div>
  );
}
