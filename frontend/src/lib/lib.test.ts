import { describe, expect, it } from "vitest";
import { chipColors } from "../components/ChipStack";
import { legal } from "../test/fixtures";
import { clamp, formatChips, potSizedRaiseTo } from "./betting";
import { cardLabel, parseCard } from "./cards";

describe("cards", () => {
  it("parses codes", () => {
    expect(parseCard("Th")).toEqual({ rank: "10", suit: "h", red: true });
    expect(parseCard("As")).toEqual({ rank: "A", suit: "s", red: false });
  });
  it("labels cards for screen readers", () => {
    expect(cardLabel("Qd")).toBe("Queen of diamonds");
    expect(cardLabel("7c")).toBe("7 of clubs");
  });
});

describe("betting", () => {
  it("clamps", () => {
    expect(clamp(5, 10, 20)).toBe(10);
    expect(clamp(25, 10, 20)).toBe(20);
  });
  it("sizes pot raises relative to the pot after calling", () => {
    // Facing a 10 big blind as small blind: pot 15, call 5 -> pot after call 20.
    const l = legal({ call_amount: 5, min_raise_to: 20, max_raise_to: 1000 });
    expect(potSizedRaiseTo(l, 15, 10, 1)).toBe(30);
    expect(potSizedRaiseTo(l, 15, 10, 0.5)).toBe(20);
  });
  it("sizes bets as a fraction of the pot and clamps to limits", () => {
    const l = legal({ call_amount: 0, min_raise_to: 10, max_raise_to: 50, raise_type: "bet" });
    expect(potSizedRaiseTo(l, 40, 0, 0.75)).toBe(30);
    expect(potSizedRaiseTo(l, 400, 0, 1)).toBe(50);
  });
  it("formats chips", () => {
    expect(formatChips(12500)).toBe("12,500");
  });
});

describe("chips", () => {
  it("breaks amounts into a few discs", () => {
    expect(chipColors(0)).toEqual([]);
    expect(chipColors(30)).toHaveLength(2);
    expect(chipColors(99999).length).toBeLessThanOrEqual(6);
  });
});
