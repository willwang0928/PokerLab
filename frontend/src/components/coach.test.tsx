import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { addReview, EMPTY_PROGRESS } from "../lib/progress";
import type { DecisionReview, HandReview } from "../types";
import { CoachPanel } from "./CoachPanel";
import { formatBb } from "./DecisionDetail";

function decision(overrides: Partial<DecisionReview> = {}): DecisionReview {
  return {
    street: "river",
    hole: ["3c", "2d"],
    board: ["Ah", "Kd", "Qs", "7c", "9h"],
    hand: "High Card, Ace",
    position: "big blind",
    pot: 430,
    to_call: 200,
    stack: 685,
    big_blind: 10,
    history: ["River: Opponent bets 200"],
    spot: "facing_bet",
    concept: "pot_odds",
    equity: 0,
    equity_se: 0,
    equity_method: "exact",
    required_equity: 0.317,
    range_size: 187,
    range_steps: ["Preflop: the top 70% of hands (from their raise)"],
    reads: [],
    options: [
      { key: "fold", action: "fold", amount: null, label: "Fold", ev: 0, basis: "pot odds", equity_when_called: null, fold_rate: null, detail: "" },
      { key: "call", action: "call", amount: null, label: "Call 200", ev: -200, basis: "pot odds", equity_when_called: null, fold_rate: null, detail: "0 × 630 − 200" },
    ],
    best: ["fold"],
    chosen: "call",
    grade: "mistake",
    grade_label: "Likely mistake",
    confidence: "high",
    ev_loss: 200,
    tags: ["called_without_odds"],
    headline: "Likely mistake",
    explanation: "You needed 32% equity to call 200 into 430.",
    details: ["EV(Call 200) = 0.000 × 630 − 200 = -200.0 chips = -20.0 bb"],
    caveats: [],
    spot_data: { hero: "3c2d" },
    ...overrides,
  };
}

function review(overrides: Partial<HandReview> = {}): HandReview {
  return {
    hand_number: 4,
    decisions: [decision()],
    counts: { good: 0, close: 0, questionable: 0, mistake: 1, ungraded: 0 },
    net: 630,
    net_bb: 63,
    ev_loss_bb: 20,
    quality: "costly",
    verdict: "You won 630 chips, but 1 decision gave up value by these estimates.",
    hindsight: "The opponent's cards shown at showdown were not used.",
    tags: ["called_without_odds"],
    ...overrides,
  };
}

describe("CoachPanel", () => {
  it("separates the result from decision quality and explains each decision", () => {
    const onPractice = vi.fn();
    render(
      <CoachPanel
        review={review()}
        loading={false}
        error={null}
        progress={EMPTY_PROGRESS}
        detailed={false}
        onToggleDetailed={() => {}}
        onPractice={onPractice}
      />,
    );
    expect(screen.getByTestId("coach-verdict")).toHaveTextContent(/won 630 chips, but 1 decision gave up value/);
    expect(screen.getByText(/shown at showdown were not used/)).toBeInTheDocument();
    const d = screen.getByTestId("coach-decision");
    expect(within(d).getByText("Likely mistake")).toBeInTheDocument();
    expect(within(d).getAllByText("Call 200").length).toBeGreaterThan(0);
    expect(within(d).getByTestId("options-table")).toHaveTextContent(/best estimate/);
    expect(screen.queryByTestId("decision-maths")).toBeNull();
    fireEvent.click(within(d).getByRole("button", { name: "Practise this spot" }));
    expect(onPractice).toHaveBeenCalledWith(expect.objectContaining({ chosen: "call" }));
  });

  it("shows the maths when asked, and recurring leaks from progress", () => {
    let progress = EMPTY_PROGRESS;
    progress = addReview(progress, "s", review({ hand_number: 1 }));
    progress = addReview(progress, "s", review({ hand_number: 2 }));
    render(
      <CoachPanel
        review={review()}
        loading={false}
        error={null}
        progress={progress}
        detailed
        onToggleDetailed={() => {}}
        onPractice={() => {}}
      />,
    );
    expect(screen.getByTestId("decision-maths")).toHaveTextContent(/EV\(Call 200\)/);
    expect(screen.getByTestId("decision-maths")).toHaveTextContent(/top 70% of hands/);
    expect(screen.getByTestId("leaks")).toHaveTextContent(/Calling without enough equity: 2 times/);
    expect(screen.getByTestId("coach-progress")).toHaveTextContent(/2 decisions reviewed/);
  });

  it("formats EV in big blinds without a misleading sign", () => {
    expect(formatBb(-200, 10)).toBe("−20.0 bb");
    expect(formatBb(12.3, 10)).toBe("+1.2 bb");
    expect(formatBb(0.2, 10)).toBe("0.0 bb");
    expect(formatBb(null, 10)).toBe("—");
  });
});
