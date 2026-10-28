"""Service-local SQLite owner; bounded admission and short durable transactions."""
from contextlib import contextmanager
from pathlib import Path
from threading import BoundedSemaphore, Lock
import fcntl
import os
import sqlite3
import stat
import time
from urllib.parse import quote

from ..store import StorageError
from .types import utc_us


class Unavailable(StorageError):
    pass


class ConnectionOwner:
    def __init__(self, path, *, topology='local', busy_ms=1000, queue_limit=64):
        raw = os.fspath(path)
        if (topology != 'local' or not Path(raw).is_absolute() or raw.startswith('//')
                or '\\' in raw or '\x00' in raw or not 1 <= busy_ms <= 10000
                or not 1 <= queue_limit <= 1000):
            raise ValueError('private absolute local DB path and bounded settings required')
        path = Path(raw)
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError('database path may not contain symlinks')
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        info = path.parent.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError('database directory must be service-owned mode 0700')
        # Deployment also declares a local volume; reject known network mounts.
        mounts = Path('/proc/mounts')
        if mounts.exists():
            matches = []
            for line in mounts.read_text().splitlines():
                parts = line.split()
                mount = Path(parts[1].replace('\\040', ' '))
                if path.is_relative_to(mount):
                    matches.append((len(str(mount)), parts[2]))
            if matches and max(matches)[1] in ('nfs', 'nfs4', 'cifs', 'smbfs', '9p', 'fuse.sshfs'):
                raise ValueError('network database files are unsupported')
        self.path, self.busy_ms = path, busy_ms
        self._closed = False
        self._pid = os.getpid()
        self._queue = BoundedSemaphore(queue_limit)
        self._writer = Lock()
        self.metrics = dict(transactions=0, busy=0, failures=0, queued=0, last_transaction_us=0)
        self._lease = os.open(str(path) + '.owner', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self._lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                meta = os.fstat(fd)
                if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid() or meta.st_nlink != 1:
                    raise ValueError('database must be a private regular file')
                os.fchmod(fd, 0o600)
            finally:
                os.close(fd)
            with self.connection() as conn:
                if conn.execute('PRAGMA journal_mode=WAL').fetchone()[0].lower() != 'wal':
                    raise Unavailable('WAL_UNAVAILABLE')
                if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise Unavailable('INTEGRITY_CHECK_FAILED')
                if conn.execute('PRAGMA foreign_key_check').fetchall():
                    raise Unavailable('FOREIGN_KEY_CHECK_FAILED')
        except BaseException:
            self.close()
            raise

    def _ensure_open(self):
        if self._closed or os.getpid() != self._pid:
            raise Unavailable('SQLITE_OWNER_UNAVAILABLE')

    @contextmanager
    def connection(self, *, readonly=False):
        self._ensure_open()
        # SQLite >=3.45 marks its JSON functions innocuous under trusted_schema=OFF.
        if sqlite3.sqlite_version_info < (3, 45, 0):
            raise Unavailable('SQLITE_3_45_REQUIRED')
        uri = 'file:' + quote(str(self.path), safe='/') + ('?mode=ro' if readonly else '?mode=rw')
        conn = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=self.busy_ms / 1000)
        try:
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('PRAGMA trusted_schema=OFF')
            conn.execute('PRAGMA synchronous=FULL')
            conn.execute(f'PRAGMA busy_timeout={self.busy_ms}')
            if readonly:
                conn.execute('PRAGMA query_only=ON')
            if (conn.execute('PRAGMA foreign_keys').fetchone()[0] != 1 or
                    conn.execute('PRAGMA synchronous').fetchone()[0] != 2 or
                    conn.execute('PRAGMA trusted_schema').fetchone()[0] != 0 or
                    conn.execute("SELECT json_valid('{}')").fetchone()[0] != 1):
                raise Unavailable('SQLITE_CONFIGURATION_INVALID')
            yield conn
        finally:
            conn.close()

    @contextmanager
    def write(self):
        self._ensure_open()
        if not self._queue.acquire(blocking=False):
            raise Unavailable('SQLITE_QUEUE_FULL')
        self.metrics['queued'] += 1
        acquired = False
        try:
            acquired = self._writer.acquire(timeout=self.busy_ms / 1000)
            if not acquired:
                raise Unavailable('SQLITE_WRITER_BUSY')
            start = time.monotonic_ns()
            with self.connection() as conn:
                try:
                    conn.execute('BEGIN IMMEDIATE')
                    yield conn, utc_us()
                    conn.commit()
                    self.metrics['transactions'] += 1
                except BaseException:
                    conn.rollback()
                    self.metrics['failures'] += 1
                    raise
                finally:
                    self.metrics['last_transaction_us'] = (time.monotonic_ns() - start) // 1000
        except sqlite3.OperationalError as exc:
            if getattr(exc, 'sqlite_errorcode', 0) & 255 in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                self.metrics['busy'] += 1
                raise Unavailable('SQLITE_BUSY') from exc
            raise Unavailable('SQLITE_STORAGE_UNAVAILABLE') from exc
        finally:
            if acquired:
                self._writer.release()
            self.metrics['queued'] -= 1
            self._queue.release()

    def close(self):
        if not self._closed:
            self._closed = True
            os.close(self._lease)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
