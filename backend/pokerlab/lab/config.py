"""What a Simulation Lab experiment is, and how it is split into reproducible blocks.

An experiment plays every pair of the chosen strategies (one *matchup* for two
strategies, a round robin for more). Each matchup is a fixed sequence of *sessions*:
``session_hands`` deals at each table with freshly created agents, so an adaptive
strategy learns within a session and starts again in the next. A session is also the
unit of work sent to a worker process.

**Reproducibility.** Everything random in a session (the agents' seeds and every deck)
comes from ``random.Random("<seed>:<A>:<B>:<session>")``. String seeds are hashed
with SHA-512 by Python, so they do not depend on ``PYTHONHASHSEED``, the platform, the
number of worker processes or the order sessions finish in. A matchup therefore gives
the same result on its own or inside a round robin. Results are reproducible for a given
PokerLab version; a change to an agent changes them (the version is part of every
result and of the cache key).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .. import __version__
from .strategies import STRATEGIES, hand_cost_ms

MODES = ("duplicate", "standard")
MIN_HANDS = 200
MAX_HANDS = 100_000  # per matchup
MAX_TOTAL_HANDS = 600_000  # per experiment, over all matchups
MIN_STRATEGIES, MAX_STRATEGIES = 2, 6
STACKS_BB = (20, 50, 100, 200)
SESSION_RANGE = (50, 2000)
SMALL_BLIND, BIG_BLIND = 5, 10
MAX_SEED = 2**31 - 1

# Bump when the simulation protocol changes (part of the cache key and of every result).
PROTOCOL = 1


@dataclass(frozen=True, slots=True)
class BlockTask:
    """One session of one matchup: everything a worker needs to play it."""

    a: str
    b: str
    mode: str
    session: int
    deals: int
    seed_material: str
    stack: int
    small_blind: int = SMALL_BLIND
    big_blind: int = BIG_BLIND


@dataclass(frozen=True, slots=True)
class LabConfig:
    strategies: tuple[str, ...]
    hands: int = 10_000  # per matchup
    mode: str = "duplicate"
    seed: int = 1
    stack_bb: int = 100
    session_hands: int = 200  # deals at each table before the agents are recreated

    @classmethod
    def build(cls, **kw) -> LabConfig:
        """Validated and normalised (hands rounded down to a multiple of four)."""
        kw["strategies"] = tuple(kw.get("strategies") or ())
        cfg = cls(**kw)
        cfg = cls(**{**asdict(cfg), "hands": cfg.hands - cfg.hands % 4})
        cfg.validate()
        return cfg

    def validate(self) -> None:
        s = self.strategies
        if not MIN_STRATEGIES <= len(s) <= MAX_STRATEGIES:
            raise ValueError(f"Choose between {MIN_STRATEGIES} and {MAX_STRATEGIES} strategies")
        # A strategy against itself (a "mirror match", two strategies only) is allowed:
        # its true result is zero, which makes it a check on the method's noise.
        if len(set(s)) != len(s) and len(s) > 2:
            raise ValueError("In a round robin each strategy can be chosen only once")
        unknown = [x for x in s if x not in STRATEGIES]
        if unknown:
            raise ValueError(f"Unknown strategy: {unknown[0]!r}")
        if self.mode not in MODES:
            raise ValueError(f"Mode must be one of {', '.join(MODES)}")
        if not MIN_HANDS <= self.hands <= MAX_HANDS:
            raise ValueError(f"Hands per matchup must be between {MIN_HANDS} and {MAX_HANDS}")
        if self.hands % 4:
            raise ValueError("Hands per matchup must be a multiple of 4")
        if self.total_hands > MAX_TOTAL_HANDS:
            raise ValueError(
                f"{self.total_hands:,} hands in total is too many (limit {MAX_TOTAL_HANDS:,}); "
                "choose fewer hands or fewer strategies"
            )
        if self.stack_bb not in STACKS_BB:
            raise ValueError(f"Stack must be one of {', '.join(map(str, STACKS_BB))} bb")
        lo, hi = SESSION_RANGE
        if not lo <= self.session_hands <= hi or self.session_hands % 2:
            raise ValueError(f"Session length must be an even number from {lo} to {hi}")
        if not 0 <= self.seed <= MAX_SEED:
            raise ValueError(f"Seed must be between 0 and {MAX_SEED}")

    # ------------------------------------------------------------------ plan

    @property
    def matchups(self) -> list[tuple[str, str]]:
        s = self.strategies
        return [(s[i], s[j]) for i in range(len(s)) for j in range(i + 1, len(s))]

    @property
    def deals(self) -> int:
        """Deals per matchup: each duplicate deal is played twice (two hands)."""
        return self.hands // 2 if self.mode == "duplicate" else self.hands

    @property
    def total_hands(self) -> int:
        return self.hands * len(self.matchups)

    def seed_material(self, a: str, b: str) -> str:
        # Not the mode: a standard run deals exactly the cards of a duplicate run's table 1.
        return f"{self.seed}:{a}:{b}"

    def blocks(self, a: str, b: str) -> list[BlockTask]:
        """The sessions of one matchup. The last may be shorter; every block has an even
        number of deals so each unit is balanced across the button."""
        out, left, k = [], self.deals, 0
        while left > 0:
            n = min(self.session_hands, left)
            out.append(
                BlockTask(
                    a=a,
                    b=b,
                    mode=self.mode,
                    session=k,
                    deals=n,
                    seed_material=self.seed_material(a, b),
                    stack=self.stack_bb * BIG_BLIND,
                )
            )
            left -= n
            k += 1
        return out

    def matchup_key(self, a: str, b: str) -> tuple:
        """Everything that determines a matchup's result (the cache key)."""
        return (__version__, PROTOCOL, a, b, self.mode, self.hands, self.seed, self.stack_bb,
                self.session_hands)  # fmt: skip

    def estimate_seconds(self, workers: int) -> float:
        """Rough wall-clock time. Workers share the sessions, with diminishing returns:
        on the 4-core laptop it was measured on, 2, 4 and 6 workers ran 1.9, 2.3 and 2.6
        times as fast as one (hyperthreads, shared caches, thermal limits), close to
        workers^0.6. The job manager scales this by what it observes on the machine."""
        cpu = sum(hand_cost_ms(a, b) * self.hands for a, b in self.matchups) / 1000
        sessions = sum(len(self.blocks(a, b)) for a, b in self.matchups)
        parallel = max(1, min(workers, sessions)) ** 0.6
        return cpu / parallel

    def view(self) -> dict:
        return {**asdict(self), "strategies": list(self.strategies)}
