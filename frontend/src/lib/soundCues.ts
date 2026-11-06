import type { GameEvent } from "../types";
import { boardTiming, TIMING } from "./motion";
import type { SoundKind } from "./sound";

export interface SoundCue {
  kind: SoundKind;
  /** Seconds after the event is shown. */
  delay: number;
}

/** The deal sound's slide lasts this long before the card's snap, which should match the landing. */
const DEAL_LEAD = 0.07;

/**
 * Sounds for one game event, timed to the animation it triggers.
 *
 * ``travel`` says whether cards and chips visibly move (see ``canTravel``) and ``ds``
 * is the animation duration multiplier. Without travel everything appears at once, so
 * each event gets a single sound with no delay instead of one per card.
 */
export function soundCues(event: GameEvent, opts: { travel: boolean; ds: number }): SoundCue[] {
  const { travel, ds } = opts;
  const at = (seconds: number) => (travel ? Math.max(0, seconds * ds) : 0);
  const land = (startsAt: number) => at(startsAt + TIMING.dealTravel - DEAL_LEAD / ds);
  const chipLanding = at(TIMING.chipFlight * 0.75);

  switch (event.type) {
    case "deal_hole": {
      const cards = 2 * ((event.seats as number[] | undefined)?.length ?? 2);
      if (!travel) return [{ kind: "deal", delay: 0 }];
      return Array.from({ length: cards }, (_, k) => ({ kind: "deal", delay: land(k * TIMING.dealGap) }));
    }
    case "street": {
      const dealt = (event.cards as string[] | undefined)?.length ?? 1;
      const first = event.street === "flop" ? 0 : 3;
      if (!travel) return [{ kind: "deal", delay: 0 }];
      const cues: SoundCue[] = [];
      for (let i = first; i < first + dealt; i++) {
        const t = boardTiming(i);
        cues.push({ kind: "deal", delay: land(t.delay) });
        cues.push({ kind: "flip", delay: at(t.delay + t.revealAfter) });
      }
      return cues.sort((a, b) => a.delay - b.delay);
    }
    case "reveal":
    case "showdown":
      // The opponent's cards turn over (the flip itself is shown immediately).
      return [{ kind: "flip", delay: 0 }];
    case "post_blind":
    case "uncalled_returned":
      return [{ kind: "chip", delay: chipLanding }];
    case "action": {
      const label = String(event.label ?? event.action);
      if (label === "fold") return [{ kind: "fold", delay: 0 }];
      if (label === "check") return [{ kind: "check", delay: 0 }];
      if (label === "all-in") return [{ kind: "allin", delay: at(TIMING.chipFlight * 0.4) }];
      if (label === "bet" || label === "raise") return [{ kind: "bet", delay: at(TIMING.chipFlight * 0.5) }];
      return [{ kind: "chip", delay: chipLanding }];
    }
    case "bets_collected":
      return [{ kind: "collect", delay: at(TIMING.chipFlight * 0.35) }];
    case "pot_awarded":
      return [{ kind: "win", delay: at(TIMING.chipFlight * 0.3) }];
    default:
      return [];
  }
}
