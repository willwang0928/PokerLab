"""In-memory session store (bounded, thread-safe). Swappable for persistence later."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict

from ..session import HeadsUpSession, SessionStatus


class SessionStore:
    """
    Keeps at most ``max_sessions`` games. Games idle for longer than ``idle_ttl`` seconds
    are dropped; when full, finished games go before live ones, oldest first.
    """

    def __init__(self, max_sessions: int = 500, idle_ttl: float = 6 * 3600):
        self._sessions: OrderedDict[str, HeadsUpSession] = OrderedDict()
        self._lock = threading.Lock()
        self.max_sessions = max_sessions
        self.idle_ttl = idle_ttl

    def add(self, session: HeadsUpSession) -> None:
        with self._lock:
            now = time.monotonic()
            idle = [s for s, v in self._sessions.items() if now - v.last_active > self.idle_ttl]
            for sid in idle:
                del self._sessions[sid]
            self._sessions[session.id] = session
            self._sessions.move_to_end(session.id)
            while len(self._sessions) > self.max_sessions:
                finished = next(
                    (s for s, v in self._sessions.items() if v.status == SessionStatus.GAME_OVER),
                    None,
                )
                if finished is not None:
                    del self._sessions[finished]
                else:
                    self._sessions.popitem(last=False)

    def get(self, session_id: str) -> HeadsUpSession | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                if time.monotonic() - session.last_active > self.idle_ttl:
                    del self._sessions[session_id]
                    return None
                session.last_active = time.monotonic()
                self._sessions.move_to_end(session_id)
            return session

    def remove(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)
