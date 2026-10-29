"""Local, isolated journal ports. NEVER a connection to the Studio SQLite owner.

Records contain opaque references, hashes and policy metadata only. Confidential
bytes must reside behind a separately authenticated encrypted custody port.
"""
from __future__ import annotations

from dataclasses import dataclass
import copy
import hashlib
import json
import sqlite3
import threading
from typing import Any, Protocol


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode()).hexdigest()


def clone(value: Any) -> Any:
    return copy.deepcopy(value)


class Conflict(Exception):
    """Optimistic revision, event-id collision, or idempotency collision."""


@dataclass(frozen=True)
class Receipt:
    event_id: str
    fingerprint: str
    value: dict[str, Any]


class Journal(Protocol):
    def get(self, namespace: str, key: str) -> tuple[int, dict[str, Any] | None]: ...
    def receipt(self, event_id: str, fingerprint: str) -> Receipt | None: ...
    def commit(self, namespace: str, key: str, expected_revision: int,
               record: dict[str, Any], event_id: str, fingerprint: str,
               response: dict[str, Any]) -> bool: ...
    def audit(self, namespace: str, key: str) -> tuple[dict[str, Any], ...]: ...


class MemoryJournal:
    """Deterministic test/adapter journal; not persistent across process death."""
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
        self._receipts: dict[str, Receipt] = {}
        self._audit: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def get(self, namespace: str, key: str) -> tuple[int, dict[str, Any] | None]:
        with self._lock:
            item = self._records.get((namespace, key))
            return (item[0], clone(item[1])) if item else (0, None)

    def receipt(self, event_id: str, fingerprint: str) -> Receipt | None:
        with self._lock:
            result = self._receipts.get(event_id)
            if result and result.fingerprint != fingerprint:
                raise Conflict('event ID reused with different contents')
            return clone(result)

    def commit(self, namespace: str, key: str, expected_revision: int,
               record: dict[str, Any], event_id: str, fingerprint: str,
               response: dict[str, Any]) -> bool:
        with self._lock:
            existing = self._receipts.get(event_id)
            if existing:
                if existing.fingerprint != fingerprint:
                    raise Conflict('event ID reused with different contents')
                return False
            revision, _ = self.get(namespace, key)
            if revision != expected_revision:
                raise Conflict('stale revision')
            self._records[(namespace, key)] = (revision + 1, clone(record))
            self._receipts[event_id] = Receipt(event_id, fingerprint, clone(response))
            self._audit.setdefault((namespace, key), []).append({
                'event_id': event_id, 'revision': revision + 1,
                'record_digest': digest(record), 'request_digest': fingerprint})
            return True

    def audit(self, namespace: str, key: str) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(clone(self._audit.get((namespace, key), [])))


class SQLiteJournal:
    """Opt-in durable metadata journal. Caller supplies its own private path.

    No schema installation, DB file or external effect occurs until constructed.
    SQLite transaction atomically advances CAS, idempotency receipt and audit.
    Production confidentiality, filesystem encryption and backup are P2 gates.
    """
    def __init__(self, private_path: str) -> None:
        if not private_path or private_path == ':memory:':
            raise ValueError('explicit private persistence path required')
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(private_path, check_same_thread=False,
                                           isolation_level=None, timeout=10)
        self._connection.execute('PRAGMA busy_timeout=10000')
        self._connection.executescript('''
            CREATE TABLE IF NOT EXISTS hr_records (
                namespace TEXT NOT NULL, record_key TEXT NOT NULL,
                revision INTEGER NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY(namespace, record_key));
            CREATE TABLE IF NOT EXISTS hr_receipts (
                event_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                response TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS hr_audit (
                namespace TEXT NOT NULL, record_key TEXT NOT NULL,
                revision INTEGER NOT NULL, event_id TEXT NOT NULL UNIQUE,
                record_digest TEXT NOT NULL, request_digest TEXT NOT NULL,
                PRIMARY KEY(namespace, record_key, revision));
        ''')

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def get(self, namespace: str, key: str) -> tuple[int, dict[str, Any] | None]:
        with self._lock:
            row = self._connection.execute(
                'SELECT revision, body FROM hr_records WHERE namespace=? AND record_key=?',
                (namespace, key)).fetchone()
            return (row[0], json.loads(row[1])) if row else (0, None)

    def receipt(self, event_id: str, fingerprint: str) -> Receipt | None:
        with self._lock:
            row = self._connection.execute(
                'SELECT fingerprint, response FROM hr_receipts WHERE event_id=?',
                (event_id,)).fetchone()
            if row and row[0] != fingerprint:
                raise Conflict('event ID reused with different contents')
            return Receipt(event_id, row[0], json.loads(row[1])) if row else None

    def commit(self, namespace: str, key: str, expected_revision: int,
               record: dict[str, Any], event_id: str, fingerprint: str,
               response: dict[str, Any]) -> bool:
        with self._lock:
            db = self._connection
            db.execute('BEGIN IMMEDIATE')
            try:
                old = db.execute('SELECT fingerprint FROM hr_receipts WHERE event_id=?',
                                 (event_id,)).fetchone()
                if old:
                    if old[0] != fingerprint:
                        raise Conflict('event ID reused with different contents')
                    db.execute('ROLLBACK')
                    return False
                old = db.execute('SELECT revision FROM hr_records WHERE namespace=? AND record_key=?',
                                 (namespace, key)).fetchone()
                previous = old[0] if old else 0
                if previous != expected_revision:
                    raise Conflict('stale revision')
                body = json.dumps(record, sort_keys=True, separators=(',', ':'))
                db.execute('INSERT INTO hr_records(namespace,record_key,revision,body) '
                           'VALUES(?,?,?,?) ON CONFLICT(namespace,record_key) '
                           'DO UPDATE SET revision=excluded.revision,body=excluded.body',
                           (namespace, key, previous + 1, body))
                db.execute('INSERT INTO hr_receipts(event_id,fingerprint,response) VALUES(?,?,?)',
                           (event_id, fingerprint, json.dumps(response, sort_keys=True)))
                db.execute('INSERT INTO hr_audit(namespace,record_key,revision,event_id,record_digest,request_digest) '
                           'VALUES(?,?,?,?,?,?)',
                           (namespace, key, previous + 1, event_id, digest(record), fingerprint))
                db.execute('COMMIT')
                return True
            except Exception:
                if db.in_transaction:
                    db.execute('ROLLBACK')
                raise

    def audit(self, namespace: str, key: str) -> tuple[dict[str, Any], ...]:
        with self._lock:
            rows = self._connection.execute(
                'SELECT revision,event_id,record_digest,request_digest FROM hr_audit '
                'WHERE namespace=? AND record_key=? ORDER BY revision',
                (namespace, key)).fetchall()
            return tuple({'revision': r, 'event_id': e, 'record_digest': d,
                          'request_digest': f} for r, e, d, f in rows)
