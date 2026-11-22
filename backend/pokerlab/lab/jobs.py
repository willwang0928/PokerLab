"""Run lab experiments in the background: a queue, a process pool, progress and a cache.

One experiment runs at a time (they would only compete for the same cores); others wait
in a queue. Its sessions go to a pool of worker processes, and results are filed by
session index, so the outcome does not depend on the number of workers or on the order
sessions finish in. Finished matchups are cached by everything that determines them
(:meth:`LabConfig.matchup_key`), so re-running an experiment, or a round robin that
contains an earlier pairing, reuses the result.

Jobs and the cache live in memory and are lost when the server restarts; any result can
be recreated exactly from its configuration.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import threading
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from typing import Any

from . import report as R
from .config import BIG_BLIND, BlockTask, LabConfig
from .simulate import BlockResult, run_block
from .strategies import get

log = logging.getLogger(__name__)

MAX_QUEUED = 8
HISTORY = 20  # finished jobs kept
CACHE_MATCHUPS = 48
LIVE_POINTS = 300  # trajectory points in a running job's live view


def default_workers() -> int:
    """``POKERLAB_LAB_WORKERS`` if set (0 runs sessions in the server process), else one
    fewer than the CPU count, at most 6 (memory: each worker holds its own agents)."""
    env = os.getenv("POKERLAB_LAB_WORKERS")
    if env is not None and env.strip():
        return max(0, int(env))
    return max(1, min(6, (os.cpu_count() or 2) - 1))


class Busy(Exception):
    """Too many experiments are queued."""


# --------------------------------------------------------------------------- execution


MAX_RESTARTS = 3  # times a run replaces a pool whose worker died before giving up


class WorkerPool:
    """A process pool that is replaced when one of its workers dies.

    A worker can die from outside the lab's control (the operating system reclaiming
    memory, an interpreter crash: one segfault inside CPython 3.14.0's garbage collector
    was seen during a long validation run). That breaks the whole pool. Sessions are
    deterministic, so the unfinished ones are simply run again on a fresh pool and the
    results are exactly what they would have been.
    """

    def __init__(self, workers: int):
        self.workers = workers
        self._executor: ProcessPoolExecutor | None = None
        self._lock = threading.Lock()
        self.restarts = 0

    def get(self) -> ProcessPoolExecutor:
        with self._lock:
            if self._executor is None:
                self._executor = _pool(self.workers)
            return self._executor

    def replace(self, broken: ProcessPoolExecutor) -> None:
        with self._lock:
            if self._executor is broken:
                self._executor = None
                self.restarts += 1
        broken.shutdown(wait=False, cancel_futures=True)

    def stop(self) -> None:
        with self._lock:
            executor, self._executor = self._executor, None
        if executor is not None:
            _stop_pool(executor)


def execute(
    tasks: Iterable[BlockTask],
    pool: WorkerPool | None,
    cancelled: Callable[[], bool] = lambda: False,
    max_restarts: int = MAX_RESTARTS,
    fn: Callable[[BlockTask], BlockResult] | None = None,
) -> Iterator[tuple[BlockTask, BlockResult]]:
    """Run sessions and yield ``(task, result)`` as they finish (in completion order).

    With no pool the sessions run here, one after another. Cancelling stops queued
    sessions; ones already running finish and are discarded. If a worker dies, the
    unfinished sessions are run again on a new pool, at most ``max_restarts`` times.
    ``fn`` replaces :func:`run_block` (tests).
    """
    fn = fn or run_block
    remaining = dict(enumerate(tasks))
    if pool is None:
        for _, t in sorted(remaining.items()):
            if cancelled():
                return
            yield t, fn(t)
        return
    failures = 0
    while remaining:
        executor = pool.get()
        futures: dict[Future, int] = {}
        broken = False
        try:
            for i, t in remaining.items():
                futures[executor.submit(fn, t)] = i
        except BrokenProcessPool:
            broken = True
        pending = set(futures)
        try:
            while pending and not broken:
                done, pending = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
                for f in done:
                    try:
                        res = f.result()
                    except BrokenProcessPool:
                        broken = True
                        continue
                    yield remaining.pop(futures[f]), res
                if cancelled():
                    return
        finally:
            for f in pending:
                f.cancel()
        if broken:
            failures += 1
            log.warning("A lab worker died; re-running %d unfinished sessions on a new pool "
                        "(restart %d of %d)", len(remaining), failures, max_restarts)  # fmt: skip
            pool.replace(executor)
            if failures > max_restarts:
                raise BrokenProcessPool(f"Lab workers died {failures} times; giving up")


def run(cfg: LabConfig, workers: int = 0, on_block: Callable[[BlockResult], None] | None = None):
    """Run a whole experiment synchronously and return its report (CLI and tests)."""
    blocks: dict[tuple[str, str], list[BlockResult]] = {m: [] for m in cfg.matchups}
    tasks = [t for a, b in cfg.matchups for t in cfg.blocks(a, b)]
    pool = WorkerPool(workers) if workers > 0 else None
    try:
        for _, res in execute(tasks, pool):
            blocks[(res.a, res.b)].append(res)
            if on_block:
                on_block(res)
    finally:
        if pool is not None:
            pool.stop()
    return R.experiment_report(
        cfg, [R.matchup_report(cfg, a, b, blocks[(a, b)]) for a, b in cfg.matchups]
    )


def _watch_parent(parent: int) -> None:
    """Worker initializer: exit when the process that started the pool is gone.

    A server that is killed outright (a crash, ``kill -9``) cannot shut its pool down,
    and idle workers would otherwise live on as orphans holding memory.
    """

    def watch() -> None:
        while True:
            time.sleep(1.0)
            if os.getppid() != parent:
                os._exit(0)

    threading.Thread(target=watch, name="lab-parent-watch", daemon=True).start()


def _pool(workers: int) -> ProcessPoolExecutor:
    # "spawn": the server process has threads, and forking a threaded process is unsafe.
    return ProcessPoolExecutor(
        max_workers=workers,
        mp_context=multiprocessing.get_context("spawn"),
        initializer=_watch_parent,
        initargs=(os.getpid(),),
    )


def _stop_pool(executor: ProcessPoolExecutor) -> None:
    """Stop a pool now: queued sessions are dropped and running ones abandoned."""
    terminate = getattr(executor, "terminate_workers", None)  # Python 3.14+
    if terminate is not None:
        try:
            terminate()
            return
        except Exception:  # pragma: no cover - fall back to a normal shutdown
            log.debug("terminate_workers failed", exc_info=True)
    executor.shutdown(wait=True, cancel_futures=True)


# --------------------------------------------------------------------------- jobs


@dataclass
class _Matchup:
    a: str
    b: str
    planned: int  # sessions
    blocks: dict[int, BlockResult] = field(default_factory=dict)
    report: dict | None = None
    cached: bool = False

    @property
    def done(self) -> bool:
        return self.report is not None and self.report["complete"]


@dataclass
class Job:
    id: str
    config: LabConfig
    created: float
    workers: int
    status: str = "queued"  # queued | running | done | cancelled | failed
    started: float | None = None
    finished: float | None = None
    hands_done: int = 0
    hands_run: int = 0  # simulated in this job (not from the cache), for the rate
    cpu_seconds: float = 0.0
    error: str | None = None
    report: dict | None = None
    matchups: list[_Matchup] = field(default_factory=list)
    cancel: threading.Event = field(default_factory=threading.Event)

    @property
    def total_hands(self) -> int:
        return self.config.total_hands


class LabJobs:
    def __init__(self, workers: int | None = None):
        self.workers = default_workers() if workers is None else workers
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._queue: deque[str] = deque()
        self._cache: OrderedDict[tuple, tuple[dict, list[BlockResult]]] = OrderedDict()
        self._pool: WorkerPool | None = WorkerPool(self.workers) if self.workers > 0 else None
        self._thread: threading.Thread | None = None
        self._closed = False
        # Observed ÷ modelled run time on this machine, learned from finished runs.
        self._speed = 1.0

    # ------------------------------------------------------------------ public

    def submit(self, cfg: LabConfig) -> dict:
        with self._lock:
            waiting = sum(1 for j in self._jobs.values() if j.status in ("queued", "running"))
            if waiting >= MAX_QUEUED:
                raise Busy("Too many experiments are waiting; try again when one finishes")
            job = Job(
                id=uuid.uuid4().hex[:12], config=cfg, created=time.time(), workers=self.workers
            )
            for a, b in cfg.matchups:
                m = _Matchup(a, b, planned=len(cfg.blocks(a, b)))
                hit = self._cache.get(cfg.matchup_key(a, b))
                if hit is not None:
                    self._cache.move_to_end(cfg.matchup_key(a, b))
                    m.report, blocks = hit
                    m.blocks = {blk.session: blk for blk in blocks}
                    m.cached = True
                    job.hands_done += cfg.hands
                job.matchups.append(m)
            self._jobs[job.id] = job
            if all(m.cached for m in job.matchups):
                self._finish(job, "done")
            else:
                self._queue.append(job.id)
                self._ensure_thread()
                self._wake.notify_all()
            self._trim()
            return self._view(job)

    def estimate(self, cfg: LabConfig) -> float:
        """Expected wall-clock seconds for an experiment on this machine."""
        return cfg.estimate_seconds(self.workers) * self._speed

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return None if job is None else self._view(job)

    def list(self) -> list[dict]:
        with self._lock:
            return [self._summary(j) for j in reversed(self._jobs.values())]

    def cancel(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.status == "queued":
                self._queue.remove(job.id)
                self._finish(job, "cancelled")
            elif job.status == "running":
                job.cancel.set()  # the dispatcher finishes it
            return self._view(job)

    def deals(self, job_id: str) -> Iterator[list[Any]] | None:
        """Per-deal rows of a finished job, for CSV export."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status in ("queued", "running"):
                return None
            matchups = [
                (m.a, m.b, sorted(m.blocks.values(), key=lambda r: r.session)) for m in job.matchups
            ]
        bb = BIG_BLIND

        def rows():
            for a, b, blocks in matchups:
                for blk in blocks:
                    for i, x in enumerate(blk.table1):
                        y = blk.table2[i] if blk.table2 is not None else None
                        yield [a, b, blk.session, i, i % 2, x / bb, None if y is None else y / bb]

        return rows()

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            for job in self._jobs.values():
                job.cancel.set()
            self._wake.notify_all()
        if self._pool is not None:
            self._pool.stop()

    # ------------------------------------------------------------------ dispatcher

    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, name="lab-dispatcher", daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        while True:
            with self._lock:
                while not self._queue and not self._closed:
                    self._wake.wait()
                if self._closed:
                    return
                job = self._jobs[self._queue.popleft()]
                job.status = "running"
                job.started = time.time()
            try:
                self._run(job)
            except BrokenProcessPool:
                log.exception("Lab worker processes kept dying")
                with self._lock:
                    self._finish(
                        job,
                        "failed",
                        "Simulation workers kept stopping unexpectedly (possibly out of "
                        "memory); finished sessions are kept. Try fewer workers "
                        "(POKERLAB_LAB_WORKERS) or fewer hands.",
                    )
            except Exception as exc:  # pragma: no cover - defensive
                log.exception("Lab experiment failed")
                with self._lock:
                    self._finish(job, "failed", f"{type(exc).__name__}: {exc}")

    def _run(self, job: Job) -> None:
        cfg = job.config
        index = {(m.a, m.b): m for m in job.matchups}
        tasks = [t for m in job.matchups if not m.cached for t in cfg.blocks(m.a, m.b)]
        for _, res in execute(tasks, self._pool, job.cancel.is_set):
            with self._lock:
                m = index[(res.a, res.b)]
                m.blocks[res.session] = res
                job.hands_done += res.hands
                job.hands_run += res.hands
                job.cpu_seconds += res.elapsed
                if len(m.blocks) == m.planned:
                    m.report = R.matchup_report(cfg, m.a, m.b, list(m.blocks.values()))
                    self._cache[cfg.matchup_key(m.a, m.b)] = (m.report, list(m.blocks.values()))
                    while len(self._cache) > CACHE_MATCHUPS:
                        self._cache.popitem(last=False)
        with self._lock:
            self._finish(job, "cancelled" if job.cancel.is_set() else "done")

    # ------------------------------------------------------------------ views

    def _finish(self, job: Job, status: str, error: str | None = None) -> None:
        cfg = job.config
        reports = []
        for m in job.matchups:
            if m.report is None and m.blocks:
                m.report = R.matchup_report(cfg, m.a, m.b, list(m.blocks.values()))
            if m.report is not None:
                reports.append(m.report)
        job.report = R.experiment_report(cfg, reports) if reports else None
        job.status = status
        job.error = error
        job.finished = time.time()
        self._learn_speed(job)

    def _learn_speed(self, job: Job) -> None:
        """Move the speed factor toward what this run took versus the model (runs predicted
        under 5 s are skipped: starting the workers dominates them)."""
        if job.status != "done" or not job.started or not job.hands_run:
            return
        share = job.hands_run / job.total_hands
        predicted = job.config.estimate_seconds(job.workers) * share
        if predicted < 5:
            return
        ratio = (job.finished - job.started) / predicted
        self._speed = min(8.0, max(0.25, 0.6 * self._speed + 0.4 * ratio))

    def _trim(self) -> None:
        finished = [j for j in self._jobs.values() if j.status not in ("queued", "running")]
        for job in finished[: max(0, len(finished) - HISTORY)]:
            del self._jobs[job.id]

    def _progress(self, job: Job) -> dict:
        now = job.finished or time.time()
        elapsed = (now - job.started) if job.started else 0.0
        rate = job.hands_run / elapsed if elapsed > 0 and job.hands_run else None
        remaining = job.total_hands - job.hands_done
        if job.status in ("queued", "running"):
            eta = remaining / rate if rate else self.estimate(job.config)
        else:
            eta = 0.0
        return {
            "hands_done": job.hands_done,
            "total_hands": job.total_hands,
            "fraction": job.hands_done / job.total_hands if job.total_hands else 1.0,
            "elapsed": elapsed,
            "hands_per_second": rate,
            "eta_seconds": eta,
            "cpu_seconds": job.cpu_seconds,
        }

    def _summary(self, job: Job) -> dict:
        return {
            "id": job.id,
            "status": job.status,
            "config": job.config.view(),
            "labels": [get(s).label for s in job.config.strategies],
            "created": job.created,
            "finished": job.finished,
            "progress": self._progress(job),
        }

    def _live(self, job: Job, m: _Matchup) -> dict:
        """A running matchup: its provisional rate and the trajectory of the sessions
        completed so far, in order (only the contiguous prefix: later sessions that
        finished early are not drawn yet)."""
        prefix = []
        for k in range(m.planned):
            if k not in m.blocks:
                break
            prefix.append(m.blocks[k])
        done = list(m.blocks.values())
        units, *_ = R._units(done, BIG_BLIND)
        mean = sum(units) / len(units) * R.S.TO_BB100 if units else None
        traj = None
        if prefix:
            t1 = [x for blk in prefix for x in blk.table1]
            t2 = [x for blk in prefix for x in blk.table2] if prefix[0].table2 is not None else []
            traj = R._trajectory(t1, t2, BIG_BLIND, bool(t2))
            # Lighter than the final report: fewer points while running.
            if len(traj["x"]) > LIVE_POINTS:
                step = len(traj["x"]) / LIVE_POINTS
                keep = sorted({int(i * step) for i in range(LIVE_POINTS)} | {len(traj["x"]) - 1})
                for key in ("x", "total", "table1", "table2"):
                    if traj[key] is not None:
                        traj[key] = [traj[key][i] for i in keep]
        return {"provisional_bb_per_100": mean, "trajectory": traj}

    def _view(self, job: Job) -> dict:
        out = self._summary(job)
        out["workers"] = job.workers
        out["estimate_seconds"] = self.estimate(job.config)
        out["error"] = job.error
        out["matchups"] = []
        for m in job.matchups:
            hands = sum(blk.hands for blk in m.blocks.values())
            row = {
                "a": m.a,
                "b": m.b,
                "a_label": get(m.a).label,
                "b_label": get(m.b).label,
                "hands_done": hands,
                "planned_hands": job.config.hands,
                "cached": m.cached,
                "status": "done" if m.done else ("running" if m.blocks else "pending"),
            }
            if m.done:
                # The headline only; the full report comes with the finished job.
                row["estimate"] = m.report["estimate"]
            elif job.status == "running" and m.blocks:
                row["live"] = self._live(job, m)
            out["matchups"].append(row)
        out["report"] = job.report
        return out
