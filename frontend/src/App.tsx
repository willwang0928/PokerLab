import { AnimatePresence } from "motion/react";
import { useEffect, useState } from "react";
import { ActionBar } from "./components/ActionBar";
import { GameOverModal } from "./components/GameOverModal";
import { HandLog } from "./components/HandLog";
import { SettingsMenu } from "./components/SettingsMenu";
import { StartScreen } from "./components/StartScreen";
import { Table } from "./components/Table";
import { AI, usePokerGame } from "./hooks/usePokerGame";
import { useSettings } from "./hooks/useSettings";
import type { GameConfig } from "./types";

const DEFAULT_CONFIG: GameConfig = {
  player_name: "You",
  starting_stack: 1000,
  small_blind: 5,
  big_blind: 10,
};

export default function App() {
  const [settings, updateSettings] = useSettings();
  const game = usePokerGame(settings);
  const { state } = game;
  const [config, setConfig] = useState<GameConfig>(DEFAULT_CONFIG);
  const [showSetup, setShowSetup] = useState(true);

  const start = async (c: GameConfig) => {
    setConfig(c);
    setShowSetup(false);
    await game.newGame(c);
  };

  // If the very first request fails, return to the setup screen to show the error.
  useEffect(() => {
    if (game.error && !state) setShowSetup(true);
  }, [game.error, state]);

  const busy = game.animating || game.pending;
  const humanTurn = Boolean(state?.legal) && !busy;
  const matchEnded = state?.status === "game_over" && !busy;
  // Let the final showdown sink in before covering the table.
  const [gameOver, setGameOver] = useState(false);
  useEffect(() => {
    if (!matchEnded) {
      setGameOver(false);
      return;
    }
    const t = setTimeout(() => setGameOver(true), 2200);
    return () => clearTimeout(t);
  }, [matchEnded]);

  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex items-center justify-between gap-3 px-4 py-2.5 sm:px-6">
        <div className="flex items-center gap-3">
          <div className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-emerald-500 to-teal-800 text-lg shadow-lg ring-1 ring-white/10" aria-hidden>
            ♠
          </div>
          <div className="leading-tight">
            <div className="font-display text-lg font-bold text-amber-100">PokerLab</div>
            <div className="hidden text-xs text-slate-400 sm:block">Heads-Up No-Limit Hold'em</div>
          </div>
        </div>
        <div className="flex items-center gap-2 sm:gap-3">
          {state && (
            <div className="hidden items-center gap-3 rounded-xl bg-slate-900/70 px-3 py-1.5 text-xs text-slate-300 ring-1 ring-white/5 sm:flex">
              <span>
                Hand <b className="tabular-nums text-slate-100">#{state.hand_number}</b>
              </span>
              <span className="h-3 w-px bg-white/10" />
              <span>
                Blinds{" "}
                <b className="tabular-nums text-slate-100">
                  {state.config.small_blind}/{state.config.big_blind}
                </b>
              </span>
            </div>
          )}
          <SettingsMenu settings={settings} onChange={updateSettings} />
          <button
            type="button"
            onClick={() => setShowSetup(true)}
            className="rounded-xl bg-slate-800/80 px-3 py-2 text-sm font-semibold text-slate-200 ring-1 ring-white/5 hover:bg-slate-700"
          >
            New game
          </button>
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-[1400px] flex-1 flex-col gap-4 px-3 pb-4 sm:px-6 lg:flex-row lg:items-start">
        <div className="flex min-w-0 flex-1 flex-col items-center gap-3">
          {state ? (
            <>
              <Table
                state={state}
                lastEvent={game.lastEvent}
                thinkingSeat={game.thinkingSeat}
                humanTurn={humanTurn}
              />
              <div className="w-full max-w-[760px]">
                {state.legal && state.status === "in_hand" && !game.animating ? (
                  <ActionBar
                    legal={state.legal}
                    pot={state.pot}
                    currentBet={state.current_bet}
                    bigBlind={state.config.big_blind}
                    disabled={busy}
                    onAction={(a, amt) => void game.act(a, amt)}
                  />
                ) : (
                  <StatusBar
                    waiting={state.status === "waiting" && !busy}
                    thinking={game.thinkingSeat === AI}
                    opponent={state.players[AI].name}
                    latest={state.log[state.log.length - 1] ?? ""}
                    autoDealAt={game.autoDealAt}
                    onDeal={() => void game.deal()}
                  />
                )}
              </div>
            </>
          ) : (
            <div className="grid flex-1 place-items-center text-slate-500">
              {game.pending ? "Shuffling…" : ""}
            </div>
          )}
        </div>

        {state && (
          <aside className="w-full lg:sticky lg:top-4 lg:flex lg:h-[calc(100vh-6rem)] lg:w-80 lg:flex-col">
            <HandLog lines={state.log} />
          </aside>
        )}
      </main>

      {game.error && state && (
        <div role="alert" className="fixed bottom-4 left-1/2 z-40 flex -translate-x-1/2 items-center gap-3 rounded-xl bg-rose-600 px-4 py-2.5 text-sm font-medium text-white shadow-2xl">
          {game.error}
          <button type="button" onClick={game.clearError} className="rounded bg-black/20 px-2 py-0.5 text-xs">
            Dismiss
          </button>
        </div>
      )}

      <AnimatePresence>
        {showSetup && (
          <StartScreen
            key="setup"
            initial={config}
            busy={game.pending}
            error={state ? null : game.error}
            onStart={(c) => void start(c)}
            onCancel={state ? () => setShowSetup(false) : undefined}
          />
        )}
        {gameOver && !showSetup && state && (
          <GameOverModal
            key="over"
            state={state}
            onPlayAgain={() => void start(config)}
            onChangeSettings={() => setShowSetup(true)}
          />
        )}
      </AnimatePresence>
    </div>
  );
}

function StatusBar({
  waiting,
  thinking,
  opponent,
  autoDealAt,
  onDeal,
  latest,
}: {
  waiting: boolean;
  thinking: boolean;
  opponent: string;
  latest: string;
  autoDealAt: number | null;
  onDeal: () => void;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!autoDealAt) return;
    const t = setInterval(() => setNow(Date.now()), 200);
    return () => clearInterval(t);
  }, [autoDealAt]);

  useEffect(() => {
    if (!waiting) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === " " || e.key.toLowerCase() === "n") {
        if (e.target instanceof HTMLInputElement || e.target instanceof HTMLButtonElement) return;
        e.preventDefault();
        onDeal();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [waiting, onDeal]);

  const seconds = autoDealAt ? Math.max(0, Math.ceil((autoDealAt - now) / 1000)) : null;

  return (
    <div className="flex min-h-[3.25rem] items-center justify-center gap-3 rounded-2xl bg-slate-900/60 px-4 py-2.5 text-sm text-slate-300 ring-1 ring-white/5">
      {waiting ? (
        <>
          <span>{seconds !== null ? `Next hand in ${seconds}s` : "Hand complete"}</span>
          <button
            type="button"
            onClick={onDeal}
            className="rounded-xl bg-gradient-to-b from-amber-300 to-amber-500 px-4 py-2 text-sm font-bold text-slate-950 shadow hover:from-amber-200 hover:to-amber-400"
          >
            Deal next hand
          </button>
        </>
      ) : thinking ? (
        <span>{opponent} is thinking…</span>
      ) : (
        <span className="truncate text-slate-400">{latest || "Dealing…"}</span>
      )}
    </div>
  );
}
