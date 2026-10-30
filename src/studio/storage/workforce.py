"""Durable, project-scoped database transaction boundary for browser workforce.

The coordinator owns this connection; worker conversations never connect directly.
Production uses APSW. SQLite DB-API connections may be injected for reference tests.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
from threading import RLock
from typing import Any, Callable

from studio.workforce import WorkforceFault
from .errors import Unavailable
from .schema import create_new_database, ensure_existing_database


def _canonical(result: dict) -> str:
    return json.dumps(result, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


class WorkforceTransaction:
    """Bound SQL executor; only trusted server-side workforce commands receive it."""

    def __init__(self, connection: Any):
        self._connection = connection

    def one(self, sql: str, params: tuple = ()) -> dict | None:
        cursor = self._connection.execute(sql, params)
        columns = [col[0] for col in cursor.getdescription()] if hasattr(cursor, 'getdescription') else [col[0] for col in cursor.description]
        row = cursor.fetchone()
        return dict(zip(columns, row)) if row is not None else None

    def all(self, sql: str, params: tuple = ()) -> list[dict]:
        cursor = self._connection.execute(sql, params)
        columns = [col[0] for col in cursor.getdescription()] if hasattr(cursor, 'getdescription') else [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor]

    def change(self, sql: str, params: tuple = ()) -> int:
        before = self._connection.total_changes if hasattr(self._connection, 'total_changes') else self._connection.totalchanges()
        self._connection.execute(sql, params)
        after = self._connection.total_changes if hasattr(self._connection, 'total_changes') else self._connection.totalchanges()
        return after - before


class WorkforceStorage:
    """Single-writer, atomic command results and payload-bound retry protection.

    A complete command and its idempotency result commit together. Remote writes
    happen later from the durable outbox. Unknown external effects must be
    reconciled by the existing workforce service, never replayed here.
    """

    def __init__(self, connection: Any):
        self._connection = connection
        self._lock = RLock()

    @classmethod
    def open(cls, path: str | Path, *, fresh: bool = False) -> 'WorkforceStorage':
        try:
            import apsw
        except ImportError as exc:
            raise Unavailable('APSW is required for the production workforce store') from exc
        connection = apsw.Connection(str(path))
        try:
            connection.setbusytimeout(5000)
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('PRAGMA synchronous=FULL')
            if fresh:
                create_new_database(connection)
            else:
                ensure_existing_database(connection)
            return cls(connection)
        except BaseException:
            connection.close()
            raise

    @contextmanager
    def _transaction(self, *, write: bool):
        with self._lock:
            self._connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            try:
                yield WorkforceTransaction(self._connection)
                self._connection.execute('COMMIT')
            except BaseException:
                self._connection.execute('ROLLBACK')
                raise

    def read(self, project_id: str, fn: Callable[[WorkforceTransaction], Any]) -> Any:
        if not isinstance(project_id, str) or not project_id:
            raise WorkforceFault('invalid', 'project ID required')
        with self._transaction(write=False) as tx:
            return fn(tx)

    def command(self, project_id: str, operation: str, request_id: str,
                payload_sha256: str, fn: Callable[[WorkforceTransaction], dict]) -> dict:
        if (not all(isinstance(x, str) and x for x in (project_id, operation, request_id)) or
                not isinstance(payload_sha256, str) or len(payload_sha256) != 64 or
                any(x not in '0123456789abcdef' for x in payload_sha256)):
            raise WorkforceFault('invalid', 'invalid idempotency command identity')
        with self._transaction(write=True) as tx:
            previous = tx.one('''SELECT payload_digest,status,result_digest FROM idempotency
                WHERE project_id=? AND action_owner=? AND request_id=?''',
                (project_id, operation, request_id))
            if previous is not None:
                if previous['payload_digest'] != payload_sha256:
                    raise WorkforceFault('conflict', 'request ID reused with different content')
                if previous['status'] != 'finished':
                    raise WorkforceFault('unknown', 'prior command not conclusively committed')
                result = tx.one('''SELECT result_json FROM workforce_command_results
                    WHERE project_id=? AND action_owner=? AND request_id=?''',
                    (project_id, operation, request_id))
                if result is None or hashlib.sha256(result['result_json'].encode('utf-8')).hexdigest() != previous['result_digest']:
                    raise WorkforceFault('unknown', 'idempotency result bytes unavailable or altered')
                return json.loads(result['result_json'])
            tx.change('''INSERT INTO idempotency(project_id,action_owner,request_id,payload_digest,status)
                VALUES (?,?,?,?,'started')''', (project_id, operation, request_id, payload_sha256))
            result = fn(tx)
            if not isinstance(result, dict):
                raise WorkforceFault('invalid', 'workforce transaction must return an object')
            encoded = _canonical(result)
            result_digest = hashlib.sha256(encoded.encode('utf-8')).hexdigest()
            tx.change('''INSERT INTO workforce_command_results(project_id,action_owner,request_id,result_json)
                VALUES (?,?,?,?)''', (project_id, operation, request_id, encoded))
            tx.change('''UPDATE idempotency SET status='finished',result_digest=?
                WHERE project_id=? AND action_owner=? AND request_id=?''',
                (result_digest, project_id, operation, request_id))
            return result

    def close(self) -> None:
        with self._lock:
            self._connection.close()
