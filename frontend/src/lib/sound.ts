/**
 * Table sound effects, synthesised with the Web Audio API.
 *
 * Every sound is generated in code (filtered noise and short inharmonic partials), so
 * there are no audio files to license or download. Sounds are deliberately short and
 * quiet, vary slightly in pitch and level on each play so repeats don't sound
 * mechanical, and are rate-limited so a burst of events never turns into a clatter.
 *
 * Browsers only allow audio after a user gesture, so nothing is created until
 * ``unlock()`` runs from a click, tap or key press. Until then ``play()`` is a no-op.
 */

export type SoundKind =
  | "deal" // a card sliding off the deck onto the felt
  | "flip" // a card turned over
  | "chip" // a few chips set down (blinds, calls, returned bets)
  | "bet" // a stack pushed out (bet / raise)
  | "allin" // the whole stack pushed in
  | "check" // two knuckle taps on the felt
  | "fold" // cards slid away
  | "collect" // bets gathered into the pot
  | "win"; // the pot pushed to a player

/** Shortest gap (ms) between two plays of the same sound. */
const MIN_GAP: Record<SoundKind, number> = {
  deal: 40,
  flip: 60,
  chip: 70,
  bet: 120,
  allin: 250,
  check: 150,
  fold: 200,
  collect: 200,
  win: 400,
};
/** Upper bound on sounds started within one ``WINDOW_MS``, whatever their kind. */
const MAX_PER_WINDOW = 6;
const WINDOW_MS = 250;
/** Master level at volume 1; individual sounds are mixed below this. */
const HEADROOM = 0.55;

/** Perceptual volume curve: the slider feels even rather than all at the top end. */
export function gainForVolume(volume: number): number {
  const v = Math.min(1, Math.max(0, Number.isFinite(volume) ? volume : 0));
  return v * v * HEADROOM;
}

type Ctx = AudioContext;
type CtxFactory = () => Ctx | null;

const defaultFactory: CtxFactory = () => {
  const C =
    typeof window === "undefined"
      ? undefined
      : (window.AudioContext ??
        (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext);
  return C ? new C({ latencyHint: "interactive" }) : null;
};

export class SoundEngine {
  private ctx: Ctx | null = null;
  private master: GainNode | null = null;
  private noise: AudioBuffer | null = null;
  private lastPlayed = new Map<SoundKind, number>();
  private recent: number[] = [];
  private volume = 0.6;
  private enabled = true;
  private readonly factory: CtxFactory;
  private readonly now: () => number;
  private readonly random: () => number;

  constructor(opts: { factory?: CtxFactory; now?: () => number; random?: () => number } = {}) {
    this.factory = opts.factory ?? defaultFactory;
    this.now = opts.now ?? (() => performance.now());
    this.random = opts.random ?? Math.random;
  }

  /** True once audio is allowed and running. */
  get ready(): boolean {
    return this.ctx !== null && this.ctx.state === "running";
  }

  /** Create or resume the audio context. Call from a user gesture. */
  unlock(): void {
    try {
      if (!this.ctx) {
        this.ctx = this.factory();
        if (!this.ctx) return;
        this.master = this.ctx.createGain();
        this.master.gain.value = gainForVolume(this.volume);
        this.master.connect(this.ctx.destination);
      }
      if (this.ctx.state === "suspended") void this.ctx.resume().catch(() => {});
    } catch {
      this.ctx = null; // audio unavailable: stay silent
    }
  }

  configure({ enabled, volume }: { enabled: boolean; volume: number }): void {
    this.enabled = enabled;
    this.volume = volume;
    if (this.master && this.ctx) {
      this.master.gain.setTargetAtTime(gainForVolume(volume), this.ctx.currentTime, 0.02);
    }
  }

  /**
   * Play ``kind`` after ``delay`` seconds. Returns whether it was scheduled (it is not
   * when sound is off, audio isn't unlocked yet, the tab is hidden, or it was throttled).
   */
  play(kind: SoundKind, delay = 0): boolean {
    const ctx = this.ctx;
    if (!ctx || !this.master || !this.enabled || this.volume <= 0) return false;
    if (ctx.state !== "running") return false;
    if (typeof document !== "undefined" && document.hidden) return false;
    const at = this.now() + delay * 1000;
    if (at - (this.lastPlayed.get(kind) ?? -Infinity) < MIN_GAP[kind]) return false;
    this.recent = this.recent.filter((t) => Math.abs(at - t) < WINDOW_MS);
    if (this.recent.length >= MAX_PER_WINDOW) return false;
    this.lastPlayed.set(kind, at);
    this.recent.push(at);
    try {
      VOICES[kind](this, ctx.currentTime + Math.max(0, delay));
    } catch {
      return false;
    }
    return true;
  }

  // ---- building blocks used by the voices below -------------------------------

  /** A small random factor around 1 (±``spread``) so repeats never sound identical. */
  vary(spread: number): number {
    return 1 + (this.random() * 2 - 1) * spread;
  }

  private noiseBuffer(ctx: Ctx): AudioBuffer {
    if (!this.noise) {
      const len = Math.floor(ctx.sampleRate * 0.5);
      this.noise = ctx.createBuffer(1, len, ctx.sampleRate);
      const data = this.noise.getChannelData(0);
      for (let i = 0; i < len; i++) data[i] = Math.random() * 2 - 1;
    }
    return this.noise;
  }

  /** Filtered noise with a fast attack and an exponential tail. */
  noiseBurst(
    t: number,
    o: {
      gain: number;
      attack: number;
      decay: number;
      type: BiquadFilterType;
      freq: number;
      freqEnd?: number;
      q?: number;
    },
  ): void {
    const ctx = this.ctx!;
    const src = ctx.createBufferSource();
    src.buffer = this.noiseBuffer(ctx);
    src.playbackRate.value = this.vary(0.08);
    const filter = ctx.createBiquadFilter();
    filter.type = o.type;
    filter.Q.value = o.q ?? 1;
    filter.frequency.setValueAtTime(o.freq, t);
    if (o.freqEnd) filter.frequency.exponentialRampToValueAtTime(o.freqEnd, t + o.attack + o.decay);
    const env = ctx.createGain();
    env.gain.setValueAtTime(0.0001, t);
    env.gain.linearRampToValueAtTime(o.gain, t + o.attack);
    env.gain.exponentialRampToValueAtTime(0.0001, t + o.attack + o.decay);
    src.connect(filter).connect(env).connect(this.master!);
    const offset = this.random() * 0.3;
    src.start(t, offset, o.attack + o.decay + 0.02);
  }

  /** A decaying sine partial. */
  tone(t: number, freq: number, gain: number, decay: number, type: OscillatorType = "sine"): void {
    const ctx = this.ctx!;
    const osc = ctx.createOscillator();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, t);
    const env = ctx.createGain();
    env.gain.setValueAtTime(0.0001, t);
    env.gain.linearRampToValueAtTime(gain, t + 0.002);
    env.gain.exponentialRampToValueAtTime(0.0001, t + decay);
    osc.connect(env).connect(this.master!);
    osc.start(t);
    osc.stop(t + decay + 0.02);
  }

  /** One clay chip landing on another: a click plus a couple of short inharmonic rings. */
  clack(t: number, level = 1): void {
    const p = this.vary(0.06);
    const g = 0.22 * level * this.vary(0.15);
    this.noiseBurst(t, { gain: g, attack: 0.001, decay: 0.018, type: "highpass", freq: 2600 });
    this.tone(t, 3150 * p, g * 0.55, 0.05);
    this.tone(t, 4720 * p, g * 0.3, 0.035);
    this.tone(t, 6380 * p, g * 0.16, 0.025);
  }

  /** ``count`` chips settling one after another over roughly ``span`` seconds. */
  chips(t: number, count: number, span: number, level = 1): void {
    let at = t;
    for (let i = 0; i < count; i++) {
      this.clack(at, level * (1 - (i / count) * 0.45));
      at += (span / Math.max(1, count - 1)) * this.vary(0.35);
    }
  }
}

/** How each sound is put together. ``t`` is the AudioContext start time. */
const VOICES: Record<SoundKind, (e: SoundEngine, t: number) => void> = {
  deal(e, t) {
    // Short papery slide, then a soft snap as the card lands.
    e.noiseBurst(t, { gain: 0.1, attack: 0.012, decay: 0.07, type: "bandpass", freq: 5200, freqEnd: 2400, q: 0.9 });
    e.noiseBurst(t + 0.07, { gain: 0.14, attack: 0.001, decay: 0.022, type: "bandpass", freq: 1900, q: 1.2 });
  },
  flip(e, t) {
    e.noiseBurst(t, { gain: 0.08, attack: 0.008, decay: 0.05, type: "bandpass", freq: 3400, freqEnd: 2000, q: 1 });
    e.noiseBurst(t + 0.05, { gain: 0.1, attack: 0.001, decay: 0.018, type: "bandpass", freq: 2200, q: 1.4 });
  },
  chip(e, t) {
    e.chips(t, 2 + Math.round(e.vary(0.5)), 0.07, 0.85);
  },
  bet(e, t) {
    // A short push across the felt, then the stack settles.
    e.noiseBurst(t, { gain: 0.05, attack: 0.03, decay: 0.08, type: "lowpass", freq: 900 });
    e.chips(t + 0.05, 4, 0.12);
  },
  allin(e, t) {
    e.noiseBurst(t, { gain: 0.08, attack: 0.06, decay: 0.16, type: "lowpass", freq: 700 });
    e.chips(t + 0.1, 7, 0.24, 1.1);
  },
  check(e, t) {
    // Two muffled knuckle taps.
    for (const dt of [0, 0.11]) {
      const p = e.vary(0.05);
      e.tone(t + dt, 150 * p, 0.32, 0.09);
      e.noiseBurst(t + dt, { gain: 0.12, attack: 0.001, decay: 0.03, type: "lowpass", freq: 700 });
    }
  },
  fold(e, t) {
    e.noiseBurst(t, { gain: 0.07, attack: 0.05, decay: 0.2, type: "bandpass", freq: 2400, freqEnd: 900, q: 0.7 });
  },
  collect(e, t) {
    e.noiseBurst(t, { gain: 0.05, attack: 0.04, decay: 0.14, type: "lowpass", freq: 1100 });
    e.chips(t + 0.12, 5, 0.18, 0.8);
  },
  win(e, t) {
    e.noiseBurst(t, { gain: 0.07, attack: 0.08, decay: 0.22, type: "lowpass", freq: 900 });
    e.chips(t + 0.2, 9, 0.42, 0.95);
  },
};

/** The app-wide engine. */
export const sound = new SoundEngine();

/**
 * Unlock audio on user gestures (browsers block it before the first one). Listeners
 * stay attached because mobile browsers can suspend audio again, for example after the
 * tab has been in the background; ``unlock()`` is cheap once audio is running.
 * Returns a cleanup function.
 */
export function unlockOnGesture(engine: SoundEngine = sound): () => void {
  if (typeof window === "undefined") return () => {};
  const events = ["pointerdown", "keydown", "touchend"] as const;
  const handler = () => engine.unlock();
  events.forEach((e) => window.addEventListener(e, handler, true));
  return () => events.forEach((e) => window.removeEventListener(e, handler, true));
}
