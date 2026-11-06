/** Regression tests for game-flow bugs found in the v0.2.4 audit. */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { gameState } from "./test/fixtures";
import type { GameState } from "./types";

type Handler = (url: string, init?: RequestInit) => unknown;

function mockFetch(handler: Handler) {
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const result = handler(url, init);
    if (result instanceof Response) return result;
    if (result instanceof Error) throw result;
    return new Response(JSON.stringify(result), { status: 200 });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

const waiting = (s: GameState): GameState => ({ ...s, status: "waiting", legal: null, to_act: null });
const calls = (fn: ReturnType<typeof mockFetch>, suffix: string) =>
  fn.mock.calls.filter(([url, init]) => String(url).endsWith(suffix) && init?.method === "POST").length;

function settings(extra: object = {}) {
  localStorage.setItem("pokerlab.settings.v1", JSON.stringify({ speed: "instant", autoDeal: false, ...extra }));
}

beforeEach(() => settings());
afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

async function startGame() {
  render(<App />);
  await userEvent.click(screen.getByRole("button", { name: "Take a seat" }));
}

describe("keyboard shortcuts", () => {
  it("don't act on the live hand while the player types in the new-game dialog", async () => {
    const dealt = gameState();
    const fetchMock = mockFetch((url) => {
      if (url.endsWith("/api/sessions")) return { state: waiting({ ...dealt, hand_number: 0 }), steps: [] };
      if (url.endsWith("/hands")) return { state: dealt, steps: [] };
      throw new Error(`unexpected ${url}`);
    });
    await startGame();
    await screen.findByRole("button", { name: /Call 5/ });
    await userEvent.click(screen.getByRole("button", { name: "New game" }));
    const name = screen.getByRole("textbox", { name: /name/i });
    await userEvent.clear(name);
    await userEvent.type(name, "Fred Carl");
    expect(name).toHaveValue("Fred Carl");
    expect(calls(fetchMock, "/actions")).toBe(0);
  });

  it("don't act while a dialog covers the table, even with focus elsewhere", async () => {
    const dealt = gameState();
    const fetchMock = mockFetch((url) => {
      if (url.endsWith("/api/sessions")) return { state: waiting({ ...dealt, hand_number: 0 }), steps: [] };
      if (url.endsWith("/hands")) return { state: dealt, steps: [] };
      throw new Error(`unexpected ${url}`);
    });
    await startGame();
    await screen.findByRole("button", { name: /Call 5/ });
    await userEvent.click(screen.getByRole("button", { name: "New game" }));
    (document.activeElement as HTMLElement | null)?.blur();
    await userEvent.keyboard("fcr");
    expect(calls(fetchMock, "/actions")).toBe(0);
  });

  it("ignore auto-repeat so holding a key can't send two actions", async () => {
    const dealt = gameState();
    const fetchMock = mockFetch((url) => {
      if (url.endsWith("/api/sessions")) return { state: waiting({ ...dealt, hand_number: 0 }), steps: [] };
      if (url.endsWith("/hands")) return { state: dealt, steps: [] };
      return { state: waiting(dealt), steps: [] };
    });
    await startGame();
    await screen.findByRole("button", { name: /Call 5/ });
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "c", repeat: true }));
    expect(calls(fetchMock, "/actions")).toBe(0);
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "c" }));
    await waitFor(() => expect(calls(fetchMock, "/actions")).toBe(1));
  });
});

describe("automatic dealing", () => {
  it("waits while the new-game dialog is open", async () => {
    settings({ autoDeal: true });
    const done = waiting(gameState());
    const fetchMock = mockFetch((url) => {
      if (url.endsWith("/api/sessions")) return { state: waiting({ ...done, hand_number: 0 }), steps: [] };
      if (url.endsWith("/hands")) return { state: done, steps: [] };
      throw new Error(`unexpected ${url}`);
    });
    await startGame();
    await screen.findByText(/Next hand in/);
    await userEvent.click(screen.getByRole("button", { name: "New game" }));
    await new Promise((r) => setTimeout(r, 1200)); // longer than the instant-speed auto-deal delay
    expect(calls(fetchMock, "/hands")).toBe(1);
    await userEvent.keyboard(" ");
    expect(calls(fetchMock, "/hands")).toBe(1);
  });
});

describe("connection problems", () => {
  it("offers a new game when the server has lost the session", async () => {
    const dealt = gameState();
    mockFetch((url) => {
      if (url.endsWith("/api/sessions")) return { state: waiting({ ...dealt, hand_number: 0 }), steps: [] };
      if (url.endsWith("/hands")) return { state: dealt, steps: [] };
      return new Response(JSON.stringify({ detail: "Session not found" }), { status: 404 });
    });
    await startGame();
    await userEvent.click(await screen.findByRole("button", { name: /Call 5/ }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/no longer on the server/);
    await userEvent.click(within(alert).getByRole("button", { name: "New game" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "Take a seat" })).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: /Back to current game/ })).not.toBeInTheDocument();
  });

  it("refreshes the table from the server when an action is out of step", async () => {
    const dealt = gameState();
    const fresh = gameState({ street: "flop", board: ["2c", "7h", "9d"], log: ["Flop: 2c 7h 9d"] });
    mockFetch((url, init) => {
      if (url.endsWith("/api/sessions")) return { state: waiting({ ...dealt, hand_number: 0 }), steps: [] };
      if (url.endsWith("/hands")) return { state: dealt, steps: [] };
      if (url.endsWith("/actions")) return new Response(JSON.stringify({ detail: "It is not your turn" }), { status: 409 });
      if (url.endsWith("/api/sessions/abc") && !init?.method) return fresh;
      throw new Error(`unexpected ${url}`);
    });
    await startGame();
    await userEvent.click(await screen.findByRole("button", { name: /Call 5/ }));
    expect(await screen.findByText(/It is not your turn. The table has been refreshed/)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "7 of hearts" })).toBeInTheDocument();
  });

  it("lets the player retry after the connection drops, then resumes the real game", async () => {
    const dealt = gameState();
    const after = gameState({ street: "flop", board: ["2c", "7h", "9d"] });
    let online = true;
    mockFetch((url, init) => {
      if (!online) return new TypeError("Failed to fetch");
      if (url.endsWith("/api/sessions")) return { state: waiting({ ...dealt, hand_number: 0 }), steps: [] };
      if (url.endsWith("/hands")) return { state: dealt, steps: [] };
      if (url.endsWith("/api/sessions/abc") && !init?.method) return after;
      throw new Error(`unexpected ${url}`);
    });
    await startGame();
    const call = await screen.findByRole("button", { name: /Call 5/ });
    online = false;
    await userEvent.click(call);
    expect(await screen.findByRole("alert")).toHaveTextContent(/Can't reach the PokerLab server/);
    online = true;
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    // The server had in fact received the call: the table shows where the hand really is.
    expect(screen.getByRole("img", { name: "7 of hearts" })).toBeInTheDocument();
  });
});
