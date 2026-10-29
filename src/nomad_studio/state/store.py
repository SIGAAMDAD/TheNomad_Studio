"""WG-A: one process-owned SQLite writer, bounded callers and immutable event journal.

Only this component opens the Studio's mutable SQLite authority. Its executor
serializes transactions on one dedicated thread; no database/provider I/O is
performed by the authority outside that thread except bounded RO snapshots.
"""
from __future__ import annotations

from concurrent.futures import Future, TimeoutError as FutureTimeout
from contextlib import contextmanager, closing
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import sqlite3
import stat
import threading
import time
import uuid
from typing import Any, Callable, Mapping, Sequence, TypeVar

from nomad_studio.contracts.v1 import DomainEvent, StateReader, StateStore, StateTx
from .errors import (Backpressure, ClosedStore, ConfigurationError, DeadlineExceeded,
                     IdempotencyConflict, InvalidState, OwnerLocked,
                     UnknownCommit)
from .schema import initialize_v1_schema, SCHEMA_NAMESPACES

MODULE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

T = TypeVar('T')
_WRITE_ACTIONS = frozenset((sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE))
_CONTROL_RE = re.compile(r'^\s*(?:BEGIN|COMMIT|END|ROLLBACK|SAVEPOINT|RELEASE|ATTACH|DETACH|VACUUM|PRAGMA|CREATE|DROP|ALTER|REINDEX|ANALYZE)\b', re.I)
_DENY_PREFIX = ('state_', 'sqlite_', 'studio_v1_')
_SENSITIVE_NAMES = frozenset(('password', 'secret', 'token', 'private_key', 'authorization', 'bearer', 'launch_capability', 'raw_credential'))


def _us() -> int:
    return time.time_ns() // 1000


def _json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'),
                          allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise InvalidState('only finite, JSON-compatible durable data is supported') from exc


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _identifier(value: str, *, kind: str = 'identifier') -> str:
    if not isinstance(value, str) or not value or len(value) > 512 or '\x00' in value:
        raise InvalidState('invalid ' + kind)
    return value


def _no_secrets(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, part in value.items():
            if not isinstance(key, str):
                raise InvalidState('JSON object keys must be strings')
            if key.lower() in _SENSITIVE_NAMES:
                raise InvalidState('unredacted sensitive field in event')
            _no_secrets(part)
    elif isinstance(value, (list, tuple)):
        for part in value:
            _no_secrets(part)


def _check_private_directory(directory: Path) -> None:
    if directory.is_symlink():
        raise ConfigurationError('SQLite authority directory may not be a symlink')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == 'posix':
        info = directory.stat()
        if info.st_uid != os.getuid() or (stat.S_IMODE(info.st_mode) & 0o077):
            raise ConfigurationError('SQLite authority directory must be owned by service and mode 0700')


def _check_local_disk(directory: Path) -> None:
    """Fail closed for known remote/pseudo mounts on Linux; no opt-out flag."""
    if os.name == 'nt':
        import ctypes
        # DRIVE_REMOTE == 4; UNC roots are not local authority storage.
        if str(directory).startswith('\\\\') or ctypes.windll.kernel32.GetDriveTypeW(
                str(directory.drive) + '\\') in (0, 1, 4):
            raise ConfigurationError('remote or unrecognized filesystem refused')
        return
    if os.path.exists('/proc/self/mountinfo'):
        best = ('', '')
        with open('/proc/self/mountinfo', encoding='utf-8') as f:
            for line in f:
                left, divider, right = line.partition(' - ')
                if not divider:
                    continue
                parts = left.split()
                if len(parts) < 5:
                    continue
                mount_path = parts[4].replace('\\040', ' ')
                actual = os.path.realpath(directory)
                if (actual == mount_path or actual.startswith(mount_path.rstrip('/') + '/')) and len(mount_path) > len(best[0]):
                    best = (mount_path, right.split()[0] if right.split() else '')
        if not best[0]:
            raise ConfigurationError('could not establish authority filesystem type')
        fs = best[1].lower()
        if fs in {'nfs', 'nfs4', 'cifs', 'smbfs', 'sshfs', '9p', 'ceph', 'glusterfs',
                  'lustre', 'afs', 'davfs', 'fuse.sshfs', 'fuse.rclone', 'fuse.s3fs',
                  'fuse.gcsfuse', 'fuse.goofys', 'fuse.ceph', 'fuse.glusterfs',
                  'fuseblk', 'fuse', 'tmpfs', 'ramfs'} or fs.startswith(('nfs', 'cifs', 'smb', 'fuse.')):
            raise ConfigurationError('SQLite authority requires a local durable filesystem: ' + fs)
        return
    # A non-Linux Unix host needs an independently verified local-filesystem
    # adapter; do not silently assume local storage on unknown mounts.
    raise ConfigurationError('filesystem locality cannot be verified on this host')


class _OwnerLock:
    def __init__(self, filename: Path):
        if filename.is_symlink():
            raise ConfigurationError('owner lock path cannot be a symlink')
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, 'O_NOFOLLOW'):
            flags |= os.O_NOFOLLOW
        self.fd = os.open(filename, flags, 0o600)
        try:
            if os.name == 'nt':
                import msvcrt
                os.lseek(self.fd, 0, os.SEEK_SET)
                os.write(self.fd, b'0')
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if os.name == 'posix' and (stat.S_IMODE(os.fstat(self.fd).st_mode) & 0o077):
                raise ConfigurationError('owner lock permissions are not private')
        except (BlockingIOError, OSError) as exc:
            os.close(self.fd)
            raise OwnerLocked('another process already owns SQLite authority') from exc
        except Exception:
            os.close(self.fd)
            raise

    def close(self):
        if self.fd is None:
            return
        if os.name == 'nt':
            import msvcrt
            os.lseek(self.fd, 0, os.SEEK_SET)
            msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)
        self.fd = None


class _Reader:
    def __init__(self, connection: sqlite3.Connection, max_rows: int = 10000):
        self._conn = connection
        self._max_rows = max_rows

    def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> Mapping[str, Any] | None:
        row = self._conn.execute(sql, tuple(params)).fetchone()
        return dict(row) if row is not None else None

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> Sequence[Mapping[str, Any]]:
        rows = self._conn.execute(sql, tuple(params)).fetchmany(self._max_rows + 1)
        if len(rows) > self._max_rows:
            raise Backpressure('read result exceeds configured row bound; use pagination')
        return [dict(r) for r in rows]


class _Tx(_Reader):
    def __init__(self, connection: sqlite3.Connection, state: 'SQLiteStateStore', namespace: str | None = None):
        super().__init__(connection, state._max_read_rows)
        self._store = state
        self._namespace = namespace

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        if not isinstance(sql, str) or _CONTROL_RE.match(sql):
            raise InvalidState('DDL/transaction control is reserved for Studio V1 schema authority')
        before = self._conn.total_changes
        try:
            self._conn.execute(sql, tuple(params))
        except sqlite3.DatabaseError as exc:
            if 'not authorized' in str(exc):
                raise InvalidState('cannot modify authority-protected table') from exc
            raise
        return self._conn.total_changes - before

    def append_event(self, event: DomainEvent) -> str:
        if not isinstance(event, DomainEvent):
            raise InvalidState('DomainEvent is required')
        if self._namespace is not None and event.domain != self._namespace:
            raise InvalidState('event domain is not owned by this transaction')
        for item in (event.event_id, event.domain, event.kind, event.actor_principal_id):
            _identifier(item)
        if not MODULE_RE.fullmatch(event.domain):
            raise InvalidState('invalid event domain')
        if event.assignment_epoch is not None and (not isinstance(event.assignment_epoch, int) or event.assignment_epoch < 0):
            raise InvalidState('negative assignment epoch')
        _no_secrets(event.payload)
        payload = _json(event.payload)
        payload_hash = _sha(payload.encode('utf-8'))
        if event.payload_sha256 is not None and event.payload_sha256 != payload_hash:
            raise InvalidState('event payload digest mismatch')
        fingerprint = _sha(_json({
            'event_id': event.event_id, 'domain': event.domain, 'kind': event.kind,
            'actor_principal_id': event.actor_principal_id, 'correlation_id': event.correlation_id,
            'causation_id': event.causation_id, 'job_id': event.job_id,
            'shift_id': event.shift_id, 'assignment_epoch': event.assignment_epoch,
            'payload_sha256': payload_hash,
        }).encode('utf-8'))
        # A repeated event_id is idempotent only when all semantically relevant
        # fields have the same fingerprint; issued_at_us is service-owned.
        with self._store._authority_write():
            old = self._conn.execute('SELECT event_fingerprint FROM state_events WHERE event_id=?', (event.event_id,)).fetchone()
            if old is not None:
                if old['event_fingerprint'] != fingerprint:
                    raise IdempotencyConflict('duplicate event ID with conflicting content')
                return event.event_id
            evidence = event.payload.get('source_evidence_ref') if isinstance(event.payload, Mapping) else None
            if evidence is not None and not isinstance(evidence, str):
                raise InvalidState('invalid source evidence reference')
            self._conn.execute('''INSERT INTO state_events
                (event_id,event_fingerprint,domain,kind,actor_principal_id,issued_at_us,
                 correlation_id,causation_id,job_id,shift_id,assignment_epoch,
                 payload_sha256,payload_json,source_evidence_ref)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (event.event_id, fingerprint, event.domain, event.kind, event.actor_principal_id,
                 _us(), event.correlation_id, event.causation_id, event.job_id,
                 event.shift_id, event.assignment_epoch, payload_hash, payload, evidence))
        return event.event_id

    def get_record(self, namespace: str, key: str) -> Mapping[str, Any] | None:
        self._record_scope(namespace, key)
        row = self.fetch_one('SELECT value_json,version,updated_at_us FROM state_records WHERE namespace=? AND record_key=?',
                             (namespace, key))
        if row is None:
            return None
        return {'value': json.loads(row['value_json']), 'version': row['version'], 'updated_at_us': row['updated_at_us']}

    def _record_scope(self, namespace: str, key: str):
        if not isinstance(namespace, str) or not MODULE_RE.fullmatch(namespace):
            raise InvalidState('invalid namespace')
        _identifier(key, kind='record key')
        if self._namespace is not None and namespace != self._namespace:
            raise InvalidState('cross-namespace record mutation denied')

    def put_record(self, namespace: str, key: str, value: Any, *, expected_version: int | None = None) -> int:
        self._record_scope(namespace, key)
        encoded = _json(value)
        old = self.get_record(namespace, key)
        if expected_version is not None and (old['version'] if old else 0) != expected_version:
            raise IdempotencyConflict('stale record version')
        version = (old['version'] + 1) if old else 1
        with self._store._authority_write():
            self._conn.execute('''INSERT INTO state_records(namespace,record_key,value_json,version,updated_at_us)
                VALUES(?,?,?,?,?) ON CONFLICT(namespace,record_key) DO UPDATE SET
                value_json=excluded.value_json,version=excluded.version,updated_at_us=excluded.updated_at_us''',
                (namespace, key, encoded, version, _us()))
        return version

    def delete_record(self, namespace: str, key: str, *, expected_version: int) -> bool:
        self._record_scope(namespace, key)
        old = self.get_record(namespace, key)
        if (old['version'] if old else 0) != expected_version:
            raise IdempotencyConflict('stale record version')
        if old is None:
            return False
        with self._store._authority_write():
            self._conn.execute('DELETE FROM state_records WHERE namespace=? AND record_key=?', (namespace, key))
        return True

    def record_receipt(self, namespace: str, request_id: str, event_id: str, result: Any) -> str:
        if not MODULE_RE.fullmatch(namespace):
            raise InvalidState('invalid receipt namespace')
        if self._namespace is not None and namespace != self._namespace:
            raise InvalidState('cross-namespace receipt denied')
        _identifier(request_id, kind='request ID')
        _identifier(event_id, kind='event ID')
        encoded = _json(result)
        old = self._conn.execute('SELECT receipt_id,event_id,result_json FROM state_receipts WHERE namespace=? AND request_id=?',
                                 (namespace, request_id)).fetchone()
        if old is not None:
            if old['event_id'] != event_id or old['result_json'] != encoded:
                raise IdempotencyConflict('request receipt content changed')
            return old['receipt_id']
        receipt_id = str(uuid.uuid4())
        with self._store._authority_write():
            self._conn.execute('''INSERT INTO state_receipts
                (receipt_id,namespace,request_id,event_id,result_json,created_at_us) VALUES (?,?,?,?,?,?)''',
                (receipt_id, namespace, request_id, event_id, encoded, _us()))
        return receipt_id


class SQLiteStateStore(StateStore):
    """Bounded, single-writer authority implementing frozen StateStore/StateTx."""
    def __init__(self, config: Mapping[str, object]):
        path = config.get('path')
        if not isinstance(path, (str, os.PathLike)) or not str(path):
            raise ConfigurationError('absolute SQLite path is required')
        self.path = Path(path).absolute()
        if not self.path.is_absolute() or self.path.name in ('.', '..') or self.path.suffix not in ('.sqlite3', '.db', '.sqlite'):
            raise ConfigurationError('absolute .sqlite3/.db/.sqlite path is required')
        if self.path.exists() and (self.path.is_symlink() or not self.path.is_file()):
            raise ConfigurationError('database path is not a regular file')
        _check_private_directory(self.path.parent)
        _check_local_disk(self.path.parent)
        if self.path.exists() and os.name == 'posix':
            current = self.path.stat()
            if current.st_uid != os.getuid() or (stat.S_IMODE(current.st_mode) & 0o077):
                raise ConfigurationError('existing authority file is not private')
        self._lock = _OwnerLock(Path(str(self.path) + '.lock'))
        self._max_pending = self._integer(config, 'max_pending', 1024, 1, 100000)
        self._max_readers = self._integer(config, 'max_readers', 16, 1, 1024)
        self._max_read_rows = self._integer(config, 'max_read_rows', 10000, 1, 1000000)
        self._admission_seconds = self._float(config, 'admission_seconds', 0.5)
        self._request_seconds = self._float(config, 'request_seconds', 30.0)
        self._busy_seconds = self._float(config, 'busy_seconds', 3.0)
        self._max_tx_seconds = self._float(config, 'max_tx_seconds', 10.0)
        self._max_read_seconds = self._float(config, 'max_read_seconds', 10.0)
        self._max_backup_seconds = self._float(config, 'max_backup_seconds', 120.0)
        self._read_sem = threading.BoundedSemaphore(self._max_readers)
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=self._max_pending)
        self._closed = threading.Event()
        self._guard = threading.Lock()
        self._writer_thread: threading.Thread | None = None
        self._authority_write_enabled = False
        self._active_namespace: str | None = None
        self._registered_modules: set[str] = set(SCHEMA_NAMESPACES)
        self._dedup = 0
        self._conn: sqlite3.Connection | None = None
        try:
            self._conn = sqlite3.connect(str(self.path), isolation_level=None,
                                         check_same_thread=False, cached_statements=0, timeout=self._busy_seconds)
            self._conn.row_factory = sqlite3.Row
            if os.name == 'posix':
                os.chmod(self.path, 0o600)
            self._conn.execute('PRAGMA busy_timeout=' + str(int(self._busy_seconds * 1000)))
            self._conn.execute('PRAGMA foreign_keys=ON')
            self._conn.execute('PRAGMA trusted_schema=OFF')
            mode = self._conn.execute('PRAGMA journal_mode=WAL').fetchone()[0]
            self._conn.execute('PRAGMA synchronous=FULL')
            effective = {
                'journal_mode': mode.lower(),
                'synchronous': self._conn.execute('PRAGMA synchronous').fetchone()[0],
                'foreign_keys': self._conn.execute('PRAGMA foreign_keys').fetchone()[0],
                'trusted_schema': self._conn.execute('PRAGMA trusted_schema').fetchone()[0]
            }
            if effective != {'journal_mode': 'wal', 'synchronous': 2, 'foreign_keys': 1, 'trusted_schema': 0}:
                raise ConfigurationError('required SQLite pragmas not effective: ' + repr(effective))
            if 'migration_roots' in config or 'migrations' in config:
                raise ConfigurationError('development migration inputs are not supported by Studio V1')
            initialize_v1_schema(self._conn)
            self._conn.set_authorizer(self._authorizer)
            self._writer_thread = threading.Thread(target=self._run, name='nomad-sqlite-writer', daemon=True)
            self._writer_thread.start()
        except Exception:
            if self._conn is not None:
                self._conn.close()
            self._lock.close()
            raise

    @staticmethod
    def _integer(config, key, default, low, high):
        value = config.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ConfigurationError('invalid setting: ' + key)
        return value

    @staticmethod
    def _float(config, key, default):
        value = config.get(key, default)
        if type(value) not in (float, int) or not 0 < value <= 300:
            raise ConfigurationError('invalid setting: ' + key)
        return float(value)

    def _authorizer(self, action, arg1, arg2, db, source):
        if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
            return sqlite3.SQLITE_DENY
        ddl = {sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_TEMP_TABLE,
               sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_CREATE_TEMP_INDEX,
               sqlite3.SQLITE_CREATE_TRIGGER, sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
               sqlite3.SQLITE_CREATE_VIEW, sqlite3.SQLITE_CREATE_TEMP_VIEW,
               sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_DROP_TEMP_TABLE,
               sqlite3.SQLITE_DROP_INDEX, sqlite3.SQLITE_DROP_TEMP_INDEX,
               sqlite3.SQLITE_DROP_TRIGGER, sqlite3.SQLITE_DROP_TEMP_TRIGGER,
               sqlite3.SQLITE_DROP_VIEW, sqlite3.SQLITE_DROP_TEMP_VIEW,
               sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_REINDEX,
               sqlite3.SQLITE_ANALYZE, sqlite3.SQLITE_PRAGMA}
        if action in ddl and not self._authority_write_enabled:
            return sqlite3.SQLITE_DENY
        if action in _WRITE_ACTIONS and arg1 is not None:
            if arg1.startswith(_DENY_PREFIX) and not self._authority_write_enabled:
                return sqlite3.SQLITE_DENY
            if not self._authority_write_enabled and self._active_namespace is not None:
                for module in self._registered_modules:
                    if arg1.startswith(module + '_') and module != self._active_namespace:
                        return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    @contextmanager
    def _authority_write(self):
        old = self._authority_write_enabled
        self._authority_write_enabled = True
        try:
            yield
        finally:
            self._authority_write_enabled = old

    def _run(self):
        while True:
            entry = self._queue.get()
            if entry is None:
                self._queue.task_done()
                return
            action, result, deadline = entry
            try:
                if time.monotonic() >= deadline:
                    raise DeadlineExceeded('write timed out before execution')
                result.set_result(action())
            except BaseException as exc:
                result.set_exception(exc)
            finally:
                self._queue.task_done()

    def _submit(self, callback: Callable[[], T]) -> T:
        if threading.current_thread() is self._writer_thread:
            raise InvalidState('nested write submission from writer thread is forbidden')
        deadline = time.monotonic() + self._request_seconds
        future: Future[T] = Future()
        with self._guard:
            if self._closed.is_set():
                raise ClosedStore('store has been closed')
            try:
                self._queue.put_nowait((callback, future, deadline))
            except queue.Full as exc:
                raise Backpressure('writer queue is full') from exc
        try:
            return future.result(timeout=max(0.001, deadline - time.monotonic()))
        except FutureTimeout as exc:
            # A started callback may commit after the caller's deadline. Caller
            # must resolve via idempotency key; never claim rollback here.
            raise UnknownCommit('write completion unknown; retry with same idempotency key') from exc

    def transact(self, callback: Callable[[StateTx], T], *, idempotency_key: str | None = None) -> T:
        return self.transact_guarded(callback, idempotency_key=idempotency_key)

    def transact_domain(self, namespace: str, callback: Callable[[StateTx], T], *,
                        idempotency_key: str | None = None, fingerprint: str | None = None) -> T:
        if not MODULE_RE.fullmatch(namespace):
            raise InvalidState('invalid domain namespace')
        return self.transact_guarded(callback, idempotency_key=idempotency_key,
                                     fingerprint=fingerprint, namespace=namespace)

    def transact_guarded(self, callback: Callable[[StateTx], T], *,
                         idempotency_key: str | None = None, fingerprint: str | None = None,
                         namespace: str | None = None) -> T:
        if idempotency_key is not None:
            _identifier(idempotency_key, kind='idempotency key')
        if fingerprint is not None and (not isinstance(fingerprint, str) or not re.fullmatch('[0-9a-f]{64}', fingerprint)):
            raise InvalidState('fingerprint must be sha256')
        if fingerprint is not None and idempotency_key is None:
            raise InvalidState('fingerprint requires idempotency key')

        def work():
            assert self._conn is not None
            connection = self._conn
            started = time.monotonic()
            connection.set_progress_handler(lambda: 1 if time.monotonic() - started > self._max_tx_seconds else 0, 500)
            try:
                connection.execute('BEGIN IMMEDIATE')
                if idempotency_key is not None:
                    old = connection.execute('SELECT result_json,fingerprint FROM state_idempotency WHERE key=?', (idempotency_key,)).fetchone()
                    if old is not None:
                        if old['fingerprint'] != fingerprint:
                            raise IdempotencyConflict('replayed idempotency key has different fingerprint')
                        self._dedup += 1
                        connection.commit()
                        return json.loads(old['result_json'])
                self._active_namespace = namespace
                try:
                    value = callback(_Tx(connection, self, namespace))
                finally:
                    self._active_namespace = None
                if time.monotonic() - started > self._max_tx_seconds:
                    raise DeadlineExceeded('maximum mutation duration exceeded')
                if idempotency_key is not None:
                    serialized = _json(value)
                    with self._authority_write():
                        connection.execute('INSERT INTO state_idempotency(key,fingerprint,result_json,created_at_us) VALUES (?,?,?,?)',
                                           (idempotency_key, fingerprint, serialized, _us()))
                connection.commit()
                return value
            except BaseException as exc:
                connection.rollback()
                if isinstance(exc, sqlite3.OperationalError) and ('database is locked' in str(exc) or 'database is busy' in str(exc)):
                    raise Backpressure('SQLite writer is temporarily busy') from exc
                raise
            finally:
                connection.set_progress_handler(None, 0)
        return self._submit(work)

    def read(self, callback: Callable[[StateReader], T]) -> T:
        if self._closed.is_set():
            raise ClosedStore('store has been closed')
        if not self._read_sem.acquire(timeout=min(self._admission_seconds, self._request_seconds)):
            raise Backpressure('read capacity is full')
        connection = None
        start = time.monotonic()
        try:
            # Snapshot transaction is genuinely read-only and cannot become
            # a second writer even if a callback attempts a write.
            connection = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, isolation_level=None,
                                         timeout=self._busy_seconds)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA query_only=ON')
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA trusted_schema=OFF')
            connection.set_progress_handler(lambda: 1 if time.monotonic() - start > self._max_read_seconds else 0, 500)
            connection.execute('BEGIN')
            try:
                return callback(_Reader(connection, self._max_read_rows))
            finally:
                connection.rollback()
        finally:
            if connection is not None:
                connection.close()
            self._read_sem.release()

    def schema_identity(self) -> Mapping[str, Any]:
        """Expose the current V1 schema identity, not a historical upgrade log."""
        from .schema import SCHEMA_SHA256, SCHEMA_VERSION
        return {'version': SCHEMA_VERSION, 'sha256': SCHEMA_SHA256}

    def diagnostics(self) -> Mapping[str, Any]:
        def work():
            assert self._conn is not None
            with self._authority_write():
                return self._diagnostic_pragmas()
        return self._submit(work)

    def _diagnostic_pragmas(self):
        assert self._conn is not None
        return {'journal_mode': self._conn.execute('PRAGMA journal_mode').fetchone()[0],
                    'synchronous': self._conn.execute('PRAGMA synchronous').fetchone()[0],
                    'foreign_keys': self._conn.execute('PRAGMA foreign_keys').fetchone()[0],
                    'trusted_schema': self._conn.execute('PRAGMA trusted_schema').fetchone()[0],
                    'queue_pending': self._queue.qsize(), 'queue_capacity': self._max_pending,
                    'deduplicated': self._dedup,
                    'sqlite_version': sqlite3.sqlite_version}

    def integrity_check(self) -> Mapping[str, Any]:
        def inspect(rd):
            entries = rd.fetch_all('PRAGMA integrity_check')
            foreign = rd.fetch_all('PRAGMA foreign_key_check')
            return {'ok': len(entries) == 1 and next(iter(entries[0].values())) == 'ok' and len(foreign) == 0,
                    'integrity': [list(row.values())[0] for row in entries], 'foreign_key_violations': foreign}
        return self.read(inspect)

    def backup_snapshot(self, destination: str | os.PathLike[str]) -> Mapping[str, Any]:
        """Online SQLite backup, atomic publish, independent integrity check and digest."""
        if self._closed.is_set():
            raise ClosedStore('store has been closed')
        target = Path(destination).absolute()
        if target.suffix not in ('.sqlite3', '.db', '.sqlite') or target == self.path:
            raise ConfigurationError('snapshot must be separate .sqlite3/.db/.sqlite file')
        if target.is_symlink() or target.exists():
            raise ConfigurationError('snapshot destination must not exist')
        if not target.parent.is_dir() or target.parent.is_symlink():
            raise ConfigurationError('snapshot directory must exist and be private')
        _check_private_directory(target.parent)
        _check_local_disk(target.parent)
        temp = target.with_name('.' + target.name + '.' + uuid.uuid4().hex + '.tmp')
        read_conn = None
        dst = None
        if not self._read_sem.acquire(timeout=self._admission_seconds):
            raise Backpressure('snapshot reader capacity full')
        try:
            read_conn = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True,
                                        isolation_level=None, timeout=self._busy_seconds)
            read_conn.execute('PRAGMA trusted_schema=OFF')
            dst = sqlite3.connect(str(temp), isolation_level=None)
            if os.name == 'posix':
                os.chmod(temp, 0o600)
            deadline = time.monotonic() + self._max_backup_seconds
            def progress(status, remaining, total):
                if time.monotonic() > deadline:
                    raise DeadlineExceeded('online backup exceeded configured time budget')
            read_conn.backup(dst, pages=256, progress=progress, sleep=0.01)
            dst.commit()
            result = dst.execute('PRAGMA integrity_check').fetchone()[0]
            fks = dst.execute('PRAGMA foreign_key_check').fetchall()
            if result != 'ok' or fks:
                raise InvalidState('backup integrity verification failed')
            dst.close(); dst = None
            with open(temp, 'rb') as snapshot:
                os.fsync(snapshot.fileno())
                digest = hashlib.file_digest(snapshot, 'sha256').hexdigest()
                size = snapshot.seek(0, os.SEEK_END)
            # Hard-link publishes only when absent: retry races never overwrite
            # another completed archive. Both paths are on the same filesystem.
            os.link(temp, target)
            temp.unlink()
            if hasattr(os, 'O_DIRECTORY'):
                fd = os.open(str(target.parent), os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            return {'sha256': digest, 'bytes': size, 'integrity': 'ok', 'path': str(target)}
        finally:
            if dst is not None: dst.close()
            if read_conn is not None: read_conn.close()
            self._read_sem.release()
            if temp.exists(): temp.unlink()

    def inspect_snapshot(self, snapshot: str | os.PathLike[str]) -> Mapping[str, Any]:
        """Independently validate an existing file (including post-crash orphan)."""
        path = Path(snapshot).absolute()
        if not path.is_file() or path.is_symlink() or path == self.path:
            raise InvalidState('snapshot not present or invalid')
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as conn:
            conn.execute('PRAGMA trusted_schema=OFF')
            integrity = conn.execute('PRAGMA integrity_check').fetchone()[0]
            fks = conn.execute('PRAGMA foreign_key_check').fetchall()
            if integrity != 'ok' or fks:
                raise InvalidState('independent snapshot verification failed')
        with open(path, 'rb') as f:
            checksum = hashlib.file_digest(f, 'sha256').hexdigest()
            length = f.seek(0, os.SEEK_END)
        return {'sha256': checksum, 'bytes': length, 'integrity': 'ok', 'path': str(path)}

    def set_actor_epoch(self, principal_id: str, epoch: int, *, issuer_principal_id: str) -> int:
        """Trusted gateway bootstrap/rotation adapter; never wire-exposed."""
        _identifier(principal_id, kind='principal')
        _identifier(issuer_principal_id, kind='verified issuer principal')
        if type(epoch) is not int or epoch < 0:
            raise InvalidState('invalid epoch')
        def work():
            assert self._conn is not None
            self._conn.execute('BEGIN IMMEDIATE')
            try:
                old = self._conn.execute('SELECT epoch FROM state_actor_epochs WHERE principal_id=?',
                                         (principal_id,)).fetchone()
                if old is not None and epoch < old['epoch']:
                    raise IdempotencyConflict('epoch rollback denied')
                if old is not None and epoch == old['epoch']:
                    self._conn.commit()
                    return epoch
                with self._authority_write():
                    self._conn.execute('''INSERT INTO state_actor_epochs(principal_id,epoch,changed_at_us)
                        VALUES(?,?,?) ON CONFLICT(principal_id) DO UPDATE SET
                        epoch=excluded.epoch,changed_at_us=excluded.changed_at_us''',
                        (principal_id, epoch, _us()))
                _Tx(self._conn, self, 'state').append_event(DomainEvent(
                    'state:epoch:' + _sha(_json([principal_id, epoch]).encode()),
                    'state', 'ACTOR_EPOCH_CHANGED', issuer_principal_id, 0,
                    payload={'principal_id': principal_id, 'epoch': epoch}))
                self._conn.commit()
                return epoch
            except BaseException:
                self._conn.rollback()
                raise
        return self._submit(work)

    def actor_epoch(self, principal_id: str) -> int | None:
        row = self.read(lambda rd: rd.fetch_one('SELECT epoch FROM state_actor_epochs WHERE principal_id=?', (principal_id,)))
        return row['epoch'] if row else None

    def close(self) -> None:
        with self._guard:
            if self._closed.is_set():
                return
            self._closed.set()
            try:
                self._queue.put(None, timeout=self._request_seconds)
            except queue.Full as exc:
                raise UnknownCommit('writer queue cannot drain during shutdown') from exc
        if self._writer_thread is not None:
            self._writer_thread.join(timeout=self._request_seconds + self._max_tx_seconds)
            if self._writer_thread.is_alive():
                raise UnknownCommit('writer still executing; authority ownership retained')
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self._lock.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def open_store(config: Mapping[str, object]) -> StateStore:
    return SQLiteStateStore(config)
