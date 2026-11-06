/** Accessibility regression tests from the v0.2.4 audit. */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AnimatePresence } from "motion/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { ActionBar } from "./components/ActionBar";
import { HandLog } from "./components/HandLog";
import { Modal } from "./components/Modal";
import { gameState, legal } from "./test/fixtures";
import type { GameState } from "./types";

const waiting = (s: GameState): GameState => ({ ...s, status: "waiting", legal: null, to_act: null });

beforeEach(() => {
  localStorage.setItem("pokerlab.settings.v1", JSON.stringify({ speed: "instant", autoDeal: false }));
});
afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  document.getElementById("root")?.remove();
});

function mockGame(dealt: GameState) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const body = url.endsWith("/api/sessions")
        ? { state: waiting({ ...dealt, hand_number: 0 }), steps: [] }
        : { state: dealt, steps: [] };
      return new Response(JSON.stringify(body));
    }),
  );
}

describe("dialogs", () => {
  it("make the app inert, take focus, close on Escape and give focus back", async () => {
    const root = document.createElement("div");
    root.id = "root";
    document.body.appendChild(root);
    const trigger = document.createElement("button");
    root.appendChild(trigger);
    const mount = document.createElement("div");
    root.appendChild(mount);
    trigger.focus();
    const onClose = vi.fn();

    const { rerender } = render(
      <AnimatePresence>
        <Modal key="m" labelledBy="t" onClose={onClose}>
          <h2 id="t">Title</h2>
          <button type="button">Other</button>
          <button type="button" data-autofocus>
            Main
          </button>
        </Modal>
      </AnimatePresence>,
      { container: mount },
    );
    expect(root).toHaveAttribute("inert");
    expect(screen.getByRole("dialog", { name: "Title" })).toHaveAttribute("aria-modal", "true");
    expect(screen.getByRole("button", { name: "Main" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();

    rerender(<AnimatePresence>{null}</AnimatePresence>);
    await waitFor(() => expect(root).not.toHaveAttribute("inert"));
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("lets Escape close the new-game dialog and return to the current game", async () => {
    mockGame(gameState());
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "Take a seat" }));
    await screen.findByRole("button", { name: /Call 5/ });
    await userEvent.click(screen.getByRole("button", { name: "New game" }));
    expect(screen.getByRole("button", { name: "Start new game" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: /Call 5/ })).toBeInTheDocument();
  });
});

describe("betting controls", () => {
  it("have clean button names with their shortcuts exposed separately", () => {
    render(<ActionBar legal={legal()} pot={15} currentBet={10} bigBlind={10} disabled={false} onAction={() => {}} />);
    expect(screen.getByRole("group", { name: "Betting controls" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Fold" })).toHaveAttribute("aria-keyshortcuts", "F");
    expect(screen.getByRole("button", { name: "Call 5" })).toHaveAttribute("aria-keyshortcuts", "C");
    expect(screen.getByRole("button", { name: "Raise to 20" })).toHaveAttribute("aria-keyshortcuts", "R");
  });

  it("take keyboard focus when they appear if a keyboard player's focus was lost", () => {
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" }));
    (document.activeElement as HTMLElement | null)?.blur();
    render(<ActionBar legal={legal()} pot={15} currentBet={10} bigBlind={10} disabled={false} onAction={() => {}} />);
    expect(screen.getByRole("button", { name: "Call 5" })).toHaveFocus();
  });

  it("don't grab focus for pointer and touch players", () => {
    window.dispatchEvent(new Event("pointerdown"));
    (document.activeElement as HTMLElement | null)?.blur();
    render(<ActionBar legal={legal()} pot={15} currentBet={10} bigBlind={10} disabled={false} onAction={() => {}} />);
    expect(screen.getByRole("button", { name: "Call 5" })).not.toHaveFocus();
  });

  it("leave focus alone if the player is somewhere else", () => {
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab" }));
    const other = document.createElement("input");
    document.body.appendChild(other);
    other.focus();
    render(<ActionBar legal={legal()} pot={15} currentBet={10} bigBlind={10} disabled={false} onAction={() => {}} />);
    expect(other).toHaveFocus();
    other.remove();
  });
});

describe("screen-reader announcements", () => {
  it("announce the player's turn and options", async () => {
    mockGame(gameState({ legal: legal({ call_amount: 5 }) }));
    render(<App />);
    expect(screen.getByTestId("turn-announcer")).toHaveTextContent("");
    await userEvent.click(screen.getByRole("button", { name: "Take a seat" }));
    await waitFor(() =>
      expect(screen.getByTestId("turn-announcer")).toHaveTextContent("Your turn: fold, call 5, raise."),
    );
  });

  it("read the cards in the hand history", () => {
    render(<HandLog lines={["Flop: 2c 7h 9d", "Dealer Bot shows Ah Kd (Pair of Aces)"]} />);
    const history = screen.getByRole("region", { name: "Hand history" });
    expect(within(history).getByRole("img", { name: "7 of hearts" })).toBeInTheDocument();
    expect(within(history).getByRole("img", { name: "Ace of hearts" })).toBeInTheDocument();
    expect(within(history).getByRole("list")).toHaveAttribute("tabindex", "0");
  });

  it("give the page a top-level heading", () => {
    render(<App />);
    expect(screen.getAllByRole("heading", { level: 1, name: "PokerLab" }).length).toBeGreaterThan(0);
  });
});
