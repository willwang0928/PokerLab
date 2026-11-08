import { useRef, useState } from "react";
import type { ProgressStore } from "../hooks/useProgressStore";
import { quarantined } from "../lib/store";

function since(t: number, now: number): string {
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

/** Where progress is stored, whether it is synced, and the profile controls. */
export function StoragePanel({ store, now = Date.now() }: { store: ProgressStore; now?: number }) {
  const s = store.syncState;
  const [showKey, setShowKey] = useState(false);
  const [linking, setLinking] = useState(false);
  const [keyInput, setKeyInput] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);
  const [name, setName] = useState(store.active.name);
  const fileRef = useRef<HTMLInputElement>(null);

  const status =
    s.status === "synced"
      ? { tone: "text-emerald-300", text: `Saved on this device and on the PokerLab server · synced ${since(s.at, now)}` }
      : s.status === "syncing"
        ? { tone: "text-slate-300", text: "Syncing with the server…" }
        : s.status === "local"
          ? { tone: "text-slate-300", text: "Saved on this device. It will also be saved on the server once it is reachable." }
          : s.status === "unlinked"
            ? { tone: "text-amber-300", text: `${s.message} Your progress is safe on this device.` }
            : { tone: "text-amber-300", text: `${s.message} Progress is saved on this device and will sync when it is back.` };

  return (
    <section className="surface rounded-2xl p-4 lg:col-span-2" aria-labelledby="storage-title" data-testid="storage-panel">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="storage-title" className="eyebrow text-slate-400">
          Profile and storage
        </h2>
        <button type="button" onClick={() => void store.sync()} className="text-[12px] font-semibold text-amber-200 hover:text-amber-100">
          Sync now
        </button>
      </div>
      <p className={`mt-1 text-[12.5px] ${status.tone}`} role="status" data-testid="sync-status">
        {status.text}
      </p>
      {(store.pending > 0 || store.rejected > 0) && (
        <p className="mt-0.5 text-[12px] text-slate-400" data-testid="sync-counts">
          {store.pending > 0 && <>{store.pending} record{store.pending === 1 ? "" : "s"} waiting to sync. </>}
          {store.rejected > 0 && <>{store.rejected} refused by the server (kept on this device and in exports).</>}
        </p>
      )}
      {s.status === "unlinked" && (
        <button type="button" onClick={() => void store.relink()} className="btn-dark mt-2 rounded-lg px-3 py-1.5 text-[12px] font-semibold">
          Save everything to the server again
        </button>
      )}
      {store.notices.map((n) => (
        <div
          key={n.text}
          className={`mt-2 flex items-start gap-2 rounded-lg px-3 py-2 text-[12px] ring-1 ${
            n.kind === "migrated" ? "bg-sky-500/10 text-sky-100 ring-sky-400/20" : "bg-amber-500/10 text-amber-100 ring-amber-400/20"
          }`}
          role={n.kind === "storage" ? "alert" : "status"}
          data-testid={`notice-${n.kind}`}
        >
          <span className="flex-1">{n.text}</span>
          {n.kind !== "storage" && (
            <button type="button" onClick={() => store.dismissNotice(n)} className="font-semibold opacity-80 hover:opacity-100">
              Dismiss
            </button>
          )}
        </div>
      ))}

      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <div>
          <label className="block text-[11px] font-semibold uppercase tracking-wide text-slate-400" htmlFor="profile-name">
            Profile name
          </label>
          <div className="mt-1 flex gap-2">
            <input
              id="profile-name"
              value={name}
              maxLength={40}
              onChange={(e) => setName(e.target.value)}
              className="min-w-0 flex-1 rounded-lg bg-slate-800 px-2.5 py-1.5 text-[13px] text-slate-100 ring-1 ring-white/10"
            />
            <button
              type="button"
              disabled={name.trim() === store.active.name}
              onClick={() => void store.rename(name)}
              className="btn-dark rounded-lg px-3 py-1.5 text-[12px] font-semibold disabled:opacity-50"
            >
              Rename
            </button>
          </div>
          {store.profiles.length > 1 && (
            <label className="mt-2 block text-[12px] text-slate-300">
              Switch profile{" "}
              <select
                value={store.active.key}
                onChange={(e) => {
                  store.switchTo(e.target.value);
                  setName(store.profiles.find((p) => p.key === e.target.value)?.name ?? "");
                }}
                className="ml-1 rounded-lg bg-slate-800 px-2 py-1 text-[12.5px] text-slate-100 ring-1 ring-white/10"
              >
                {store.profiles.map((p) => (
                  <option key={p.key} value={p.key}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <button
            type="button"
            onClick={() => {
              store.createProfile("Player");
              setName("Player");
            }}
            className="mt-2 text-[12px] font-semibold text-amber-200 hover:text-amber-100"
          >
            + New profile (for another player on this device)
          </button>
        </div>

        <div className="text-[12px] text-slate-300">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Use on another browser</div>
          {store.active.token ? (
            <>
              <p className="mt-1 text-slate-400">
                Your profile key opens this progress anywhere that uses the same PokerLab server. Treat it like a password.
              </p>
              {showKey ? (
                <div className="mt-1 flex gap-2">
                  <code className="min-w-0 flex-1 break-all rounded bg-black/30 px-2 py-1 text-[11px] text-slate-200" data-testid="profile-key">
                    {store.active.token}
                  </code>
                  <button
                    type="button"
                    onClick={() => void navigator.clipboard?.writeText(store.active.token!)}
                    className="btn-dark rounded-lg px-2 py-1 text-[11.5px] font-semibold"
                  >
                    Copy
                  </button>
                </div>
              ) : (
                <button type="button" onClick={() => setShowKey(true)} className="mt-1 font-semibold text-amber-200 hover:text-amber-100">
                  Show profile key
                </button>
              )}
            </>
          ) : (
            <p className="mt-1 text-slate-400">A profile key appears here once the progress is saved on the server.</p>
          )}
          {linking ? (
            <form
              className="mt-2 flex gap-2"
              onSubmit={async (e) => {
                e.preventDefault();
                const err = await store.linkKey(keyInput);
                setMessage(err);
                if (!err) {
                  setLinking(false);
                  setKeyInput("");
                }
              }}
            >
              <input
                aria-label="Profile key"
                value={keyInput}
                onChange={(e) => setKeyInput(e.target.value)}
                placeholder="Paste a profile key"
                className="min-w-0 flex-1 rounded-lg bg-slate-800 px-2.5 py-1.5 text-[12.5px] text-slate-100 ring-1 ring-white/10"
              />
              <button type="submit" className="btn-dark rounded-lg px-3 py-1.5 text-[12px] font-semibold">
                Open
              </button>
            </form>
          ) : (
            <button type="button" onClick={() => setLinking(true)} className="mt-2 block font-semibold text-amber-200 hover:text-amber-100">
              Open a profile from another browser…
            </button>
          )}
        </div>
      </div>

      {message && (
        <p className="mt-2 text-[12px] text-amber-200" role="alert">
          {message}
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-white/5 pt-3 text-[12px] text-slate-400">
        <span className="mr-auto">
          {store.progress.answers.length} answers and {store.progress.decisions.length} table decisions in this profile.
          {store.quarantined > 0 && <> {store.quarantined} unreadable record(s) kept aside.</>}
        </span>
        <button
          type="button"
          onClick={() => download("pokerlab-progress.json", store.exportJson())}
          className="btn-dark rounded-lg px-3 py-1.5 font-semibold"
        >
          Export
        </button>
        <button type="button" onClick={() => fileRef.current?.click()} className="btn-dark rounded-lg px-3 py-1.5 font-semibold">
          Import
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="application/json,.json"
          className="hidden"
          data-testid="import-file"
          onChange={async (e) => {
            const file = e.target.files?.[0];
            e.target.value = "";
            if (!file) return;
            const res = store.importText(await file.text());
            setMessage(
              res
                ? `Imported ${res.added} new record${res.added === 1 ? "" : "s"}` +
                    (res.invalid ? `; ${res.invalid} could not be read and were skipped.` : ".")
                : "That file is not a PokerLab progress export.",
            );
          }}
        />
        {confirmReset ? (
          <>
            <span className="text-rose-200">Delete this profile's progress here and on the server?</span>
            <button
              type="button"
              onClick={async () => {
                setMessage(await store.reset());
                setConfirmReset(false);
              }}
              className="rounded-lg bg-rose-600 px-3 py-1.5 font-semibold text-white"
            >
              Delete
            </button>
            <button type="button" onClick={() => setConfirmReset(false)} className="btn-dark rounded-lg px-3 py-1.5 font-semibold">
              Keep
            </button>
          </>
        ) : (
          <button type="button" onClick={() => setConfirmReset(true)} className="btn-dark rounded-lg px-3 py-1.5 font-semibold">
            Reset progress
          </button>
        )}
      </div>
      {quarantined().length > 0 && (
        <p className="mt-1 text-[11px] text-slate-500">Unreadable records are included in exports under "unreadable".</p>
      )}
    </section>
  );
}
