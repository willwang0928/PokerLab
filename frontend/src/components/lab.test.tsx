import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/api";
import { cliCommand, configTitle, duration, headline, pText, reductionText, sessionCount, verdictSentence } from "../lib/lab";
import fixture from "../test/lab.fixture.json";
import type { LabInfo, LabJob, MatchupReport } from "../types";
import { LabView } from "./LabView";

// Real reports from the backend: regenerate with backend/scripts/make_lab_fixture.py.
const INFO = fixture.info as unknown as LabInfo;
const H2H = fixture.h2h as unknown as LabJob;
const RR = fixture.rr as unknown as LabJob;
const RUNNING = fixture.running as unknown as LabJob;

function mockApi(over: Partial<Record<string, unknown>> = {}) {
  return {
    info: vi.fn().mockResolvedValue(INFO),
    estimate: vi.fn().mockImplementation(async (config) => ({
      config,
      matchups: (config.strategies.length * (config.strategies.length - 1)) / 2,
      total_hands: config.hands,
      estimate_seconds: 95,
      workers: 6,
    })),
    submit: vi.fn().mockResolvedValue(H2H),
    get: vi.fn().mockResolvedValue(H2H),
    list: vi.fn().mockResolvedValue({ experiments: [] }),
    cancel: vi.fn().mockResolvedValue({ ...RUNNING, status: "cancelled" }),
    dealsUrl: (id: string) => `/api/lab/experiments/${id}/deals.csv`,
    ...over,
  };
}

beforeEach(() => localStorage.clear());

describe("lab helpers", () => {
  it("formats durations and p-values", () => {
    expect(duration(0.3)).toBe("under a second");
    expect(duration(42)).toBe("42 s");
    expect(duration(185)).toBe("3 min");
    expect(duration(4800)).toBe("1 h 20 min");
    expect(pText(0.0004)).toBe("p < 0.001");
    expect(pText(0.0043)).toBe("p = 0.004");
    expect(pText(0.0213)).toBe("p = 0.02");
    expect(pText(0.4)).toBe("p = 0.40");
  });

  it("words verdicts from the real report", () => {
    const m = H2H.report!.matchups[0] as MatchupReport;
    expect(m.estimate.verdict).toBe("b_better");
    expect(headline(m)).toBe("v0.1 bot beats Maniac");
    expect(verdictSentence(m)).toMatch(/^v0\.1 bot wins by at least [\d.]+ bb\/100 against Maniac/);
    expect(headline({ ...m, estimate: { ...m.estimate, verdict: "unclear" } })).toBe("No clear difference");
    expect(reductionText(m)).toMatch(/as precise as about [\d,]+ ordinary hands/);
    expect(reductionText({ ...m, variance: { ...m.variance!, reduction: null } })).toMatch(/cancelled exactly/);
  });

  it("builds the reproduce command and titles", () => {
    expect(cliCommand(H2H.config)).toBe("python -m pokerlab.lab aggressive basic --hands 4000 --seed 42");
    expect(cliCommand({ ...H2H.config, mode: "standard", stack_bb: 50, session_hands: 100 })).toBe(
      "python -m pokerlab.lab aggressive basic --hands 4000 --seed 42 --mode standard --stack 50 --session 100",
    );
    expect(configTitle(["Advanced", "Advanced"])).toBe("Advanced vs itself");
    expect(configTitle(["A", "B", "C"])).toBe("Round robin: A, B, C");
  });
});

describe("Simulation Lab", () => {
  it("lists strategies by family, estimates, and submits the chosen experiment", async () => {
    const api = mockApi();
    render(<LabView api={api} pollMs={10} />);
    const setup = await screen.findByTestId("lab-setup");
    expect(within(setup).getByText("PokerLab opponents")).toBeInTheDocument();
    expect(within(setup).getByText("Fixed styles with leaks")).toBeInTheDocument();
    // Defaults: Advanced vs Intermediate.
    expect(within(setup).getByRole("button", { name: /^1Advanced/ })).toHaveAttribute("aria-pressed", "true");
    await waitFor(() => expect(screen.getByTestId("lab-estimate")).toHaveTextContent("10,000 hands · about 2 min on 6 cores"));
    fireEvent.click(within(setup).getByRole("button", { name: "20,000" }));
    fireEvent.click(within(setup).getByRole("button", { name: "Standard" }));
    await waitFor(() => expect(api.estimate).toHaveBeenLastCalledWith(expect.objectContaining({ hands: 20_000, mode: "standard" })));
    fireEvent.click(screen.getByTestId("lab-run"));
    await waitFor(() => expect(api.submit).toHaveBeenCalledWith(expect.objectContaining({ strategies: ["advanced", "intermediate"], hands: 20_000 })));
    expect(await screen.findByTestId("matchup-headline")).toHaveTextContent("v0.1 bot beats Maniac");
  });

  it("runs one selected strategy against itself, and three as a round robin", async () => {
    const api = mockApi();
    render(<LabView api={api} pollMs={10} />);
    const setup = await screen.findByTestId("lab-setup");
    fireEvent.click(within(setup).getByRole("button", { name: /^2Intermediate/ }));
    expect(screen.getByTestId("lab-run")).toHaveTextContent("Run Advanced against itself");
    await waitFor(() => expect(api.estimate).toHaveBeenLastCalledWith(expect.objectContaining({ strategies: ["advanced", "advanced"] })));
    fireEvent.click(within(setup).getByRole("button", { name: /Calling station/ }));
    fireEvent.click(within(setup).getByRole("button", { name: /Rock/ }));
    expect(screen.getByTestId("lab-run")).toHaveTextContent("Run round robin (3 matchups)");
  });

  it("shows a finished head-to-head with its interval, charts and the reproduce command", async () => {
    localStorage.setItem("pokerlab.lab.job", JSON.stringify(H2H.id));
    render(<LabView api={mockApi()} pollMs={10} />);
    const detail = await screen.findByTestId("matchup-detail");
    const m = H2H.report!.matchups[0];
    expect(within(detail).getByTestId("matchup-rate")).toHaveTextContent(/^−\d+\.\d bb\/100$/);
    expect(detail).toHaveTextContent("95% interval");
    expect(within(detail).getByTestId("matchup-reduction")).toHaveTextContent("ordinary hands");
    expect(within(detail).getByTestId("trajectory-chart")).toHaveTextContent("Table 2 (cards swapped)");
    expect(within(detail).getByTestId("convergence-chart")).toHaveTextContent("same hands unpaired");
    expect(within(detail).getByTestId("matchup-styles")).toHaveTextContent("Maniac");
    expect(screen.getByTestId("lab-cli")).toHaveTextContent("python -m pokerlab.lab aggressive basic --hands 4000 --seed 42");
    expect(screen.getByRole("link", { name: "Deals (CSV)" })).toHaveAttribute("href", `/api/lab/experiments/${H2H.id}/deals.csv`);
    expect(m.complete).toBe(true);
  });

  it("shows progress and a live chart while running, and can stop", async () => {
    localStorage.setItem("pokerlab.lab.job", JSON.stringify(RUNNING.id));
    const api = mockApi({ get: vi.fn().mockResolvedValue(RUNNING) });
    render(<LabView api={api} pollMs={10} />);
    expect(await screen.findByTestId("lab-progress")).toHaveTextContent("200 of 1,200 hands");
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "17");
    expect(screen.getByTestId("lab-live")).toHaveTextContent("provisional");
    expect(screen.getByTestId("lab-run")).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(api.cancel).toHaveBeenCalledWith(RUNNING.id));
  });

  it("shows a round robin's league and matrix, and opens a matchup from the matrix", async () => {
    localStorage.setItem("pokerlab.lab.job", JSON.stringify(RR.id));
    render(<LabView api={mockApi({ get: vi.fn().mockResolvedValue(RR) })} pollMs={10} />);
    const league = await screen.findByTestId("league");
    expect(within(league).getAllByRole("row")).toHaveLength(4);
    const matrix = screen.getByTestId("matrix");
    const cells = within(matrix).getAllByRole("button");
    expect(cells).toHaveLength(6);
    expect(screen.queryByTestId("matchup-detail")).toBeNull();
    fireEvent.click(within(matrix).getByRole("button", { name: /^Calling station against Rock/ }));
    expect(await screen.findByTestId("matchup-detail")).toHaveTextContent("Calling station against Rock");
    expect(screen.getByTestId("lab-matchups")).toHaveTextContent("Calling station vs Rock");
  });

  it("explains when the server is unreachable, and forgets experiments the server lost", async () => {
    const api = mockApi({ info: vi.fn().mockRejectedValueOnce(new ApiError("Cannot reach the PokerLab server.", 0)).mockResolvedValue(INFO) });
    render(<LabView api={api} pollMs={10} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("needs the PokerLab server");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByTestId("lab-setup")).toBeInTheDocument();

    localStorage.setItem("pokerlab.lab.job", JSON.stringify("gone"));
    const lost = mockApi({ get: vi.fn().mockRejectedValue(new ApiError("Experiment not found", 404)) });
    render(<LabView api={lost} pollMs={10} />);
    await waitFor(() => expect(lost.get).toHaveBeenCalled());
    await waitFor(() => expect(localStorage.getItem("pokerlab.lab.job")).toBeNull());
  });

  it("warns before running when an adaptive strategy would get too few sessions", async () => {
    expect(sessionCount({ hands: 10_000, mode: "duplicate", session_hands: 200 })).toBe(25);
    expect(sessionCount({ hands: 1_000, mode: "standard", session_hands: 200 })).toBe(5);
    render(<LabView api={mockApi()} pollMs={10} />);
    const setup = await screen.findByTestId("lab-setup");
    expect(screen.queryByTestId("lab-session-hint")).toBeNull(); // 25 sessions
    fireEvent.click(within(setup).getByRole("button", { name: "1,000" }));
    expect(screen.getByTestId("lab-session-hint")).toHaveTextContent("these settings give 3");
    // Two fixed strategies have no memory: no warning.
    fireEvent.click(within(setup).getByRole("button", { name: /^1Advanced/ }));
    fireEvent.click(within(setup).getByRole("button", { name: /^1Intermediate/ }));
    fireEvent.click(within(setup).getByRole("button", { name: /Calling station/ }));
    fireEvent.click(within(setup).getByRole("button", { name: /Rock/ }));
    expect(screen.queryByTestId("lab-session-hint")).toBeNull();
  });

  it("shows the server's validation message instead of an estimate", async () => {
    const api = mockApi({ estimate: vi.fn().mockRejectedValue(new ApiError("Hands per matchup must be between 200 and 100000", 422)) });
    render(<LabView api={api} pollMs={10} />);
    expect(await screen.findByText("Hands per matchup must be between 200 and 100000")).toBeInTheDocument();
    expect(screen.getByTestId("lab-run")).toBeDisabled();
  });
});
