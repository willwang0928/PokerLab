import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { gameState, player } from "../test/fixtures";
import type { GameState, HandResult } from "../types";
import { EquityPanel, formatMargin, formatPercent } from "./EquityPanel";

function finished(result: Partial<HandResult>): GameState {
  return gameState({
    status: "waiting",
    players: [player(), player({ seat: 1, name: "Dealer Bot", is_human: false, hole: ["Qs", "Qd"] })],
    result: { winnings: { 1: 200 }, uncontested: false, pots: [], showdown: null, ...result },
  });
}

const EQUITY: HandResult["equity"] = [
  { street: "preflop", method: "monte_carlo", equity: { 0: 0.4512, 1: 0.5488 }, std_error: { 0: 0.004, 1: 0.004 } },
  { street: "flop", method: "exact", equity: { 0: 0.2556, 1: 0.7444 }, std_error: { 0: 0, 1: 0 } },
  { street: "turn", method: "exact", equity: { 0: 0.0045, 1: 0.9955 }, std_error: { 0: 0, 1: 0 } },
];

describe("formatPercent", () => {
  it("never rounds an uncertain outcome to a certainty", () => {
    expect(formatPercent(0)).toBe("0%");
    expect(formatPercent(1)).toBe("100%");
    expect(formatPercent(0.004)).toBe("<1%");
    expect(formatPercent(0.996)).toBe(">99%");
    expect(formatPercent(0.4512)).toBe("45%");
  });

  it("shows a 95% margin only for Monte Carlo estimates", () => {
    expect(formatMargin(EQUITY![0], 0)).toBe("±0.8%");
    expect(formatMargin(EQUITY![1], 0)).toBe("");
  });
});

describe("EquityPanel", () => {
  it("lists each street with both players' equity, estimates marked", () => {
    render(<EquityPanel state={finished({ equity: EQUITY })} />);
    const rows = within(screen.getByTestId("equity-panel")).getAllByRole("row");
    expect(rows[0]).toHaveTextContent("You");
    expect(rows[0]).toHaveTextContent("Dealer Bot");
    expect(rows[1]).toHaveTextContent(/Preflop\s*\(est\.\)\s*45%±0\.8%55%±0\.8%/);
    expect(rows[2]).toHaveTextContent("Flop26%74%");
    expect(rows[2]).not.toHaveTextContent("est.");
    expect(rows[3]).toHaveTextContent("Turn<1%>99%");
  });

  it("stays hidden without a showdown or while a hand is in progress", () => {
    const { rerender } = render(<EquityPanel state={finished({ equity: null })} />);
    expect(screen.queryByTestId("equity-panel")).not.toBeInTheDocument();
    rerender(<EquityPanel state={{ ...finished({ equity: EQUITY }), status: "in_hand" }} />);
    expect(screen.queryByTestId("equity-panel")).not.toBeInTheDocument();
  });
});
