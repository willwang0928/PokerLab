import type { SuitChar } from "../lib/cards";

/**
 * Vector suit glyphs. Font glyphs (♠♥♦♣) render inconsistently across platforms —
 * some substitute colour emoji — so cards and the hand log draw these instead.
 */
const PATHS: Record<Exclude<SuitChar, "c">, string> = {
  h: "M50 90C22 68 5 51 5 31 5 17 16 7 29 7c9 0 17 5 21 13 4-8 12-13 21-13 13 0 24 10 24 24 0 20-17 37-45 59Z",
  d: "M50 4C61 22 74 37 89 50 74 63 61 78 50 96 39 78 26 63 11 50 26 37 39 22 50 4Z",
  s: "M50 5C62 25 93 40 93 62c0 13-10 22-22 22-8 0-15-4-19-11 1 9 5 17 13 23H35c8-6 12-14 13-23-4 7-11 11-19 11C17 84 7 75 7 62 7 40 38 25 50 5Z",
};

export function SuitIcon({ suit, className, style }: { suit: SuitChar; className?: string; style?: React.CSSProperties }) {
  return (
    <svg viewBox="0 0 100 100" className={className} style={style} fill="currentColor" aria-hidden focusable="false">
      {suit === "c" ? (
        <>
          <circle cx="50" cy="29" r="20" />
          <circle cx="27" cy="60" r="20" />
          <circle cx="73" cy="60" r="20" />
          <path d="M42 42h16v14H42zM46 62c0 13-4 24-12 34h32c-8-10-12-21-12-34Z" />
        </>
      ) : (
        <path d={PATHS[suit]} />
      )}
    </svg>
  );
}
