import { motion } from "motion/react";
import type { CSSProperties } from "react";
import { cardLabel, parseCard } from "../lib/cards";
import { SuitIcon } from "./SuitIcon";

interface Props {
  code?: string | null;
  faceUp?: boolean;
  size?: "md" | "lg";
  delay?: number;
  dimmed?: boolean;
  highlight?: boolean;
  /** Fan angle in degrees, used for hole cards. */
  tilt?: number;
}

/** A playing card that deals in and flips from back to face when revealed. */
export function PlayingCard({ code, faceUp = true, size = "md", delay = 0, dimmed, highlight, tilt = 0 }: Props) {
  const showFace = Boolean(faceUp && code);
  const width = size === "lg" ? "var(--card-w-lg)" : "var(--card-w)";
  return (
    <motion.div
      className="relative shrink-0"
      style={{ width, aspectRatio: "5 / 7", perspective: 800, "--w": width, transformOrigin: "50% 90%" } as CSSProperties}
      initial={{ opacity: 0, y: -28, scale: 0.85, rotate: 0 }}
      animate={{ opacity: dimmed ? 0.4 : 1, y: highlight ? -6 : 0, scale: 1, rotate: tilt }}
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
        <div className="card-face backface-hidden absolute inset-0 overflow-hidden rounded-[8%]">
          {code && <CardFace code={code} />}
        </div>
        <div
          className="card-back backface-hidden absolute inset-0 rounded-[8%]"
          style={{ transform: "rotateY(180deg)" }}
        >
          <div className="card-back-inner grid place-items-center">
            <SuitIcon suit="s" className="text-[#f0c96a]/70" style={{ width: "calc(var(--w) * 0.2)" }} />
          </div>
        </div>
      </motion.div>
    </motion.div>
  );
}

const COURT = new Set(["J", "Q", "K"]);

function CardFace({ code }: { code: string }) {
  const { rank, suit, red } = parseCard(code);
  const color = red ? "text-suit-red" : "text-suit-black";
  const index = (
    <div className="flex flex-col items-center" style={{ gap: "calc(var(--w) * 0.02)" }}>
      <span
        className="font-bold leading-[0.85]"
        style={{
          fontSize: "calc(var(--w) * 0.27)",
          letterSpacing: rank === "10" ? "-0.09em" : "-0.02em",
          marginLeft: rank === "10" ? "-0.09em" : undefined,
        }}
      >
        {rank}
      </span>
      <SuitIcon suit={suit} style={{ width: "calc(var(--w) * 0.17)" }} />
    </div>
  );

  return (
    <div className={`absolute inset-0 select-none ${color}`} aria-hidden>
      <div className="absolute left-[7%] top-[5.5%]">{index}</div>
      <div className="absolute bottom-[5.5%] right-[7%] rotate-180">{index}</div>
      {COURT.has(rank) ? (
        <div className="card-court absolute inset-x-[27%] inset-y-[16%] flex flex-col items-center justify-center">
          <span className="font-display font-bold leading-none" style={{ fontSize: "calc(var(--w) * 0.32)" }}>
            {rank}
          </span>
          <SuitIcon suit={suit} style={{ width: "calc(var(--w) * 0.2)", marginTop: "calc(var(--w) * 0.03)" }} />
        </div>
      ) : (
        <div className="absolute inset-0 grid place-items-center">
          <SuitIcon suit={suit} style={{ width: `calc(var(--w) * ${rank === "A" ? 0.5 : 0.4})` }} />
        </div>
      )}
    </div>
  );
}

export function CardSlot({ size = "md" }: { size?: "md" | "lg" }) {
  const width = size === "lg" ? "var(--card-w-lg)" : "var(--card-w)";
  return <div className="card-slot shrink-0 rounded-[8%]" style={{ width, aspectRatio: "5 / 7" }} />;
}
