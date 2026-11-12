import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { EMPTY_PROGRESS, type Progress } from "../lib/progress";
import { recordAnswer } from "../lib/skills";
import { callingLeak, leakReport } from "../test/leaks";
import type { LeakItem, LeakReport } from "../types";
import { ProgressDashboard } from "./ProgressDashboard";

const NOW = 2_000_000_000_000;
const ready = (report: LeakReport = leakReport) => ({ status: "ready" as const, report });

function dashboard(extra: Partial<Parameters<typeof ProgressDashboard>[0]> = {}, progress: Progress = EMPTY_PROGRESS) {
  const onStart = vi.fn();
  const onShowHand = vi.fn();
  render(<ProgressDashboard progress={progress} onStart={onStart} onShowHand={onShowHand} now={NOW} leaks={ready()} {...extra} />);
  return { onStart, onShowHand };
}

describe("Strengths and leaks on the Progress tab", () => {
  it("explains a leak with its numbers, the reason it matters, its hands and practice", () => {
    const { onStart, onShowHand } = dashboard();
    const card = screen.getByTestId("leak");
    expect(within(card).getByTestId("leak-evidence")).toHaveTextContent(
      "Pattern: 6 of 12 such decisions flagged (50%; likely between 27% and 73%). 1 more were flagged with low confidence and are not counted.",
    );
    expect(card).toHaveTextContent("Mostly on the river: 5 of 6 (83%), versus 17% in your other 6.");
    expect(card).toHaveTextContent("Why it matters: A call is profitable only when your equity is above call ÷ (pot + call)");
    // The hand shows decision-time facts: equity then and the price, not the result.
    const hand = within(card).getByTestId("leak-hands");
    expect(hand).toHaveTextContent("#1");
    expect(hand).toHaveTextContent("river called: about 18% equity, 25% needed");
    // 6 flagged, 1 shown: the count refers to all of them.
    expect(within(card).getByTestId("leak-hands-more")).toHaveTextContent("The 1 most recent of 6 flagged decisions.");
    fireEvent.click(within(hand).getByRole("button"));
    expect(onShowHand).toHaveBeenCalledWith(callingLeak.hands[0].id, 2);
    fireEvent.click(within(card).getByTestId("leak-practice"));
    expect(onStart).toHaveBeenCalledWith(
      expect.objectContaining({ concept: "pot_odds", difficulty: "beginner", focus: "river", leak: "called_without_odds" }),
    );
    expect(within(card).getByTestId("leak-progress")).toHaveTextContent("No practice aimed at this leak yet.");
    expect(within(card).getByTestId("leak-progress")).toHaveTextContent("Not enough decisions yet to compare");
  });

  it("shows strengths, early signals and missing data without calling them leaks", () => {
    dashboard();
    expect(screen.getByTestId("strengths")).toHaveTextContent("Folds facing bets: flagged in 1 of 28 confidently graded decisions (likely under 8%)");
    expect(screen.getByTestId("watch")).toHaveTextContent("Not a leak yet: needs 6 more chances at this decision");
    expect(screen.getByTestId("leaks-no-data")).toHaveTextContent("folding too much preflop");
    expect(screen.getAllByTestId("leak")).toHaveLength(1);
  });

  it("says when there are too few hands, and shows no leak cards", () => {
    dashboard({ leaks: ready({ ...leakReport, status: "insufficient", needed: 12, leaks: [], strengths: [] }) });
    expect(screen.getByTestId("leaks-insufficient")).toHaveTextContent("12 more graded decisions needed");
    expect(screen.queryByTestId("leak")).toBeNull();
  });

  it("marks low-confidence evidence as uncertain", () => {
    const uncertain: LeakItem = { ...callingLeak, status: "uncertain", flagged: 1, uncertain: 5, missing: ["5 of the 6 flagged decisions depended on an assumption"] };
    dashboard({ leaks: ready({ ...leakReport, leaks: [], watch: [uncertain] }) });
    expect(screen.getByTestId("watch")).toHaveTextContent("The coach is not sure: 5 of the 6 flagged decisions depended on an assumption");
    expect(screen.getByTestId("no-leaks")).toBeInTheDocument();
  });

  it("tracks practice and the table since, wording the change by its evidence", () => {
    const practised: LeakItem = {
      ...callingLeak,
      practice: {
        answers: 12,
        score: 0.75,
        recent_score: 0.9,
        first_at: NOW - 1e6,
        last_at: NOW,
        since: { before: { flagged: 8, opportunities: 10, rate: 0.8 }, after: { flagged: 1, opportunities: 10, rate: 0.1 }, change: "improving" },
      },
    };
    dashboard({ leaks: ready({ ...leakReport, leaks: [practised] }) });
    const p = screen.getByTestId("leak-progress");
    expect(p).toHaveTextContent("12 practice answers aimed at this leak, 75% good or close (last 10: 90%).");
    expect(p).toHaveTextContent("Improving: flagged in 1 of 10 since (10%) versus 8 of 10 before (80%)");
  });

  it("does not claim a change the test does not support", () => {
    const flat: LeakItem = {
      ...callingLeak,
      trend: { before: { flagged: 4, opportunities: 10, rate: 0.4 }, after: { flagged: 3, opportunities: 10, rate: 0.3 }, change: "no_clear_change" },
    };
    dashboard({ leaks: ready({ ...leakReport, leaks: [flat] }) });
    expect(screen.getByTestId("leak-progress")).toHaveTextContent("No clear change yet: 3 of 10 in your recent half (30%)");
  });

  it("recommends the leak next unless a review is due", () => {
    const { onStart } = dashboard();
    expect(screen.getByTestId("next-practice")).toHaveTextContent("Recurring leak in your hands: calling bets without the odds (6 of 12");
    fireEvent.click(screen.getByRole("button", { name: "Start practice" }));
    expect(onStart).toHaveBeenCalledWith(expect.objectContaining({ leak: "called_without_odds", focus: "river" }));
  });

  it("keeps a due review first", () => {
    const p = recordAnswer(EMPTY_PROGRESS, { id: "x1", concept: "draws", difficulty: "intermediate", grade: "mistake" }, NOW - 20 * 60_000);
    dashboard({}, p);
    expect(screen.getByTestId("next-practice")).toHaveTextContent(/Review/);
  });

  it("shows only device-local flags without a server profile, labelled as such", () => {
    dashboard({ leaks: { status: "none" } });
    expect(screen.queryByTestId("leak-panel")).toBeNull();
    expect(screen.getByTestId("dashboard-mistakes")).toHaveTextContent("Counted on this device only");
  });

  it("hides the device-local flags when the report is available", () => {
    dashboard();
    expect(screen.queryByTestId("dashboard-mistakes")).toBeNull();
  });

  it("reports a failed load, keeps the last report, and retries", () => {
    const onRetryLeaks = vi.fn();
    dashboard({ leaks: { status: "error", message: "Your leak report could not be loaded.", report: leakReport }, onRetryLeaks });
    expect(screen.getByRole("alert")).toHaveTextContent("could not be loaded");
    expect(screen.getByTestId("leak")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetryLeaks).toHaveBeenCalled();
  });

  it("highlights the leak a link pointed to", () => {
    dashboard({ focusLeak: { key: "called_without_odds", n: 1 } });
    expect(screen.getByTestId("leak").className).toContain("ring-amber-300/70");
  });
});
