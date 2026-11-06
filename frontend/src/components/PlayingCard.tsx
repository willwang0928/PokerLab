import { animate, motion } from "motion/react";
import { useContext, useLayoutEffect, useRef, type CSSProperties } from "react";
import { cardLabel, parseCard } from "../lib/cards";
import { canTravel, DeckContext, durationScale, TIMING, useMotionPrefs } from "../lib/motion";
import { SuitIcon } from "./SuitIcon";

export type CardEmphasis = "win" | "muted" | "folded";

interface Props {
  code?: string | null;
  faceUp?: boolean;
  size?: "md" | "lg";
  /** Seconds before the card leaves the deck. */
  delay?: number;
  /** Deal the card face down and turn it over this many seconds after it leaves the deck. */
  revealAfter?: number;
  /** Extra delay (seconds) before a later flip, e.g. to stagger the opponent's cards at showdown. */
  flipDelay?: number;
  emphasis?: CardEmphasis;
  /** Fan angle in degrees, used for hole cards. */
  tilt?: number;
  /** Where the card slides when it is mucked or collected (px, relative to its spot). */
  exitTo?: { x: number; y: number };
  className?: string;
  style?: CSSProperties;
}

/**
 * A playing card. On mount it travels from the deck (when one is on the table), it flips
 * from back to face when revealed, and it slides away when mucked or collected.
 */
export function PlayingCard({
  code,
  faceUp = true,
  size = "md",
  delay = 0,
  revealAfter,
  flipDelay = 0,
  emphasis,
  tilt = 0,
  exitTo = { x: 0, y: 0 },
  className = "",
  style,
}: Props) {
  const prefs = useMotionPrefs();
  const ds = durationScale(prefs);
  const travel = canTravel(prefs);
  const deck = useContext(DeckContext);
  const travelRef = useRef<HTMLDivElement>(null);

  const showFace = Boolean(faceUp && code);
  const width = size === "lg" ? "var(--card-w-lg)" : "var(--card-w)";
  // Cards that are dealt face down first start on their back whatever ``faceUp`` says.
  const dealtDown = revealAfter !== undefined;
  const flipAt = dealtDown && travel ? (delay + revealAfter) * ds : flipDelay * ds;

  // Fly in from the deck. Measured before paint so the card never flashes in its final spot.
  useLayoutEffect(() => {
    const el = travelRef.current;
    const origin = deck?.current;
    if (!el) return;
    if (!origin || !travel) {
      const ctl = animate(el, { opacity: [0, 1] }, { duration: 0.2 * ds, delay: travel ? delay * ds : 0 });
      return () => ctl.stop();
    }
    const a = origin.getBoundingClientRect();
    const b = el.getBoundingClientRect();
    if (!b.width || !a.width) return;
    const dx = a.left + a.width / 2 - (b.left + b.width / 2);
    const dy = a.top + a.height / 2 - (b.top + b.height / 2);
    el.style.opacity = "0";
    const ctl = animate(
      el,
      {
        x: [dx, 0],
        y: [dy, 0],
        scale: [a.width / b.width, 1],
        rotate: [-tilt * 2 - 8, 0],
        opacity: [0, 1, 1],
      },
      {
        duration: TIMING.dealTravel * ds,
        delay: delay * ds,
        ease: [0.18, 0.7, 0.25, 1],
        opacity: { duration: TIMING.dealTravel * ds, times: [0, 0.18, 1], delay: delay * ds },
      },
    );
    return () => {
      ctl.stop();
      // Leave the element untouched so a StrictMode re-run measures its real position.
      el.style.transform = "";
      el.style.opacity = "";
    };
    // Only on mount: later prop changes must not replay the deal.
  }, []);

  const pose =
    emphasis === "win"
      ? { opacity: 1, y: size === "lg" ? -10 : -8, scale: 1.04 }
      : emphasis === "muted"
        ? { opacity: 0.55, y: 0, scale: 1 }
        : emphasis === "folded"
          ? { opacity: 0.32, y: exitTo.y * 0.25, scale: 0.92 }
          : { opacity: 1, y: 0, scale: 1 };

  return (
    <motion.div
      className={`shrink-0 ${className || "relative"}`}
      style={{ width, aspectRatio: "5 / 7", ...style }}
      exit={{
        opacity: 0,
        x: exitTo.x,
        y: exitTo.y,
        scale: 0.8,
        transition: { duration: TIMING.exit * ds, ease: "easeIn" },
      }}
      role="img"
      aria-label={showFace && code ? cardLabel(code) : "Face-down card"}
      data-testid={showFace && code ? `card-${code}` : "card-back"}
      data-emphasis={emphasis}
    >
      <div ref={travelRef} className="absolute inset-0">
        <motion.div
          className="absolute inset-0"
          style={{ perspective: 800, "--w": width, transformOrigin: "50% 90%" } as CSSProperties}
          initial={{ rotate: tilt, opacity: 1, y: 0, scale: 1 }}
          animate={{ rotate: tilt, ...pose }}
          transition={{ type: "spring", stiffness: 320, damping: 26 }}
        >
          <motion.div
            className="preserve-3d absolute inset-0"
            initial={{ rotateY: dealtDown ? 180 : showFace ? 0 : 180 }}
            animate={{ rotateY: showFace ? 0 : 180 }}
            transition={{ duration: TIMING.flipDuration * ds, delay: flipAt, ease: [0.4, 0.2, 0.2, 1] }}
          >
            <div
              className={`card-face backface-hidden absolute inset-0 overflow-hidden rounded-[8%] ${
                emphasis === "win" ? "card-win" : ""
              }`}
            >
              {code && <CardFace code={code} />}
            </div>
            <CardBack className="backface-hidden absolute inset-0" style={{ transform: "rotateY(180deg)" }} />
          </motion.div>
        </motion.div>
      </div>
    </motion.div>
  );
}

export function CardBack({ className = "", style }: { className?: string; style?: CSSProperties }) {
  return (
    <div className={`card-back rounded-[8%] ${className}`} style={style}>
      <div className="card-back-inner grid place-items-center">
        <SuitIcon suit="s" className="text-[#f0c96a]/70" style={{ width: "calc(var(--w) * 0.2)" }} />
      </div>
    </div>
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
