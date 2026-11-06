import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { gameState, player } from "../test/fixtures";
import type { GameState } from "../types";
import { chipMoves } from "./ChipFlights";
import { Seat } from "./Seat";
import { Table, winningCards } from "./Table";

const seatBase = {
  isButton: false,
  isTurn: false,
  thinking: false,
  isWinner: false,
  winAmount: 0,
  position: "bottom" as const,
  handNumber: 1,
  handOver: false,
};

function withBets(s: GameState, bets: [number, number], collected = 0): GameState {
  return {
    ...s,
    collected_pot: collected,
    players: s.players.map((p, i) => ({ ...p, bet: bets[i] })) as GameState["players"],
  };
}

describe("chipMoves", () => {
  const base = gameState();

  it("moves blinds and the added part of a call or raise from stack to bet", () => {
    expect(chipMoves({ type: "post_blind", seat: 1, amount: 10 }, null, base)).toEqual([
      { from: "stack-1", to: "bet-1", amount: 10 },
    ]);
    const before = withBets(base, [5, 10]);
    const after = withBets(base, [30, 10]);
    expect(chipMoves({ type: "action", seat: 0, action: "raise" }, before, after)).toEqual([
      { from: "stack-0", to: "bet-0", amount: 25 },
    ]);
    expect(chipMoves({ type: "action", seat: 0, action: "check" }, after, after)).toEqual([]);
  });

  it("sweeps every bet into the pot when the round ends", () => {
    const before = withBets(base, [20, 20]);
    const after = withBets(base, [0, 0], 40);
    expect(chipMoves({ type: "bets_collected" }, before, after)).toEqual([
      { from: "bet-0", to: "pot", amount: 20 },
      { from: "bet-1", to: "pot", amount: 20 },
    ]);
  });

  it("returns uncalled chips to their owner", () => {
    expect(chipMoves({ type: "uncalled_returned", seat: 1, amount: 15 }, base, base)).toEqual([
      { from: "bet-1", to: "stack-1", amount: 15 },
    ]);
  });

  it("pays each winner their share at showdown", () => {
    const before = withBets(base, [0, 0], 200);
    const ev = { type: "pot_awarded", payouts: { 0: 100, 1: 100 }, hand: "Pair of Kings", winners: [0, 1] };
    expect(chipMoves(ev, before, before)).toEqual([
      { from: "pot", to: "stack-0", amount: 100, slow: true },
      { from: "pot", to: "stack-1", amount: 100, slow: true },
    ]);
  });

  it("gives an uncontested pot and any bets still out to the winner", () => {
    const before = withBets(base, [10, 10], 40);
    const ev = { type: "pot_awarded", payouts: { 1: 60 }, hand: null, winners: [1] };
    expect(chipMoves(ev, before, withBets(base, [0, 0]))).toEqual([
      { from: "bet-0", to: "stack-1", amount: 10, slow: true },
      { from: "bet-1", to: "stack-1", amount: 10, slow: true },
      { from: "pot", to: "stack-1", amount: 40, slow: true },
    ]);
  });
});

describe("winningCards", () => {
  it("uses the pot being awarded, then the main pot once the hand is over", () => {
    const s = gameState();
    expect(winningCards(s, { type: "pot_awarded", cards: ["As", "Kd"] })).toEqual(new Set(["As", "Kd"]));
    expect(winningCards(s, { type: "pot_awarded", cards: [] })).toBeNull();
    const done = gameState({
      status: "waiting",
      result: {
        winnings: { 0: 40 },
        uncontested: false,
        pots: [{ amount: 40, winners: [0], hand: "Pair of Aces", cards: ["As", "Ah"] }],
        showdown: null,
      },
    });
    expect(winningCards(done, { type: "hand_complete" })).toEqual(new Set(["As", "Ah"]));
    expect(winningCards(gameState(), { type: "action" })).toBeNull();
  });
});

describe("showdown highlighting", () => {
  it("lifts the winning cards and mutes the rest", () => {
    const state = gameState({
      status: "waiting",
      street: "complete",
      board: ["Ah", "Kc", "7d", "2s", "9h"],
      legal: null,
      players: [
        player({ hole: ["As", "Qd"], bet: 0 }),
        player({ seat: 1, name: "Dealer Bot", is_human: false, hole: ["3c", "4c"], bet: 0 }),
      ],
      result: {
        winnings: { 0: 40 },
        uncontested: false,
        pots: [{ amount: 40, winners: [0], hand: "Pair of Aces", cards: ["As", "Ah", "Kc", "Qd", "9h"] }],
        showdown: null,
      },
    });
    render(<Table state={state} lastEvent={{ type: "hand_complete" }} thinkingSeat={null} humanTurn={false} />);
    expect(screen.getByTestId("card-Ah")).toHaveAttribute("data-emphasis", "win");
    expect(screen.getByTestId("card-As")).toHaveAttribute("data-emphasis", "win");
    expect(screen.getByTestId("card-7d")).toHaveAttribute("data-emphasis", "muted");
    expect(screen.getByTestId("card-3c")).toHaveAttribute("data-emphasis", "muted");
    expect(screen.getByTestId("result-banner")).toHaveTextContent("You win 40");
  });
});

describe("Seat action feedback", () => {
  it("flashes a reaction matching the action and mutes folded cards", () => {
    const { rerender } = render(
      <Seat
        {...seatBase}
        player={player({ last_action: "all-in", all_in: true })}
        actionEvent={{ type: "action", seat: 0, action: "raise", label: "all-in" }}
      />,
    );
    expect(screen.getByTestId("reaction-0")).toHaveAttribute("data-kind", "all-in");
    rerender(
      <Seat
        {...seatBase}
        player={player({ last_action: "fold", folded: true })}
        actionEvent={{ type: "action", seat: 0, action: "fold", label: "fold" }}
      />,
    );
    expect(screen.getByTestId("reaction-0")).toHaveAttribute("data-kind", "fold");
    expect(screen.getByTestId("card-As")).toHaveAttribute("data-emphasis", "folded");
  });

  it("hides last-action tags once the betting round is over", () => {
    render(<Seat {...seatBase} hideAction player={player({ last_action: "call" })} />);
    expect(screen.queryByText("Call")).not.toBeInTheDocument();
  });
});
