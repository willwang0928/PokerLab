import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { GameConfig } from "../types";
import { StartScreen } from "./StartScreen";

const INITIAL: GameConfig = {
  player_name: "You",
  starting_stack: 1000,
  small_blind: 5,
  big_blind: 10,
  difficulty: "intermediate",
  adaptive: true,
};

describe("StartScreen difficulty", () => {
  it("defaults to the initial level and explains each choice", () => {
    render(<StartScreen initial={INITIAL} busy={false} error={null} onStart={() => {}} />);
    expect(screen.getByRole("button", { name: "Intermediate" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("difficulty-hint")).toHaveTextContent("pot odds");
    fireEvent.click(screen.getByRole("button", { name: "Beginner" }));
    expect(screen.getByTestId("difficulty-hint")).toHaveTextContent("Plays its own cards");
    fireEvent.click(screen.getByRole("button", { name: "Advanced" }));
    expect(screen.getByTestId("difficulty-hint")).toHaveTextContent("expected value");
    expect(screen.getByTestId("difficulty-hint")).toHaveTextContent("not a solver");
  });

  it("starts the game with the chosen level", () => {
    const onStart = vi.fn();
    render(<StartScreen initial={INITIAL} busy={false} error={null} onStart={onStart} />);
    fireEvent.click(screen.getByRole("button", { name: "Advanced" }));
    fireEvent.click(screen.getByRole("button", { name: "Take a seat" }));
    expect(onStart).toHaveBeenCalledWith({ ...INITIAL, difficulty: "advanced" });
  });
});

describe("StartScreen adaptation toggle", () => {
  it("sends the choice and is disabled for Beginner", () => {
    const onStart = vi.fn();
    render(<StartScreen initial={INITIAL} busy={false} error={null} onStart={onStart} />);
    const toggle = screen.getByRole("checkbox", { name: /adapts to your play/ });
    expect(toggle).toBeChecked();
    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole("button", { name: "Take a seat" }));
    expect(onStart).toHaveBeenCalledWith({ ...INITIAL, adaptive: false });
    fireEvent.click(screen.getByRole("button", { name: "Beginner" }));
    expect(toggle).toBeDisabled();
    expect(screen.getByText(/Beginner never adapts/)).toBeInTheDocument();
  });
});
