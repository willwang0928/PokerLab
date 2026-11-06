import { motion } from "motion/react";
import { formatChips } from "../lib/betting";

const DENOMS: { value: number; color: string }[] = [
  { value: 1000, color: "#7c3aed" },
  { value: 100, color: "#111827" },
  { value: 25, color: "#15803d" },
  { value: 5, color: "#dc2626" },
  { value: 1, color: "#e5e7eb" },
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

interface Props {
  amount: number;
  label?: string;
  className?: string;
}

export function ChipStack({ amount, label, className = "" }: Props) {
  const chips = chipColors(amount);
  return (
    <motion.div
      layout
      initial={{ opacity: 0, scale: 0.6 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.6 }}
      transition={{ type: "spring", stiffness: 380, damping: 26 }}
      className={`flex items-center gap-1.5 ${className}`}
    >
      <div className="relative h-6 w-5" aria-hidden>
        {chips.map((color, i) => (
          <span
            key={i}
            className="chip absolute left-0 h-5 w-5"
            style={{ color, bottom: i * 3 }}
          />
        ))}
      </div>
      <span className="rounded-full bg-black/55 px-2 py-0.5 text-xs font-semibold tabular-nums text-amber-100 ring-1 ring-white/10 sm:text-sm">
        {label ?? formatChips(amount)}
      </span>
    </motion.div>
  );
}
