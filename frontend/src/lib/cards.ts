export type SuitChar = "s" | "h" | "d" | "c";

export const SUIT_SYMBOL: Record<SuitChar, string> = { s: "♠", h: "♥", d: "♦", c: "♣" };
export const SUIT_NAME: Record<SuitChar, string> = {
  s: "spades",
  h: "hearts",
  d: "diamonds",
  c: "clubs",
};
const RANK_NAME: Record<string, string> = {
  T: "10", J: "Jack", Q: "Queen", K: "King", A: "Ace",
};

export function parseCard(code: string): { rank: string; suit: SuitChar; red: boolean } {
  const rank = code[0] === "T" ? "10" : code[0];
  const suit = code[1] as SuitChar;
  return { rank, suit, red: suit === "h" || suit === "d" };
}

export function cardLabel(code: string): string {
  const name = RANK_NAME[code[0]] ?? code[0];
  return `${name} of ${SUIT_NAME[code[1] as SuitChar]}`;
}
