"""A late immutable GitHub receipt cannot complete a superseded task lease."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from studio.workforce import Workforce, WorkforceFault


class Tx:
    def __init__(self, connection):
        self.connection = connection

    def one(self, sql, params=()):
        cursor = self.connection.execute(sql, params)
        row = cursor.fetchone()
        return dict(zip([col[0] for col in cursor.description], row)) if row is not None else None

    def change(self, sql, params=()):
        return self.connection.execute(sql, params).rowcount


class Store:
    def __init__(self):
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.db.executescript("""
            CREATE TABLE command_outbox(project_id TEXT,command_id TEXT,worker_id TEXT,
                                        shift_id TEXT,assignment_id TEXT,state TEXT);
            CREATE TABLE shift_assignments(project_id TEXT,assignment_id TEXT,shift_id TEXT,
                                           worker_id TEXT,task_id TEXT,lease_generation INTEGER,state TEXT);
            CREATE TABLE leases(project_id TEXT,task_id TEXT,generation INTEGER,
                                worker_id TEXT,status TEXT,expires_at TEXT);
            CREATE TABLE command_receipts(project_id TEXT,receipt_id TEXT,command_id TEXT,
                                          worker_id TEXT,shift_id TEXT,kind TEXT,
                                          result_digest TEXT,remote_commit_sha TEXT,observed_at TEXT);
        """)

    def command(self, project_id, operation, request_id, digest, fn):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            result = fn(Tx(self.db))
            self.db.execute("COMMIT")
            return result
        except BaseException:
            self.db.execute("ROLLBACK")
            raise


def make_receipt(mode):
    store = Store()
    db = store.db
    expires = datetime.now(timezone.utc) + timedelta(hours=(-1 if mode == "expired" else 1))
    lease_status = "released" if mode == "released" else "active"
    worker = "other" if mode == "wrong-worker" else "worker"
    db.execute("INSERT INTO command_outbox VALUES('p','cmd','worker','shift','assignment','published')")
    db.execute("INSERT INTO shift_assignments VALUES('p','assignment',?,'worker','task',1,'assigned')",
               ('other-shift' if mode == 'wrong-shift' else 'shift',))
    db.execute("INSERT INTO leases VALUES('p','task',1,?,?,?)",
               (worker, lease_status, expires.isoformat()))
    if mode == "superseded":
        db.execute("INSERT INTO leases VALUES('p','task',2,'other','active',?)",
                   ((datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),))
    workforce = Workforce(store, require_action=lambda *_: None)
    receipt = {"request_id": "receipt", "receipt_id": "r1", "command_id": "cmd",
               "worker_id": "worker", "shift_id": "shift", "kind": "completed",
               "result_digest": "a" * 64, "receipt_commit_sha": "b" * 40}
    return db, workforce, receipt


@pytest.mark.parametrize("mode", ["expired", "released", "superseded", "wrong-worker", "wrong-shift"])
def test_stale_generation_cannot_write_receipt_or_complete_command(mode):
    db, workforce, receipt = make_receipt(mode)
    try:
        with pytest.raises(WorkforceFault, match="lease"):
            workforce.observe_command_receipt(
                {"principal_id": "owner"}, "p", receipt, verify=lambda _: True)
        assert db.execute("SELECT COUNT(*) FROM command_receipts").fetchone()[0] == 0
        assert db.execute("SELECT state FROM command_outbox").fetchone()[0] == "published"
        assert db.execute("SELECT state FROM shift_assignments").fetchone()[0] == "assigned"
    finally:
        db.close()


def test_latest_live_lease_can_complete_verified_command():
    db, workforce, receipt = make_receipt("valid")
    try:
        assert workforce.observe_command_receipt(
            {"principal_id": "owner"}, "p", receipt, verify=lambda _: True) == {
                "command_id": "cmd", "state": "completed"}
        assert db.execute("SELECT COUNT(*) FROM command_receipts").fetchone()[0] == 1
        assert db.execute("SELECT state FROM shift_assignments").fetchone()[0] == "submitted"
    finally:
        db.close()
