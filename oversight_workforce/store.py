"""Isolated Oversight-only local journal; receipts, not private case narratives.

Production custody remains behind the private repository's injected encrypted store.
This implementation is suitable for pure evaluation and load fixtures only.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, Optional, Tuple


def canonical(value: Any) -> str:
    def encode(v: Any) -> Any:
        if isinstance(v, Enum):
            return v.value
        if is_dataclass(v):
            return encode(asdict(v))
        if isinstance(v, dict):
            return {k: encode(x) for k, x in sorted(v.items())}
        if isinstance(v, (list, tuple, set, frozenset)):
            items = [encode(x) for x in v]
            return sorted(items) if isinstance(v, (set, frozenset)) else items
        return v
    return json.dumps(encode(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


class LocalJournal:
    """Atomic replay-safe recommendation + work-event journal; no external IO by default."""

    def __init__(self, path: str = ":memory:") -> None:
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._lock = threading.RLock()
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS recommendations (
                request_id TEXT PRIMARY KEY, request_digest TEXT NOT NULL,
                worker_id TEXT NOT NULL, decision_json TEXT NOT NULL,
                created_epoch INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_recommendation_worker
              ON recommendations(worker_id, created_epoch);
            CREATE TABLE IF NOT EXISTS work_events (
                event_id TEXT PRIMARY KEY, event_digest TEXT NOT NULL,
                worker_id TEXT NOT NULL, received_at INTEGER NOT NULL,
                state TEXT NOT NULL DEFAULT 'PENDING' CHECK(state IN ('PENDING','ACKED'))
            );
            CREATE INDEX IF NOT EXISTS idx_events_worker ON work_events(worker_id);
            CREATE TABLE IF NOT EXISTS review_intake (
                intake_id TEXT PRIMARY KEY, content_digest TEXT NOT NULL,
                intake_json TEXT NOT NULL, priority INTEGER NOT NULL,
                received_at INTEGER NOT NULL
            );
        """)

    def find_recommendation(self, request_id: str) -> Optional[Tuple[str, dict]]:
        with self._lock:
            row = self._db.execute("SELECT request_digest,decision_json FROM recommendations WHERE request_id=?", (request_id,)).fetchone()
            return (row[0], json.loads(row[1])) if row else None

    def write_recommendation(self, request_id: str, request_digest: str, worker_id: str,
                             decision: Any, now: int) -> bool:
        with self._lock:
            cur = self._db.execute("INSERT OR IGNORE INTO recommendations VALUES(?,?,?,?,?)",
                                   (request_id, request_digest, worker_id, canonical(decision), now))
            return cur.rowcount == 1

    def history(self, worker_id: str) -> Tuple[dict, ...]:
        with self._lock:
            rows = self._db.execute("SELECT decision_json FROM recommendations WHERE worker_id=? ORDER BY created_epoch,request_id", (worker_id,)).fetchall()
            return tuple(json.loads(r[0]) for r in rows)

    def record_work_event(self, event_id: str, worker_id: str, event_digest: str, now: int) -> str:
        with self._lock:
            row = self._db.execute("SELECT event_digest FROM work_events WHERE event_id=?", (event_id,)).fetchone()
            if row:
                return "DUPLICATE" if row[0] == event_digest else "DENIED_REPLAY_CONFLICT"
            self._db.execute("INSERT INTO work_events(event_id,event_digest,worker_id,received_at) VALUES(?,?,?,?)", (event_id, event_digest, worker_id, now))
            return "RECORDED"

    def record_intake(self, intake_id: str, content_digest: str, payload: Any, priority: int, now: int) -> str:
        with self._lock:
            row = self._db.execute("SELECT content_digest FROM review_intake WHERE intake_id=?", (intake_id,)).fetchone()
            if row:
                return "DUPLICATE" if row[0] == content_digest else "DENIED_REPLAY_CONFLICT"
            self._db.execute("INSERT INTO review_intake VALUES(?,?,?,?,?)", (intake_id, content_digest, canonical(payload), priority, now))
            return "RECORDED"

    def intake_rows(self) -> Tuple[dict, ...]:
        with self._lock:
            rows = self._db.execute("SELECT intake_json FROM review_intake ORDER BY priority DESC,received_at,intake_id").fetchall()
            return tuple(json.loads(row[0]) for row in rows)

    def pending_work_events(self, limit: int) -> Tuple[Tuple[str, str], ...]:
        if limit <= 0:
            return ()
        with self._lock:
            return tuple(self._db.execute(
                "SELECT event_id, worker_id FROM work_events WHERE state='PENDING' "
                "ORDER BY received_at,event_id LIMIT ?", (limit,)).fetchall())

    def acknowledge_work_event(self, event_id: str, digest_value: str) -> str:
        """Local durable cursor update only, with exact event digest binding."""
        with self._lock:
            row = self._db.execute("SELECT event_digest,state FROM work_events WHERE event_id=?", (event_id,)).fetchone()
            if row is None:
                return 'NOT_FOUND'
            if row[0] != digest_value:
                return 'DENIED_DIGEST_MISMATCH'
            if row[1] == 'ACKED':
                return 'DUPLICATE'
            self._db.execute("UPDATE work_events SET state='ACKED' WHERE event_id=?", (event_id,))
            return 'ACKED'

    def count_events(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM work_events").fetchone()[0]

    def count_workers(self) -> int:
        return self._db.execute("SELECT COUNT(DISTINCT worker_id) FROM work_events").fetchone()[0]

    def database_bytes(self) -> int:
        return self._db.execute("PRAGMA page_count").fetchone()[0] * self._db.execute("PRAGMA page_size").fetchone()[0]

    def close(self) -> None:
        self._db.close()
