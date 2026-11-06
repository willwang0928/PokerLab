import { useEffect, useRef } from "react";

export function HandLog({ lines }: { lines: string[] }) {
  const ref = useRef<HTMLOListElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines.length]);

  return (
    <section className="flex min-h-0 flex-col rounded-2xl bg-slate-900/60 ring-1 ring-white/5">
      <h2 className="border-b border-white/5 px-4 py-2.5 text-xs font-semibold uppercase tracking-widest text-slate-400">
        Hand history
      </h2>
      <ol ref={ref} className="max-h-64 flex-1 space-y-1 overflow-y-auto px-4 py-3 text-sm lg:max-h-none" aria-live="polite">
        {lines.length === 0 && <li className="text-slate-500">No actions yet.</li>}
        {lines.map((line, i) => (
          <li
            key={i}
            className={
              line.startsWith("Hand #")
                ? "pb-1 font-semibold text-slate-200"
                : /^(Flop|Turn|River):/.test(line)
                  ? "pt-1 font-semibold text-emerald-300"
                  : / wins? | shared/.test(line)
                    ? "font-semibold text-amber-300"
                    : "text-slate-400"
            }
          >
            {line}
          </li>
        ))}
      </ol>
    </section>
  );
}
