import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HandLog } from "./HandLog";

describe("HandLog", () => {
  it("renders street cards as mini cards", () => {
    render(<HandLog lines={["Hand #3 — You have the button", "Turn: Kh 9s As 7d"]} />);
    const turn = screen.getByText("Turn").closest("li")!;
    expect(within(turn).getByLabelText("King of hearts")).toBeInTheDocument();
    expect(within(turn).getByLabelText("7 of diamonds")).toBeInTheDocument();
    expect(turn).not.toHaveTextContent("Kh");
  });

  it("renders shown hole cards and keeps the surrounding text", () => {
    render(<HandLog lines={["You show Qh Qc (Pair of Queens); Dealer Bot shows 5c 6c (High Card, Ace)"]} />);
    const li = screen.getByRole("listitem");
    expect(within(li).getByLabelText("Queen of clubs")).toBeInTheDocument();
    expect(within(li).getByLabelText("6 of clubs")).toBeInTheDocument();
    expect(li).toHaveTextContent("(Pair of Queens); Dealer Bot shows");
  });

  it("leaves card-like words in ordinary lines alone", () => {
    render(<HandLog lines={["As calls 10"]} />);
    expect(screen.getByRole("listitem")).toHaveTextContent("As calls 10");
    expect(screen.queryByLabelText("Ace of spades")).not.toBeInTheDocument();
  });

  it("shows an empty state", () => {
    render(<HandLog lines={[]} />);
    expect(screen.getByText("No actions yet.")).toBeInTheDocument();
  });
});
