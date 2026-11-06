import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { gameState, legal, player } from "./test/fixtures";
import type { GameState } from "./types";

type Handler = (url: string, init?: RequestInit) => unknown;

function mockFetch(handler: Handler) {
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const result = handler(url, init);
    if (result instanceof Response) return result;
    return new Response(JSON.stringify(result), { status: 200 });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

const waiting = (s: GameState): GameState => ({ ...s, status: "waiting", legal: null, to_act: null });

beforeEach(() => {
  localStorage.setItem("pokerlab.settings.v1", JSON.stringify({ speed: "instant", autoDeal: false }));
});
afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("App", () => {
  it("starts a game, deals, and sends the player's action", async () => {
    const dealt = gameState();
    const afterCall = gameState({
      street: "flop",
      board: ["2c", "7h", "9d"],
      collected_pot: 20,
      pot: 20,
      players: [
        player({ bet: 0, stack: 990, hand_description: "High Card, Ace" }),
        player({ seat: 1, name: "Dealer Bot", is_human: false, hole: null, bet: 0, stack: 990 }),
      ],
      legal: legal({ can_check: true, can_call: false, can_fold: false, call_amount: 0, raise_type: "bet", min_raise_to: 10, max_raise_to: 990, raise_is_all_in: true }),
      log: ["Hand #1 — You have the button", "You call 5", "Dealer Bot checks", "Flop: 2c 7h 9d"],
    });
    const fetchMock = mockFetch((url, init) => {
      if (url.endsWith("/api/sessions")) return { state: waiting({ ...dealt, hand_number: 0 }), steps: [] };
      if (url.endsWith("/hands")) return { state: dealt, steps: [{ event: { type: "deal_hole" }, state: dealt }] };
      if (url.endsWith("/actions")) {
        expect(JSON.parse(String(init?.body))).toEqual({ action: "call" });
        return { state: afterCall, steps: [{ event: { type: "street" }, state: afterCall }] };
      }
      throw new Error(`unexpected ${url}`);
    });

    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "Take a seat" }));
    expect(await screen.findByRole("img", { name: "Ace of spades" })).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: /Call 5/ }));
    const board = screen.getByTestId("board");
    expect(await within(board).findByRole("img", { name: "7 of hearts" })).toBeInTheDocument();
    await waitFor(() => expect(within(board).getAllByRole("img")).toHaveLength(3));
    expect(screen.getByTestId("hand-desc-0")).toHaveTextContent("High Card, Ace");
    expect(screen.getByRole("button", { name: /^Check/ })).toBeInTheDocument();
    expect(screen.getByText("Flop: 2c 7h 9d")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("announces the winner and lets the player deal the next hand", async () => {
    const done = waiting(
      gameState({
        board: ["2c", "7h", "9d", "Jc", "Qh"],
        players: [
          player({ stack: 1010, bet: 0 }),
          player({ seat: 1, name: "Dealer Bot", is_human: false, hole: ["8c", "8d"], bet: 0, stack: 990 }),
        ],
        result: { winnings: { "0": 20 }, uncontested: false, pots: [{ amount: 20, winners: [0], hand: "Pair of Aces" }], showdown: null },
      }),
    );
    const next = gameState({ hand_number: 2 });
    mockFetch((url) => {
      if (url.endsWith("/api/sessions")) return { state: waiting({ ...done, hand_number: 0, result: null }), steps: [] };
      if (url.endsWith("/hands")) {
        return fetchCount++ === 0 ? { state: done, steps: [] } : { state: next, steps: [] };
      }
      throw new Error(url);
    });
    let fetchCount = 0;

    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "Take a seat" }));
    const banner = await screen.findByTestId("result-banner");
    expect(banner).toHaveTextContent("You win 20");
    expect(banner).toHaveTextContent("Pair of Aces");
    expect(screen.getByRole("img", { name: "8 of clubs" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Deal next hand" }));
    expect(await screen.findByText("#2")).toBeInTheDocument();
  });

  it("shows a helpful error when the server is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "Take a seat" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Cannot reach the PokerLab server/);
  });

  it("surfaces illegal-action errors from the API", async () => {
    const dealt = gameState();
    mockFetch((url) => {
      if (url.endsWith("/api/sessions")) return { state: waiting({ ...dealt, hand_number: 0 }), steps: [] };
      if (url.endsWith("/hands")) return { state: dealt, steps: [] };
      return new Response(JSON.stringify({ detail: "Raise must be between 20 and 1000" }), { status: 400 });
    });
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "Take a seat" }));
    await userEvent.click(await screen.findByRole("button", { name: /Raise to 20/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Raise must be between 20 and 1000");
  });

  it("shows the game-over dialog when the match ends", async () => {
    const over: GameState = {
      ...gameState({ hand_number: 9, winner: 1 }),
      status: "game_over",
      legal: null,
      to_act: null,
    };
    mockFetch((url) => {
      if (url.endsWith("/api/sessions")) return { state: waiting({ ...over, status: "waiting", hand_number: 0 }), steps: [] };
      return { state: over, steps: [] };
    });
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "Take a seat" }));
    expect(await screen.findByText("Dealer Bot wins the match", {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Play again" })).toBeInTheDocument();
  });
});
