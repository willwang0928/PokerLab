import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import fixture from "../test/hands.fixture.json";
import type { HandRecord, HandSummary } from "../types";
import {
  activeFilterCount,
  clearPendingHands,
  filterHands,
  flushHands,
  matchesQuery,
  NO_FILTERS,
  pendingHands,
  PENDING_LIMIT,
  queueHand,
  strengthOf,
} from "./hands";
import { quarantined } from "./store";
import { syncProfile } from "./sync";
import { emptyDoc } from "./store";

const folded = fixture.folded.record as unknown as HandRecord;
const showdown = fixture.showdown.record as unknown as HandRecord;
const sumFolded = fixture.folded.summary as unknown as HandSummary;
const sumShowdown = fixture.showdown.summary as unknown as HandSummary;

function server(route: (url: string, init?: RequestInit) => unknown) {
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const out = route(url, init);
    if (out instanceof Error) throw out;
    if (out instanceof Response) return out;
    return new Response(JSON.stringify(out), { status: 200 });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("search and filters", () => {
  const day = (iso: string) => new Date(`${iso}T12:00:00`).getTime();
  const hands: HandSummary[] = [
    { ...sumFolded, id: "a", t: day("2026-11-01") }, // AKs, won 15 bb, pot 30 bb, flagged
    { ...sumShowdown, id: "b", t: day("2026-11-09") }, // AKo, lost, showdown, pot 6 bb
    { ...sumShowdown, id: "c", t: day("2026-11-10"), hand_class: "72o", hand_percentile: 0.99, hero_cards: ["7c", "2d"],
      final_hand: "High Card, Queen", final_category: "High Card", result: "split", net: 0, net_bb: 0, flagged: 0, hand_number: 12 },
  ];
  const ids = (f: Partial<typeof NO_FILTERS>) => filterHands(hands, { ...NO_FILTERS, ...f }).map((h) => h.id);

  it("uses the real summaries from the server", () => {
    expect(sumFolded).toMatchObject({ result: "won", hand_class: "AKs", pot_bb: 30, flagged: 1, showdown: false });
    expect(sumShowdown).toMatchObject({ result: "lost", showdown: true, opponent_cards: ["9d", "9s"] });
  });

  it("filters by result, strength, pot size, flags, showdown and date", () => {
    expect(ids({})).toEqual(["a", "b", "c"]);
    expect(ids({ result: "won" })).toEqual(["a"]);
    expect(ids({ result: "split" })).toEqual(["c"]);
    expect(ids({ strength: "premium" })).toEqual(["a", "b"]);
    expect(ids({ strength: "weak" })).toEqual(["c"]);
    expect(ids({ largePots: true })).toEqual(["a"]);
    expect(ids({ flagged: true })).toEqual(["a"]);
    expect(ids({ showdown: "yes" })).toEqual(["b", "c"]);
    expect(ids({ from: "2026-11-09" })).toEqual(["b", "c"]);
    expect(ids({ to: "2026-11-09" })).toEqual(["a", "b"]);
    expect(ids({ from: "2026-11-09", to: "2026-11-09", result: "lost" })).toEqual(["b"]);
  });

  it("searches hand classes, cards, hand numbers and final hands", () => {
    const q = (s: string) => hands.filter((h) => matchesQuery(h, s)).map((h) => h.id);
    expect(q("AK")).toEqual(["a", "b"]);
    expect(q("aks")).toEqual(["a"]);
    expect(q("Kh")).toEqual(["a"]);
    expect(q("#12")).toEqual(["c"]);
    expect(q("high card")).toEqual(["b", "c"]);
    expect(q("AK pair")).toEqual(["a"]); // every term must match
    expect(q("  ")).toEqual(["a", "b", "c"]);
  });

  it("buckets starting hands and counts active filters", () => {
    expect([0, 0.05, 0.06, 0.2, 0.49, 0.5].map(strengthOf)).toEqual(["premium", "premium", "strong", "playable", "playable", "weak"]);
    expect(activeFilterCount(NO_FILTERS)).toBe(0);
    expect(activeFilterCount({ ...NO_FILTERS, flagged: true, query: "AK" })).toBe(2);
  });
});

describe("the waiting queue", () => {
  it("queues once per hand and keeps hands per profile and epoch", () => {
    expect(queueHand("p", "e1", folded)).toBeNull();
    queueHand("p", "e1", folded);
    queueHand("p", "e1", showdown);
    expect(pendingHands("p", "e1").map((h) => h.id)).toEqual([folded.id, showdown.id]);
    expect(pendingHands("p", "e2")).toEqual([]); // after a reset (new epoch)
    expect(pendingHands("q", "e1")).toEqual([]);
    queueHand("p", "e2", showdown); // a new epoch replaces the old queue
    expect(pendingHands("p", "e2").map((h) => h.id)).toEqual([showdown.id]);
    clearPendingHands("p");
    expect(pendingHands("p", "e2")).toEqual([]);
  });

  it("caps the queue and ignores unreadable data", () => {
    for (let i = 0; i < PENDING_LIMIT + 3; i++) queueHand("p", "e", { ...folded, id: `h${i}` });
    const q = pendingHands("p", "e");
    expect(q).toHaveLength(PENDING_LIMIT);
    expect(q[0].id).toBe("h3");
    localStorage.setItem("pokerlab.hands.pending.v1.p", "{not json");
    expect(pendingHands("p", "e")).toEqual([]);
  });

  it("reports a full browser store instead of failing silently", () => {
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("full", "QuotaExceededError");
    });
    expect(queueHand("p", "e", folded)).toBe("quota");
    spy.mockRestore();
  });

  it("uploads waiting hands; refused ones are quarantined, not lost", async () => {
    queueHand("p", "e", folded);
    queueHand("p", "e", showdown);
    const bodies: { hands: HandRecord[] }[] = [];
    server((url, init) => {
      expect(url).toContain("/api/profile/hands");
      expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer tok");
      const body = JSON.parse(String(init?.body));
      bodies.push(body);
      return { added: 1, accepted: 1, rejected: [{ id: showdown.id, error: "final stacks do not match" }] };
    });
    const res = await flushHands("tok", "p", "e");
    expect(res).toEqual({ saved: 1, refused: 1 });
    expect(bodies[0].hands.map((h) => h.id)).toEqual([folded.id, showdown.id]);
    expect(pendingHands("p", "e")).toEqual([]);
    const q = quarantined();
    expect(q).toHaveLength(1);
    expect(q[0].reason).toMatch(/hand refused by the server: final stacks/);
  });

  it("keeps hands waiting while the server is unreachable", async () => {
    queueHand("p", "e", folded);
    server(() => new TypeError("Failed to fetch"));
    await expect(flushHands("tok", "p", "e")).rejects.toThrow();
    expect(pendingHands("p", "e")).toHaveLength(1);
  });

  it("links a profile so that a hand with no decisions can still be saved", async () => {
    server((url) => {
      if (url.endsWith("/api/profiles")) return { key: "secret", profile: { profile_id: "p1", name: "A" } };
      if (url.endsWith("/api/profile/records")) return { answers: [], decisions: [], truncated: false };
      throw new Error(url);
    });
    const plain = await syncProfile({ key: "k", name: "A" }, emptyDoc("e"));
    expect(plain.state.status).toBe("local");
    const forced = await syncProfile({ key: "k", name: "A" }, emptyDoc("e"), { forceLink: true });
    expect(forced.profile.token).toBe("secret");
  });
});
