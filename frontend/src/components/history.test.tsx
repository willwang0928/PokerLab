import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ProgressStore } from "../hooks/useProgressStore";
import { EMPTY_PROGRESS } from "../lib/progress";
import fixture from "../test/hands.fixture.json";
import type { HandFrame, HandRecord, HandSummary, Weaknesses } from "../types";
import { HandReplayer } from "./HandReplayer";
import { HistoryView } from "./HistoryView";

const folded = fixture.folded as unknown as { record: HandRecord; frames: HandFrame[]; summary: HandSummary };
const showdown = fixture.showdown as unknown as { record: HandRecord; frames: HandFrame[]; summary: HandSummary };

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function replayer(hand = folded, props: Partial<Parameters<typeof HandReplayer>[0]> = {}) {
  return render(
    <HandReplayer record={hand.record} frames={hand.frames} detailed={false} onToggleDetailed={() => {}} {...props} />,
  );
}

const next = () => fireEvent.click(screen.getByRole("button", { name: "Next step" }));
const pot = () => screen.getByTestId("replay-pot").textContent;
const oppCards = () =>
  within(screen.getByTestId("replay-opponent"))
    .getAllByRole("img")
    .map((e) => e.getAttribute("aria-label"));

describe("HandReplayer", () => {
  it("steps through the recorded events with the server's pots, skipping silent collections", () => {
    replayer();
    const steps = folded.frames.filter((f) => f.type !== "bets_collected");
    expect(screen.getByTestId("replay-step")).toHaveTextContent(`Step 1 of ${steps.length}`);
    const seen: string[] = [];
    for (let i = 0; i < steps.length; i++) {
      const f = steps[i];
      // At your own decisions the table shows what you faced (the state before acting).
      const before = f.decision !== null ? folded.frames[folded.frames.indexOf(f) - 1] : f;
      expect(pot()).toContain(`Pot ${before.pot.toLocaleString("en-US")}`);
      seen.push(pot() ?? "");
      if (i < steps.length - 1) next();
    }
    expect(screen.getByRole("button", { name: "Next step" })).toBeDisabled();
    expect(seen.some((p) => p.includes("Pot 500"))).toBe(true); // hero's turn bet before the fold
  });

  it("never shows a folded opponent's cards", () => {
    const { container } = replayer();
    fireEvent.click(screen.getByRole("button", { name: "Last step" }));
    expect(oppCards()).toEqual(["Face-down card", "Face-down card"]);
    expect(container.textContent).not.toMatch(/Qs|Qd|Queen of/);
  });

  it("shows the opponent's cards only from the showdown on", () => {
    replayer(showdown);
    const steps = showdown.frames.filter((f) => f.type !== "bets_collected");
    const sd = steps.findIndex((f) => f.type === "showdown");
    for (let i = 0; i < sd; i++) {
      expect(oppCards()).toEqual(["Face-down card", "Face-down card"]);
      next();
    }
    expect(oppCards()).toEqual(["9 of diamonds", "9 of spades"]);
  });

  it("explains each decision with what was known then, apart from the result", () => {
    replayer(showdown);
    fireEvent.click(screen.getByRole("button", { name: /Your next decision/ }));
    const panel = screen.getByTestId("replay-decision");
    const d0 = showdown.record.coaching!.decisions[0];
    expect(panel).toHaveTextContent(`Your decision: You check`);
    expect(panel).toHaveTextContent(d0.grade_label);
    expect(within(panel).getByTestId("lesson")).toHaveTextContent("You checked");
    expect(within(panel).getByTestId("decision-time-note")).toHaveTextContent(
      /Later cards and the opponent's shown cards were not used, and the result of the hand does not change the grade/,
    );
    expect(screen.getByTestId("replay-your-turn")).toHaveTextContent("Your turn: you chose to check");
    // The table at the decision has no flop yet: no later cards.
    expect(within(screen.getByTestId("replay-table")).getByText("Preflop: no board yet")).toBeInTheDocument();
    // The final step keeps the result apart from decision quality.
    fireEvent.click(screen.getByRole("button", { name: "Last step" }));
    const result = screen.getByTestId("replay-result");
    expect(result).toHaveTextContent("You lost 30 chips");
    expect(result).toHaveTextContent(showdown.record.coaching!.verdict);
  });

  it("supports the keyboard, the timeline, playback and opening at a decision", () => {
    vi.useFakeTimers();
    replayer(folded, { startDecision: 2 });
    expect(screen.getByTestId("replay-decision")).toHaveTextContent("You raise to 120");
    fireEvent.keyDown(window, { key: "Home" });
    expect(screen.getByTestId("replay-step")).toHaveTextContent("Step 1 of");
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(screen.getByTestId("replay-step")).toHaveTextContent("Step 2 of");
    fireEvent.click(within(screen.getByTestId("replay-timeline")).getByText("You bet 200"));
    expect(screen.getByTestId("replay-decision")).toHaveTextContent("You bet 200");
    fireEvent.keyDown(window, { key: "End" });
    expect(screen.getByTestId("replay-result")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Play" })); // at the end: restart and play
    expect(screen.getByTestId("replay-step")).toHaveTextContent("Step 1 of");
    act(() => void vi.advanceTimersByTime(950));
    act(() => void vi.advanceTimersByTime(950));
    expect(screen.getByTestId("replay-step")).toHaveTextContent("Step 3 of");
    vi.useRealTimers();
  });

  it("offers practice for a reviewed decision", () => {
    const onPractice = vi.fn();
    replayer(folded, { onPractice, startDecision: 0 });
    fireEvent.click(screen.getByRole("button", { name: "Practise this spot" }));
    expect(onPractice).toHaveBeenCalledWith(folded.record.coaching!.decisions[0]);
  });
});

// ------------------------------------------------------------------ the History tab

const weaknesses: Weaknesses = {
  hands: 2,
  decisions: 9,
  patterns: [
    {
      key: "called_without_odds",
      title: "Calling bets without the odds",
      description: "Calls where your equity was below the price.",
      concept: "pot_odds",
      opportunities: 12,
      flagged: 6,
      rate: 0.5,
      low: 0.27,
      high: 0.73,
      hands: [{ id: folded.record.id, decision: 2, hand_number: 1, t: 0 }],
      conditions: [{ key: "large_bets", label: "facing large bets (¾ pot or more)", flagged: 4, opportunities: 5, rate: 0.8, rate_elsewhere: 0.29 }],
    },
  ],
  early: [
    {
      key: "missed_value",
      title: "Checking or calling strong hands",
      description: "",
      concept: "value_betting",
      opportunities: 4,
      flagged: 2,
      rate: 0.5,
      low: 0.1,
      high: 0.9,
      hands: [],
      missing: ["6 more chances to make this decision", "at least 3 flagged decisions"],
    },
  ],
  rules: { min_opportunities: 10, min_flagged: 3, min_rate: 0.15, interval: "90% Wilson" },
};

function fakeStore(over: Partial<ProgressStore> = {}): ProgressStore {
  return { active: { key: "k", name: "A", token: "tok" }, handsVersion: 0, pendingHands: 0, ...over } as ProgressStore;
}

function api(handStatus = 200) {
  const fn = vi.fn(async (url: string) => {
    if (url.includes("/api/profile/hands?"))
      return new Response(JSON.stringify({ hands: [showdown.summary, folded.summary], total: 2, weaknesses }));
    if (url.includes("/api/profile/hands/")) {
      if (handStatus !== 200) return new Response(JSON.stringify({ detail: "This saved hand is damaged and cannot be replayed: pot" }), { status: handStatus });
      const id = decodeURIComponent(url.split("/").pop()!);
      const hand = id === folded.record.id ? folded : showdown;
      return new Response(JSON.stringify({ record: hand.record, frames: hand.frames }));
    }
    throw new Error(url);
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

function history(store = fakeStore(), onTrain = vi.fn()) {
  render(
    <HistoryView store={store} progress={EMPTY_PROGRESS} detailed={false} onToggleDetailed={() => {}} onPractice={() => {}} onTrain={onTrain} />,
  );
  return onTrain;
}

describe("HistoryView", () => {
  it("lists saved hands, filters them, and opens one in the replayer", async () => {
    api();
    history();
    await waitFor(() => expect(screen.getAllByTestId("history-row")).toHaveLength(2));
    expect(screen.getByTestId("history-count")).toHaveTextContent("2 of 2 hands");
    fireEvent.click(screen.getByLabelText("Decisions flagged for review"));
    expect(screen.getAllByTestId("history-row")).toHaveLength(1);
    expect(screen.getByTestId("history-count")).toHaveTextContent("1 of 2 hands");
    fireEvent.click(screen.getByText(/Clear 1 filter/));
    fireEvent.change(screen.getByLabelText("Search hands"), { target: { value: "high card" } });
    expect(screen.getAllByTestId("history-row")).toHaveLength(1);
    fireEvent.click(screen.getAllByTestId("history-row")[0]);
    await waitFor(() => expect(screen.getByTestId("hand-replayer")).toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "Hand #1" })).toBeInTheDocument();
  });

  it("shows patterns with their evidence, links their hands, and starts practice", async () => {
    api();
    const onTrain = history();
    const panel = await screen.findByTestId("weakness-panel");
    const p = within(panel).getByTestId("pattern");
    expect(p).toHaveTextContent("6 of 12 such decisions flagged (50%; likely between 27% and 73%)");
    expect(p).toHaveTextContent("Mostly facing large bets (¾ pot or more): 4 of 5 (80%, versus 29% otherwise)");
    expect(within(panel).getByTestId("early-signals")).toHaveTextContent(
      "Not a pattern yet: needs 6 more chances to make this decision and at least 3 flagged decisions",
    );
    fireEvent.click(within(p).getByRole("button", { name: "#1" }));
    await waitFor(() => expect(screen.getByTestId("replay-decision")).toHaveTextContent("You raise to 120"));
    fireEvent.click(within(p).getByRole("button", { name: /Practise pot odds/ }));
    expect(onTrain).toHaveBeenCalledWith(
      expect.objectContaining({ concept: "pot_odds", difficulty: "beginner", reason: "From your hand history: calling bets without the odds" }),
    );
  });

  it("says plainly when there is not enough evidence for a pattern", async () => {
    api();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ hands: [folded.summary], total: 1, weaknesses: { ...weaknesses, patterns: [], early: [] } }))),
    );
    history();
    expect(await screen.findByTestId("no-patterns")).toHaveTextContent(
      "No recurring weakness yet. A pattern is only reported after at least 10 chances",
    );
  });

  it("reports a damaged hand instead of replaying it", async () => {
    api(422);
    history();
    await waitFor(() => expect(screen.getAllByTestId("history-row")).toHaveLength(2));
    fireEvent.click(screen.getAllByTestId("history-row")[0]);
    expect(await screen.findByRole("alert")).toHaveTextContent("damaged and cannot be replayed");
  });

  it("explains where hands are before the profile is on the server", () => {
    history(fakeStore({ active: { key: "k", name: "A" }, pendingHands: 2 }));
    expect(screen.getByTestId("history-view")).toHaveTextContent("2 finished hands are waiting on this device");
  });
});
