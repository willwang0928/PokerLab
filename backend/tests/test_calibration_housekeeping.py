"""Storage guards for the calibration harness: space checks, the run lock, the
partial-file sweep and the cleanup plan (no simulation is run)."""

from __future__ import annotations

import multiprocessing as mp

import pytest

from pokerlab.calibration import housekeeping as H
from pokerlab.calibration import run as R
from pokerlab.calibration.policies import RunConfig


def test_ensure_space_refuses_when_the_reserve_would_be_breached(tmp_path):
    free = H.free_mb(tmp_path)
    assert H.ensure_space(tmp_path, need_mb=1, reserve_mb=0) == pytest.approx(free, rel=0.01)
    with pytest.raises(H.DiskSpaceError, match="housekeeping --clean"):
        H.ensure_space(tmp_path, need_mb=0, reserve_mb=free + 10_000)


def _try_lock(path, q):
    try:
        with H.run_lock(path):
            q.put("got it")
    except H.RunInProgress as e:
        q.put(str(e))


def test_a_second_run_on_the_same_cache_is_refused_and_the_lock_is_released(tmp_path):
    q = mp.get_context("spawn").Queue()
    with H.run_lock(tmp_path):
        p = mp.get_context("spawn").Process(target=_try_lock, args=(tmp_path, q))
        p.start()
        p.join(60)
        assert "already using" in q.get(timeout=5)
    p = mp.get_context("spawn").Process(target=_try_lock, args=(tmp_path, q))
    p.start()
    p.join(60)
    assert q.get(timeout=5) == "got it"


def test_partial_files_are_swept_and_stale_folders_planned_for_removal(tmp_path):
    cfg = RunConfig(hands=20, session_hands=10)
    keep = R.config_hash(cfg)
    (tmp_path / keep).mkdir()
    (tmp_path / keep / "baseline-000.json").write_text("{}")
    (tmp_path / keep / "baseline-001.json.tmp").write_text("{")
    (tmp_path / "old0config00").mkdir()
    (tmp_path / "old0config00" / "x.json").write_text("{}" * 100)
    planned = {p.name: why for p, _, why in H.plan(tmp_path, {keep})}
    assert set(planned) == {"baseline-001.json.tmp", "old0config00"}
    assert H.remove_partials(tmp_path / keep) == 1
    assert [p.name for p in (tmp_path / keep).iterdir()] == ["baseline-000.json"]


def test_run_all_stops_before_simulating_when_the_disk_is_too_full(tmp_path, monkeypatch):
    monkeypatch.setattr(H, "MIN_FREE_MB", H.free_mb(tmp_path) + 10_000)
    cfg = RunConfig(hands=20, session_hands=10)
    with pytest.raises(H.DiskSpaceError):
        R.run_all(cfg, policies=["baseline"], cache=tmp_path, workers=1, log=lambda m: None)
    assert not list((tmp_path / R.config_hash(cfg)).glob("*.json"))
