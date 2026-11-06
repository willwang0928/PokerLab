import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { player } from "../test/fixtures";
import { Seat } from "./Seat";

const base = {
  isButton: false,
  isTurn: false,
  thinking: false,
  isWinner: false,
  winAmount: 0,
  position: "bottom" as const,
  handNumber: 1,
  handOver: false,
};

describe("Seat", () => {
  it("shows the human's cards face up with name and stack", () => {
    render(<Seat {...base} player={player({ stack: 1234 })} />);
    expect(screen.getByRole("img", { name: "Ace of spades" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "King of diamonds" })).toBeInTheDocument();
    expect(screen.getByTestId("stack-0")).toHaveTextContent("1,234");
  });

  it("hides the opponent's cards", () => {
    render(<Seat {...base} position="top" player={player({ seat: 1, is_human: false, hole: null })} />);
    expect(screen.getAllByRole("img", { name: "Face-down card" })).toHaveLength(2);
  });

  it("renders no cards before the deal", () => {
    render(<Seat {...base} player={player({ hole: [], has_cards: false })} />);
    expect(screen.queryAllByRole("img")).toHaveLength(0);
  });

  it("shows action labels, all-in badge, dealer button and thinking state", () => {
    const { rerender } = render(
      <Seat {...base} isButton player={player({ last_action: "raise", bet: 60, all_in: false })} />,
    );
    expect(screen.getByText("Raise 60")).toBeInTheDocument();
    expect(screen.getByTitle("Dealer button")).toBeInTheDocument();
    rerender(<Seat {...base} thinking player={player({ all_in: true })} />);
    expect(screen.getByLabelText("Thinking")).toBeInTheDocument();
    expect(screen.getAllByText(/All-in/i).length).toBeGreaterThan(0);
  });

  it("drops the amount from bet labels once chips are swept into the pot", () => {
    render(<Seat {...base} player={player({ last_action: "raise", bet: 0 })} />);
    expect(screen.getByText("Raise")).toBeInTheDocument();
  });

  it("hides action labels once the hand is over and shows winnings", () => {
    render(<Seat {...base} handOver isWinner winAmount={240} player={player({ last_action: "check" })} />);
    expect(screen.queryByText("Check")).not.toBeInTheDocument();
    expect(screen.getByTestId("win-0")).toHaveTextContent("+240");
  });
});
