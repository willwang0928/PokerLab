"""Durable storage of learning progress in an embedded SQLite database.

**Model.** Progress is an append-only log of immutable records: graded table decisions
and training answers, each with a client-generated id. Everything else the app shows
(skill estimates, the review schedule, trends) is derived from that log in the client.
Because records never change, syncing two copies is a set union: inserting a record
that already exists does nothing, so retries, duplicate uploads and several devices
writing at once cannot conflict or lose data.

**Identity.** There are no accounts. A profile is created with a random 256-bit key,
which the client keeps and sends as ``Authorization: Bearer <key>``. The database stores
only its SHA-256 hash, so a copy of the database file cannot be used to read or write a
profile. Each profile also has a random public id that is safe to show.

**Durability.** WAL journaling with ``synchronous=FULL``; every upload is one
transaction, so an interrupted write leaves either all of its records or none. On
startup the file is checked (``PRAGMA quick_check``). If it is unreadable or corrupt the
store reports itself unavailable, and the file is left untouched for recovery, rather
than being replaced with an empty one.

**Hand histories** (schema 2, v0.4.3) live in their own table: one completed hand per
row, with the full record and a small summary for listing and filtering. They are
validated (and replayed) before they reach the store, see :mod:`pokerlab.history`.
Opening a schema-1 database adds the table and keeps every existing record.

Only the standard library is used.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2
MAX_RECORDS_PER_PROFILE = 200_000  # per table; far beyond any realistic history
MAX_HANDS_PER_PROFILE = 50_000  # hand records are larger (a few KB to 20 KB each)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY,
    public_id TEXT NOT NULL UNIQUE,
    token_hash TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS answers (
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    t REAL NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (profile_id, id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS answers_by_time ON answers(profile_id, t);
CREATE TABLE IF NOT EXISTS decisions (
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    t REAL NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (profile_id, id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS decisions_by_time ON decisions(profile_id, t);
CREATE TABLE IF NOT EXISTS hands (
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    t REAL NOT NULL,
    data TEXT NOT NULL,
    summary TEXT NOT NULL,
    PRIMARY KEY (profile_id, id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS hands_by_time ON hands(profile_id, t);
"""

TABLES = ("answers", "decisions")


class StorageUnavailable(RuntimeError):
    """The database could not be opened or failed its integrity check."""


class ProfileNotFound(LookupError):
    pass


class QuotaExceeded(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Profile:
    id: int
    public_id: str
    name: str
    created_at: float
    updated_at: float

    def view(self, counts: dict[str, int] | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "profile_id": self.public_id,
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if counts is not None:
            out["counts"] = counts
        return out


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def default_path() -> str:
    """``POKERLAB_DB`` if set, otherwise ``<POKERLAB_DATA_DIR or ./data>/pokerlab.sqlite3``."""
    if os.getenv("POKERLAB_DB"):
        return os.environ["POKERLAB_DB"]
    return str(Path(os.getenv("POKERLAB_DATA_DIR", "data")) / "pokerlab.sqlite3")


class ProgressStore:
    """Thread-safe access to the progress database (one connection, serialised)."""

    def __init__(self, path: str | None = None):
        self.path = path or default_path()
        self.error: str | None = None
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None
        try:
            self._db = self._open()
        except (sqlite3.Error, OSError) as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            log.error("Progress storage unavailable (%s): %s", self.path, self.error)

    # ------------------------------------------------------------------ setup

    def _open(self) -> sqlite3.Connection:
        memory = self.path == ":memory:"
        if not memory:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        try:
            check = db.execute("PRAGMA quick_check").fetchone()
            if not check or check[0] != "ok":
                raise sqlite3.DatabaseError(f"integrity check failed: {check}")
            if not memory:
                db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=5000")
            db.executescript(_SCHEMA)
            row = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
            elif int(row[0]) > SCHEMA_VERSION:
                raise sqlite3.DatabaseError(
                    f"database schema {row[0]} is newer than this server ({SCHEMA_VERSION})"
                )
            elif int(row[0]) < SCHEMA_VERSION:
                # 1 -> 2 only adds the hands table (created above); records are untouched.
                log.info("Progress database migrated from schema %s to %s", row[0], SCHEMA_VERSION)
                db.execute(
                    "UPDATE meta SET value = ? WHERE key = 'schema_version'",
                    (str(SCHEMA_VERSION),),
                )
        except BaseException:
            db.close()
            raise
        return db

    @property
    def available(self) -> bool:
        return self._db is not None

    def _conn(self) -> sqlite3.Connection:
        if self._db is None:
            raise StorageUnavailable(self.error or "storage unavailable")
        return self._db

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None

    # ------------------------------------------------------------------ profiles

    def create_profile(self, name: str) -> tuple[str, Profile]:
        """A new profile and its secret key (returned once, stored only as a hash)."""
        token = secrets.token_urlsafe(32)
        public_id = secrets.token_hex(8)
        now = time.time()
        with self._lock:
            db = self._conn()
            cur = db.execute(
                "INSERT INTO profiles(public_id, token_hash, name, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (public_id, hash_token(token), name, now, now),
            )
            pid = int(cur.lastrowid or 0)
        return token, Profile(pid, public_id, name, now, now)

    def profile(self, token: str) -> Profile:
        with self._lock:
            row = (
                self._conn()
                .execute(
                    "SELECT id, public_id, name, created_at, updated_at FROM profiles"
                    " WHERE token_hash = ?",
                    (hash_token(token),),
                )
                .fetchone()
            )
        if row is None:
            raise ProfileNotFound("Unknown profile key")
        return Profile(*row)

    def rename(self, profile: Profile, name: str) -> Profile:
        now = time.time()
        with self._lock:
            self._conn().execute(
                "UPDATE profiles SET name = ?, updated_at = ? WHERE id = ?",
                (name, now, profile.id),
            )
        return Profile(profile.id, profile.public_id, name, profile.created_at, now)

    def delete_profile(self, profile: Profile) -> None:
        """Delete the profile and every record in it (records cascade)."""
        with self._lock:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                for table in (*TABLES, "hands"):
                    db.execute(f"DELETE FROM {table} WHERE profile_id = ?", (profile.id,))  # noqa: S608
                db.execute("DELETE FROM profiles WHERE id = ?", (profile.id,))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def counts(self, profile: Profile) -> dict[str, int]:
        with self._lock:
            db = self._conn()
            return {
                t: int(
                    db.execute(
                        f"SELECT COUNT(*) FROM {t} WHERE profile_id = ?",  # noqa: S608
                        (profile.id,),
                    ).fetchone()[0]
                )
                for t in (*TABLES, "hands")
            }

    # ------------------------------------------------------------------ records

    def add(self, profile: Profile, records: dict[str, Sequence[dict[str, Any]]]) -> dict[str, int]:
        """Insert records (already validated) in one transaction; existing ids are skipped.

        Returns how many were new per table. Either every record is stored or none is.
        """
        added: dict[str, int] = {}
        with self._lock:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                for table in TABLES:
                    rows = records.get(table, ())
                    if not rows:
                        added[table] = 0
                        continue
                    have = db.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE profile_id = ?",  # noqa: S608
                        (profile.id,),
                    ).fetchone()[0]
                    if have + len(rows) > MAX_RECORDS_PER_PROFILE:
                        raise QuotaExceeded(f"Too many {table} for one profile")
                    before = db.total_changes
                    db.executemany(
                        f"INSERT OR IGNORE INTO {table}(profile_id, id, t, data)"  # noqa: S608
                        " VALUES (?, ?, ?, ?)",
                        self._rows(profile.id, rows),
                    )
                    added[table] = db.total_changes - before
                db.execute(
                    "UPDATE profiles SET updated_at = ? WHERE id = ?", (time.time(), profile.id)
                )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return added

    @staticmethod
    def _rows(pid: int, rows: Iterable[dict[str, Any]]) -> Iterable[tuple[Any, ...]]:
        for r in rows:
            yield (
                pid,
                r["id"],
                float(r["t"]),
                json.dumps(r, separators=(",", ":"), sort_keys=True),
            )

    def records(self, profile: Profile, limit: int) -> dict[str, list[dict[str, Any]]]:
        """The most recent ``limit`` records of each kind, oldest first."""
        out: dict[str, list[dict[str, Any]]] = {}
        with self._lock:
            db = self._conn()
            for table in TABLES:
                rows = db.execute(
                    f"SELECT data FROM {table} WHERE profile_id = ?"  # noqa: S608
                    " ORDER BY t DESC, id DESC LIMIT ?",
                    (profile.id, limit),
                ).fetchall()
                out[table] = [json.loads(r[0]) for r in reversed(rows)]
        return out

    # ------------------------------------------------------------------ hand histories

    def add_hands(self, profile: Profile, hands: Sequence[tuple[dict[str, Any], dict]]) -> int:
        """Insert validated ``(record, summary)`` pairs in one transaction; repeats are
        skipped. Returns how many were new."""
        if not hands:
            return 0
        with self._lock:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                have = db.execute(
                    "SELECT COUNT(*) FROM hands WHERE profile_id = ?", (profile.id,)
                ).fetchone()[0]
                if have + len(hands) > MAX_HANDS_PER_PROFILE:
                    raise QuotaExceeded("Too many hands for one profile")
                before = db.total_changes
                db.executemany(
                    "INSERT OR IGNORE INTO hands(profile_id, id, t, data, summary)"
                    " VALUES (?, ?, ?, ?, ?)",
                    [
                        (
                            profile.id,
                            r["id"],
                            float(r["t"]),
                            json.dumps(r, separators=(",", ":"), sort_keys=True),
                            json.dumps(s, separators=(",", ":"), sort_keys=True),
                        )
                        for r, s in hands
                    ],
                )
                added = db.total_changes - before
                db.execute(
                    "UPDATE profiles SET updated_at = ? WHERE id = ?", (time.time(), profile.id)
                )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return added

    def hand_summaries(self, profile: Profile, limit: int) -> list[dict[str, Any]]:
        """Summaries of the most recent ``limit`` hands, newest first. A row that cannot
        be read is skipped (and logged) rather than failing the whole list."""
        with self._lock:
            rows = (
                self._conn()
                .execute(
                    "SELECT id, summary FROM hands WHERE profile_id = ?"
                    " ORDER BY t DESC, id DESC LIMIT ?",
                    (profile.id, limit),
                )
                .fetchall()
            )
        out = []
        for hid, raw in rows:
            try:
                out.append(json.loads(raw))
            except ValueError:
                log.warning("Unreadable hand summary %s skipped", hid)
        return out

    def hand(self, profile: Profile, hand_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = (
                self._conn()
                .execute(
                    "SELECT data FROM hands WHERE profile_id = ? AND id = ?",
                    (profile.id, hand_id),
                )
                .fetchone()
            )
        return None if row is None else json.loads(row[0])

    def update_summaries(self, profile: Profile, summaries: dict[str, dict[str, Any]]) -> None:
        """Replace stored hand summaries (a summary format upgrade; records untouched)."""
        if not summaries:
            return
        with self._lock:
            db = self._conn()
            db.execute("BEGIN IMMEDIATE")
            try:
                db.executemany(
                    "UPDATE hands SET summary = ? WHERE profile_id = ? AND id = ?",
                    [
                        (json.dumps(s, separators=(",", ":"), sort_keys=True), profile.id, hid)
                        for hid, s in summaries.items()
                    ],
                )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def focused_answers(self, profile: Profile, limit: int = 20_000) -> list[dict[str, Any]]:
        """Training answers aimed at a leak (v0.4.4 ``leak`` field), oldest first."""
        with self._lock:
            rows = (
                self._conn()
                .execute(
                    "SELECT data FROM answers WHERE profile_id = ?"
                    " AND json_extract(data, '$.leak') IS NOT NULL"
                    " ORDER BY t DESC, id DESC LIMIT ?",
                    (profile.id, limit),
                )
                .fetchall()
            )
        return [json.loads(r[0]) for r in reversed(rows)]
