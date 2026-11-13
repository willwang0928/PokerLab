"""Run the simulated sessions in parallel and cache each one as JSON.

Sessions are independent and seeded from (seed, policy, index), so a cached session is
reused only when the configuration that produced it is identical (its hash is part of
the cache path).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Any

from .housekeeping import SESSION_MB, DiskSpaceError, ensure_space, remove_partials, run_lock
from .policies import POLICIES, RunConfig
from .simulate import run_session

DEFAULT_CACHE = Path(__file__).resolve().parents[2] / ".calibration-cache"


def config_hash(cfg: RunConfig) -> str:
    raw = json.dumps(dataclasses.asdict(cfg), sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _path(cache: Path, cfg: RunConfig, policy: str, session: int, folder: str | None = None):
    return cache / (folder or config_hash(cfg)) / f"{policy}-{session:03d}.json"


def load_folder(cfg: RunConfig, folder: str, cache: Path = DEFAULT_CACHE):
    """Sessions saved under a named folder (no simulation), or None if incomplete."""
    sessions = cfg.hands // cfg.session_hands
    out = {}
    for p in POLICIES:
        paths = [_path(cache, cfg, p.name, s, folder) for s in range(sessions)]
        if not all(x.exists() for x in paths):
            return None
        out[p.name] = [json.loads(x.read_text()) for x in paths]
    return out


def _task(args: tuple[str, int, RunConfig, str]) -> tuple[str, int]:
    policy, session, cfg, out = args
    result = run_session(policy, session, cfg)
    tmp = Path(out + ".tmp")
    tmp.write_text(json.dumps(result))
    tmp.replace(out)
    return policy, session


def run_all(
    cfg: RunConfig,
    policies: Iterable[str] | None = None,
    cache: Path = DEFAULT_CACHE,
    workers: int | None = None,
    log=print,
) -> dict[str, list[dict[str, Any]]]:
    names = list(policies or [p.name for p in POLICIES])
    sessions = cfg.hands // cfg.session_hands
    with run_lock(cache):
        return _run_locked(cfg, names, sessions, cache, workers, log)


def _run_locked(cfg, names, sessions, cache, workers, log):
    folder = cache / config_hash(cfg)
    folder.mkdir(parents=True, exist_ok=True)
    if n := remove_partials(folder):
        log(f"removed {n} partial session files from an interrupted run")
    todo = [
        (p, s, cfg, str(_path(cache, cfg, p, s)))
        for p in names
        for s in range(sessions)
        if not _path(cache, cfg, p, s).exists()
    ]
    # Random play is by far the slowest (long hands, many all-in rollouts): start it first.
    todo.sort(key=lambda t: t[0] != "random")
    if todo:
        free = ensure_space(cache, need_mb=len(todo) * SESSION_MB)
        log(f"running {len(todo)} sessions ({cfg.session_hands} hands each); "
            f"~{len(todo) * SESSION_MB:.0f} MB to write, {free:.0f} MB free")  # fmt: skip
        workers = workers or max(1, (os.cpu_count() or 2) - 1)
        if workers == 1:
            for t in todo:
                ensure_space(cache, need_mb=SESSION_MB)
                _task(t)
        else:
            # A worker that dies (an interpreter crash was seen with Python 3.14) breaks the
            # pool; finished sessions are already saved, so start a new pool for the rest.
            for attempt in range(4):
                left = [t for t in todo if not Path(t[3]).exists()]
                if not left:
                    break
                try:
                    with ProcessPoolExecutor(workers) as pool:
                        futures = [pool.submit(_task, t) for t in left]
                        for k, f in enumerate(as_completed(futures), 1):
                            p, s = f.result()
                            log(f"  [{k}/{len(left)}] {p} session {s}")
                            try:  # stop early rather than fill the disk mid-run
                                ensure_space(cache, need_mb=SESSION_MB)
                            except DiskSpaceError:
                                for other in futures:
                                    other.cancel()
                                raise
                except BrokenProcessPool:
                    log(f"worker crashed (attempt {attempt + 1}); resuming the rest")
    out: dict[str, list[dict[str, Any]]] = {}
    for p in names:
        out[p] = [json.loads(_path(cache, cfg, p, s).read_text()) for s in range(sessions)]
    return out
