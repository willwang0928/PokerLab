"""Disk-space guards, a run lock and a cleanup command for the calibration cache.

Measured storage (v0.4.5 configuration): one simulated session is about 165 KB of
JSON, so a full run (14 policies x 8 sessions) is about 20 MB and the report pass adds
nothing but ``report.md`` and ``results.json`` (~0.3 MB). The cache only grows when the
configuration changes (each configuration gets its own folder), so old folders are what
accumulates; ``clean`` removes them.

    python -m pokerlab.calibration.housekeeping            # show the cache, remove nothing
    python -m pokerlab.calibration.housekeeping --clean    # remove stale folders + temp files

``--clean`` keeps the folders for the current full and ``--quick`` configurations and
the named discovery run (inputs to docs/calibration/report.md); ``--all`` removes those
too (they are re-simulated on the next run, about an hour).
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
from collections.abc import Iterator
from pathlib import Path

# Refuse to start (or keep going) when less than this is free. The run itself needs
# ~20 MB; the margin is for the rest of the machine (swap files share the disk on macOS).
MIN_FREE_MB = int(os.environ.get("POKERLAB_MIN_FREE_MB", "1024"))
SESSION_MB = 0.25  # measured ~0.165 MB per session, with a margin


class DiskSpaceError(RuntimeError):
    pass


class RunInProgress(RuntimeError):
    pass


def free_mb(path: Path) -> float:
    path = Path(path)
    while not path.exists():
        path = path.parent
    return shutil.disk_usage(path).free / 2**20


def ensure_space(path: Path, need_mb: float = 0.0, reserve_mb: float | None = None) -> float:
    """Raise :class:`DiskSpaceError` unless ``need_mb`` fits above the reserve."""
    reserve = MIN_FREE_MB if reserve_mb is None else reserve_mb
    free = free_mb(path)
    if free - need_mb < reserve:
        raise DiskSpaceError(
            f"only {free:.0f} MB free at {path}; this run needs ~{need_mb:.0f} MB and "
            f"{reserve:.0f} MB is kept in reserve (POKERLAB_MIN_FREE_MB). Free some space "
            "or run `python -m pokerlab.calibration.housekeeping --clean`."
        )
    return free


@contextlib.contextmanager
def run_lock(cache: Path) -> Iterator[None]:
    """Allow one calibration (or diagnostic) run per cache at a time.

    Uses an advisory ``flock``, which the OS releases if the process dies, so a crashed
    run never leaves a stale lock behind. No-op where ``fcntl`` is unavailable.
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows
        yield
        return
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / ".lock"
    with open(path, "a+") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.seek(0)
            owner = fh.read().strip() or "another process"
            raise RunInProgress(
                f"a calibration run is already using {cache} (pid {owner}); "
                "wait for it to finish instead of running two at once"
            ) from None
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
        try:
            yield
        finally:
            fh.seek(0)
            fh.truncate()
            fcntl.flock(fh, fcntl.LOCK_UN)


def remove_partials(folder: Path) -> int:
    """Delete ``*.tmp`` files left by an interrupted run; returns how many."""
    n = 0
    for p in folder.glob("*.tmp"):
        p.unlink(missing_ok=True)
        n += 1
    return n


def _size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def plan(cache: Path, keep: set[str]) -> list[tuple[Path, int, str]]:
    """What ``clean`` would remove: (path, bytes, reason)."""
    out = []
    if not cache.exists():
        return out
    for d in sorted(p for p in cache.iterdir() if p.is_dir()):
        if d.name in keep:
            for t in d.glob("*.tmp"):
                out.append((t, t.stat().st_size, "partial file from an interrupted run"))
        else:
            out.append((d, _size(d), "cache for a configuration no longer in use"))
    return out


def main(argv: list[str] | None = None) -> int:
    from .policies import DISCOVERY, QUICK, RunConfig
    from .run import DEFAULT_CACHE, config_hash

    ap = argparse.ArgumentParser(prog="python -m pokerlab.calibration.housekeeping")
    ap.add_argument("--clean", action="store_true", help="remove what is listed")
    ap.add_argument("--all", action="store_true", help="also remove the folders in use")
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    args = ap.parse_args(argv)
    in_use = {config_hash(RunConfig()), config_hash(RunConfig(**QUICK)), DISCOVERY}
    keep = set() if args.all else in_use
    cache: Path = args.cache
    total = _size(cache) if cache.exists() else 0
    print(f"cache: {cache} ({total / 2**20:.1f} MB); free on disk: {free_mb(cache):.0f} MB")
    for name in sorted(keep):
        if (cache / name).exists():
            print(f"  keep    {name} ({_size(cache / name) / 2**20:.1f} MB, in use)")
    items = plan(cache, keep)
    for path, size, why in items:
        print(f"  {'remove' if args.clean else 'stale '}  {path.relative_to(cache)} "
              f"({size / 2**20:.1f} MB): {why}")  # fmt: skip
    if not items:
        print("  nothing to remove")
        return 0
    if not args.clean:
        print("dry run: nothing removed (add --clean)")
        return 0
    with run_lock(cache):
        for path, _, _ in items:
            shutil.rmtree(path) if path.is_dir() else path.unlink(missing_ok=True)
    print(f"removed {sum(s for _, s, _ in items) / 2**20:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
