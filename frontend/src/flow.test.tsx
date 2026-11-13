/** Regression tests for game-flow bugs found in the v0.2.4 audit. */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { gameState } from "./test/fixtures";
import handFixture from "./test/hands.fixture.json";
import { leakReport } from "./test/leaks";
import type { GameState } from "./types";

type Handler = (url: string, init?: RequestInit) => unknown; // may return a Promise

function mockFetch(handler: Handler) {
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const result = await handler(url, init);
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
    // Space with focus outside the dialog's buttons must not deal behind it either.
    (document.activeElement as HTMLElement | null)?.blur();
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
    expect(within(screen.getByTestId("board")).getByRole("img", { name: "7 of hearts" })).toBeInTheDocument();
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
    expect(within(screen.getByTestId("board")).getByRole("img", { name: "7 of hearts" })).toBeInTheDocument();
  });
});

describe("coach review", () => {
  it("fetches the review only after the hand is over and records progress", async () => {
    const dealt = gameState();
    const done = {
      ...waiting(dealt),
      result: { winnings: { 0: 20 }, uncontested: true, pots: [], showdown: null } as unknown as GameState["result"],
    };
    const review = {
      hand_number: dealt.hand_number,
      decisions: [],
      counts: { good: 0, close: 0, questionable: 0, mistake: 0, ungraded: 0 },
      net: 20,
      net_bb: 2,
      ev_loss_bb: 0,
      quality: "none",
      verdict: "You won 20 chips. You made no decisions that could be graded this hand.",
      hindsight: null,
      tags: [],
    };
    const fetchMock = mockFetch((url) => {
      if (url.endsWith("/api/sessions")) return { state: waiting({ ...dealt, hand_number: 0 }), steps: [] };
      if (url.endsWith("/hands")) return { state: dealt, steps: [] };
      if (url.endsWith("/review")) return review;
      return { state: done, steps: [] };
    });
    const reviews = () => fetchMock.mock.calls.filter(([u]) => String(u).endsWith("/review")).length;
    await startGame();
    const call = await screen.findByRole("button", { name: /Call 5/ });
    expect(reviews()).toBe(0); // never during the hand
    await userEvent.click(call);
    expect(await screen.findByTestId("coach-verdict")).toHaveTextContent(/You won 20 chips/);
    expect(reviews()).toBe(1);
    expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith(`/hands/${dealt.hand_number}/review`))).toBe(true);
  });
  it("keeps a review that arrives after the next hand was dealt, and records it", async () => {
    // Regression (v0.4.6): dealing the next hand before the review returned cancelled it,
    // leaving "Reviewing your decisions…" on screen and the hand missing from progress.
    const dealt = gameState();
    const done = {
      ...waiting(dealt),
      result: { winnings: { 0: 20 }, uncontested: true, pots: [], showdown: null } as unknown as GameState["result"],
    };
    const next = { ...dealt, hand_number: dealt.hand_number + 1 };
    const coaching = handFixture.folded.record.coaching;
    const review = { ...coaching, hand_number: dealt.hand_number, verdict: "Late review arrived." };
    let release: (v: unknown) => void = () => {};
    const pending = new Promise((r) => (release = r));
    let dealtHands = 0;
    mockFetch((url) => {
      if (url.endsWith("/api/sessions")) return { state: waiting({ ...dealt, hand_number: 0 }), steps: [] };
      if (url.endsWith("/hands")) return { state: dealtHands++ === 0 ? dealt : next, steps: [] };
      if (url.endsWith("/review")) return pending.then(() => new Response(JSON.stringify(review)));
      if (url.endsWith("/record")) return new Response("{}", { status: 404 });
      if (url.includes("/api/profile")) return new Response("{}", { status: 404 });
      return { state: done, steps: [] };
    });
    await startGame();
    await userEvent.click(await screen.findByRole("button", { name: /Call 5/ }));
    expect(await screen.findByText("Reviewing your decisions…")).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: /Deal next hand/ }));
    await screen.findByRole("button", { name: /Call 5/ }); // the next hand is live
    release(null);
    expect(await screen.findByTestId("coach-verdict")).toHaveTextContent("Late review arrived.");
    expect(screen.getByTestId("coach-announcer")).toHaveTextContent(/hand \d+: .*Late review arrived\./);
    expect(screen.queryByText("Reviewing your decisions…")).not.toBeInTheDocument();
    const graded = coaching.decisions.filter((d: { grade: string }) => d.grade !== "ungraded").length;
    expect(graded).toBeGreaterThan(0);
    await waitFor(() => {
      const stored = Object.keys(localStorage)
        .filter((k) => k.startsWith("pokerlab.progress.v2"))
        .map((k) => localStorage.getItem(k) ?? "")
        .join("");
      expect(stored).toContain(`#${dealt.hand_number}:`);
    });
  });
});

describe("hand history", () => {
  it("saves each finished hand to the server, linking the profile when needed", async () => {
    const dealt = gameState();
    const done = {
      ...waiting(dealt),
      result: { winnings: { 0: 20 }, uncontested: true, pots: [], showdown: null } as unknown as GameState["result"],
    };
    const record = { ...handFixture.folded.record, hand_number: dealt.hand_number };
    const uploaded: string[] = [];
    const fetchMock = mockFetch((url, init) => {
      if (url.endsWith("/api/profile/hands") && init?.method === "POST") {
        uploaded.push(...JSON.parse(String(init.body)).hands.map((h: { id: string }) => h.id));
        return { added: 1, accepted: 1, rejected: [] };
      }
      if (url.endsWith("/api/sessions")) return { state: waiting({ ...dealt, hand_number: 0 }), steps: [] };
      if (url.endsWith("/hands")) return { state: dealt, steps: [] };
      if (url.endsWith("/review")) return { ...handFixture.folded.record.coaching, decisions: [] };
      if (url.endsWith("/record")) return record;
      if (url.endsWith("/api/profiles")) return { key: "secret", profile: { profile_id: "p1", name: "Player" } };
      if (url.endsWith("/api/profile/records") && init?.method === "POST")
        return { added: { answers: 0, decisions: 0 }, accepted: { answers: 0, decisions: 0 }, rejected: [] };
      if (url.endsWith("/api/profile/records")) return { answers: [], decisions: [], truncated: false };
      return { state: done, steps: [] };
    });
    await startGame();
    await userEvent.click(await screen.findByRole("button", { name: /Call 5/ }));
    await waitFor(() => expect(uploaded).toEqual([record.id]), { timeout: 4000 });
    // The record was fetched only after the hand was over, for that hand.
    expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith(`/hands/${dealt.hand_number}/record`))).toBe(true);
    expect(localStorage.getItem("pokerlab.hands.pending.v1." + JSON.parse(localStorage.getItem("pokerlab.profiles.v1")!).active)).toBeNull();
  });
});

describe("training mode", () => {
  it("does not let table shortcuts act on the hidden game", async () => {
    const dealt = gameState();
    const fetchMock = mockFetch((url) => {
      if (url.endsWith("/api/sessions")) return { state: waiting({ ...dealt, hand_number: 0 }), steps: [] };
      if (url.endsWith("/hands")) return { state: dealt, steps: [] };
      if (url.includes("/api/training/")) return new Response("{}", { status: 503 });
      return { state: waiting(dealt), steps: [] };
    });
    await startGame();
    await screen.findByRole("button", { name: /Call 5/ });
    await userEvent.click(screen.getByRole("tab", { name: "Train" }));
    expect(await screen.findByTestId("training-view")).toBeInTheDocument();
    (document.activeElement as HTMLElement | null)?.blur();
    await userEvent.keyboard("fcr ");
    expect(calls(fetchMock, "/actions")).toBe(0);
    await userEvent.click(screen.getByRole("tab", { name: "Play" }));
    expect(await screen.findByRole("button", { name: /Call 5/ })).toBeInTheDocument();
  });
});

describe("personalised learning", () => {
  const scenario = {
    id: "v1.beginner.pot_odds.1",
    difficulty: "beginner",
    concept: "pot_odds",
    concept_label: "Pot odds",
    concept_blurb: "",
    hero: ["As", "Kd"],
    board: [],
    street: "river",
    position: "button",
    pot: 100,
    to_call: 50,
    stack: 900,
    opponent_stack: 900,
    big_blind: 10,
    history: [],
    hand: "AKo",
    choices: [
      { key: "fold", label: "Fold" },
      { key: "call", label: "Call 50" },
    ],
    opponent: null,
    hints: [],
  };
  const answer = {
    id: scenario.id,
    concept: "pot_odds",
    choice: "call",
    correct: false,
    acceptable: false,
    analysis: {
      street: "river", hole: [], board: [], hand: "", position: "", pot: 100, to_call: 50, stack: 900, big_blind: 10,
      history: [], spot: "facing_bet", concept: "pot_odds", equity: 0.2, equity_se: 0, equity_method: "exact",
      required_equity: 0.33, range_size: 50, range_steps: [], reads: [], options: [], best: ["fold"], chosen: "call",
      grade: "mistake", grade_label: "Likely mistake", confidence: "high", ev_loss: 20, tags: [], headline: "",
      explanation: "", details: [], caveats: [],
    },
    alternatives: [],
  };

  it("persists a missed answer, schedules a review, and survives a reload", async () => {
    const fetchMock = mockFetch((url) => {
      if (url.includes("/api/training/scenario")) return scenario;
      if (url.includes("/api/training/answer")) return answer;
      throw new Error(`unexpected ${url}`);
    });
    const { unmount } = render(<App />);
    await userEvent.click(screen.getByRole("button", { name: /training mode/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Call 50" }));
    await screen.findByTestId("training-feedback");
    const { active } = JSON.parse(localStorage.getItem("pokerlab.profiles.v1")!);
    const stored = JSON.parse(localStorage.getItem(`pokerlab.progress.v2.${active}`)!);
    expect(stored.answers).toHaveLength(1);
    expect(stored.unsynced.answers).toEqual([scenario.id]); // the server was unreachable
    expect(stored.reviews).toBeUndefined(); // the schedule is derived, never stored
    // Auto difficulty asked the server for the adaptive level (Beginner for a new player).
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("difficulty=beginner"))).toBe(true);
    unmount();

    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: /training mode/ }));
    await userEvent.click(screen.getByRole("tab", { name: "Progress" }));
    expect(await screen.findByTestId("dashboard-reviews")).toHaveTextContent(/Pot odds/);
    expect(screen.getByTestId("dashboard-history")).toHaveTextContent(/Likely mistake/);
    expect(screen.getByTestId("dash-skill-pot_odds")).toHaveTextContent(/Training: 0 of 1 good/);
  });
});

describe("leak workflow (v0.4.4)", () => {
  it("goes from a leak to its hand, the decision, focused practice, and records the answer against the leak", async () => {
    localStorage.setItem("pokerlab.profiles.v1", JSON.stringify({ active: "k1", profiles: [{ key: "k1", name: "Player", token: "tok" }] }));
    const scenario = {
      id: "v2.beginner.pot_odds.7.river",
      difficulty: "beginner",
      concept: "pot_odds",
      concept_label: "Pot odds",
      concept_blurb: "",
      hero: ["As", "Kd"],
      board: ["2c", "7d", "9h", "Jc", "3s"],
      street: "river",
      position: "button",
      pot: 100,
      to_call: 50,
      stack: 900,
      opponent_stack: 900,
      big_blind: 10,
      history: [],
      hand: "AKo",
      choices: [
        { key: "fold", label: "Fold" },
        { key: "call", label: "Call 50" },
      ],
      opponent: null,
      hints: [],
      focus: "river",
      focus_label: "on the river",
    };
    const answer = {
      id: scenario.id, concept: "pot_odds", choice: "fold", correct: true, acceptable: true,
      analysis: {
        street: "river", hole: [], board: [], hand: "", position: "", pot: 100, to_call: 50, stack: 900, big_blind: 10,
        history: [], spot: "facing_bet", concept: "pot_odds", equity: 0.2, equity_se: 0, equity_method: "exact",
        required_equity: 0.33, range_size: 50, range_steps: [], reads: [], options: [], best: ["fold"], chosen: "fold",
        grade: "good", grade_label: "Good", confidence: "high", ev_loss: 0, tags: [], headline: "",
        explanation: "", details: [], caveats: [],
      },
      alternatives: [],
    };
    const uploaded: { leak?: string }[] = [];
    const scenarioUrls: string[] = [];
    const folded = handFixture.folded;
    mockFetch((url, init) => {
      if (url.includes("/api/profile/leaks")) return leakReport;
      if (url.includes("/api/profile/hands?")) return { hands: [folded.summary], total: 1 };
      if (url.includes("/api/profile/hands/")) return { record: folded.record, frames: folded.frames };
      if (url.endsWith("/api/profile/records") && init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        uploaded.push(...body.answers);
        return { added: { answers: body.answers.length, decisions: 0 }, accepted: { answers: body.answers.length, decisions: 0 }, rejected: [] };
      }
      if (url.includes("/api/profile/records")) return { answers: [], decisions: [], truncated: false };
      if (url.includes("/api/training/scenario")) {
        scenarioUrls.push(url);
        return scenario;
      }
      if (url.includes("/api/training/answer")) return answer;
      throw new Error(`unexpected ${url}`);
    });
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: /training mode/ }));
    await userEvent.click(screen.getByRole("tab", { name: "Progress" }));

    // 1. The leak, with its evidence.
    const leak = await screen.findByTestId("leak");
    expect(leak).toHaveTextContent("6 of 12 such decisions flagged");
    // 2. Its hand opens in the History tab at the decision.
    await userEvent.click(within(screen.getByTestId("leak-hands")).getByRole("button", { name: /#1/ }));
    expect(await screen.findByTestId("hand-replayer")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("replay-decision")).toHaveTextContent("You raise to 120"));
    // Judged with decision-time information only.
    expect(screen.getByTestId("decision-time-note")).toHaveTextContent("the result of the hand does not change the grade");
    // 3. The decision links back to the leak and on to practice.
    const finding = screen.getByTestId("decision-finding");
    await userEvent.click(within(finding).getByRole("button", { name: "Practise this leak" }));
    // 4. Focused practice: same concept, the leak's situation.
    await screen.findByTestId("scenario");
    expect(scenarioUrls.at(-1)).toContain("concept=pot_odds");
    expect(scenarioUrls.at(-1)).toContain("focus=river");
    expect(screen.getByTestId("leak-practice-note")).toHaveTextContent("set on the river");
    await userEvent.click(screen.getByRole("button", { name: "Fold" }));
    await screen.findByTestId("training-feedback");
    // 5. The first answer is recorded against the leak, and reaches the server.
    const stored = JSON.parse(localStorage.getItem("pokerlab.progress.v2.k1")!);
    expect(stored.answers).toEqual([expect.objectContaining({ id: scenario.id, leak: "called_without_odds" })]);
    await waitFor(() => expect(uploaded).toEqual([expect.objectContaining({ leak: "called_without_odds" })]), { timeout: 4000 });
    // "Next" stays on the leak.
    await userEvent.click(screen.getByRole("button", { name: "Next scenario" }));
    await waitFor(() => expect(scenarioUrls.length).toBeGreaterThanOrEqual(2));
    expect(scenarioUrls.at(-1)).toContain("focus=river");
  });
});
