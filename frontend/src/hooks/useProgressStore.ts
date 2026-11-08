import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, profileApi } from "../lib/api";
import type { Progress } from "../lib/progress";
import {
  applyChange,
  DOC_PREFIX,
  emptyDoc,
  loadLocal,
  type LocalDoc,
  type LocalProfile,
  mergeDocs,
  newProfile,
  type Notice,
  parseImport,
  PROFILES_KEY,
  type ProfilesState,
  quarantined,
  readDoc,
  readProfiles,
  replaceDoc,
  saveDoc,
  saveProfiles,
  storageErrorText,
  type StorageError,
  toProgress,
} from "../lib/store";
import { pendingCount, rejectedCount, syncProfile, type SyncState } from "../lib/sync";

const RETRY_MS = [5_000, 15_000, 60_000, 300_000];

export interface ProgressStore {
  progress: Progress;
  record: (f: (p: Progress) => Progress) => void;
  sync: () => Promise<void>;
  syncState: SyncState;
  pending: number;
  rejected: number;
  quarantined: number;
  lastSync: number | null;
  notices: Notice[];
  dismissNotice: (n: Notice) => void;
  storageError: StorageError | null;
  profiles: LocalProfile[];
  active: LocalProfile;
  rename: (name: string) => Promise<void>;
  switchTo: (key: string) => void;
  createProfile: (name: string) => void;
  linkKey: (token: string) => Promise<string | null>;
  relink: () => Promise<void>;
  reset: () => Promise<string | null>;
  importText: (text: string) => { added: number; invalid: number } | null;
  exportJson: () => string;
}

/**
 * Progress for the active profile: saved on this device at once (merge-on-write), synced
 * to the server in the background, and kept consistent with other tabs.
 */
export function useProgressStore(): ProgressStore {
  const initial = useMemo(() => loadLocal(), []);
  const [profiles, setProfiles] = useState<ProfilesState>(initial.profiles);
  const [doc, setDoc] = useState<LocalDoc>(initial.doc);
  const [notices, setNotices] = useState<Notice[]>(initial.notices);
  const [storageError, setStorageError] = useState<StorageError | null>(initial.error);
  const active = profiles.profiles.find((p) => p.key === profiles.active) ?? profiles.profiles[0];
  const [syncState, setSyncState] = useState<SyncState>(active.token ? { status: "syncing" } : { status: "local" });

  // Refs so async work always sees the latest values.
  const docRef = useRef(doc);
  docRef.current = doc;
  const profilesRef = useRef(profiles);
  profilesRef.current = profiles;
  const running = useRef(false);
  const again = useRef(false);
  const retry = useRef(0);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);

  const persist = useCallback((key: string, next: LocalDoc) => {
    const saved = saveDoc(key, next);
    setStorageError(saved.error);
    return saved.doc;
  }, []);

  const updateProfile = useCallback((p: LocalProfile) => {
    const state = profilesRef.current;
    const next = { ...state, profiles: state.profiles.map((x) => (x.key === p.key ? p : x)) };
    profilesRef.current = next;
    setProfiles(next);
    saveProfiles(next);
  }, []);

  const sync = useCallback(async () => {
    if (running.current) {
      again.current = true;
      return;
    }
    running.current = true;
    if (retryTimer.current) clearTimeout(retryTimer.current);
    try {
      do {
        again.current = false;
        const state = profilesRef.current;
        const profile = state.profiles.find((p) => p.key === state.active)!;
        setSyncState({ status: "syncing" });
        const startEpoch = docRef.current.epoch;
        const res = await syncProfile(profile, docRef.current);
        if (profilesRef.current.active !== profile.key) return; // switched profile meanwhile
        // A reset (here or in another tab) while the request was in flight: the result
        // describes the deleted data, so merging it would bring the records back.
        const now = profilesRef.current.profiles.find((p) => p.key === profile.key);
        if (docRef.current.epoch !== startEpoch || now?.token !== profile.token) {
          again.current = true;
          continue;
        }
        if (res.profile !== profile) updateProfile(res.profile);
        const merged = persist(profile.key, mergeDocs(docRef.current, res.doc));
        docRef.current = merged;
        setDoc(merged);
        setSyncState(res.state);
        if (res.invalidRemote)
          setNotices((n) => [
            ...n,
            { kind: "quarantined", text: `${res.invalidRemote} record(s) on the server could not be read here and were skipped.` },
          ]);
        if (res.state.status === "offline" || res.state.status === "unavailable") {
          const wait = RETRY_MS[Math.min(retry.current++, RETRY_MS.length - 1)];
          retryTimer.current = setTimeout(() => void sync(), wait);
          return;
        }
        retry.current = 0;
      } while (again.current);
    } finally {
      running.current = false;
    }
  }, [persist, updateProfile]);

  const scheduleSync = useCallback(() => {
    if (debounce.current) clearTimeout(debounce.current);
    debounce.current = setTimeout(() => void sync(), 400);
  }, [sync]);

  // First sync, and again whenever the browser comes back online.
  useEffect(() => {
    void sync();
    const online = () => {
      retry.current = 0;
      void sync();
    };
    window.addEventListener("online", online);
    return () => {
      window.removeEventListener("online", online);
      if (retryTimer.current) clearTimeout(retryTimer.current);
      if (debounce.current) clearTimeout(debounce.current);
    };
  }, [sync]);

  // Another tab saved: merge its records (or adopt its reset), and its profile list.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      const key = profilesRef.current.active;
      if (e.key === DOC_PREFIX + key) {
        const { doc: other } = readDoc(key);
        if (!other) return;
        const mine = docRef.current;
        const next = other.epoch === mine.epoch ? mergeDocs(mine, other) : other;
        docRef.current = next;
        setDoc(next);
      } else if (e.key === PROFILES_KEY) {
        const next = readProfiles();
        if (next) {
          profilesRef.current = { ...next, active: key };
          setProfiles(profilesRef.current);
        }
      }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const record = useCallback(
    (f: (p: Progress) => Progress) => {
      const current = docRef.current;
      const changed = applyChange(current, f);
      if (changed === current) return;
      const saved = persist(profilesRef.current.active, changed);
      docRef.current = saved;
      setDoc(saved);
      scheduleSync();
    },
    [persist, scheduleSync],
  );

  const activate = useCallback(
    (state: ProfilesState) => {
      profilesRef.current = state;
      setProfiles(state);
      setStorageError(saveProfiles(state));
      const { doc: d } = readDoc(state.active);
      const next = d ?? emptyDoc();
      docRef.current = next;
      setDoc(next);
      const p = state.profiles.find((x) => x.key === state.active)!;
      setSyncState(p.token ? { status: "syncing" } : { status: "local" });
      void sync();
    },
    [sync],
  );

  const rename = async (name: string) => {
    const clean = name.trim().slice(0, 40) || "Player";
    const p = { ...active, name: clean };
    updateProfile(p);
    if (p.token) {
      try {
        await profileApi.rename(p.token, clean);
      } catch {
        /* the local name is kept; the server name updates on a later rename */
      }
    }
  };

  const switchTo = (key: string) => {
    if (key === profiles.active || !profiles.profiles.some((p) => p.key === key)) return;
    activate({ ...profiles, active: key });
  };

  const createProfile = (name: string) => {
    const p = { ...newProfile(name.trim().slice(0, 40) || "Player") };
    activate({ active: p.key, profiles: [...profiles.profiles, p] });
  };

  /** Use an existing server profile on this device. Returns an error message or null. */
  const linkKey = async (token: string): Promise<string | null> => {
    const key = token.trim();
    if (!key) return "Paste a profile key.";
    const existing = profiles.profiles.find((p) => p.token === key);
    if (existing) {
      switchTo(existing.key);
      return null;
    }
    try {
      const server = await profileApi.get(key);
      const p: LocalProfile = { ...newProfile(server.name), token: key, profileId: server.profile_id };
      activate({ active: p.key, profiles: [...profiles.profiles, p] });
      return null;
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return "The server does not know that key.";
      if (e instanceof ApiError && e.status === 0) return "The server is not reachable right now.";
      return e instanceof Error ? e.message : "Could not use that key.";
    }
  };

  /** After "unlinked": make a new server copy of everything on this device. */
  const relink = async () => {
    const p: LocalProfile = { key: active.key, name: active.name };
    updateProfile(p);
    await sync();
  };

  /** Delete this profile's progress here and on the server. Returns an error or null. */
  const reset = async (): Promise<string | null> => {
    if (active.token) {
      try {
        await profileApi.remove(active.token);
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 401)) {
          return "The server copy could not be deleted (the server is unreachable), so nothing was deleted. Try again when it is back.";
        }
      }
    }
    const fresh = emptyDoc();
    setStorageError(replaceDoc(active.key, fresh));
    docRef.current = fresh;
    setDoc(fresh);
    updateProfile({ key: active.key, name: active.name });
    setSyncState({ status: "local" });
    void sync();
    return null;
  };

  const importText = (text: string) => {
    const parsed = parseImport(text);
    if (!parsed) return null;
    const incoming: LocalDoc = {
      ...emptyDoc(docRef.current.epoch),
      decisions: parsed.decisions,
      answers: parsed.answers,
      unsynced: { answers: parsed.answers.map((a) => a.id), decisions: parsed.decisions.map((d) => d.id) },
    };
    const before = docRef.current.answers.length + docRef.current.decisions.length;
    const saved = persist(active.key, mergeDocs(docRef.current, incoming));
    docRef.current = saved;
    setDoc(saved);
    scheduleSync();
    return { added: saved.answers.length + saved.decisions.length - before, invalid: parsed.dropped.length };
  };

  const exportJson = () =>
    JSON.stringify(
      { format: "pokerlab-progress", version: 2, exported_at: new Date().toISOString(), profile: active.name,
        decisions: doc.decisions, answers: doc.answers, unreadable: quarantined() },  // prettier-ignore
      null,
      2,
    );

  const progress = useMemo(() => toProgress(doc), [doc]);
  const shownNotices = storageError ? [...notices.filter((n) => n.kind !== "storage"), { kind: "storage" as const, text: storageErrorText(storageError) }] : notices;

  return {
    progress,
    record,
    sync,
    syncState,
    pending: pendingCount(doc),
    rejected: rejectedCount(doc),
    quarantined: quarantined().length,
    lastSync: doc.lastSync ?? null,
    notices: shownNotices,
    dismissNotice: (n) => setNotices((xs) => xs.filter((x) => x !== n)),
    storageError,
    profiles: profiles.profiles,
    active,
    rename,
    switchTo,
    createProfile,
    linkKey,
    relink,
    reset,
    importText,
    exportJson,
  };
}
