import { afterEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_SETTINGS, sanitizeSettings } from "../hooks/useSettings";
import { boardTiming, TIMING } from "./motion";
import { gainForVolume, SoundEngine, unlockOnGesture } from "./sound";
import { soundCues } from "./soundCues";

/** Minimal stand-in for AudioContext that counts the sources it is asked to start. */
function fakeContext(initial: AudioContextState = "suspended") {
  const param = () => ({
    value: 0,
    setValueAtTime: vi.fn(),
    linearRampToValueAtTime: vi.fn(),
    exponentialRampToValueAtTime: vi.fn(),
    setTargetAtTime: vi.fn(),
  });
  const node = () => {
    const n: Record<string, unknown> = {};
    n.connect = vi.fn(() => n);
    return n;
  };
  const ctx = {
    state: initial,
    currentTime: 0,
    sampleRate: 8000,
    destination: {},
    started: 0,
    resume: vi.fn(async () => {
      ctx.state = "running";
    }),
    createGain: () => ({ ...node(), gain: param() }),
    createBiquadFilter: () => ({ ...node(), frequency: param(), Q: param(), type: "lowpass" }),
    createBuffer: (_c: number, len: number) => ({ getChannelData: () => new Float32Array(len) }),
    createBufferSource: () => ({
      ...node(),
      playbackRate: param(),
      buffer: null,
      start: () => ctx.started++,
    }),
    createOscillator: () => ({
      ...node(),
      frequency: param(),
      type: "sine",
      start: () => ctx.started++,
      stop: vi.fn(),
    }),
  };
  return ctx;
}

function engineWithClock() {
  const ctx = fakeContext();
  let t = 0;
  const engine = new SoundEngine({
    factory: () => ctx as unknown as AudioContext,
    now: () => t,
    random: () => 0.5,
  });
  return { engine, ctx, advance: (ms: number) => (t += ms) };
}

describe("SoundEngine", () => {
  it("stays silent until a user gesture unlocks audio", async () => {
    const { engine, ctx } = engineWithClock();
    expect(engine.play("chip")).toBe(false);
    expect(ctx.started).toBe(0);
    engine.unlock();
    await Promise.resolve();
    expect(ctx.resume).toHaveBeenCalled();
    expect(engine.ready).toBe(true);
    expect(engine.play("chip")).toBe(true);
    expect(ctx.started).toBeGreaterThan(0);
  });

  it("plays nothing when muted or at zero volume", async () => {
    const { engine, ctx, advance } = engineWithClock();
    engine.unlock();
    await Promise.resolve();
    engine.configure({ enabled: false, volume: 0.6 });
    expect(engine.play("deal")).toBe(false);
    engine.configure({ enabled: true, volume: 0 });
    advance(1000);
    expect(engine.play("deal")).toBe(false);
    expect(ctx.started).toBe(0);
    engine.configure({ enabled: true, volume: 0.3 });
    expect(engine.play("deal")).toBe(true);
  });

  it("rate-limits repeats and bursts so audio never piles up", async () => {
    const { engine, advance } = engineWithClock();
    engine.unlock();
    await Promise.resolve();
    expect(engine.play("win")).toBe(true);
    expect(engine.play("win")).toBe(false); // same sound straight away
    advance(500);
    expect(engine.play("win")).toBe(true);
    // A burst of different sounds is capped too.
    advance(1000);
    const kinds = ["deal", "flip", "chip", "bet", "check", "fold", "collect", "allin"] as const;
    const played = kinds.map((k) => engine.play(k)).filter(Boolean).length;
    expect(played).toBe(6);
  });

  it("counts delayed sounds at the time they will actually play", async () => {
    const { engine } = engineWithClock();
    engine.unlock();
    await Promise.resolve();
    // Four dealt cards, staggered like the deal animation: none are dropped.
    const delays = [0.3, 0.43, 0.56, 0.69];
    expect(delays.map((d) => engine.play("deal", d))).toEqual([true, true, true, true]);
  });

  it("doesn't play while the tab is hidden", async () => {
    const { engine } = engineWithClock();
    engine.unlock();
    await Promise.resolve();
    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    expect(engine.play("chip")).toBe(false);
    hidden.mockRestore();
    expect(engine.play("chip")).toBe(true);
  });

  it("does nothing at all where Web Audio is unavailable", () => {
    const engine = new SoundEngine({ factory: () => null });
    engine.unlock();
    expect(engine.ready).toBe(false);
    expect(engine.play("chip")).toBe(false);
  });

  it("unlocks from pointer and key events", () => {
    const engine = new SoundEngine({ factory: () => null });
    const spy = vi.spyOn(engine, "unlock");
    const stop = unlockOnGesture(engine);
    window.dispatchEvent(new Event("pointerdown"));
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "a" }));
    expect(spy).toHaveBeenCalledTimes(2);
    stop();
    window.dispatchEvent(new Event("pointerdown"));
    expect(spy).toHaveBeenCalledTimes(2);
  });
});

describe("gainForVolume", () => {
  it("follows a perceptual curve and clamps bad input", () => {
    expect(gainForVolume(0)).toBe(0);
    expect(gainForVolume(0.5)).toBeCloseTo(gainForVolume(1) / 4);
    expect(gainForVolume(2)).toBe(gainForVolume(1));
    expect(gainForVolume(Number.NaN)).toBe(0);
  });
});

describe("soundCues", () => {
  const moving = { travel: true, ds: 1 };
  const still = { travel: false, ds: 0.5 };

  it("snaps each hole card as it lands, in deal order", () => {
    const cues = soundCues({ type: "deal_hole", seats: [1, 0] }, moving);
    expect(cues.map((c) => c.kind)).toEqual(["deal", "deal", "deal", "deal"]);
    cues.forEach((c, k) => {
      // The snap comes 0.07s into the sound and should coincide with the card landing.
      expect(c.delay + 0.07).toBeCloseTo(k * TIMING.dealGap + TIMING.dealTravel);
    });
  });

  it("deals then turns over each flop card", () => {
    const cues = soundCues({ type: "street", street: "flop", cards: ["2c", "7h", "9d"] }, moving);
    expect(cues.filter((c) => c.kind === "deal")).toHaveLength(3);
    const flips = cues.filter((c) => c.kind === "flip").map((c) => c.delay);
    expect(flips).toEqual([0, 1, 2].map((i) => boardTiming(i).delay + boardTiming(i).revealAfter));
    expect(cues.map((c) => c.delay)).toEqual([...cues.map((c) => c.delay)].sort((a, b) => a - b));
  });

  it("scales with animation speed", () => {
    const normal = soundCues({ type: "street", street: "river", cards: ["Qh"] }, moving);
    const slow = soundCues({ type: "street", street: "river", cards: ["Qh"] }, { travel: true, ds: 1.3 });
    expect(slow[1].delay).toBeCloseTo(normal[1].delay * 1.3);
  });

  it("plays one immediate sound per event when nothing travels", () => {
    expect(soundCues({ type: "deal_hole", seats: [1, 0] }, still)).toEqual([{ kind: "deal", delay: 0 }]);
    expect(soundCues({ type: "street", street: "flop", cards: ["2c", "7h", "9d"] }, still)).toEqual([
      { kind: "deal", delay: 0 },
    ]);
    expect(soundCues({ type: "action", seat: 0, action: "raise", label: "raise" }, still)).toEqual([
      { kind: "bet", delay: 0 },
    ]);
  });

  it("gives each action its own sound", () => {
    const kind = (label: string, action = label) =>
      soundCues({ type: "action", seat: 1, action, label }, moving)[0].kind;
    expect(kind("fold")).toBe("fold");
    expect(kind("check")).toBe("check");
    expect(kind("call")).toBe("chip");
    expect(kind("bet")).toBe("bet");
    expect(kind("raise")).toBe("bet");
    expect(kind("all-in", "raise")).toBe("allin");
    expect(kind("all-in", "call")).toBe("allin");
  });

  it("covers blinds, returned bets, the sweep, the showdown and the award", () => {
    const k = (type: string) => soundCues({ type }, moving).map((c) => c.kind);
    expect(k("post_blind")).toEqual(["chip"]);
    expect(k("uncalled_returned")).toEqual(["chip"]);
    expect(k("bets_collected")).toEqual(["collect"]);
    expect(k("showdown")).toEqual(["flip"]);
    expect(k("pot_awarded")).toEqual(["win"]);
    expect(k("hand_start")).toEqual([]);
    expect(k("hand_complete")).toEqual([]);
  });
});

describe("sanitizeSettings", () => {
  afterEach(() => localStorage.clear());

  it("fills in defaults for settings saved by older versions", () => {
    expect(sanitizeSettings({ speed: "fast", autoDeal: false })).toEqual({
      ...DEFAULT_SETTINGS,
      speed: "fast",
      autoDeal: false,
    });
  });

  it("rejects invalid values instead of breaking the game", () => {
    expect(
      sanitizeSettings({ speed: "warp", volume: 7, theme: "neon", sound: "yes", autoDeal: 1 }),
    ).toEqual({ ...DEFAULT_SETTINGS, volume: 1 });
    expect(sanitizeSettings({ volume: -1 }).volume).toBe(0);
    expect(sanitizeSettings(null)).toEqual(DEFAULT_SETTINGS);
    expect(sanitizeSettings("garbage")).toEqual(DEFAULT_SETTINGS);
  });
});
