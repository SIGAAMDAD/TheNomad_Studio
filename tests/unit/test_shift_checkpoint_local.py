"""Local checkpoint atomicity and tamper checks using a minimal C store shape."""
import sqlite3
import weakref
import pytest

from studio.storage.workforce import WorkforceStorage
from studio.storage.shift_checkpoints import DurableShiftCheckpoints, CheckpointFault


class Tx:
    def __init__(self, db):
        self.db = db

    def one(self, sql, params=()):
        cursor = self.db.execute(sql, params)
        row = cursor.fetchone()
        return dict(zip([col[0] for col in cursor.description], row)) if row else None

    def change(self, sql, params=()):
        return self.db.execute(sql, params).rowcount


class MemoryC(WorkforceStorage):
    def __init__(self):
        db = sqlite3.connect(':memory:', isolation_level=None)
        super().__init__(db)
        weakref.finalize(self, db.close)
        db.executescript('''CREATE TABLE worker_shifts(project_id TEXT,shift_id TEXT,worker_id TEXT,state TEXT);
                         CREATE TABLE checkpoints(project_id TEXT,checkpoint_id TEXT,shift_id TEXT,
                         context_digest TEXT,relay_cursor TEXT,recorded_at TEXT);''')
        db.execute("INSERT INTO worker_shifts VALUES('project','shift','worker','active')")
        self.requests = {}

    def command(self, project, operation, request_id, digest, fn):
        key = (operation, request_id)
        if key in self.requests:
            assert self.requests[key][0] == digest
            return self.requests[key][1]
        self._connection.execute('BEGIN')
        try:
            result = fn(Tx(self._connection))
            self._connection.execute('COMMIT')
        except BaseException:
            self._connection.execute('ROLLBACK')
            raise
        self.requests[key] = digest, result
        return result

    def read(self, project, fn):
        return fn(Tx(self._connection))


def test_local_checkpoint_commit_restart_shape_and_immutability(tmp_path):
    db = MemoryC()
    store = DurableShiftCheckpoints(db, tmp_path)
    kwargs = dict(project_id='project', worker_id='worker', shift_id='shift')
    one = store.save_checkpoint(**kwargs, cursor=None, processed=(), fingerprints={})
    assert store.load_latest(**kwargs)['processed'] == []
    two = store.save_checkpoint(**kwargs, cursor='a'*40,
                                processed=('cmd',), fingerprints={'cmd':'b'*64})
    assert one != two
    assert store.load_latest(**kwargs)['processed'] == ['cmd']
    with pytest.raises(CheckpointFault, match='roll back'):
        store.save_checkpoint(**kwargs, cursor='a'*40, processed=(), fingerprints={})
    path = store._path('project','shift',two)
    path.write_bytes(b'{}')
    with pytest.raises(CheckpointFault, match='digest'):
        store.load_latest(**kwargs)
    with pytest.raises(CheckpointFault, match='immutable'):
        store.save_checkpoint(**kwargs, cursor='a'*40,
                              processed=('cmd',), fingerprints={'cmd':'b'*64})
