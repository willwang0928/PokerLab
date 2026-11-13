import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { addReview, EMPTY_PROGRESS } from "../lib/progress";
import type { DecisionReview, HandReview, TrainingAnswer, TrainingScenario } from "../types";
import { CoachPanel } from "./CoachPanel";
import { DecisionDetail, formatBb } from "./DecisionDetail";
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
    expect(screen.getByTestId("leaks")).toHaveTextContent(/Calling without enough equity: flagged 2 times/);
    expect(screen.getByTestId("coach-progress")).toHaveTextContent(/2 decisions reviewed/);
  });

  it("formats EV in big blinds without a misleading sign", () => {
    expect(formatBb(-200, 10)).toBe("−20.0 bb");
    expect(formatBb(12.3, 10)).toBe("+1.2 bb");
    expect(formatBb(0.2, 10)).toBe("0.0 bb");
    expect(formatBb(null, 10)).toBe("—");
  });

  it("shows how the verdict changes with a tighter or looser range", () => {
    const d = decision({
      equity: 0.432,
      required_equity: 0.317,
      chosen: "call",
      grade: "good",
      confidence: "low",
      robust: false,
      options: [
        { key: "fold", action: "fold", amount: null, label: "Fold", ev: 0, basis: "pot odds", equity_when_called: null, fold_rate: null, detail: "" },
        { key: "call", action: "call", amount: null, label: "Call 200", ev: 72, basis: "pot odds", equity_when_called: null, fold_rate: null, detail: "" },
      ],
      range_check: [
        { name: "tighter", label: "Tighter range", description: "stronger hands only", range_size: 40, equity: 0, evs: { fold: 0, call: -200 } },
        { name: "looser", label: "Looser range", description: "more hands", range_size: 300, equity: 0.735, evs: { fold: 0, call: 263 } },
      ],
    });
    render(<DecisionDetail review={d} detailed />);
    const table = screen.getByTestId("range-check");
    const rows = within(table).getAllByRole("row").slice(1).map((r) => r.textContent);
    expect(rows[0]).toMatch(/Estimated range.*43%.*Call 200/);
    expect(rows[1]).toMatch(/Tighter range.*<1%|Tighter range.*0%/);
    expect(rows[1]).toMatch(/Fold$/);
    expect(rows[2]).toMatch(/Looser range.*74%.*Call 200/);
    expect(screen.getByTestId("robustness").textContent).toMatch(/not a settled answer/);
    expect(screen.getByText(/Low confidence: the verdict depends on assumptions/)).toBeTruthy();
    expect(screen.getByTestId("number-kinds").textContent).toMatch(/None of this is\s+solver output/);
  });

  it("shows Monte Carlo uncertainty but none for exact equity", () => {
    const { unmount } = render(<DecisionDetail review={decision({ equity: 0.4, equity_method: "monte_carlo", equity_se: 0.008 })} detailed={false} />);
    expect(screen.getByTestId("key-numbers").textContent).toMatch(/40%\s*±2%/);
    unmount();
    render(<DecisionDetail review={decision({ equity: 0.4 })} detailed={false} />);
    expect(screen.getByTestId("key-numbers").textContent).not.toMatch(/±/);
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

describe("DecisionDetail lesson (v0.4.2)", () => {
  const lesson = {
    did: "You called (Call 200) on the river with High Card, Ace, as the big blind.",
    known: ["Your cards: 3c 2d", "The board: Ah Kd Qs 7c 9h", "Pot 430, 200 to call"],
    estimated: ["Their range: about 187 hand combinations, estimated from their actions"],
    math: [
      { text: "Price: you needed 32% equity (200 to win 630).", kind: "exact" as const },
      { text: "Your equity against their estimated range: about 0%.", kind: "estimate" as const },
      { text: "With betting still to come, a call must realise at least 91% of that equity to pay.", kind: "assumption" as const },
    ],
    alternative: "Fold scored about +20.0 bb better.",
    principle: "Judge a hand against their likely range, not on its own.",
    also: ["Before the river, equity is only worth what you can realise."],
  };

  it("walks through the decision with each number labelled by kind", () => {
    render(<DecisionDetail review={decision({ lesson })} detailed={false} />);
    const steps = screen.getByTestId("lesson");
    expect(steps).toHaveTextContent("You called (Call 200)");
    expect(steps).toHaveTextContent("Your cards: 3c 2d · The board");
    expect(steps).toHaveTextContent("about 187 hand combinations");
    const kinds = [...screen.getByTestId("lesson-math").querySelectorAll("[data-kind]")].map((e) =>
      e.getAttribute("data-kind"),
    );
    expect(kinds).toEqual(["exact", "estimate", "assumption"]);
    expect(screen.getByTestId("lesson-principle")).toHaveTextContent(/Judge a hand.*Before the river/);
  });

  it("still renders reviews saved before lessons existed", () => {
    render(<DecisionDetail review={decision({ lesson: {} })} detailed />);
    expect(screen.queryByTestId("lesson")).not.toBeInTheDocument();
    expect(screen.getByText(/You needed 32% equity/)).toBeInTheDocument();
  });

  it("shows opponent tendencies with their sample size in the detailed view", () => {
    const opponent = {
      evidence: [
        { key: "vpip", phrase: "plays a hand preflop", estimate: 0.8, low: 0.7, high: 0.9, reference: 0.83, seen: 6, reliable: false, used: false, clear: false },
        { key: "fold_to_bet", phrase: "folds to a bet", estimate: 0.0, low: 0, high: 0, reference: 0.4, seen: 0, reliable: false, used: false, clear: false },
      ],
      used: [],
      style: [],
      note: "Too few hands to read the opponent yet.",
    };
    const { rerender } = render(<DecisionDetail review={decision({ opponent })} detailed={false} />);
    expect(screen.queryByTestId("opponent-evidence")).not.toBeInTheDocument();
    rerender(<DecisionDetail review={decision({ opponent })} detailed />);
    const box = screen.getByTestId("opponent-evidence");
    expect(box).toHaveTextContent("Too few hands");
    expect(box).toHaveTextContent(/plays a hand preflop.*80%.*70%–90%.*6too few/);
    expect(box).not.toHaveTextContent("folds to a bet"); // never observed: not listed
  });
});

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

  it("ignores a scenario that arrives after a newer one was requested", async () => {
    let release: (() => void) | null = null;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url.includes("difficulty=advanced")) {
          await new Promise<void>((r) => (release = r)); // the slow, older request
          return new Response(JSON.stringify({ ...SCENARIO, id: "old", concept_label: "Old one" }), { status: 200 });
        }
        if (url.includes("/api/training/scenario")) return new Response(JSON.stringify(SCENARIO), { status: 200 });
        if (url.includes("/api/training/answer")) {
          const body = JSON.parse(String(init?.body));
          return new Response(JSON.stringify(answerFor(body.choice)), { status: 200 });
        }
        return new Response("{}", { status: 404 });
      }),
    );
    render(<TrainingView progress={EMPTY_PROGRESS} onAnswered={() => {}} />);
    await screen.findByTestId("scenario");
    fireEvent.click(screen.getByRole("radio", { name: "Advanced" })); // slow
    fireEvent.click(screen.getByRole("radio", { name: "Beginner" })); // fast, newer
    await waitFor(() => expect(screen.queryByTestId("dealing")).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Fold" }));
    await screen.findByTestId("training-feedback");
    release!(); // the stale scenario finally arrives
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByTestId("training-feedback")).toBeInTheDocument(); // not wiped
    expect(screen.getByTestId("scenario")).toHaveAttribute("data-id", SCENARIO.id);
  });
});
