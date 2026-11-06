import { useEffect, useMemo, useState } from "react";
import { clamp, formatChips, potSizedRaiseTo } from "../lib/betting";
import type { ActionType, LegalView } from "../types";

interface Props {
  legal: LegalView;
  pot: number;
  currentBet: number;
  bigBlind: number;
  disabled: boolean;
  onAction: (action: ActionType, amount?: number) => void;
}

const PRESETS: { label: string; fraction: number }[] = [
  { label: "½ Pot", fraction: 0.5 },
  { label: "¾ Pot", fraction: 0.75 },
  { label: "Pot", fraction: 1 },
];

export function ActionBar({ legal, pot, currentBet, bigBlind, disabled, onAction }: Props) {
  const { min_raise_to: min, max_raise_to: max } = legal;
  const [amount, setAmount] = useState(min);
  const [draft, setDraft] = useState(String(min));

  useEffect(() => {
    setAmount(min);
    setDraft(String(min));
  }, [min, max]);

  const setBoth = (v: number) => {
    const c = clamp(Math.round(v), min, max);
    setAmount(c);
    setDraft(String(c));
  };

  const raiseWord = legal.raise_type === "bet" ? "Bet" : "Raise to";
  const isAllIn = legal.raise_is_all_in && amount === max;
  const fill = max > min ? ((amount - min) / (max - min)) * 100 : 100;
  const presets = useMemo(
    () =>
      PRESETS.map((p) => ({ ...p, value: potSizedRaiseTo(legal, pot, currentBet, p.fraction) })),
    [legal, pot, currentBet],
  );

  const doRaise = () => {
    if (isAllIn) onAction("all_in");
    else onAction(legal.raise_type, amount);
  };
  const doPassive = () => (legal.can_check ? onAction("check") : onAction("call"));

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (disabled || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.target instanceof HTMLInputElement && e.target.type === "number") return;
      const k = e.key.toLowerCase();
      if (k === "f" && legal.can_fold) onAction("fold");
      else if (k === "c") doPassive();
      else if (k === "r" && legal.can_raise) doRaise();
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const btn =
    "relative flex flex-1 items-center justify-center rounded-xl px-3 py-3 text-[15px] font-bold tracking-wide transition active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-35 disabled:saturate-50 sm:text-base";
  const kbd = "ml-2 hidden rounded-[5px] bg-black/20 px-1.5 py-0.5 text-[10px] font-semibold leading-none opacity-70 ring-1 ring-black/10 sm:inline";

  return (
    <div className="surface flex w-full flex-col gap-2.5 rounded-2xl p-2.5 sm:p-3" aria-label="Betting controls">
      {legal.can_raise && (
        <div className="flex flex-col gap-2.5 md:flex-row md:items-center md:gap-3">
          <div className="flex flex-wrap gap-1.5">
            <Preset label="Min" onClick={() => setBoth(min)} disabled={disabled} active={amount === min} />
            {presets.map((p) => (
              <Preset
                key={p.label}
                label={p.label}
                onClick={() => setBoth(p.value)}
                disabled={disabled || p.value === min || p.value === max}
                active={amount === p.value && p.value !== min && p.value !== max}
              />
            ))}
            <Preset
              label={legal.raise_is_all_in ? "All-in" : "Max"}
              onClick={() => setBoth(max)}
              disabled={disabled}
              active={amount === max}
            />
          </div>
          <div className="flex flex-1 items-center gap-2">
            <Nudge label="−" title={`-${bigBlind}`} onClick={() => setBoth(amount - bigBlind)} disabled={disabled || amount <= min} />
            <input
              type="range"
              className="bet-slider flex-1"
              min={min}
              max={max}
              step={1}
              value={amount}
              onChange={(e) => setBoth(Number(e.target.value))}
              disabled={disabled}
              aria-label="Raise amount"
              style={{ ["--fill" as string]: `${fill}%` }}
            />
            <input
              type="number"
              inputMode="numeric"
              className="w-[5.5rem] rounded-lg bg-black/40 px-2.5 py-1.5 text-right text-[15px] font-bold tabular-nums text-amber-100 ring-1 ring-white/10 focus:outline-none focus:ring-2 focus:ring-amber-300/60"
              min={min}
              max={max}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={() => setBoth(Number(draft) || min)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  setBoth(Number(draft) || min);
                  (e.target as HTMLInputElement).blur();
                }
              }}
              disabled={disabled}
              aria-label="Raise amount in chips"
            />
            <Nudge label="+" title={`+${bigBlind}`} onClick={() => setBoth(amount + bigBlind)} disabled={disabled || amount >= max} />
          </div>
        </div>
      )}

      <div className="flex gap-2 sm:gap-2.5">
        <button
          type="button"
          className={`${btn} btn-dark`}
          onClick={() => onAction("fold")}
          disabled={disabled || !legal.can_fold}
        >
          Fold<span className={kbd}>F</span>
        </button>
        <button
          type="button"
          className={`${btn} btn-green`}
          onClick={doPassive}
          disabled={disabled}
        >
          {legal.can_check ? "Check" : `Call ${formatChips(legal.call_amount)}`}
          <span className={kbd}>C</span>
        </button>
        {legal.can_raise && (
          <button
            type="button"
            className={`${btn} btn-gold`}
            onClick={doRaise}
            disabled={disabled}
          >
            {isAllIn ? `All-in ${formatChips(amount)}` : `${raiseWord} ${formatChips(amount)}`}
            <span className={kbd}>R</span>
          </button>
        )}
      </div>
    </div>
  );
}

function Preset({
  label,
  onClick,
  disabled,
  active,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  active?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition disabled:opacity-30 ${
        active
          ? "bg-amber-300 text-slate-950 shadow-[inset_0_1px_0_rgba(255,255,255,.5)]"
          : "bg-white/[0.06] text-slate-200 ring-1 ring-white/[0.06] hover:bg-white/10"
      }`}
    >
      {label}
    </button>
  );
}

function Nudge({
  label,
  title,
  onClick,
  disabled,
}: {
  label: string;
  title: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      title={title}
      aria-label={`Adjust raise ${title}`}
      onClick={onClick}
      disabled={disabled}
      className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-white/[0.06] text-lg font-bold text-slate-200 ring-1 ring-white/[0.06] transition hover:bg-white/10 disabled:opacity-30"
    >
      {label}
    </button>
  );
}
