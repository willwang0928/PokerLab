import { motion } from "motion/react";
import { useId } from "react";
import { formatChips } from "../lib/betting";

const DENOMS: { value: number; color: string }[] = [
  { value: 1000, color: "#d99a1e" },
  { value: 500, color: "#7e3ff2" },
  { value: 100, color: "#1d2330" },
  { value: 25, color: "#17955a" },
  { value: 5, color: "#d42a3a" },
  { value: 1, color: "#e9e6df" },
];

/** Break an amount into up to ``max`` chip discs for display. */
export function chipColors(amount: number, max = 6): string[] {
  const out: string[] = [];
  let rest = amount;
  for (const d of DENOMS) {
    while (rest >= d.value && out.length < max) {
      out.push(d.color);
      rest -= d.value;
    }
  }
  return out.length ? out : amount > 0 ? [DENOMS[DENOMS.length - 1].color] : [];
}

/** A single casino chip seen from above: coloured body, white edge inserts, inlaid centre. */
export function Chip({ color, className = "", style }: { color: string; className?: string; style?: React.CSSProperties }) {
  const light = color === "#e9e6df";
  const insert = light ? "#3b4a63" : "#ffffff";
  const sheen = `chip-sheen-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  return (
    <span className={`chip block ${className}`} style={{ color, ...style }}>
      <svg viewBox="0 0 40 40" className="block h-full w-full" aria-hidden focusable="false">
        <circle cx="20" cy="20" r="20" fill={color} />
        {/* six edge inserts */}
        <circle
          cx="20"
          cy="20"
          r="17"
          fill="none"
          stroke={insert}
          strokeWidth="6"
          strokeDasharray="8.9 8.9"
          opacity={light ? 0.55 : 0.92}
        />
        <circle cx="20" cy="20" r="11.5" fill={color} />
        <circle cx="20" cy="20" r="11.5" fill="none" stroke={insert} strokeOpacity="0.55" strokeWidth="1" strokeDasharray="2 2" />
        <circle cx="20" cy="20" r="20" fill={`url(#${sheen})`} />
        <defs>
          <radialGradient id={sheen} cx="35%" cy="28%" r="75%">
            <stop offset="0%" stopColor="#fff" stopOpacity="0.28" />
            <stop offset="55%" stopColor="#fff" stopOpacity="0" />
            <stop offset="100%" stopColor="#000" stopOpacity="0.22" />
          </radialGradient>
        </defs>
      </svg>
    </span>
  );
}

interface Props {
  amount: number;
  label?: string;
  className?: string;
  /** Number of side-by-side columns (used for the pot). */
  columns?: number;
  /** Put the amount tag before the chips instead of after. */
  tagFirst?: boolean;
  /** Hide the amount tag (when the caller renders its own). */
  hideTag?: boolean;
  /** Skip the stack's own pop-in (when a parent animates it). */
  still?: boolean;
  /** Seconds to wait before popping in (lets flying chips land first). */
  enterDelay?: number;
  /** Seconds the stack takes to disappear. */
  exitDuration?: number;
}

export function ChipStack({
  amount,
  label,
  className = "",
  columns = 1,
  tagFirst = false,
  hideTag = false,
  still = false,
  enterDelay = 0,
  exitDuration,
}: Props) {
  const perColumn = 5;
  const chips = chipColors(amount, perColumn * columns);
  const stacks: string[][] = [];
  for (let i = 0; i < chips.length; i += perColumn) stacks.push(chips.slice(i, i + perColumn));
  const tallest = Math.max(1, ...stacks.map((s) => s.length));

  const tag = (
    <span className="amount-tag px-2 py-0.5 text-xs font-semibold tabular-nums text-amber-50 sm:text-[13px]">
      <span className="sr-only">Bet </span>
      {label ?? formatChips(amount)}
    </span>
  );

  return (
    <motion.div
      layout={!still}
      initial={still ? false : { opacity: 0, scale: 0.6 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={
        exitDuration === undefined
          ? { opacity: 0, scale: 0.6, transition: { duration: 0.2, delay: 0 } }
          : { opacity: 0, transition: { duration: exitDuration, delay: 0 } }
      }
      transition={{ type: "spring", stiffness: 380, damping: 26, delay: enterDelay }}
      className={`flex items-center gap-1.5 ${className}`}
    >
      {tagFirst && !hideTag && tag}
      <div className="flex items-end" style={{ gap: "calc(var(--chip) * 0.08)" }} aria-hidden>
        {stacks.map((stack, s) => (
          <div
            key={s}
            className="relative"
            style={{ width: "var(--chip)", height: `calc(var(--chip) + ${(tallest - 1) * 3}px)` }}
          >
            {stack.map((color, i) => (
              <Chip
                key={i}
                color={color}
                className="absolute left-0"
                style={{ width: "var(--chip)", height: "var(--chip)", bottom: i * 3 }}
              />
            ))}
          </div>
        ))}
      </div>
      {!tagFirst && !hideTag && tag}
    </motion.div>
  );
}
