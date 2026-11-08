import { useEffect, useRef, type ReactNode } from "react";
import { cardLabel, parseCard } from "../lib/cards";
import { SuitIcon } from "./SuitIcon";

const CARD_RUN = "[2-9TJQKA][shdc](?: [2-9TJQKA][shdc])*";
const STREET_RE = new RegExp(`^(Flop|Turn|River): (${CARD_RUN})$`);
const SHOW_RE = new RegExp(`(shows? )(${CARD_RUN})(?= \\()`, "g");

/** A tiny inline card used in the log ("8♦"). */
export function MiniCard({ code }: { code: string }) {
  const { rank, suit, red } = parseCard(code);
  return (
    <span
      role="img"
      aria-label={cardLabel(code)}
      title={cardLabel(code)}
      className={`mx-[1px] inline-flex h-[18px] items-center gap-[1px] rounded-[4px] bg-[#f5f2ea] px-[4px] align-[-3px] text-[11.5px] font-bold leading-none shadow-[0_1px_2px_rgba(0,0,0,.5)] ${
        red ? "text-suit-red" : "text-suit-black"
      }`}
    >
      <span aria-hidden>{rank}</span>
      <SuitIcon suit={suit} className="h-[9px] w-[9px]" />
    </span>
  );
}

function cards(run: string): ReactNode[] {
  return run.split(" ").map((c, i) => <MiniCard key={i} code={c} />);
}

/** Render card codes in street and showdown lines as mini cards. */
function renderLogLine(line: string): ReactNode {
  const street = STREET_RE.exec(line);
  if (street) {
    return (
      <>
        <span className="mr-1">{street[1]}</span>
        {cards(street[2])}
      </>
    );
  }
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of line.matchAll(SHOW_RE)) {
    const start = m.index + m[1].length;
    out.push(line.slice(last, start));
    out.push(<span key={start}>{cards(m[2])}</span>);
    last = start + m[2].length;
  }
  if (!out.length) return line;
  out.push(line.slice(last));
  return out;
}

function lineClass(line: string): string {
  if (line.startsWith("Hand #")) return "mt-3 first:mt-0 border-t border-white/5 pt-3 first:border-0 first:pt-0 text-[12.5px] font-semibold text-slate-200";
  if (STREET_RE.test(line)) return "pt-1.5 font-semibold text-emerald-300";
  if (/ wins? | shared/.test(line)) return "font-semibold text-amber-300";
  if (/ shows? /.test(line)) return "text-slate-300";
  return "text-slate-400";
}

export function HandLog({ lines }: { lines: string[] }) {
  const ref = useRef<HTMLOListElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines.length]);

  return (
    <section className="surface flex min-h-0 flex-col rounded-2xl" aria-labelledby="hand-history-title">
      <h2 id="hand-history-title" className="eyebrow flex items-center gap-2 border-b border-white/5 px-4 py-3 text-slate-400">
        <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
          <path d="M3 12a9 9 0 1 0 3-6.7L3 8" />
          <path d="M3 3v5h5M12 7v5l3 2" />
        </svg>
        Hand history
      </h2>
      <ol
        ref={ref}
        className="scroll-thin max-h-64 flex-1 space-y-1 overflow-y-auto px-4 py-3 text-[13px] leading-relaxed lg:max-h-none"
        aria-live="polite"
        aria-labelledby="hand-history-title"
        // Focusable so the history can be scrolled with the keyboard.
        tabIndex={0}
      >
        {lines.length === 0 && <li className="text-slate-400">No actions yet.</li>}
        {lines.map((line, i) => (
          <li key={i} className={lineClass(line)}>
            {renderLogLine(line)}
          </li>
        ))}
      </ol>
    </section>
  );
}
