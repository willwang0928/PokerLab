/**
 * Local persistence of progress: profiles, the record log, sync bookkeeping, migration.
 *
 * Layout in ``localStorage``:
 *
 *   pokerlab.profiles.v1           which profiles exist on this device, and the active one
 *   pokerlab.progress.v2.<key>     one profile's records (``LocalDoc``)
 *   pokerlab.progress.v1           the v0.3.x store; migrated once, then removed
 *   pokerlab.progress.v1.backup    the raw v0.3.x data, kept as it was
 *   pokerlab.progress.quarantine   records that failed validation, kept instead of deleted
 *
 * **Writes merge.** Records are immutable, so saving reads what is stored, takes the union
 * with what is in memory, and writes that. Two tabs therefore cannot overwrite each
 * other's new records (a plain "save my copy" would lose whichever tab saved first).
 * A reset starts a new ``epoch``; a stored doc from an older epoch is replaced, not merged,
 * so a reset is not undone by another tab.
 *
 * **Failures are reported, not swallowed.** A write that fails (storage full, blocked,
 * private mode) returns an error the UI shows; the records stay in memory and are retried
 * on the next save.
 */
import {
  type AnswerRecord,
  type DecisionRecord,
  type Dropped,
  PROGRESS_KEY,
  type Progress,
  sanitizeRecords,
} from "./progress";
import { withReviews } from "./skills";

export const PROFILES_KEY = "pokerlab.profiles.v1";
export const DOC_PREFIX = "pokerlab.progress.v2.";
export const LEGACY_BACKUP_KEY = "pokerlab.progress.v1.backup";
export const QUARANTINE_KEY = "pokerlab.progress.quarantine";
/** Records kept on this device per kind; unsynced records are never trimmed. */
export const LOCAL_LIMIT = 20_000;
const QUARANTINE_LIMIT = 500;

export type Kind = "answers" | "decisions";
const KINDS: Kind[] = ["answers", "decisions"];

export interface LocalProfile {
  key: string; // local id: names the storage key, never sent anywhere
  name: string;
  token?: string; // server profile key, once linked
  profileId?: string; // the server's public id
}

export interface ProfilesState {
  active: string;
  profiles: LocalProfile[];
}

export interface LocalDoc {
  version: 2;
  epoch: string;
  decisions: DecisionRecord[];
  answers: AnswerRecord[];
  /** Ids not yet confirmed by the server. */
  unsynced: Record<Kind, string[]>;
  /** Ids the server refused (kept locally, not retried). */
  rejected: Record<Kind, string[]>;
  lastSync?: number;
}

export type StorageError = "quota" | "unavailable";

export interface Notice {
  kind: "migrated" | "quarantined" | "storage";
  text: string;
}

const randomId = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID().replace(/-/g, "").slice(0, 16)
    : Math.random().toString(36).slice(2, 18);

export function emptyDoc(epoch = randomId()): LocalDoc {
  return {
    version: 2,
    epoch,
    decisions: [],
    answers: [],
    unsynced: { answers: [], decisions: [] },
    rejected: { answers: [], decisions: [] },
  };
}

// ---------------------------------------------------------------- raw access

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): StorageError | null {
  try {
    localStorage.setItem(key, value);
    return null;
  } catch (e) {
    const name = e instanceof DOMException ? e.name : "";
    return name === "QuotaExceededError" || name === "NS_ERROR_DOM_QUOTA_REACHED" ? "quota" : "unavailable";
  }
}

function parse(raw: string | null): unknown {
  if (raw === null) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return undefined; // corrupt JSON (distinct from "nothing stored")
  }
}

// ---------------------------------------------------------------- quarantine

export function quarantined(): { at: number; reason: string; record: unknown }[] {
  const q = parse(read(QUARANTINE_KEY));
  return Array.isArray(q) ? q : [];
}

export function quarantine(items: Dropped[] | { reason: string; record: unknown }[], now: number): void {
  if (!items.length) return;
  const next = [...quarantined(), ...items.map((d) => ({ at: now, reason: d.reason, record: d.record }))];
  write(QUARANTINE_KEY, JSON.stringify(next.slice(-QUARANTINE_LIMIT)));
}

// ---------------------------------------------------------------- docs

function idSet(xs: unknown): string[] {
  return Array.isArray(xs) ? xs.filter((x): x is string => typeof x === "string") : [];
}

/**
 * Parse a stored doc. Invalid records are moved to the quarantine (and counted in the
 * returned ``dropped``); unreadable JSON is quarantined whole and an empty doc returned.
 */
export function readDoc(key: string, now = Date.now()): { doc: LocalDoc | null; dropped: number } {
  const raw = read(DOC_PREFIX + key);
  const data = parse(raw);
  if (data === null) return { doc: null, dropped: 0 };
  if (data === undefined || typeof data !== "object") {
    quarantine([{ reason: "unreadable progress data", record: raw }], now);
    return { doc: null, dropped: 1 };
  }
  const d = data as Record<string, unknown>;
  const { decisions, answers, dropped } = sanitizeRecords(d);
  quarantine(dropped, now);
  const unsynced = (d.unsynced ?? {}) as Record<string, unknown>;
  const rejected = (d.rejected ?? {}) as Record<string, unknown>;
  return {
    doc: {
      version: 2,
      epoch: typeof d.epoch === "string" ? d.epoch : randomId(),
      decisions,
      answers,
      unsynced: { answers: idSet(unsynced.answers), decisions: idSet(unsynced.decisions) },
      rejected: { answers: idSet(rejected.answers), decisions: idSet(rejected.decisions) },
      lastSync: typeof d.lastSync === "number" ? d.lastSync : undefined,
    },
    dropped: dropped.length,
  };
}

/** Union of two docs of the same epoch (records by id; sync bookkeeping combined). */
export function mergeDocs(a: LocalDoc, b: LocalDoc): LocalDoc {
  const out: LocalDoc = { ...a, unsynced: { ...a.unsynced }, rejected: { ...a.rejected } };
  for (const k of KINDS) {
    const byId = new Map<string, AnswerRecord | DecisionRecord>();
    for (const r of [...a[k], ...b[k]]) if (!byId.has(r.id)) byId.set(r.id, r);
    const list = [...byId.values()].sort((x, y) => x.t - y.t || (x.id < y.id ? -1 : x.id > y.id ? 1 : 0));
    (out as unknown as Record<Kind, unknown[]>)[k] = list;
    // An id is synced if either copy knows the server has it.
    const syncedA = new Set(a[k].map((r) => r.id).filter((id) => !a.unsynced[k].includes(id)));
    const syncedB = new Set(b[k].map((r) => r.id).filter((id) => !b.unsynced[k].includes(id)));
    out.unsynced[k] = [...new Set([...a.unsynced[k], ...b.unsynced[k]])].filter((id) => !syncedA.has(id) && !syncedB.has(id));
    out.rejected[k] = [...new Set([...a.rejected[k], ...b.rejected[k]])];
  }
  out.lastSync = Math.max(a.lastSync ?? 0, b.lastSync ?? 0) || undefined;
  return trim(out);
}

/** Keep at most ``LOCAL_LIMIT`` records per kind, never dropping unsynced ones. */
function trim(doc: LocalDoc): LocalDoc {
  const out = { ...doc };
  for (const k of KINDS) {
    const list = doc[k] as { id: string }[];
    if (list.length <= LOCAL_LIMIT) continue;
    const keep = new Set(doc.unsynced[k]);
    const excess = list.length - LOCAL_LIMIT;
    let removed = 0;
    (out as unknown as Record<Kind, unknown[]>)[k] = list.filter((r) => keep.has(r.id) || removed++ >= excess);
  }
  return out;
}

/** Save with merge-on-write. Returns the doc as stored (or kept in memory on failure). */
export function saveDoc(key: string, doc: LocalDoc): { doc: LocalDoc; error: StorageError | null } {
  const { doc: stored } = readDoc(key);
  const merged = stored && stored.epoch === doc.epoch ? mergeDocs(doc, stored) : trim(doc);
  return { doc: merged, error: write(DOC_PREFIX + key, JSON.stringify(merged)) };
}

/** Replace a doc outright (a reset): a new epoch so other tabs adopt it. */
export function replaceDoc(key: string, doc: LocalDoc): StorageError | null {
  return write(DOC_PREFIX + key, JSON.stringify(doc));
}

/** Add records produced by ``f`` and mark the new ones unsynced. */
export function applyChange(doc: LocalDoc, f: (p: Progress) => Progress): LocalDoc {
  const before = withReviews(doc);
  const after = f(before);
  if (after === before) return doc;
  const out: LocalDoc = { ...doc, unsynced: { ...doc.unsynced } };
  for (const k of KINDS) {
    const known = new Set((doc[k] as { id: string }[]).map((r) => r.id));
    const added = (after[k] as { id: string }[]).filter((r) => !known.has(r.id));
    if (!added.length) continue;
    (out as unknown as Record<Kind, unknown[]>)[k] = [...(doc[k] as unknown[]), ...added];
    out.unsynced[k] = [...out.unsynced[k], ...added.map((r) => r.id)];
  }
  return out;
}

export const toProgress = (doc: LocalDoc): Progress => withReviews(doc);

// ---------------------------------------------------------------- profiles

export function readProfiles(): ProfilesState | null {
  const p = parse(read(PROFILES_KEY));
  if (!p || typeof p !== "object") return null;
  const r = p as Record<string, unknown>;
  const profiles = (Array.isArray(r.profiles) ? r.profiles : []).filter(
    (x): x is LocalProfile =>
      !!x &&
      typeof x === "object" &&
      typeof x.key === "string" &&
      /^[a-z0-9]{1,32}$/.test(x.key) &&
      typeof x.name === "string" &&
      (x.token === undefined || typeof x.token === "string") &&
      (x.profileId === undefined || typeof x.profileId === "string"),
  );
  if (!profiles.length) return null;
  const active = profiles.some((x) => x.key === r.active) ? (r.active as string) : profiles[0].key;
  return { active, profiles };
}

export function saveProfiles(state: ProfilesState): StorageError | null {
  return write(PROFILES_KEY, JSON.stringify(state));
}

export function newProfile(name = "Player"): LocalProfile {
  return { key: randomId().toLowerCase().replace(/[^a-z0-9]/g, "").slice(0, 16) || "p" + Date.now(), name };
}

// ---------------------------------------------------------------- startup and migration

export interface Loaded {
  profiles: ProfilesState;
  doc: LocalDoc;
  notices: Notice[];
  error: StorageError | null;
}

/**
 * Open local storage: the profile registry and the active profile's doc. On the first run
 * after v0.3.x, the old progress is migrated into a new profile: the raw data is copied to
 * a backup key first, records that fail validation go to the quarantine, and the old key
 * is removed only once the migrated copy has been written successfully.
 */
export function loadLocal(now = Date.now()): Loaded {
  const notices: Notice[] = [];
  let profiles = readProfiles();
  let error: StorageError | null = null;
  if (!profiles) {
    const first = newProfile();
    profiles = { active: first.key, profiles: [first] };
    error = saveProfiles(profiles);
  }
  const active = profiles.active;
  let { doc, dropped } = readDoc(active, now);

  const legacyRaw = read(PROGRESS_KEY);
  if (legacyRaw !== null) {
    const legacy = parse(legacyRaw);
    if (read(LEGACY_BACKUP_KEY) === null) write(LEGACY_BACKUP_KEY, legacyRaw);
    const { decisions, answers, dropped: bad } =
      legacy && typeof legacy === "object" ? sanitizeRecords(legacy) : { decisions: [], answers: [], dropped: [] };
    if (legacy === undefined) quarantine([{ reason: "unreadable v0.3 progress data", record: legacyRaw }], now);
    quarantine(bad, now);
    const migrated: LocalDoc = {
      ...emptyDoc(doc?.epoch),
      decisions,
      answers,
      unsynced: { answers: answers.map((a) => a.id), decisions: decisions.map((d) => d.id) },
    };
    const saved = saveDoc(active, doc ? mergeDocs(doc, migrated) : migrated);
    if (!saved.error) {
      try {
        localStorage.removeItem(PROGRESS_KEY);
      } catch {
        /* keep it: migration is idempotent */
      }
      doc = saved.doc;
      const n = answers.length + decisions.length;
      notices.push({
        kind: "migrated",
        text:
          `Moved ${n} saved record${n === 1 ? "" : "s"} to the new progress store.` +
          (bad.length || legacy === undefined
            ? ` ${bad.length || 1} could not be read and ${bad.length === 1 ? "was" : "were"} kept aside, not deleted.`
            : "") +
          " A backup of the old data stays in this browser.",
      });
    } else {
      error = saved.error;
      doc = doc ? mergeDocs(doc, migrated) : migrated;
    }
  }
  if (dropped) {
    notices.push({
      kind: "quarantined",
      text: `${dropped} saved record${dropped === 1 ? "" : "s"} could not be read. ${dropped === 1 ? "It was" : "They were"} kept aside, not deleted, and can be exported.`,
    });
  }
  if (!doc) {
    doc = emptyDoc();
    error = error ?? replaceDoc(active, doc);
  }
  if (error) notices.push({ kind: "storage", text: storageErrorText(error) });
  return { profiles, doc, notices, error };
}

export function storageErrorText(e: StorageError): string {
  return e === "quota"
    ? "This browser's storage is full, so new progress is only kept until you close the page. Export your progress or free some space."
    : "This browser is blocking storage (private mode or site settings), so progress is only kept until you close the page.";
}

/** Records from an exported file (v0.3.x or v0.4 format), validated. */
export function parseImport(text: string): { decisions: DecisionRecord[]; answers: AnswerRecord[]; dropped: Dropped[] } | null {
  const data = parse(text);
  if (!data || typeof data !== "object") return null;
  return sanitizeRecords(data);
}
