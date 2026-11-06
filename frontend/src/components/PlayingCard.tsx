import { motion } from "motion/react";
import type { CSSProperties } from "react";
import { cardLabel, parseCard, SUIT_SYMBOL } from "../lib/cards";

interface Props {
  code?: string | null;
  faceUp?: boolean;
  size?: "md" | "lg";
  delay?: number;
  dimmed?: boolean;
  highlight?: boolean;
}

/** A playing card that deals in and flips from back to face when revealed. */
export function PlayingCard({ code, faceUp = true, size = "md", delay = 0, dimmed, highlight }: Props) {
  const showFace = Boolean(faceUp && code);
  const width = size === "lg" ? "var(--card-w-lg)" : "var(--card-w)";
  return (
    <motion.div
      className="relative shrink-0"
      style={{ width, aspectRatio: "5 / 7", perspective: 800, "--w": width } as CSSProperties}
      initial={{ opacity: 0, y: -28, scale: 0.85 }}
      animate={{ opacity: dimmed ? 0.45 : 1, y: highlight ? -6 : 0, scale: 1 }}
      transition={{ duration: 0.35, delay, ease: "easeOut" }}
      role="img"
      aria-label={showFace && code ? cardLabel(code) : "Face-down card"}
      data-testid={showFace && code ? `card-${code}` : "card-back"}
    >
      <motion.div
        className="preserve-3d absolute inset-0"
        initial={false}
        animate={{ rotateY: showFace ? 0 : 180 }}
        transition={{ duration: 0.5, ease: [0.4, 0.2, 0.2, 1] }}
      >
        <div className="card-face backface-hidden absolute inset-0 rounded-[9%]">
          {code && <CardFace code={code} />}
        </div>
        <div
          className="card-back backface-hidden absolute inset-0 rounded-[9%]"
          style={{ transform: "rotateY(180deg)" }}
        />
      </motion.div>
    </motion.div>
  );
}

function CardFace({ code }: { code: string }) {
  const { rank, suit, red } = parseCard(code);
  const color = red ? "text-[#c81e3a]" : "text-[#111827]";
  const symbol = SUIT_SYMBOL[suit];
  return (
    <div className={`absolute inset-0 select-none ${color}`} aria-hidden>
      <div className="absolute left-[8%] top-[5%] flex flex-col items-center leading-none">
        <span className="font-bold tracking-tighter" style={{ fontSize: "calc(var(--w) * 0.26)" }}>
          {rank}
        </span>
        <span style={{ fontSize: "calc(var(--w) * 0.22)" }}>{symbol}</span>
      </div>
      <div
        className="absolute inset-0 flex items-center justify-center"
        style={{ fontSize: "calc(var(--w) * 0.5)" }}
      >
        {symbol}
      </div>
      <div className="absolute bottom-[5%] right-[8%] flex rotate-180 flex-col items-center leading-none">
        <span className="font-bold tracking-tighter" style={{ fontSize: "calc(var(--w) * 0.26)" }}>
          {rank}
        </span>
        <span style={{ fontSize: "calc(var(--w) * 0.22)" }}>{symbol}</span>
      </div>
    </div>
  );
}

export function CardSlot({ size = "md" }: { size?: "md" | "lg" }) {
  const width = size === "lg" ? "var(--card-w-lg)" : "var(--card-w)";
  return <div className="card-slot shrink-0 rounded-[9%]" style={{ width, aspectRatio: "5 / 7" }} />;
}
