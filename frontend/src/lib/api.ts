import type {
  ActionType,
  Concept,
  GameConfig,
  GameState,
  HandFrame,
  HandRecord,
  HandReview,
  HandSummary,
  LeakReport,
  Focus,
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
  /** The finished hand as a history record (public information only), for saving. */
  record: (sessionId: string, hand: number) => request<HandRecord>(`/api/sessions/${sessionId}/hands/${hand}/record`),
  scenario: (difficulty: TrainingDifficulty, concept: Concept, seed?: number, focus?: Focus | null) =>
    request<TrainingScenario>(
      `/api/training/scenario?difficulty=${difficulty}&concept=${concept}` +
        (seed === undefined ? "" : `&seed=${seed}`) +
        (focus ? `&focus=${focus}` : ""),
    ),
  practice: (spot: Record<string, unknown>, difficulty: TrainingDifficulty, chosen?: string | null) =>
    request<TrainingScenario>("/api/training/practice", {
      method: "POST",
      body: JSON.stringify({ spot, difficulty, chosen: chosen ?? null }),
    }),
  answer: (id: string, choice: string) =>
    request<TrainingAnswer>("/api/training/answer", { method: "POST", body: JSON.stringify({ id, choice }) }),
};

// ------------------------------------------------------------------ progress storage (v0.4)

export interface ServerProfile {
  profile_id: string;
  name: string;
  created_at: number;
  updated_at: number;
  counts?: { answers: number; decisions: number; hands?: number };
}

export interface UploadResult {
  added: { answers: number; decisions: number };
  accepted: { answers: number; decisions: number };
  rejected: { kind: "answers" | "decisions"; id: string | null; error: string }[];
}

function authed(token: string, init: RequestInit = {}): RequestInit {
  return { ...init, headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` } };
}

export const profileApi = {
  create: (name: string) =>
    request<{ key: string; profile: ServerProfile }>("/api/profiles", { method: "POST", body: JSON.stringify({ name }) }),
  get: (token: string) => request<ServerProfile>("/api/profile", authed(token)),
  rename: (token: string, name: string) =>
    request<ServerProfile>("/api/profile", authed(token, { method: "PATCH", body: JSON.stringify({ name }) })),
  remove: (token: string) => request<null>("/api/profile", authed(token, { method: "DELETE" })),
  records: (token: string) =>
    request<{ answers: unknown[]; decisions: unknown[]; truncated: boolean }>("/api/profile/records", authed(token)),
  upload: (token: string, answers: unknown[], decisions: unknown[]) =>
    request<UploadResult>("/api/profile/records", authed(token, { method: "POST", body: JSON.stringify({ answers, decisions }) })),
  // Hand histories (v0.4.3)
  uploadHands: (token: string, hands: HandRecord[]) =>
    request<{ added: number; accepted: number; rejected: { id: string | null; error: string }[] }>(
      "/api/profile/hands",
      authed(token, { method: "POST", body: JSON.stringify({ hands }) }),
    ),
  hands: (token: string, limit = 500) =>
    request<{ hands: HandSummary[]; total: number }>(`/api/profile/hands?limit=${limit}`, authed(token)),
  /** Recurring weaknesses with their evidence, strengths, trends and practice (v0.4.4). */
  leaks: (token: string) => request<LeakReport>("/api/profile/leaks", authed(token)),
  hand: (token: string, id: string) =>
    request<{ record: HandRecord; frames: HandFrame[] }>(`/api/profile/hands/${encodeURIComponent(id)}`, authed(token)),
};
