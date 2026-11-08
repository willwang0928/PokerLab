import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { gameState, player } from "../test/fixtures";
import type { GameState, OpponentModelView, TrackedStat } from "../types";
import { TendenciesPanel } from "./TendenciesPanel";

function stat(overrides: Partial<TrackedStat>): TrackedStat {
  return {
    key: "fold_to_bet",
    label: "Fold to bet",
    hint: "",
    rate: 0.62,
    count: 15.4,
    opportunities: 24.8,
    estimate: 0.55,
    baseline: 0.4,
    reliable: true,
    ...overrides,
  };
}

function model(overrides: Partial<OpponentModelView> = {}): OpponentModelView {
  return {
    adapts: true,
    weight: 1,
    policy: "Advanced adapts fully, using each stat once it has 15 or more opportunities, within fixed limits.",
    hands: 31,
    min_opportunities: 15,
    stats: [stat({}), stat({ key: "wsd", label: "Won at showdown", rate: null, count: 0, opportunities: 0, baseline: 0.5, reliable: false })],
    adjustments: ["You fold to 55% of bets (reference 40%), so it bluffs more often."],
    ...overrides,
  };
}

function withModel(m: OpponentModelView | null, extra: Partial<GameState> = {}): GameState {
  return gameState({
    players: [player(), player({ seat: 1, name: "Dealer Bot", is_human: false })],
    opponent_model: m,
    ...extra,
  });
}

describe("TendenciesPanel", () => {
  it("shows observed rates with samples, references and reliability", () => {
    render(<TendenciesPanel state={withModel(model())} />);
    const rows = within(screen.getByTestId("tendencies-panel")).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("Fold to bet62%15/2540%");
    expect(rows[2]).toHaveTextContent("Won at showdown—0/0 · needs 1550%");
    expect(screen.getByText("31 hands")).toBeInTheDocument();
  });

  it("lists active adjustments and the difficulty's policy", () => {
    render(<TendenciesPanel state={withModel(model())} />);
    expect(screen.getByTestId("adaptation-policy")).toHaveTextContent("Advanced adapts fully");
    expect(screen.getByTestId("adjustments")).toHaveTextContent("bluffs more often");
  });

  it("explains an empty adjustment list", () => {
    render(<TendenciesPanel state={withModel(model({ adjustments: [] }))} />);
    expect(screen.getByTestId("adjustments")).toHaveTextContent("None yet");
  });

  it("shows last-hand notes only after the hand, including when nothing changed", () => {
    const result = { winnings: { 0: 20 }, uncontested: true, pots: [], showdown: null };
    const { rerender } = render(
      <TendenciesPanel
        state={withModel(model(), {
          status: "waiting",
          result: { ...result, adaptation_notes: ["River: chose to bet 60 instead of checking. You fold to 55% of bets"] },
        })}
      />,
    );
    expect(screen.getByTestId("adaptation-notes")).toHaveTextContent("instead of checking");
    rerender(<TendenciesPanel state={withModel(model(), { status: "waiting", result: { ...result, adaptation_notes: [] } })} />);
    expect(screen.getByTestId("adaptation-notes")).toHaveTextContent("did not change any");
    rerender(<TendenciesPanel state={withModel(model(), { status: "in_hand", result: null })} />);
    expect(screen.queryByTestId("adaptation-notes")).not.toBeInTheDocument();
  });

  it("for a non-adapting opponent, shows stats and policy but no adjustments", () => {
    const m = model({ adapts: false, weight: 0, adjustments: [], policy: "Beginner does not track your play. Your stats are shown for your own review." });
    render(<TendenciesPanel state={withModel(m)} />);
    expect(screen.getByTestId("adaptation-policy")).toHaveTextContent("Beginner does not track");
    expect(screen.queryByTestId("adjustments")).not.toBeInTheDocument();
  });
});
