import { AnimatePresence, MotionConfig, useReducedMotion } from "motion/react";
import { useEffect, useLayoutEffect, useMemo, useState } from "react";
import { ActionBar } from "./components/ActionBar";
import { GameOverModal } from "./components/GameOverModal";
import { HandLog } from "./components/HandLog";
import { MuteButton, SettingsMenu } from "./components/SettingsMenu";
import { StartScreen } from "./components/StartScreen";
import { SuitIcon } from "./components/SuitIcon";
import { Table } from "./components/Table";
import { AI, usePokerGame } from "./hooks/usePokerGame";
import { useGameSounds } from "./hooks/useGameSounds";
import { SPEED_SCALE, useSettings } from "./hooks/useSettings";
import { type MotionPrefs, MotionPrefsContext } from "./lib/motion";
import { sound } from "./lib/sound";
import type { GameConfig } from "./types";

const DEFAULT_CONFIG: GameConfig = {
  player_name: "You",
  starting_stack: 1000,
  small_blind: 5,
  big_blind: 10,
};

export default function App() {
  const [settings, updateSettings] = useSettings();
  const reducedMotion = Boolean(useReducedMotion());
  const game = usePokerGame(settings);
  const motionPrefs = useMemo<MotionPrefs>(
    () => ({ scale: SPEED_SCALE[settings.speed], reduced: reducedMotion }),
    [settings.speed, reducedMotion],
  );
  const { state } = game;
  useGameSounds(game.lastEvent, settings, motionPrefs);

  // Before paint, so a saved theme never flashes the default felt.
  useLayoutEffect(() => {
    document.documentElement.dataset.tableTheme = settings.theme;
  }, [settings.theme]);
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
    <MotionConfig reducedMotion="user">
    <MotionPrefsContext value={motionPrefs}>
    <div className="flex min-h-screen flex-col">
      <header className="flex items-center justify-between gap-3 px-4 py-3 sm:px-6">
        <div className="flex items-center gap-3">
          <div
            className="grid h-9 w-9 place-items-center rounded-[11px] bg-gradient-to-br from-emerald-400 via-emerald-600 to-teal-900 text-white shadow-lg ring-1 ring-white/15"
            style={{ boxShadow: "inset 0 1px 0 rgba(255,255,255,.3), 0 6px 16px -6px rgba(16,185,129,.6)" }}
            aria-hidden
          >
            <SuitIcon suit="s" className="h-[18px] w-[18px] drop-shadow" />
          </div>
          <div className="hidden leading-tight min-[420px]:block">
            <div className="font-display text-[19px] font-bold tracking-wide text-amber-50">PokerLab</div>
            <div className="hidden text-[11px] font-medium uppercase tracking-[0.14em] text-slate-500 sm:block">
              Heads-up No-Limit Hold'em
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 sm:gap-2.5">
          {state && (
            <div className="surface flex items-center gap-2.5 rounded-xl px-3 py-2 text-xs text-slate-400 sm:gap-3">
              <span>
                <span className="hidden sm:inline">Hand </span>
                <b className="tabular-nums font-semibold text-slate-100">#{state.hand_number}</b>
              </span>
              <span className="h-3 w-px bg-white/10" />
              <span>
                <span className="hidden sm:inline">Blinds </span>
                <b className="tabular-nums font-semibold text-slate-100">
                  {state.config.small_blind}/{state.config.big_blind}
                </b>
              </span>
            </div>
          )}
          <MuteButton
            muted={!settings.sound || settings.volume === 0}
            onToggle={() =>
              updateSettings(
                settings.sound && settings.volume > 0
                  ? { sound: false }
                  : { sound: true, volume: settings.volume > 0 ? settings.volume : 0.6 },
              )
            }
          />
          <SettingsMenu
            settings={settings}
            onChange={updateSettings}
            onPreviewSound={() => {
              sound.unlock();
              sound.play("chip");
            }}
          />
          <button
            type="button"
            onClick={() => setShowSetup(true)}
            className="btn-dark whitespace-nowrap rounded-xl px-3 py-2 text-sm font-semibold"
          >
            New game
          </button>
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-[1440px] flex-1 flex-col gap-5 px-3 pb-5 sm:px-6 lg:flex-row lg:items-start">
        <div className="flex min-w-0 flex-col items-center gap-4 lg:flex-1">
          {state ? (
            <>
              <Table
                state={state}
                lastEvent={game.lastEvent}
                thinkingSeat={game.thinkingSeat}
                humanTurn={humanTurn}
              />
              <div className="w-full max-w-[780px]">
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
            <div className="grid min-h-[50vh] w-full place-items-center text-slate-500">
              {game.pending ? "Shuffling…" : ""}
            </div>
          )}
        </div>

        {state && (
          <aside className="w-full lg:sticky lg:top-4 lg:flex lg:max-h-[calc(100vh-6rem)] lg:w-80 lg:shrink-0 lg:flex-col">
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
    </MotionPrefsContext>
    </MotionConfig>
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
    <div className="surface flex min-h-[3.5rem] items-center justify-center gap-3 rounded-2xl px-4 py-2.5 text-sm text-slate-300">
      {waiting ? (
        <>
          <span className="tabular-nums">{seconds !== null ? `Next hand in ${seconds}s` : "Hand complete"}</span>
          <button
            type="button"
            onClick={onDeal}
            className="btn-gold rounded-xl px-4 py-2 text-sm font-bold"
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
