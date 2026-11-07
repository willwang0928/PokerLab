"""All-in equity for Texas Hold'em: exact enumeration or seeded Monte Carlo.

Each player is a :class:`~pokerlab.ranges.HandRange`. A known hand is a range with one
combo. Board cards and dead cards are fixed. The quantity estimated is each player's
share of the pot if all remaining board cards are dealt with no further betting. A k-way
tie for the best hand gives each tied player 1/k of the pot.

**The probability model.** Every complete deal that is consistent with the inputs is
equally likely: each player holds a combo from their range, all cards are distinct, and
the board extends the given board. That is what a uniformly shuffled deck produces once
we condition on the ranges. Two consequences:

* *Card removal is joint.* Combos that clash with the board, the dead cards, or another
  player's only possible hand are removed up front. Clashes *between ranges* are handled
  by rejecting the whole deal and redrawing every player. Drawing players one after
  another, each from what is left, would over-weight the first player's range and bias
  the result.
* Every valid assignment of hands leaves the same number of unseen cards, so exact
  enumeration weights each (assignment, runout) pair equally.

``method="auto"`` enumerates exactly when the work is small (``exact_limit`` hand
evaluations), otherwise it runs Monte Carlo. Monte Carlo results carry a standard error
and the seed that reproduces them.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import Literal

from . import fasteval
from .cards import Card, parse_cards
from .ranges import HandRange, RangeError

Method = Literal["auto", "exact", "monte_carlo"]
IndexCombo = tuple[int, int]  # a hole-card combo as card indices

DEFAULT_ITERATIONS = 20_000
DEFAULT_EXACT_LIMIT = 600_000
# Rejection budget: a deal may be redrawn up to MAX_REJECTIONS_PER_TRIAL times, and a
# whole run may redraw at most this many times per requested trial on average. Both
# bound the work for ranges that almost never fit together.
MAX_REJECTIONS_PER_TRIAL = 10_000
MAX_REJECTIONS_PER_ITERATION = 100


@dataclass(frozen=True, slots=True)
class PlayerEquity:
    equity: float  # expected share of the pot, 0..1
    win: float  # fraction of deals won outright
    tie: float  # fraction of deals tied for best
    std_error: float  # standard error of ``equity`` (0 for exact results)

    def interval(self, z: float = 1.96) -> tuple[float, float]:
        """Normal-approximation confidence interval for ``equity`` (95% by default)."""
        return max(0.0, self.equity - z * self.std_error), min(
            1.0, self.equity + z * self.std_error
        )


@dataclass(frozen=True, slots=True)
class EquityResult:
    players: tuple[PlayerEquity, ...]
    method: Literal["exact", "monte_carlo"]
    trials: int  # deals evaluated (exact: every deal; Monte Carlo: samples)
    seed: int | None  # Monte Carlo seed that reproduces this result

    @property
    def equities(self) -> list[float]:
        return [p.equity for p in self.players]


# ---------------------------------------------------------------------- inputs


def _as_cards(cards: str | Sequence[Card] | None) -> list[Card]:
    if cards is None:
        return []
    if isinstance(cards, str):
        return parse_cards(cards)
    return list(cards)


def _as_range(spec: str | HandRange | Sequence[Card]) -> HandRange:
    if isinstance(spec, HandRange):
        return spec
    if isinstance(spec, str):
        return HandRange.parse(spec)
    cards = list(spec)
    if len(cards) != 2 or cards[0] == cards[1]:
        raise RangeError("A known hand needs exactly two different cards")
    return HandRange.of([(cards[0], cards[1])])


@dataclass(slots=True)
class _Problem:
    ranges: list[list[IndexCombo]]  # per player: combos as card-index pairs
    board: list[int]
    unseen: list[int]  # every card not on the board and not dead (hole cards included)
    missing: int  # board cards still to come


def _prepare(
    players: Sequence[str | HandRange | Sequence[Card]],
    board: str | Sequence[Card] | None,
    dead: str | Sequence[Card] | None,
) -> _Problem:
    if len(players) < 2:
        raise ValueError("Equity needs at least two players")
    board_cards = _as_cards(board)
    dead_cards = _as_cards(dead)
    if len(board_cards) not in (0, 3, 4, 5):
        raise ValueError(f"The board must have 0, 3, 4 or 5 cards, got {len(board_cards)}")
    known = board_cards + dead_cards
    if len(set(known)) != len(known):
        raise ValueError("A card appears more than once in the board and dead cards")
    if 2 * len(players) + len(known) + (5 - len(board_cards)) > 52:
        raise ValueError("Not enough cards in the deck for that many players")

    ranges = [_as_range(p).without(known) for p in players]
    for i, r in enumerate(ranges):
        if not r:
            raise ValueError(f"Player {i + 1} has no possible hand once known cards are removed")

    # Players with exactly one possible hand fix those cards for everyone else.
    fixed: list[Card] = []
    for r in ranges:
        if len(r) == 1:
            fixed.extend(next(iter(r)))
    if len(set(fixed)) != len(fixed):
        raise ValueError("Two players' known hands share a card")
    fixed_set = set(fixed)
    pruned: list[HandRange] = []
    for i, r in enumerate(ranges):
        if len(r) > 1:
            r = r.without(fixed_set)
            if not r:
                raise ValueError(f"Player {i + 1} has no possible hand that avoids the known cards")
        pruned.append(r)

    used = {c.index for c in known}
    return _Problem(
        ranges=[[(a.index, b.index) for a, b in r] for r in pruned],
        board=[c.index for c in board_cards],
        unseen=[i for i in range(52) if i not in used],
        missing=5 - len(board_cards),
    )


def _valid_assignments(ranges: list[list[IndexCombo]]) -> Iterator[tuple[IndexCombo, ...]]:
    """Every way to give each player a combo with no card used twice (depth-first)."""
    n = len(ranges)
    chosen: list[IndexCombo] = []
    used: set[int] = set()

    def rec(i: int) -> Iterator[tuple[IndexCombo, ...]]:
        if i == n:
            yield tuple(chosen)
            return
        for a, b in ranges[i]:
            if a in used or b in used:
                continue
            used.update((a, b))
            chosen.append((a, b))
            yield from rec(i + 1)
            chosen.pop()
            used.difference_update((a, b))

    yield from rec(0)


# ---------------------------------------------------------------------- core


def _showdown(scores: list[int], shares: list[float], wins: list[int], ties: list[int]) -> None:
    best = max(scores)
    winners = [i for i, s in enumerate(scores) if s == best]
    if len(winners) == 1:
        w = winners[0]
        wins[w] += 1
        shares[w] += 1.0
    else:
        part = 1.0 / len(winners)
        for w in winners:
            ties[w] += 1
            shares[w] += part


def _exact(problem: _Problem) -> EquityResult:
    n = len(problem.ranges)
    shares = [0.0] * n
    wins = [0] * n
    ties = [0] * n
    deals = 0
    board = problem.board
    for holes in _valid_assignments(problem.ranges):
        held = {c for h in holes for c in h}
        rest = [c for c in problem.unseen if c not in held]
        for runout in combinations(rest, problem.missing):
            full = board + list(runout)
            scores = [fasteval.score((a, b, *full)) for a, b in holes]
            _showdown(scores, shares, wins, ties)
            deals += 1
    if deals == 0:
        raise ValueError("The ranges leave no possible deal (every combination shares a card)")
    return EquityResult(
        players=tuple(
            PlayerEquity(shares[i] / deals, wins[i] / deals, ties[i] / deals, 0.0) for i in range(n)
        ),
        method="exact",
        trials=deals,
        seed=None,
    )


def _monte_carlo(problem: _Problem, iterations: int, seed: int) -> EquityResult:
    # Fail fast with a clear message when no deal exists at all.
    if next(_valid_assignments(problem.ranges), None) is None:
        raise ValueError("The ranges leave no possible deal (every combination shares a card)")

    rng = random.Random(seed)
    rand = rng.random
    sample = rng.sample
    n = len(problem.ranges)
    ranges = problem.ranges
    sizes = [len(r) for r in ranges]
    single = all(s == 1 for s in sizes)
    unseen = problem.unseen
    missing = problem.missing
    draw = missing + 2 * n  # enough cards to skip every hole card (see below)
    board = problem.board

    rank_key, suit_key, rank_bit = fasteval.RANK_KEY, fasteval.SUIT_KEY, fasteval.RANK_BIT
    noflush, flush_table, flush_suit = (
        fasteval.NOFLUSH_TABLE,
        fasteval.FLUSH_TABLE,
        fasteval.FLUSH_SUIT,
    )
    board_rank = sum(rank_key[c] for c in board)
    board_suit = sum(suit_key[c] for c in board)

    shares = [0.0] * n
    squares = [0.0] * n
    wins = [0] * n
    ties = [0] * n
    holes = [r[0] for r in ranges]
    budget = MAX_REJECTIONS_PER_TRIAL + MAX_REJECTIONS_PER_ITERATION * iterations
    too_rare = "The ranges overlap so heavily that valid deals are too rare to sample"

    for _ in range(iterations):
        # 1. Hole cards: draw every player independently, reject the deal on any clash.
        if not single:
            for _attempt in range(MAX_REJECTIONS_PER_TRIAL):
                holes = [r[int(rand() * s)] for r, s in zip(ranges, sizes, strict=True)]
                held = {c for h in holes for c in h}
                if len(held) == 2 * n:
                    break
                budget -= 1
                if budget <= 0:
                    raise ValueError(too_rare)
            else:
                raise ValueError(too_rare)
        held = {c for h in holes for c in h}

        # 2. Runout. Take a uniform random ordered sample of the unseen cards and keep the
        #    first `missing` that nobody holds. Filtering a uniform permutation leaves a
        #    uniform permutation of the remaining cards, and at most 2n cards are skipped,
        #    so a prefix of length missing + 2n always has enough cards.
        if missing:
            runout = [c for c in sample(unseen, min(draw, len(unseen))) if c not in held][:missing]
        else:
            runout = []
        rk = board_rank
        sk = board_suit
        for c in runout:
            rk += rank_key[c]
            sk += suit_key[c]

        # 3. Score every hand (inlined fasteval.score).
        scores = []
        for a, b in holes:
            s = noflush[rk + rank_key[a] + rank_key[b]]
            fs = flush_suit[sk + suit_key[a] + suit_key[b]]
            if fs >= 0:
                mask = 0
                for c in (a, b, *board, *runout):
                    if c & 3 == fs:
                        mask |= rank_bit[c]
                f = flush_table[mask]
                if f > s:
                    s = f
            scores.append(s)

        best = max(scores)
        k = scores.count(best)
        if k == 1:
            w = scores.index(best)
            wins[w] += 1
            shares[w] += 1.0
            squares[w] += 1.0
        else:
            part = 1.0 / k
            for i, s in enumerate(scores):
                if s == best:
                    ties[i] += 1
                    shares[i] += part
                    squares[i] += part * part

    out = []
    for i in range(n):
        mean = shares[i] / iterations
        if iterations > 1:
            var = max(0.0, (squares[i] - iterations * mean * mean) / (iterations - 1))
            se = math.sqrt(var / iterations)
        else:
            se = float("nan")
        out.append(PlayerEquity(mean, wins[i] / iterations, ties[i] / iterations, se))
    return EquityResult(players=tuple(out), method="monte_carlo", trials=iterations, seed=seed)


def _exact_cost(problem: _Problem) -> int:
    """Upper bound on hand evaluations for exact enumeration."""
    n = len(problem.ranges)
    assignments = math.prod(len(r) for r in problem.ranges)
    runouts = math.comb(len(problem.unseen) - 2 * n, problem.missing)
    return assignments * runouts * n


def calculate_equity(
    players: Sequence[str | HandRange | Sequence[Card]],
    board: str | Sequence[Card] | None = None,
    dead: str | Sequence[Card] | None = None,
    *,
    method: Method = "auto",
    iterations: int = DEFAULT_ITERATIONS,
    seed: int | None = None,
    exact_limit: int = DEFAULT_EXACT_LIMIT,
) -> EquityResult:
    """All-in equity of each player.

    ``players`` are ranges (``"QQ+, AKs"``), known hands (``"AhKh"`` or two cards), or
    :class:`HandRange` objects. ``board`` holds 0, 3, 4 or 5 cards. ``dead`` cards are
    known to be out of play, for example a folded player's exposed cards.
    """
    if method not in ("auto", "exact", "monte_carlo"):
        raise ValueError(f"Unknown method {method!r}")
    problem = _prepare(players, board, dead)
    if method == "exact" or (method == "auto" and _exact_cost(problem) <= exact_limit):
        return _exact(problem)
    if iterations < 1:
        raise ValueError("iterations must be at least 1")
    if seed is None:
        seed = random.SystemRandom().randrange(2**63)
    return _monte_carlo(problem, iterations, seed)


STREET_BOARD_SIZES = (("preflop", 0), ("flop", 3), ("turn", 4))


def equity_by_street(
    hands: Sequence[Sequence[Card]],
    board: Sequence[Card],
    *,
    iterations: int = 10_000,
    seed: int | None = None,
) -> list[tuple[str, EquityResult]]:
    """Equity of face-up ``hands`` before the flop, on the flop and on the turn.

    ``board`` is the final board. Streets it does not reach are skipped. Flop and turn
    results are exact. Preflop uses Monte Carlo (an exact preflop count takes seconds).
    """
    out = []
    for i, (street, size) in enumerate(STREET_BOARD_SIZES):
        if len(board) < size:
            break
        out.append(
            (
                street,
                calculate_equity(
                    [list(h) for h in hands],
                    board=list(board[:size]),
                    iterations=iterations,
                    seed=None if seed is None else seed + i,
                ),
            )
        )
    return out


# ---------------------------------------------------------------------- CLI


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m pokerlab.equity",
        description="All-in hold'em equity. Example: python -m pokerlab.equity AhKh 'QQ+,AKs'",
    )
    parser.add_argument("players", nargs="+", help="hand ('AhKh') or range ('TT+,AQs+') each")
    parser.add_argument("-b", "--board", default="", help="board cards, e.g. 'Ks7d2c'")
    parser.add_argument("-d", "--dead", default="", help="dead cards")
    parser.add_argument("-n", "--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("-s", "--seed", type=int, default=None)
    parser.add_argument("-m", "--method", choices=["auto", "exact", "monte_carlo"], default="auto")
    args = parser.parse_args(argv)
    try:
        result = calculate_equity(
            args.players,
            args.board or None,
            args.dead or None,
            method=args.method,
            iterations=args.iterations,
            seed=args.seed,
        )
    except ValueError as exc:
        parser.error(str(exc))
    width = max(len(p) for p in args.players)
    for spec, p in zip(args.players, result.players, strict=True):
        line = f"{spec:<{width}}  equity {p.equity:7.2%}  win {p.win:7.2%}  tie {p.tie:6.2%}"
        if result.method == "monte_carlo":
            line += f"  ±{1.96 * p.std_error:.2%}"
        print(line)
    note = f"{result.method.replace('_', ' ')}, {result.trials:,} deals"
    if result.seed is not None:
        note += f", seed {result.seed}"
    print(note)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
