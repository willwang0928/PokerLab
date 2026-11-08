import type {
  ActionType,
  Concept,
  GameConfig,
  GameState,
  HandReview,
  PlayResponse,
  TrainingAnswer,
  TrainingDifficulty,
  TrainingScenario,
} from "../types";

const BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    throw new ApiError("Cannot reach the PokerLab server. Is the backend running?", 0);
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = body?.detail;
    const message = Array.isArray(detail)
      ? detail.map((d: { msg?: string }) => d.msg).join("; ")
      : typeof detail === "string"
        ? detail
        : `Request failed (${res.status})`;
    throw new ApiError(message, res.status);
  }
  return body as T;
}

export const api = {
  createSession: (config: GameConfig) =>
    request<PlayResponse>("/api/sessions", { method: "POST", body: JSON.stringify(config) }),
  getSession: (sessionId: string) => request<GameState>(`/api/sessions/${sessionId}`),
  startHand: (sessionId: string) =>
    request<PlayResponse>(`/api/sessions/${sessionId}/hands`, { method: "POST" }),
  act: (sessionId: string, action: ActionType, amount?: number) =>
    request<PlayResponse>(`/api/sessions/${sessionId}/actions`, {
      method: "POST",
      body: JSON.stringify(amount === undefined ? { action } : { action, amount }),
    }),
};

export const coachApi = {
  review: (sessionId: string, hand: number) =>
    request<HandReview>(`/api/sessions/${sessionId}/hands/${hand}/review`),
  scenario: (difficulty: TrainingDifficulty, concept: Concept, seed?: number) =>
    request<TrainingScenario>(
      `/api/training/scenario?difficulty=${difficulty}&concept=${concept}` + (seed === undefined ? "" : `&seed=${seed}`),
    ),
  practice: (spot: Record<string, unknown>, difficulty: TrainingDifficulty, chosen?: string | null) =>
    request<TrainingScenario>("/api/training/practice", {
      method: "POST",
      body: JSON.stringify({ spot, difficulty, chosen: chosen ?? null }),
    }),
  answer: (id: string, choice: string) =>
    request<TrainingAnswer>("/api/training/answer", { method: "POST", body: JSON.stringify({ id, choice }) }),
};
