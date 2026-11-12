/**
 * Syncing a profile's local records with the PokerLab server.
 *
 * Records are immutable with unique ids, so a sync is a union in both directions:
 *
 * 1. Link: a profile without a server key gets one (``POST /api/profiles``), once there
 *    is something to keep (records, or a finished hand waiting to be saved).
 * 2. Pull: every server record the device lacks is added locally (validated first).
 * 3. Push: records the server has not confirmed are uploaded in batches. The server
 *    validates each one; accepted ones are marked synced, refused ones are kept locally
 *    but marked rejected so they are not retried forever.
 *
 * Every step is safe to repeat: an interrupted sync simply resumes next time. The device
 * keeps working when the server is down; records wait in ``unsynced`` until it is back.
 */
import { ApiError, profileApi } from "./api";
import { sanitizeRecords } from "./progress";
import { type Kind, type LocalDoc, type LocalProfile, mergeDocs } from "./store";

export const BATCH = 1000;

export type SyncState =
  | { status: "local" } // never linked to a server
  | { status: "syncing" }
  | { status: "synced"; at: number }
  | { status: "offline"; message: string } // server unreachable
  | { status: "unavailable"; message: string } // server up, storage down
  | { status: "unlinked"; message: string }; // the server does not know this profile key

export interface SyncResult {
  doc: LocalDoc;
  profile: LocalProfile;
  state: SyncState;
  /** Records the server refused during this sync. */
  rejected: number;
  /** Server records that failed validation here (left on the server, not imported). */
  invalidRemote: number;
}

function failure(e: unknown): SyncState {
  if (e instanceof ApiError) {
    if (e.status === 0) return { status: "offline", message: "The PokerLab server is not reachable." };
    if (e.status === 503) return { status: "unavailable", message: "The server's progress storage is unavailable." };
    if (e.status === 401)
      return { status: "unlinked", message: "The server no longer has this profile (it may have been deleted or reset)." };
    return { status: "offline", message: e.message };
  }
  return { status: "offline", message: "Sync failed unexpectedly." };
}

/** Strip local-only fields before upload (records are stored exactly as validated). */
function wire<T extends object>(r: T): T {
  return { ...r };
}

export async function syncProfile(
  profile: LocalProfile,
  doc: LocalDoc,
  opts: { forceLink?: boolean } = {},
): Promise<SyncResult> {
  let p = profile;
  let d = doc;
  let rejected = 0;
  let invalidRemote = 0;
  try {
    // 1. Link (only once there is something to keep: opening the app creates nothing).
    if (!p.token && !d.answers.length && !d.decisions.length && !opts.forceLink) {
      return { doc: d, profile: p, state: { status: "local" }, rejected, invalidRemote };
    }
    if (!p.token) {
      const created = await profileApi.create(p.name);
      p = { ...p, token: created.key, profileId: created.profile.profile_id };
      // Everything on this device is new to the server.
      d = {
        ...d,
        unsynced: { answers: d.answers.map((a) => a.id), decisions: d.decisions.map((x) => x.id) },
      };
    }
    const token = p.token!;

    // 2. Pull.
    const remote = await profileApi.records(token);
    const clean = sanitizeRecords(remote);
    invalidRemote = clean.dropped.length;
    const pulled: LocalDoc = {
      ...d,
      decisions: clean.decisions,
      answers: clean.answers,
      unsynced: { answers: [], decisions: [] },
      rejected: { answers: [], decisions: [] },
    };
    d = mergeDocs(d, pulled);
    // Anything the server already has is synced, whatever this device thought.
    const onServer: Record<Kind, Set<string>> = {
      answers: new Set(clean.answers.map((a) => a.id)),
      decisions: new Set(clean.decisions.map((x) => x.id)),
    };
    d = {
      ...d,
      unsynced: {
        answers: d.unsynced.answers.filter((id) => !onServer.answers.has(id)),
        decisions: d.unsynced.decisions.filter((id) => !onServer.decisions.has(id)),
      },
    };

    // 3. Push, in batches.
    const pending = (k: Kind) => new Set(d.unsynced[k].filter((id) => !d.rejected[k].includes(id)));
    const pa = pending("answers");
    const pd = pending("decisions");
    const answers = d.answers.filter((a) => pa.has(a.id)).map(wire);
    const decisions = d.decisions.filter((x) => pd.has(x.id)).map(wire);
    for (let i = 0; i < Math.max(answers.length, decisions.length); i += BATCH) {
      const ba = answers.slice(i, i + BATCH);
      const bd = decisions.slice(i, i + BATCH);
      const res = await profileApi.upload(token, ba, bd);
      const refused: Record<Kind, Set<string>> = { answers: new Set(), decisions: new Set() };
      for (const r of res.rejected) if (r.id) refused[r.kind].add(r.id);
      rejected += res.rejected.length;
      const done: Record<Kind, Set<string>> = {
        answers: new Set(ba.map((a) => a.id).filter((id) => !refused.answers.has(id))),
        decisions: new Set(bd.map((x) => x.id).filter((id) => !refused.decisions.has(id))),
      };
      d = {
        ...d,
        unsynced: {
          answers: d.unsynced.answers.filter((id) => !done.answers.has(id) && !refused.answers.has(id)),
          decisions: d.unsynced.decisions.filter((id) => !done.decisions.has(id) && !refused.decisions.has(id)),
        },
        rejected: {
          answers: [...d.rejected.answers, ...refused.answers],
          decisions: [...d.rejected.decisions, ...refused.decisions],
        },
      };
    }
    const at = Date.now();
    d = { ...d, lastSync: at };
    return { doc: d, profile: p, state: { status: "synced", at }, rejected, invalidRemote };
  } catch (e) {
    // Keep whatever progress was made (a link, pulled records): it is all safe to keep.
    return { doc: d, profile: p, state: failure(e), rejected, invalidRemote };
  }
}

export function pendingCount(doc: LocalDoc): number {
  const pending = (k: Kind) => doc.unsynced[k].filter((id) => !doc.rejected[k].includes(id)).length;
  return pending("answers") + pending("decisions");
}

export function rejectedCount(doc: LocalDoc): number {
  return doc.rejected.answers.length + doc.rejected.decisions.length;
}
