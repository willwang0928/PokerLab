import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { EMPTY_PROGRESS, type Progress } from "../lib/progress";
import { recordAnswer } from "../lib/skills";
import type { Concept, Grade } from "../types";
import { ProgressDashboard } from "./ProgressDashboard";

const NOW = 2_000_000_000_000;

function answers(p: Progress, concept: Concept, difficulty: string, grades: Grade[], start = NOW - 3_600_000) {
  grades.forEach((g, i) => {
    p = recordAnswer(p, { id: `${concept}-${difficulty}-${start + i}`, concept, difficulty, grade: g }, start + i);
  });
  return p;
}

describe("ProgressDashboard", () => {
  it("starts empty with an honest first recommendation and no estimates", () => {
    const onStart = vi.fn();
    render(<ProgressDashboard progress={EMPTY_PROGRESS} onStart={onStart} onReset={() => {}} now={NOW} />);
    expect(screen.getByTestId("next-practice")).toHaveTextContent(/not practised this yet/);
    expect(screen.getAllByTestId("skill-insufficient")).toHaveLength(7);
    expect(screen.getByTestId("dashboard-trend")).toHaveTextContent(/after 20 first answers/);
    fireEvent.click(screen.getByRole("button", { name: "Start practice" }));
    expect(onStart).toHaveBeenCalledWith(expect.objectContaining({ difficulty: "beginner", review: false }));
  });

  it("shows measured counts next to the estimate, the trend and history", () => {
    let p = answers(EMPTY_PROGRESS, "pot_odds", "beginner", Array(12).fill("good"));
    p = answers(p, "pot_odds", "intermediate", ["good", "close", "good", "good", "mistake", "good", "good", "good", "good", "good"], NOW - 1_000_000);
    render(<ProgressDashboard progress={p} onStart={() => {}} onReset={() => {}} now={NOW} />);
    const row = screen.getByTestId("dash-skill-pot_odds");
    expect(row).toHaveTextContent(/Training: 20 of 22 good, 1 close/);
    expect(within(row).getByTestId("skill-estimate")).toHaveTextContent(/Est\. \d+% \(\d+%–\d+%\)/);
    expect(within(row).getByRole("meter")).toHaveAttribute("aria-valuetext", expect.stringMatching(/likely between/));
    expect(screen.getByTestId("trend-chart")).toBeInTheDocument();
    expect(screen.getByTestId("dashboard-history").querySelectorAll("tbody tr")).toHaveLength(15);
    expect(screen.getByText(/not real-money results/)).toBeInTheDocument();
  });

  it("puts a due review first and shows the schedule", () => {
    const p = answers(EMPTY_PROGRESS, "draws", "intermediate", ["mistake"], NOW - 20 * 60_000);
    const onStart = vi.fn();
    render(<ProgressDashboard progress={p} onStart={onStart} onReset={() => {}} now={NOW} />);
    expect(screen.getByTestId("next-practice")).toHaveTextContent(/Review/);
    expect(screen.getByTestId("dashboard-reviews")).toHaveTextContent(/Drawing hands.*Intermediate.*step 1 of 5.*due now/);
    fireEvent.click(screen.getByRole("button", { name: "Start practice" }));
    expect(onStart).toHaveBeenCalledWith(expect.objectContaining({ concept: "draws", difficulty: "intermediate", review: true }));
  });

  it("asks before deleting progress", () => {
    const onReset = vi.fn();
    render(<ProgressDashboard progress={EMPTY_PROGRESS} onStart={() => {}} onReset={onReset} now={NOW} />);
    fireEvent.click(screen.getByRole("button", { name: "Reset progress" }));
    expect(onReset).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Keep" }));
    fireEvent.click(screen.getByRole("button", { name: "Reset progress" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(onReset).toHaveBeenCalledTimes(1);
  });
});
