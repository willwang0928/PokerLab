# The AI Simulation Lab (v0.5.0)

The Simulation Lab plays PokerLab's AI strategies against each other over thousands of
hands and reports which is better, by how much, and how sure that is. This document
describes the protocol, the statistics, how results are made reproducible, what was done
for speed, and how the method was validated. Generated numbers:
[`lab/validation.md`](lab/validation.md) (raw: `lab/validation.json`).

Code: `backend/pokerlab/lab/` (`config.py` the experiment and its sessions,
`simulate.py` one session, `stats.py` intervals and tests, `report.py` reports,
`jobs.py` background runs, `validate.py` this document's numbers). Interface: the **Lab**
tab (`frontend/src/components/LabView.tsx`).

Nothing here is compared with game-theory-optimal play: the lab measures how these
particular strategies do against each other.

## Reproducing

```bash
cd backend
python -m pokerlab.lab advanced intermediate --hands 10000 --seed 1     # one matchup
python -m pokerlab.lab passive folder random basic --hands 20000        # a round robin
python -m pokerlab.lab beginner basic --mode standard --json out.json   # full report as JSON
python -m pokerlab.lab.validate                                         # the validation below
pytest -q tests/test_lab.py                                             # unit and statistical tests
```

The command line and the Lab tab run the same code: the same configuration gives the same
numbers in both, on any machine, for a given PokerLab version. Each finished experiment in
the Lab tab shows its command.

## Strategies

Every strategy is an existing PokerLab agent, used unchanged.

| id | name | what it is | adapts | time per hand |
|---|---|---|---|---|
| `advanced` | Advanced | Expected-value bet sizing against an estimated range, reads checks; the table's hardest level | yes, fully | ~30 / ~90 ms |
| `advanced_fixed` | Advanced, fixed | The same with adaptation off | no | ~30 / ~90 ms |
| `intermediate` | Intermediate | Range estimate from actions, equity against pot odds | yes, at half strength | ~14 / ~30 ms |
| `intermediate_fixed` | Intermediate, fixed | The same with adaptation off | no | ~14 / ~30 ms |
| `beginner` | Beginner | Equity against any two cards, limps, pays off | no | ~15 / ~30 ms |
| `basic` | v0.1 bot | Hand-strength thresholds with occasional bluffs | no | < 1 ms |
| `aggressive` | Maniac | Plays most hands, bets and raises too often | no | < 1 ms |
| `passive` | Calling station | Calls almost everything | no | < 1 ms |
| `folder` | Rock | Plays tight and folds to most bets | no | < 1 ms |
| `random` | Random | Uniform over legal actions | no | < 1 ms |

Times are a strategy's single-core decision time per hand against a simple bot / against
an opponent that takes hands deep (the equity-based levels and the calling station). The
equity-based levels spend most of it in Monte Carlo equity, once per street played.

## Protocol

**Hands.** Heads-up No-Limit Hold'em, blinds 5/10, every hand from fresh equal stacks
(20, 50, 100 or 200 bb; a cash game with no carry-over, so one big pot cannot end a run).
The button alternates deal by deal.

**Duplicate dealing.** Each deal is played at two tables. At table 1 strategy A sits in
seat 0; at table 2 the same deck is dealt with B in seat 0 and A in seat 1, with the same
button. The engine deals hole cards by seat and the board from fixed deck positions
(burn cards included), so at table 2 B holds exactly A's table-1 cards in the same
position, and the board is the same as far as both hands get. Each table has its own
agents, seeded alike: nothing learned at one table reaches the other, and two copies of a
strategy with the same seed act identically on the same cards. A's results at the two
tables share the card luck with opposite signs, so most of it cancels in their sum.
Tests check the cards, button and board are identical at both tables for every deal, and
that a deterministic strategy (the calling station) against itself cancels exactly: every
deal sums to zero.

**Standard dealing** is table 1 alone. A duplicate run's table-1 results are exactly the
standard run of half as many hands with the same seed (tested), so the two can be
compared on common cards.

**Sessions.** A matchup is a sequence of sessions of `session_hands` deals (default 200)
with freshly created agents. An adaptive strategy learns about its opponent during a
session and starts again in the next. Sessions are also the unit of work for the worker
processes.

**Seeds.** Everything random in a session (the agents' seeds and every deck) comes from
`random.Random("<seed>:<A>:<B>:<session>")`. Python hashes string seeds with SHA-512, so
the streams do not depend on `PYTHONHASHSEED`, the platform, the number of workers or the
order sessions finish in. A pairing therefore gives the same result on its own or inside a
round robin, and results are filed by session index.

## Statistics

**Unit.** One observation is two hands balanced across the button: a duplicate deal (both
tables), or two consecutive standard deals (one with each button). The estimate is A's
mean result per unit in big blinds × 50 = bb/100. Strategy B's result is the negative.

**Interval.** Student-t with n − 1 degrees of freedom on the plain sample variance.
(PokerLab's player analytics blend a prior variance into small human samples; a
simulation has thousands of units and duplicate variance far below that prior, so no prior
is used.) Two-sided p-values use the exact t distribution (regularised incomplete beta).

**Adaptive strategies.** When either strategy adapts, units within a session are
dependent (the agent's behaviour drifts as it learns), while sessions are independent. The
session-clustered (CR1) standard error is then computed and used when larger, with G − 1
degrees of freedom. It needs at least 10 sessions; with fewer, the result is marked as
possibly too narrow, and the Lab warns before the run. A synthetic test with session
drift shows the naive interval covering well under 80% while the clustered one covers
about 95%.

**Verdict.** "A beats B" only when the 95% interval excludes zero; otherwise "no clear
difference", with the hands needed for ±5 bb/100 at the observed spread.

**Variance reduction.** For duplicate runs the same hands are also analysed as if they
had been dealt independently: button-balanced pairs of consecutive deals at each table,
the standard unit. The ratio of that variance to the duplicate variance is how many times
as many ordinary hands the duplicate run is worth (both use two-hand units over the same
hands). The correlation of A's results at the two tables is reported with it.

**Round robins.** Every pair of the chosen strategies (3–6) is a matchup with its own
seeds. "Clear" in the results matrix uses Holm-adjusted p-values over all matchups, which
keeps the chance of any false "clear" in the table at 5% whatever the dependence. A
strategy's "average against the field" is the mean of its matchup estimates; matchups use
independent cards, so their errors combine as independent, with Welch–Satterthwaite
degrees of freedom. The league ranks by that mean; neighbouring ranks whose intervals
overlap may not differ.

**Charts.** The results chart shows A's cumulative result: for duplicate runs, the total
and each table (which swing in opposite directions), with the range a break-even total
would stay inside 95% of the time (±1.96·σ·√deals). The convergence chart shows the
running estimate and its interval as hands accumulate (log scale) and, for duplicate runs,
the wider interval the same hands would have had unpaired. Running intervals are
descriptive: looking repeatedly and stopping when the line looks good would inflate false
findings, so the interface says to read the final interval. A run stopped early is
reported as incomplete with that warning.

## Efficiency

Profiling an Advanced-vs-Intermediate match put about 75% of the time in the agents'
Monte Carlo equity (`equity._monte_carlo`, already a hand-optimised loop), a third of
it in Python's own `random.sample`. Making the agents faster with identical output would
mean depending on CPython internals, and the agents are what the live game and the
calibration use, so they were left unchanged. The lab gets its speed elsewhere:

* **Parallel sessions.** Sessions run in a pool of worker processes (`spawn`; default one
  fewer than the CPU count, at most 6; `POKERLAB_LAB_WORKERS` overrides, 0 runs them in the
  server). Results do not depend on the worker count (tested).
* **Caching.** A finished matchup is cached by everything that determines it (version,
  protocol, strategies, mode, hands, seed, stacks, session length), so re-running an
  experiment, or a round robin that contains an earlier pairing, is instant.
* **One experiment at a time.** Experiments queue rather than compete for the same cores
  (at most 8 waiting).
* **Honest estimates.** Before a run the Lab estimates the time from each strategy's
  measured cost; while it runs, from the observed rate.
* **Light polling.** A running experiment's status carries progress, provisional rates
  and a thinned live chart; the full report (thinned to at most 900 points per series)
  comes once.
* **Workers never outlive the server.** Each worker exits within a second of losing its
  parent, so a crashed or killed server leaves no orphans (tested with `kill -9`).
* **A dying worker costs a few sessions, not the run.** One dead worker breaks a whole
  process pool. The lab replaces the pool and runs the unfinished sessions again (up to
  three times per run); sessions are deterministic, so the result is exactly what it would
  have been (tested by killing a worker mid-run). This was added after the first full
  validation run lost a worker to a segmentation fault inside CPython 3.14.0's garbage
  collector (`tuple_traverse`, during heavy swapping on an 8 GB machine); no matching
  CPython report was found.

## Validation

`python -m pokerlab.lab.validate` (PokerLab 0.5.0, 4 workers, 32 minutes; 1.0 million
hands). Full tables: [`lab/validation.md`](lab/validation.md).

**Coverage.** A strategy against itself (independent seeds for the two copies) has a true
result of zero by symmetry, so about 95% of 95% intervals should contain zero. Each cell
is 200 independent runs of 400 hands, except adaptive Intermediate (40 runs of 1,200
hands, 10 sessions each, session-clustered error):

| mirror match | duplicate | standard |
|---|---|---|
| Maniac | 94.0% (89.8–96.5) | 94.5% (90.4–96.9) |
| v0.1 bot | 96.0% (92.3–98.0) | 94.0% (89.8–96.5) |
| Rock | 96.5% (93.0–98.3) | 95.0% (91.0–97.3) |
| Random | 91.5% (86.8–94.6) | 96.0% (92.3–98.0) |
| Intermediate, adaptive | 100% of 40 (91.2–100) | – |

Eight of nine cells are consistent with 95%. Random against itself in duplicate covered
91.5%, slightly low: its results are heavy-tailed (random all-ins), and 200 units per run
is a small sample for such a distribution; with nine cells, one interval this far from
95% is not unusual, and at realistic run lengths (thousands of hands) the t interval is
more accurate. The adaptive cell is conservative, as expected from taking the larger of
the two errors. The unit tests repeat a smaller version (40 seeds per mode) on every run.

**Duplicate efficiency** (seed 1; variance reduction measured on the same hands):

| A vs B | hands | A's bb/100 (95% CI) | reduction | table correlation |
|---|---|---|---|---|
| Intermediate vs Intermediate, fixed | 10,000 | −0.9 (−12.6 to +10.8) | 5.4× | −0.81 |
| Advanced vs Intermediate | 4,000 | +20.3 (−19.1 to +59.8) | 2.6× | −0.60 |
| v0.1 bot vs v0.1 bot | 20,000 | −2.7 (−12.8 to +7.5) | 2.3× | −0.56 |
| Calling station vs Rock | 20,000 | −69.4 (−79.2 to −59.6) | 2.0× | −0.49 |
| Intermediate vs v0.1 bot | 4,000 | +55.4 (+34.4 to +76.5) | 1.5× | −0.30 |
| Beginner vs v0.1 bot | 4,000 | −83.7 (−124.5 to −42.9) | 1.4× | −0.27 |
| Maniac vs v0.1 bot | 20,000 | −189.7 (−218.7 to −160.7) | 1.2× | −0.15 |
| v0.1 bot vs Random | 20,000 | +181.5 (+156.3 to +206.8) | 1.0× | −0.02 |

Duplicate dealing helps in proportion to how similarly the two strategies respond to the
same cards: most between near-identical strategies (5.4×: 10,000 duplicate hands are
worth about 54,000 ordinary ones), little between very different ones, and not at all
against a random player, whose results come from its own choices. A deterministic
strategy against itself cancels exactly (tested).

What these runs show about the strategies, at these sample sizes: Intermediate beats the
v0.1 bot and Beginner loses to it; Intermediate's half-strength adaptation makes no
measurable difference against a copy of itself over 10,000 hands (the interval is ±12
bb/100); and 4,000 hands cannot separate Advanced from Intermediate (the levels were
chosen by earlier duplicate matches; this run neither confirms nor contradicts them).

**Agreement.** Duplicate and standard dealing estimate the same quantity: Maniac vs v0.1
bot −210.6 ± 14.7 vs −234.1 ± 16.2 bb/100 (difference 1.1 standard errors), v0.1 bot vs
Calling station +79.0 ± 6.4 vs +89.0 ± 9.3 (0.9 standard errors), on 20,000 hands each.

**Speed.** Single-process time per hand: v0.1 bot against Random 0.3 ms, Intermediate
against the v0.1 bot 14 ms, Intermediate against Intermediate 61 ms, Advanced against
Intermediate 122 ms. On the 4-core development laptop, 2, 4 and 6 workers ran 1.9, 2.3
and 2.6 times as fast as one; the validation's per-core figures, measured with four busy
workers on a loaded machine, are 2–3 times below the single-process ones. The time
estimate uses these measurements (two costs per strategy, workers^0.6) and then learns a
correction from the runs the server completes.

## Limitations

* **Speed of the equity-based levels.** Advanced plays about 8 hands per second per core
  against Intermediate (about 34 against a simple bot), and parallel workers scale
  sublinearly on a laptop, so 10,000 Advanced-versus-Intermediate hands take roughly
  ten minutes there. The fast strategies run thousands of hands per second.
* **Duplicate helps most when strategies react similarly to the cards.** Against a
  strategy whose results come from its own random choices (Random), or between strategies
  that play the same cards very differently, the reduction is small; the lab reports it
  rather than assuming it.
* **The estimand is this protocol.** Fresh stacks every hand, blinds 5/10, alternating
  button, sessions of a fixed length. Adaptive strategies' results depend on the session
  length (how long they have to learn).
* **Interpreter crashes.** The lab survives a worker crash but cannot prevent one. If
  workers keep dying (the run fails after three pool replacements), use fewer workers
  (`POKERLAB_LAB_WORKERS`) or a newer Python 3.14 release.
* **Memory, not persistence.** Experiments and the cache live in the server's memory and
  are lost on restart; any result can be recreated exactly from its configuration.
* **Not optimal play.** Results compare PokerLab's strategies with each other, not with a
  game-theory-optimal benchmark, and depend on the PokerLab version.
* **Running intervals are descriptive.** Only the final interval of a run that was not
  stopped because of its result has its stated coverage.
* **Stopping is per session.** Stopping an experiment drops its queued sessions; sessions
  already running finish in the background and are discarded, so a slow pairing can keep
  the cores busy for up to one session afterwards. Shutting the server down abandons them
  on Python 3.14 and waits for them on older versions.
