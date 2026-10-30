"""P2-owned, fresh-only R9 SQLite authority and exact-table writer capabilities.

Production services receive only `capability(CAB_X)` objects. All service SQL
runs against a private SQLite connection with an active per-table authorizer.
P2 outbox inserts are privileged *inside* the same serial transaction, never
by granting a domain service access to another owner's table.
"""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence
import json
import os
import sqlite3
import stat
import threading
import time
from urllib.parse import quote

SQL_SHA256 = 'f2a76f51fef851fe972500c906f31cde84bf63e446a4a149ab6ac08c9fe410f5'
# The external contract fixes the owner manifest independently of package data.
OWNERS_SHA256 = '653179312fe2e9c0f9e8a8b2a0af3736bfa7990e57508400ce87b627e21a60ff'
APPLICATION_ID = 0x4e4d4439
SCHEMA_VERSION = 1
_WRITE_ACTIONS = frozenset((sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE))
_BLOCK_ACTIONS = frozenset(getattr(sqlite3, 'SQLITE_' + name) for name in (
    'CREATE_INDEX', 'CREATE_TABLE', 'CREATE_TEMP_INDEX', 'CREATE_TEMP_TABLE',
    'CREATE_TEMP_TRIGGER', 'CREATE_TEMP_VIEW', 'CREATE_TRIGGER', 'CREATE_VIEW',
    'DROP_INDEX', 'DROP_TABLE', 'DROP_TEMP_INDEX', 'DROP_TEMP_TABLE',
    'DROP_TEMP_TRIGGER', 'DROP_TEMP_VIEW', 'DROP_TRIGGER', 'DROP_VIEW',
    'ALTER_TABLE', 'ATTACH', 'DETACH', 'PRAGMA', 'TRANSACTION', 'SAVEPOINT',
    'REINDEX', 'ANALYZE') if hasattr(sqlite3, 'SQLITE_' + name))


class AuthorityError(RuntimeError):
    """Invalid schema, owner capability, transaction, or persistent authority."""


class OwnerLocked(AuthorityError):
    pass


class WriterDenied(AuthorityError):
    pass


class IdempotencyConflict(AuthorityError):
    pass


def _now_us() -> int:
    return time.time_ns() // 1000


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _digest(value: str | bytes) -> str:
    return sha256(value.encode('utf-8') if isinstance(value, str) else value).hexdigest()


def _resources() -> tuple[str, dict[str, str]]:
    root = files('nomad_studio.state')
    sql = root.joinpath('r9_initial_schema.sql').read_bytes()
    owners = root.joinpath('r9_table_owners.json').read_bytes()
    if _digest(sql) != SQL_SHA256 or _digest(owners) != OWNERS_SHA256:
        raise AuthorityError('frozen R9 SQL or table ownership digest mismatch')
    parsed = json.loads(owners)
    table_owners = parsed.get('owner_per_table')
    if (not isinstance(table_owners, dict) or len(table_owners) != 56 or
            not all(isinstance(k, str) and k.startswith('v1_') and v in
                    {'CAB-A','CAB-B','CAB-C','CAB-D','CAB-E','CAB-F','CAB-G','CAB-H','CAB-I','CAB-J','P2'}
                    for k, v in table_owners.items())):
        raise AuthorityError('invalid frozen R9 owner manifest')
    return sql.decode('utf-8'), table_owners


def _objects(conn: sqlite3.Connection) -> dict[str, str]:
    return {str(name): str(sql) for name, sql in conn.execute(
        "SELECT name,sql FROM sqlite_master WHERE type IN ('table','view','index','trigger') "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")}


def _expected_objects(ddl: str) -> dict[str, str]:
    mem = sqlite3.connect(':memory:')
    try:
        mem.executescript(ddl)
        return _objects(mem)
    finally:
        mem.close()


def _verify_install(conn: sqlite3.Connection, ddl: str, owners: Mapping[str, str]) -> None:
    expected = _expected_objects(ddl)
    if {name for name in expected if name.startswith('v1_') and name in owners} != set(owners):
        raise AuthorityError('frozen table ownership does not match schema')
    existing = _objects(conn)
    if not existing:
        # A nonempty identity in an otherwise empty database belongs to a
        # different application. Never silently overwrite its provenance.
        if (conn.execute('PRAGMA application_id').fetchone()[0] != 0 or
                conn.execute('PRAGMA user_version').fetchone()[0] != 0):
            raise AuthorityError('foreign database identity: R9 installs only from an unclaimed empty file')
        # executescript implicitly commits before running; include full install
        # in a single explicit transaction, including the schema identity.
        trimmed = ddl.replace('PRAGMA foreign_keys=ON;', '', 1)
        try:
            conn.executescript('BEGIN IMMEDIATE;\n' + trimmed + '\n' +
                               f'PRAGMA application_id={APPLICATION_ID};\n'
                               f'PRAGMA user_version={SCHEMA_VERSION};\nCOMMIT;')
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        existing = _objects(conn)
    if existing != expected:
        raise AuthorityError('R9 schema objects mismatch: no migration or legacy schema is accepted')
    if conn.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID:
        raise AuthorityError('R9 application identity mismatch')
    if conn.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION:
        raise AuthorityError('R9 schema version mismatch')
    if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
        raise AuthorityError('R9 SQLite integrity check failed')
    if conn.execute('PRAGMA foreign_key_check').fetchone() is not None:
        raise AuthorityError('R9 SQLite foreign key check failed')


class _ProcessLock:
    def __init__(self, path: Path):
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, 'O_NOFOLLOW'):
            flags |= os.O_NOFOLLOW
        try:
            self.fd = os.open(path, flags, 0o600)
            if os.name == 'nt':
                import msvcrt
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if os.name == 'posix' and stat.S_IMODE(os.fstat(self.fd).st_mode) & 0o077:
                raise AuthorityError('authority lock is not private')
        except Exception as exc:
            if hasattr(self, 'fd'):
                os.close(self.fd)
                del self.fd
            if isinstance(exc, (BlockingIOError, OSError)):
                raise OwnerLocked('SQLite authority owner is already active or inaccessible') from exc
            raise

    def close(self) -> None:
        fd = getattr(self, 'fd', None)
        if fd is None:
            return
        if os.name == 'nt':
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        del self.fd


class _CursorProxy:
    """Cursor operations are valid only while the scoped view remains active.

    SQLite cursors expose `.connection`, so passing an unwrapped cursor to a
    service would allow it to retain the private connection after its owner
    capability ended. Do not expose the native cursor.
    """
    __slots__ = ('__cursor', '__view')

    def __init__(self, cursor: sqlite3.Cursor, view: '_View'):
        self.__cursor, self.__view = cursor, view

    def fetchone(self) -> Any:
        self.__view._check()
        return self.__cursor.fetchone()

    def fetchall(self) -> list[Any]:
        self.__view._check()
        return self.__cursor.fetchall()

    def fetchmany(self, size: int = 1) -> list[Any]:
        self.__view._check()
        return self.__cursor.fetchmany(size)

    @property
    def rowcount(self) -> int:
        self.__view._check()
        return self.__cursor.rowcount

    @property
    def description(self) -> Any:
        self.__view._check()
        return self.__cursor.description


class _View:
    """No raw SQLite connection is supplied to a domain service."""
    __slots__ = ('__authority', '__valid')

    def __init__(self, state: 'R9Authority'):
        self.__authority = state
        self.__valid = True

    def _check(self) -> None:
        if not self.__valid:
            raise AuthorityError('transaction view used after its lifetime')

    def _invalidate(self) -> None:
        self.__valid = False

    def execute(self, sql: str, params: Sequence[Any] = ()) -> _CursorProxy:
        self._check()
        if not isinstance(sql, str) or not sql.strip():
            raise WriterDenied('only SQL statements are permitted')
        try:
            return _CursorProxy(self.__authority._conn.execute(sql, tuple(params)), self)
        except sqlite3.DatabaseError as exc:
            if 'not authorized' in str(exc).lower():
                raise WriterDenied('SQL action exceeds injected table capability') from exc
            raise

    def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> Mapping[str, Any] | None:
        row = self.execute(sql, params).fetchone()
        return dict(row) if row is not None else None

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[Mapping[str, Any]]:
        return [dict(row) for row in self.execute(sql, params).fetchall()]


class ScopedWriter:
    """Exact owner-bound write capability with service-specific method shapes."""
    __slots__ = ('__authority', 'owner')

    def __init__(self, authority: 'R9Authority', owner: str):
        self.__authority = authority
        self.owner = owner

    @contextmanager
    def transaction(self) -> Iterator[_View]:
        with self.__authority._transaction(self.owner) as view:
            yield view

    def write(self, callback: Callable[[_View], Any]) -> Any:
        with self.transaction() as view:
            return callback(view)

    def transact(self, callback: Callable[[_View], Any]) -> Any:
        return self.write(callback)

    def write_owned(self, callback: Callable[[_View], Any]) -> Any:
        return self.write(callback)

    def read(self, operation: Callable[[_View], Any] | str,
             params: Sequence[Any] = ()) -> Any:
        # CAB-I's frozen SQL reader takes (sql, params); the other CABs use
        # callback-based reads. Both are read-only on the same scoped view.
        if callable(operation):
            if params:
                raise TypeError('callback read does not accept SQL parameters')
            return self.__authority._read(operation)
        if not isinstance(operation, str):
            raise TypeError('reader requires a callback or SQL query')
        return self.__authority._read(lambda view: view.fetch_all(operation, params))

    def read_one(self, table: str, condition: str, params: Sequence[Any] = ()) -> Mapping[str, Any] | None:
        if not isinstance(table, str) or table not in self.__authority.table_owners:
            raise WriterDenied('unrecognized R9 table')
        if not isinstance(condition, str) or ';' in condition or '--' in condition or '/*' in condition:
            raise WriterDenied('invalid bounded query predicate')
        return self.read(lambda view: view.fetch_one(f'SELECT * FROM "{table}" WHERE {condition}', params))


class R9Authority:
    """Only in-process state authority. Never inject this object into a service."""

    def __init__(self, path: str | Path):
        source = Path(path).expanduser()
        if source.is_symlink():
            raise AuthorityError('SQLite path cannot be a symlink')
        directory = source.absolute().parent
        if directory.is_symlink():
            raise AuthorityError('SQLite authority directory cannot be a symlink')
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        if os.name == 'posix' and (directory.stat().st_uid != os.getuid() or
                                   stat.S_IMODE(directory.stat().st_mode) & 0o077):
            raise AuthorityError('SQLite authority directory must be private (0700)')
        self.path = source.absolute()
        self._mutex = threading.RLock()
        self._active_owner: str | None = None
        self._closed = False
        self._conn: sqlite3.Connection | None = None
        self._lock = _ProcessLock(directory / (source.name + '.owner.lock'))
        try:
            if self.path.exists():
                if not self.path.is_file() or self.path.is_symlink():
                    raise AuthorityError('invalid authority database path')
                if os.name == 'posix' and stat.S_IMODE(self.path.stat().st_mode) & 0o077:
                    raise AuthorityError('SQLite DB permissions must be private')
            else:
                flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
                if hasattr(os, 'O_NOFOLLOW'):
                    flags |= os.O_NOFOLLOW
                fd = os.open(self.path, flags, 0o600)
                os.close(fd)
            self._conn = sqlite3.connect(str(self.path), check_same_thread=False,
                                         isolation_level=None, timeout=5.0,
                                         cached_statements=0)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute('PRAGMA foreign_keys=ON')
            self._conn.execute('PRAGMA busy_timeout=5000')
            ddl, self.table_owners = _resources()
            _verify_install(self._conn, ddl, self.table_owners)
            self._conn.execute('PRAGMA journal_mode=WAL')
            self._conn.execute('PRAGMA synchronous=FULL')
            self.recovered_unknown_count = self.recover_uncertain_outbox()
            self.fenced_lease_count = self.fence_recovered_worker_leases()
        except BaseException:
            self.close()
            raise

    def capability(self, owner: str) -> ScopedWriter:
        if owner not in set(self.table_owners.values()) or owner == 'P2':
            raise WriterDenied('unknown or administrative writer owner')
        if self._closed:
            raise AuthorityError('authority closed')
        return ScopedWriter(self, owner)

    def _authorizer(self, action: int, arg1: str | None, arg2: str | None,
                    db_name: str | None, trigger: str | None) -> int:
        if action in _WRITE_ACTIONS:
            return sqlite3.SQLITE_OK if (db_name == 'main' and
                self.table_owners.get(arg1) == self._active_owner) else sqlite3.SQLITE_DENY
        if action in _BLOCK_ACTIONS:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    @contextmanager
    def _transaction(self, owner: str) -> Iterator[_View]:
        with self._mutex:
            if self._closed or self._conn is None:
                raise AuthorityError('authority closed')
            if self._active_owner is not None or owner not in set(self.table_owners.values()):
                raise WriterDenied('nested or unauthorized writer transaction')
            self._conn.execute('BEGIN IMMEDIATE')
            self._active_owner = owner
            view = _View(self)
            self._conn.set_authorizer(self._authorizer)
            try:
                yield view
                self._conn.set_authorizer(None)
                self._conn.execute('COMMIT')
            except BaseException:
                self._conn.set_authorizer(None)
                self._conn.execute('ROLLBACK')
                raise
            finally:
                view._invalidate()
                self._active_owner = None

    def _read(self, callback: Callable[[_View], Any]) -> Any:
        with self._mutex:
            if self._closed or self._conn is None:
                raise AuthorityError('authority closed')
            prior = self._active_owner
            # Reads are not permitted to mutate, including when called from
            # inside another transaction. Restore the prior capability after.
            self._active_owner = None
            view = _View(self)
            self._conn.set_authorizer(self._authorizer)
            try:
                return callback(view)
            finally:
                view._invalidate()
                self._active_owner = prior
                self._conn.set_authorizer(self._authorizer if prior is not None else None)

    def read(self, callback: Callable[[_View], Any]) -> Any:
        return self._read(callback)

    def _root_sql(self, view: _View, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        # Only the trusted root's current transaction invokes this method.
        if self._closed or self._active_owner is None:
            raise WriterDenied('root write outside active authority transaction')
        original = self._active_owner
        self._active_owner = 'P2'
        try:
            return view.execute(sql, params)
        finally:
            self._active_owner = original

    def recover_uncertain_outbox(self) -> int:
        # Restart cannot safely infer whether a claimed external effect happened.
        with self._transaction('P2') as view:
            return view.execute("UPDATE v1_outbox SET state='UNKNOWN' WHERE state='CLAIMED'").rowcount

    def fence_recovered_worker_leases(self) -> int:
        """A restarted process cannot inherit an earlier host's active lease.

        The lease owner is CAB-C, not P2. It is deliberately fenced through
        that CAB's own capability; no cross-CAB privilege is conferred.
        """
        with self._transaction('CAB-C') as view:
            return view.execute('UPDATE v1_worker_leases SET fenced=1 WHERE fenced=0').rowcount

    def outbox_rows(self, state: str | None = None) -> list[Mapping[str, Any]]:
        if state is not None and state not in {'PENDING','CLAIMED','UNKNOWN','ACKED'}:
            raise ValueError('invalid outbox state')
        return self.read(lambda v: v.fetch_all('SELECT * FROM v1_outbox' +
            (' WHERE state=?' if state else '') + ' ORDER BY created_at_us,message_id',
            (state,) if state else ()))

    def close(self) -> None:
        with self._mutex:
            if self._closed:
                return
            self._closed = True
            if self._conn is not None:
                self._conn.set_authorizer(None)
                self._conn.close()
                self._conn = None
            self._lock.close()

    def __enter__(self) -> 'R9Authority':
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class DurableOutbox:
    """P2-owned durable queue. Production dispatch must verify native readback."""

    def __init__(self, authority: R9Authority, clock_us: Callable[[], int] = _now_us,
                 readback: Callable[[str, str], Mapping[str, Any] | None] | None = None):
        self._state = authority
        self._clock = clock_us
        self._readback = readback

    def append(self, tx: _View, message: Mapping[str, Any]) -> None:
        if not isinstance(message, Mapping) or not isinstance(message.get('idempotency_key'), str):
            raise ValueError('outbox message requires a stable idempotency key')
        if not isinstance(message.get('domain'), str) or not isinstance(message.get('event_type'), str):
            raise ValueError('outbox message requires domain and event_type')
        body = _canonical(dict(message))
        payload_digest = _digest(body)
        namespace = message['domain']
        key = message['idempotency_key']
        if not 0 < len(namespace) <= 128 or not 0 < len(key) <= 512:
            raise ValueError('outbox namespace/key invalid')
        msg_id = 'outbox-' + _digest(_canonical([namespace, key]))
        result_digest = _digest(_canonical({'message_id': msg_id, 'payload_digest': payload_digest}))
        tracked = tx.fetch_one('SELECT request_digest,result_digest FROM v1_idempotency '
                               'WHERE namespace=? AND idempotency_key=?', (namespace, key))
        existing = tx.fetch_one('SELECT payload_digest FROM v1_outbox WHERE message_id=?', (msg_id,))
        if existing is not None or tracked is not None:
            if (existing is None or tracked is None or existing['payload_digest'] != payload_digest
                    or tracked['request_digest'] != payload_digest
                    or tracked['result_digest'] != result_digest):
                raise IdempotencyConflict('outbox key reused with different request/result')
            return
        now = self._clock()
        if type(now) is not int or now < 1:
            raise AuthorityError('untrusted clock in durable append')
        self._state._root_sql(tx,
            'INSERT INTO v1_idempotency(namespace,idempotency_key,request_digest,result_digest,created_at_us) '
            'VALUES (?,?,?,?,?)', (namespace,key,payload_digest,result_digest,now))
        self._state._root_sql(tx,
            'INSERT INTO v1_transaction_receipts(receipt_id,namespace,actor_id,input_digest,result_digest,created_at_us) '
            'VALUES (?,?,?,?,?,?)', ('transaction-' + _digest(msg_id),namespace,'P2',payload_digest,result_digest,now))
        self._state._root_sql(tx,
            'INSERT INTO v1_outbox(message_id,project_id,feature_id,domain,event_type,payload_digest,'
            'payload_json,state,created_at_us,retry_after_us) VALUES (?,?,?,?,?,?,?,?,?,NULL)',
            (msg_id, message.get('project_id'), message.get('feature_id'), namespace,
             message['event_type'], payload_digest, body, 'PENDING', now))
        event_id = 'event-' + _digest(msg_id)
        seq = tx.fetch_one('SELECT COALESCE(MAX(event_seq),0)+1 AS next_seq FROM v1_events')['next_seq']
        self._state._root_sql(tx,
            'INSERT INTO v1_events(event_id,event_seq,project_id,feature_id,domain,kind,actor_id,'
            'correlation_id,causation_id,input_digest,payload_digest,payload_json,issued_at_us) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (event_id, seq, message.get('project_id'), message.get('feature_id'), namespace,
             message['event_type'], 'P2', None, None, _digest(_canonical([namespace,key])),
             payload_digest, body, now))

    def mark_unknown(self, message_id: str) -> bool:
        with self._state._transaction('P2') as view:
            return bool(view.execute("UPDATE v1_outbox SET state='UNKNOWN' WHERE message_id=? AND state IN ('PENDING','CLAIMED')",(message_id,)).rowcount)

    def claim(self, message_id: str) -> bool:
        with self._state._transaction('P2') as view:
            return bool(view.execute("UPDATE v1_outbox SET state='CLAIMED' WHERE message_id=? AND state='PENDING'",(message_id,)).rowcount)

    def acknowledge(self, message_id: str, *, provider_receipt_ref: str) -> bool:
        """Only a configured native readback may establish ACK.

        The callback runs outside the SQLite transaction. The subsequent
        transaction rechecks the message's exact digest and unsettled state;
        external failure/timeout keeps UNKNOWN, never guessed success.
        """
        if not isinstance(provider_receipt_ref, str) or not provider_receipt_ref.strip():
            raise WriterDenied('native receipt reference required')
        if not callable(self._readback):
            raise WriterDenied('no native outbox readback configured')
        row = self._state.read(lambda tx: tx.fetch_one(
            'SELECT * FROM v1_outbox WHERE message_id=?', (message_id,)))
        if not row or row['state'] not in ('CLAIMED', 'UNKNOWN'):
            return False
        try:
            proof = self._readback(message_id, provider_receipt_ref)
        except (OSError, TimeoutError):
            self.mark_unknown(message_id)
            return False
        if (not isinstance(proof, Mapping) or proof.get('status') != 'VERIFIED'
                or proof.get('message_id') != message_id
                or proof.get('receipt_ref') != provider_receipt_ref
                or proof.get('payload_digest') != row['payload_digest']):
            raise WriterDenied('native provider did not verify exact external effect')
        with self._state._transaction('P2') as tx:
            current = tx.fetch_one('SELECT payload_digest,state FROM v1_outbox WHERE message_id=?', (message_id,))
            if not current or current['payload_digest'] != row['payload_digest'] or current['state'] not in ('CLAIMED','UNKNOWN'):
                return False
            return bool(tx.execute("UPDATE v1_outbox SET state='ACKED' "
                "WHERE message_id=? AND payload_digest=? AND state IN ('CLAIMED','UNKNOWN')",
                (message_id, row['payload_digest'])).rowcount)


def inspect_database(path: str | Path) -> dict[str, Any]:
    """Read-only diagnosis. Never acquires the authority's exclusive writer lock.

    This is a database inspection, not a process liveness or native provider
    readiness proof. An absent/foreign database is never initialized here.
    """
    source = Path(path).absolute()
    if not source.is_file() or source.is_symlink():
        raise AuthorityError('R9 authority DB unavailable')
    if os.name == 'posix' and stat.S_IMODE(source.stat().st_mode) & 0o077:
        raise AuthorityError('SQLite DB permissions must be private')
    uri = 'file:' + quote(str(source), safe='/') + '?mode=ro'
    conn = sqlite3.connect(uri, uri=True, timeout=1.0)
    try:
        conn.execute('PRAGMA query_only=ON')
        ddl, owners = _resources()
        if not _objects(conn):
            raise AuthorityError('R9 schema absent')
        _verify_install(conn, ddl, owners)
        counts = dict(conn.execute('SELECT state,COUNT(*) FROM v1_outbox GROUP BY state').fetchall())
        return {'liveness':'NOT_OBSERVED', 'database':'R9_FRESH_SCHEMA_VERIFIED',
                'pending_outbox_count':counts.get('PENDING',0),
                'unknown_external_count':counts.get('UNKNOWN',0)}
    finally:
        conn.close()
