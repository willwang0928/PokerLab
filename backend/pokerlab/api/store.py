"""In-memory session store (bounded, thread-safe). Swappable for persistence later."""

from __future__ import annotations

import threading
from collections import OrderedDict

from ..session import HeadsUpSession


class SessionStore:
    def __init__(self, max_sessions: int = 500):
        self._sessions: OrderedDict[str, HeadsUpSession] = OrderedDict()
        self._lock = threading.Lock()
        self.max_sessions = max_sessions

    def add(self, session: HeadsUpSession) -> None:
        with self._lock:
            self._sessions[session.id] = session
            self._sessions.move_to_end(session.id)
            while len(self._sessions) > self.max_sessions:
                self._sessions.popitem(last=False)

    def get(self, session_id: str) -> HeadsUpSession | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                self._sessions.move_to_end(session_id)
            return session

    def remove(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def __len__(self) -> int:
        return len(self._sessions)
