"""Explicitly TEST-ONLY volatile store. P2 must inject encrypted AK private custody."""
from __future__ import annotations
from threading import RLock
from .models import Session

class TestOnlyMemoryStore:
    """Synthetic, nonencrypted; prohibited for real confidential case use."""
    test_only = True
    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._request_keys: dict[str, str] = {}
        self._lock = RLock()

    def create(self, session: Session) -> tuple[str, Session | None]:
        with self._lock:
            prior_id = self._request_keys.get(session.request_id)
            if prior_id is not None:
                prior = self._sessions[prior_id]
                if prior.request_fingerprint != session.request_fingerprint:
                    return 'CONFLICT', prior
                return 'REPLAY', prior
            if session.session_id in self._sessions:
                return 'CONFLICT', self._sessions[session.session_id]
            self._sessions[session.session_id] = session
            self._request_keys[session.request_id] = session.session_id
            return 'OK', session

    def read(self, session_id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(session_id)

    def update(self, previous_revision: int, changed: Session) -> bool:
        with self._lock:
            old = self._sessions.get(changed.session_id)
            if old is None or old.revision != previous_revision or changed.revision != previous_revision + 1:
                return False
            self._sessions[changed.session_id] = changed
            return True
