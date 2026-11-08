import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { addReview, EMPTY_PROGRESS } from "../lib/progress";
import type { DecisionReview, HandReview, TrainingAnswer, TrainingScenario } from "../types";
import { CoachPanel } from "./CoachPanel";
import { formatBb } from "./DecisionDetail";
import { TrainingView } from "./TrainingView";

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

// ------------------------------------------------------------------ training

const SCENARIO: TrainingScenario = {
  id: "v1.beginner.pot_odds.7",
  difficulty: "beginner",
  concept: "pot_odds",
  concept_label: "Pot odds",
  concept_blurb: "Facing a bet: compare the equity you need with the equity you have.",
  hero: ["3c", "2d"],
  board: ["Ah", "Kd", "Qs", "7c", "9h"],
  street: "river",
  position: "big blind",
  pot: 430,
  to_call: 200,
  stack: 685,
  opponent_stack: 485,
  big_blind: 10,
  history: ["River: Opponent bets 200"],
  hand: "High Card, Ace",
  choices: [
    { key: "fold", label: "Fold" },
    { key: "call", label: "Call 200" },
  ],
  opponent: null,
  hints: ["Pot odds: you need to win at least 32% of the time."],
};

function answerFor(choice: string): TrainingAnswer {
  const good = choice === "fold";
  return {
    id: SCENARIO.id,
    concept: "pot_odds",
    choice,
    correct: good,
    acceptable: good,
    analysis: decision({ chosen: choice, grade: good ? "good" : "mistake", grade_label: good ? "Good" : "Likely mistake" }),
    alternatives: [
      { key: "fold", label: "Fold", ev: 0, ev_bb: 0, basis: "pot odds", grade: "good", grade_label: "Good", explanation: "Folding gave up nothing.", fold_rate: null, equity_when_called: null, detail: "" },
      { key: "call", label: "Call 200", ev: -200, ev_bb: -20, basis: "pot odds", grade: "mistake", grade_label: "Likely mistake", explanation: "Calling loses.", fold_rate: null, equity_when_called: null, detail: "" },
    ],
  };
}

describe("TrainingView", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url.includes("/api/training/scenario")) return new Response(JSON.stringify(SCENARIO), { status: 200 });
        if (url.includes("/api/training/answer")) {
          const body = JSON.parse(String(init?.body));
          return new Response(JSON.stringify(answerFor(body.choice)), { status: 200 });
        }
        return new Response("{}", { status: 404 });
      }),
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  it("deals a scenario, gives immediate feedback, and counts only the first try", async () => {
    const onAnswered = vi.fn();
    render(<TrainingView progress={EMPTY_PROGRESS} onAnswered={onAnswered} />);
    await screen.findByTestId("scenario");
    expect(screen.getByTestId("hints")).toHaveTextContent(/32%/);
    expect(screen.getByTestId("recommendation")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Call 200" }));
    const feedback = await screen.findByTestId("training-feedback");
    expect(feedback).toHaveTextContent("Not the best choice");
    expect(within(screen.getByTestId("alternatives")).getAllByRole("listitem")).toHaveLength(2);
    expect(feedback).toHaveTextContent(/not solver-proven optimal/);
    expect(onAnswered).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    fireEvent.click(screen.getByRole("button", { name: "Fold" }));
    await waitFor(() => expect(screen.getByTestId("training-feedback")).toHaveTextContent("Good decision"));
    expect(screen.getByText(/Retry: not counted/)).toBeInTheDocument();
    expect(onAnswered).toHaveBeenCalledTimes(1);
  });

  it("requests the chosen difficulty and concept", async () => {
    render(<TrainingView progress={EMPTY_PROGRESS} onAnswered={() => {}} />);
    await screen.findByTestId("scenario");
    fireEvent.click(screen.getByRole("radio", { name: "Advanced" }));
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "draws" } });
    const urls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.map((c) => String(c[0]));
    expect(urls.some((u) => u.includes("difficulty=advanced"))).toBe(true);
    expect(urls.some((u) => u.includes("concept=draws"))).toBe(true);
    expect(screen.getByTestId("skills-panel")).toHaveTextContent("Drawing hands");
  });
});
