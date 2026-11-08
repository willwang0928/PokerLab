import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useProgressStore } from "../hooks/useProgressStore";
import type { AnswerRecord } from "./progress";
import {
  applyChange,
  DOC_PREFIX,
  emptyDoc,
  LEGACY_BACKUP_KEY,
  loadLocal,
  LOCAL_LIMIT,
  mergeDocs,
  PROFILES_KEY,
  QUARANTINE_KEY,
  quarantined,
  readDoc,
  saveDoc,
} from "./store";
import { BATCH, syncProfile } from "./sync";

const T = 1_762_600_000_000;
const ans = (i: number, extra: Partial<AnswerRecord> = {}): AnswerRecord => ({
  id: `a${i}`,
  t: T + i,
  concept: "pot_odds",
  difficulty: "beginner",
  grade: "good",
  ...extra,
});
const V1 = {
  decisions: [
    { t: T, hand: "s#1", grade: "mistake", concept: "pot_odds", tags: ["called_without_odds"], evLossBb: 2, confidence: "high" },
    { t: T, hand: "s#1", grade: "good", concept: "pot_odds", tags: [], evLossBb: 0 },
  ],
  answers: [ans(1), ans(2), { id: "bad", t: T, concept: "luck", difficulty: "beginner", grade: "good" }],
  reviews: [{ key: "old" }],
};

beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("migration from v0.3", () => {
  it("moves old progress, keeps a backup, and keeps unreadable records aside", () => {
    const raw = JSON.stringify(V1);
    localStorage.setItem("pokerlab.progress.v1", raw);
    const { doc, notices, profiles } = loadLocal(T);
    expect(doc.answers.map((a) => a.id)).toEqual(["a1", "a2"]);
    expect(doc.decisions.map((d) => d.id)).toEqual(["s#1:0", "s#1:1"]);
    expect(doc.unsynced.answers).toEqual(["a1", "a2"]); // to be uploaded
    expect(localStorage.getItem("pokerlab.progress.v1")).toBeNull();
    expect(localStorage.getItem(LEGACY_BACKUP_KEY)).toBe(raw); // untouched copy
    expect(quarantined().map((q) => q.reason)).toEqual(["concept"]); // not lost
    expect(notices[0].kind).toBe("migrated");
    expect(notices[0].text).toMatch(/Moved 4 saved records.*1 could not be read.*backup/);
    expect(localStorage.getItem(DOC_PREFIX + profiles.active)).toContain("s#1:0");
    // A second start does not migrate again or duplicate anything.
    const again = loadLocal(T);
    expect(again.doc.answers).toHaveLength(2);
    expect(again.notices).toEqual([]);
  });

  it("keeps the old data in place if the migrated copy cannot be written", () => {
    localStorage.setItem("pokerlab.progress.v1", JSON.stringify(V1));
    const real = Storage.prototype.setItem;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, k: string, v: string) {
      if (k.startsWith(DOC_PREFIX)) throw new DOMException("full", "QuotaExceededError");
      return real.call(this, k, v);
    });
    const { doc, error, notices } = loadLocal(T);
    vi.restoreAllMocks();
    expect(error).toBe("quota");
    expect(doc.answers).toHaveLength(2); // still usable in memory
    expect(localStorage.getItem("pokerlab.progress.v1")).not.toBeNull(); // nothing deleted
    expect(notices.some((n) => n.kind === "storage")).toBe(true);
  });
});

describe("local doc", () => {
  it("quarantines unreadable JSON instead of discarding it", () => {
    localStorage.setItem(PROFILES_KEY, JSON.stringify({ active: "k1", profiles: [{ key: "k1", name: "A" }] }));
    localStorage.setItem(DOC_PREFIX + "k1", "{truncated");
    const { notices, doc } = loadLocal(T);
    expect(doc.answers).toEqual([]);
    expect(JSON.parse(localStorage.getItem(QUARANTINE_KEY)!)[0].record).toBe("{truncated");
    expect(notices.map((n) => n.kind)).toContain("quarantined");
  });

  it("merges on write, so two tabs never erase each other's records", () => {
    const base = emptyDoc("e1");
    saveDoc("k", base);
    const tabA = applyChange(base, (p) => ({ ...p, answers: [...p.answers, ans(1)] }));
    const tabB = applyChange(base, (p) => ({ ...p, answers: [...p.answers, ans(2)] }));
    saveDoc("k", tabA);
    const { doc } = saveDoc("k", tabB); // tab B never saw a1
    expect(doc.answers.map((a) => a.id)).toEqual(["a1", "a2"]);
    expect(readDoc("k").doc!.unsynced.answers.sort()).toEqual(["a1", "a2"]);
  });

  it("a reset (new epoch) replaces older data instead of merging it back", () => {
    saveDoc("k", { ...emptyDoc("old"), answers: [ans(1)] });
    const { doc } = saveDoc("k", emptyDoc("new"));
    expect(doc.answers).toEqual([]);
  });

  it("merging keeps a record synced if either copy knows the server has it", () => {
    const a = { ...emptyDoc("e"), answers: [ans(1)], unsynced: { answers: ["a1"], decisions: [] } };
    const b = { ...emptyDoc("e"), answers: [ans(1)] };
    expect(mergeDocs(a, b).unsynced.answers).toEqual([]);
  });

  it("trims old records beyond the local limit but never unsynced ones", () => {
    const many = Array.from({ length: LOCAL_LIMIT + 5 }, (_, i) => ans(i));
    const doc = { ...emptyDoc("e"), answers: many, unsynced: { answers: ["a0", "a1"], decisions: [] } };
    const { doc: saved } = saveDoc("k", doc);
    expect(saved.answers).toHaveLength(LOCAL_LIMIT);
    expect(saved.answers.slice(0, 2).map((a) => a.id)).toEqual(["a0", "a1"]); // unsynced kept
    expect(saved.answers.some((a) => a.id === "a2")).toBe(false); // oldest synced trimmed
  });

  it("reports a full store instead of failing silently", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("full", "QuotaExceededError");
    });
    expect(saveDoc("k", emptyDoc()).error).toBe("quota");
    vi.restoreAllMocks();
  });
});

// ---------------------------------------------------------------- sync

type Route = (url: string, init?: RequestInit) => unknown;

function server(route: Route) {
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const out = route(url, init);
    if (out instanceof Response) return out;
    if (out instanceof Error) throw out;
    return new Response(JSON.stringify(out), { status: 200 });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

const ok = (n: number) => ({ added: { answers: n, decisions: 0 }, accepted: { answers: n, decisions: 0 }, rejected: [] });

describe("sync", () => {
  it("links a new profile, uploads everything, and marks it synced", async () => {
    const uploads: unknown[] = [];
    server((url, init) => {
      if (url.endsWith("/api/profiles")) return { key: "secret", profile: { profile_id: "p1", name: "A" } };
      if (url.endsWith("/api/profile/records") && init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        uploads.push(body);
        return ok(body.answers.length);
      }
      if (url.endsWith("/api/profile/records")) return { answers: [], decisions: [], truncated: false };
      throw new Error(url);
    });
    const doc = { ...emptyDoc("e"), answers: [ans(1), ans(2)] };
    const res = await syncProfile({ key: "k", name: "A" }, doc);
    expect(res.state.status).toBe("synced");
    expect(res.profile).toMatchObject({ token: "secret", profileId: "p1" });
    expect(res.doc.unsynced.answers).toEqual([]);
    expect(uploads).toHaveLength(1);
  });

  it("keeps everything pending while the server is unreachable", async () => {
    server(() => new TypeError("Failed to fetch"));
    const doc = { ...emptyDoc("e"), answers: [ans(1)], unsynced: { answers: ["a1"], decisions: [] } };
    const res = await syncProfile({ key: "k", name: "A", token: "t" }, doc);
    expect(res.state.status).toBe("offline");
    expect(res.doc.unsynced.answers).toEqual(["a1"]);
  });

  it("pulls records from another device and validates them", async () => {
    server((_url, init) => {
      if (init?.method === "POST") return ok(0);
      return { answers: [ans(7), { id: "evil", t: 1, concept: "x" }], decisions: [], truncated: false };
    });
    const res = await syncProfile({ key: "k", name: "A", token: "t" }, { ...emptyDoc("e"), answers: [ans(1)] });
    expect(res.doc.answers.map((a) => a.id)).toEqual(["a1", "a7"]);
    expect(res.invalidRemote).toBe(1);
  });

  it("marks refused records instead of retrying them forever", async () => {
    server((_url, init) => {
      if (init?.method === "POST")
        return { ...ok(1), rejected: [{ kind: "answers", id: "a2", error: "grade: bad" }] };
      return { answers: [], decisions: [], truncated: false };
    });
    const doc = { ...emptyDoc("e"), answers: [ans(1), ans(2)], unsynced: { answers: ["a1", "a2"], decisions: [] } };
    const res = await syncProfile({ key: "k", name: "A", token: "t" }, doc);
    expect(res.doc.unsynced.answers).toEqual([]);
    expect(res.doc.rejected.answers).toEqual(["a2"]);
    expect(res.doc.answers).toHaveLength(2); // still kept locally
  });

  it("resumes after a connection drops in the middle of a large upload", async () => {
    let posts = 0;
    server((_url, init) => {
      if (init?.method === "POST") {
        posts += 1;
        if (posts === 2) return new TypeError("connection reset");
        return ok(JSON.parse(String(init.body)).answers.length);
      }
      return { answers: [], decisions: [], truncated: false };
    });
    const many = Array.from({ length: BATCH * 2 + 10 }, (_, i) => ans(i));
    const doc = { ...emptyDoc("e"), answers: many, unsynced: { answers: many.map((a) => a.id), decisions: [] } };
    const first = await syncProfile({ key: "k", name: "A", token: "t" }, doc);
    expect(first.state.status).toBe("offline");
    expect(first.doc.unsynced.answers).toHaveLength(BATCH + 10); // the first batch is confirmed
    const second = await syncProfile({ key: "k", name: "A", token: "t" }, first.doc);
    expect(second.state.status).toBe("synced");
    expect(second.doc.unsynced.answers).toEqual([]);
  });

  it("reports an unknown key without touching local data", async () => {
    server(() => new Response(JSON.stringify({ detail: "Unknown profile key" }), { status: 401 }));
    const doc = { ...emptyDoc("e"), answers: [ans(1)], unsynced: { answers: ["a1"], decisions: [] } };
    const res = await syncProfile({ key: "k", name: "A", token: "gone" }, doc);
    expect(res.state.status).toBe("unlinked");
    expect(res.doc.answers).toHaveLength(1);
  });
});

describe("useProgressStore", () => {
  it("records offline, then syncs when the server comes back", async () => {
    let up = false;
    const posted: string[] = [];
    server((url, init) => {
      if (!up) return new TypeError("Failed to fetch");
      if (url.endsWith("/api/profiles")) return { key: "k1", profile: { profile_id: "p", name: "Player" } };
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        posted.push(...body.answers.map((a: AnswerRecord) => a.id));
        return ok(body.answers.length);
      }
      return { answers: [], decisions: [], truncated: false };
    });
    const { result } = renderHook(() => useProgressStore());
    await act(async () => {
      await result.current.sync();
    });
    expect(result.current.syncState.status).toBe("local"); // nothing to save yet: no server call
    act(() => result.current.record((p) => ({ ...p, answers: [...p.answers, ans(1)] })));
    await act(async () => {
      await result.current.sync();
    });
    expect(result.current.syncState.status).toBe("offline");
    expect(result.current.progress.answers).toHaveLength(1);
    expect(result.current.pending).toBe(1);
    up = true;
    await act(async () => {
      await result.current.sync();
    });
    expect(result.current.syncState.status).toBe("synced");
    expect(result.current.pending).toBe(0);
    expect(posted).toEqual(["a1"]);
    expect(result.current.active.token).toBe("k1");
  });

  it("picks up records saved by another tab", async () => {
    server(() => new TypeError("Failed to fetch"));
    const { result } = renderHook(() => useProgressStore());
    const key = result.current.active.key;
    const other = { ...readDoc(key).doc!, answers: [ans(5)], unsynced: { answers: ["a5"], decisions: [] } };
    localStorage.setItem(DOC_PREFIX + key, JSON.stringify(other));
    act(() => {
      window.dispatchEvent(new StorageEvent("storage", { key: DOC_PREFIX + key }));
    });
    expect(result.current.progress.answers.map((a) => a.id)).toEqual(["a5"]);
  });

  it("does not delete anything when the server copy cannot be deleted", async () => {
    let up = true;
    server((url, init) => {
      if (!up) return new TypeError("Failed to fetch");
      if (url.endsWith("/api/profiles")) return { key: "k1", profile: { profile_id: "p", name: "Player" } };
      if (init?.method === "POST") return ok(1);
      return { answers: [], decisions: [], truncated: false };
    });
    const { result } = renderHook(() => useProgressStore());
    act(() => result.current.record((p) => ({ ...p, answers: [...p.answers, ans(1)] })));
    await act(async () => {
      await result.current.sync();
    });
    up = false;
    let err: string | null = null;
    await act(async () => {
      err = await result.current.reset();
    });
    expect(err).toMatch(/nothing was deleted/);
    expect(result.current.progress.answers).toHaveLength(1);
  });

  it("a reset while a sync is in flight does not bring the deleted records back", async () => {
    // Regression: the in-flight sync's result (the pre-reset records) was merged into the
    // freshly reset doc when it arrived.
    let release: (() => void) | null = null;
    let serverHas: AnswerRecord[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        const json = (x: unknown) => new Response(JSON.stringify(x), { status: 200 });
        if (url.endsWith("/api/profiles")) return json({ key: "k1", profile: { profile_id: "p", name: "Player" } });
        if (init?.method === "POST") {
          serverHas = JSON.parse(String(init.body)).answers;
          return json(ok(serverHas.length));
        }
        const snapshot = serverHas;
        if (release === null) await new Promise<void>((r) => (release = r));
        return json({ answers: snapshot, decisions: [], truncated: false });
      }),
    );
    const { result } = renderHook(() => useProgressStore());
    const key = result.current.active.key;
    act(() => result.current.record((p) => ({ ...p, answers: [...p.answers, ans(1)] })));
    let first!: Promise<void>;
    act(() => {
      first = result.current.sync();
    });
    await act(async () => {
      while (!release) await new Promise((r) => setTimeout(r, 0));
    });
    // The sync has linked the profile and is waiting for its pull. Another tab resets:
    localStorage.setItem(DOC_PREFIX + key, JSON.stringify(emptyDoc()));
    act(() => {
      window.dispatchEvent(new StorageEvent("storage", { key: DOC_PREFIX + key }));
    });
    expect(result.current.progress.answers).toHaveLength(0);
    await act(async () => {
      (release as unknown as () => void)();
      await first;
    });
    expect(result.current.progress.answers).toHaveLength(0);
    expect(readDoc(key).doc!.answers).toHaveLength(0);
  });

  it("imports an export file and reports unreadable records", () => {
    server(() => new TypeError("Failed to fetch"));
    const { result } = renderHook(() => useProgressStore());
    let out: { added: number; invalid: number } | null = null;
    act(() => {
      out = result.current.importText(JSON.stringify({ answers: [ans(1), ans(1), { id: "x" }], decisions: [] }));
    });
    expect(out).toEqual({ added: 1, invalid: 1 });
    expect(result.current.importText("not json")).toBeNull();
    const exported = JSON.parse(result.current.exportJson());
    expect(exported).toMatchObject({ format: "pokerlab-progress", version: 2 });
    expect(exported.answers.map((a: AnswerRecord) => a.id)).toEqual(["a1"]);
  });
});
