"""Additional real-file SQLite contention and migration safety tests."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import select
import sqlite3
import subprocess
import sys
import unittest

from studio_v16.db.sqlite.connection import ConnectionOwner, Unavailable
from studio_v16.db.sqlite.migrate import MIGRATIONS, migrate
from tests.v16.db import test_sqlite_commands_c8 as fixtures


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 45), 'SQLite >=3.45 required')
class SQLiteSameCommandC8(unittest.TestCase):
    setUp = fixtures.SQLiteCommandsC8.setUp
    claim = fixtures.SQLiteCommandsC8.claim
    def test_hundred_simultaneous_same_key_claims_commit_once(self):
        claim = self.claim()
        with ThreadPoolExecutor(max_workers=25) as pool:
            results = list(pool.map(lambda _: self.store.execute(claim, fixtures.CTX), range(100)))
        self.assertEqual(sum(r['result'] == 'COMMITTED' for r in results), 1)
        self.assertEqual(sum(r['result'] == 'DUPLICATE' for r in results), 99)
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM resource_leases').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM command_results').fetchone()[0], 1)


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 45), 'SQLite >=3.45 required')
class SQLiteProcessAndMigrationC8(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.owner = ConnectionOwner(self.root / 'authority.db')
        self.addCleanup(self.owner.close)
        migrate(self.owner)

    def test_duplicate_active_assignment_is_rejected_atomically(self):
        with self.owner.write() as (db, _):
            db.execute("INSERT INTO jobs(job_id,parent_issue_ref,owner_head_id,state,approved_scope_hash,authority_revision,source_repo,source_base_sha,resource_key) VALUES('job','issue','head','READY',?,'rev','repo',?,'key')", ('a'*64, 'b'*40))
            db.execute("INSERT INTO worker_assignments(job_id,worker_id,assignment_epoch,status) VALUES('job','worker-1',1,'ACTIVE')")
        with self.assertRaises(sqlite3.IntegrityError):
            with self.owner.write() as (db, _):
                db.execute("INSERT INTO worker_assignments(job_id,worker_id,assignment_epoch,status) VALUES('job','worker-2',2,'ACTIVE')")
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute("SELECT worker_id FROM worker_assignments WHERE status='ACTIVE'").fetchall(), [('worker-1',)])
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_corrupt_database_is_not_opened_for_writes(self):
        path = self.root / 'corrupt.db'
        path.write_bytes(b'not a sqlite database')
        with self.assertRaises((Unavailable, sqlite3.DatabaseError)):
            ConnectionOwner(path)
        self.assertEqual(path.read_bytes(), b'not a sqlite database')

    def test_modified_migration_file_is_rejected_without_changes(self):
        migrations = self.root / 'migrations'
        shutil.copytree(MIGRATIONS, migrations)
        script = sorted(migrations.glob('*.sql'))[0]
        script.write_bytes(script.read_bytes() + b'\n-- modified after application\n')
        with self.owner.connection(readonly=True) as db:
            before = db.execute('SELECT version, checksum FROM schema_migrations ORDER BY version').fetchall()
        with self.assertRaisesRegex(ValueError, 'MIGRATION_CHECKSUM_MISMATCH'):
            migrate(self.owner, directory=migrations)
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(before, db.execute('SELECT version, checksum FROM schema_migrations ORDER BY version').fetchall())
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone(), ('ok',))

    def test_external_writer_lock_returns_busy_and_recovers(self):
        other = sqlite3.connect(str(self.owner.path), isolation_level=None)
        self.addCleanup(other.close)
        other.execute('BEGIN IMMEDIATE')
        try:
            with self.assertRaisesRegex(Unavailable, 'SQLITE_BUSY'):
                with self.owner.write() as (db, _):
                    db.execute('CREATE TABLE blocked (id INTEGER)')
        finally:
            other.rollback()
        with self.owner.write() as (db, _):
            db.execute('CREATE TABLE unblocked (id INTEGER)')
        with self.owner.connection(readonly=True) as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='blocked'").fetchone())
            self.assertIsNotNone(db.execute("SELECT name FROM sqlite_master WHERE name='unblocked'").fetchone())

    def test_wal_reader_retains_snapshot_while_writer_commits(self):
        with self.owner.write() as (db, _):
            db.execute('CREATE TABLE snapshot_test (id INTEGER)')
            db.execute('INSERT INTO snapshot_test VALUES (1)')
        with self.owner.connection(readonly=True) as reader:
            reader.execute('BEGIN')
            self.assertEqual(reader.execute('SELECT COUNT(*) FROM snapshot_test').fetchone()[0], 1)
            with self.owner.write() as (writer, _):
                writer.execute('INSERT INTO snapshot_test VALUES (2)')
            self.assertEqual(reader.execute('SELECT COUNT(*) FROM snapshot_test').fetchone()[0], 1)
            reader.execute('COMMIT')
            self.assertEqual(reader.execute('SELECT COUNT(*) FROM snapshot_test').fetchone()[0], 2)

    def test_process_killed_before_commit_rolls_back(self):
        path = self.root / 'crash.db'
        code = """
import sys, time
from studio_v16.db.sqlite.connection import ConnectionOwner
with ConnectionOwner(sys.argv[1]) as owner:
    with owner.write() as (db, _):
        db.execute('CREATE TABLE staged (id INTEGER)')
        db.execute('INSERT INTO staged VALUES (1)')
        print('TRANSACTION_OPEN', flush=True)
        time.sleep(30)
"""
        process = subprocess.Popen([sys.executable, '-c', code, str(path)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            readable, _, _ = select.select([process.stdout], [], [], 15)
            self.assertTrue(readable, 'child did not reach open transaction')
            self.assertEqual(process.stdout.readline().strip(), 'TRANSACTION_OPEN')
            process.kill()
            process.communicate(timeout=10)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)
        with ConnectionOwner(path) as recovered:
            with recovered.connection(readonly=True) as db:
                self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='staged'").fetchone())
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone(), ('ok',))

    def test_process_exits_after_commit_data_survives(self):
        path = self.root / 'committed.db'
        code = """
import os, sys
from studio_v16.db.sqlite.connection import ConnectionOwner
owner = ConnectionOwner(sys.argv[1])
with owner.write() as (db, _):
    db.execute('CREATE TABLE durable (id INTEGER)')
    db.execute('INSERT INTO durable VALUES (1)')
os._exit(0)
"""
        result = subprocess.run([sys.executable, '-c', code, str(path)],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        with ConnectionOwner(path) as recovered:
            with recovered.connection(readonly=True) as db:
                self.assertEqual(db.execute('SELECT id FROM durable').fetchall(), [(1,)])
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone(), ('ok',))

    def test_separate_process_cannot_acquire_existing_owner(self):
        code = '''\nimport sys\nfrom studio_v16.db.sqlite.connection import ConnectionOwner\ntry:\n    owner = ConnectionOwner(sys.argv[1])\nexcept (BlockingIOError, OSError):\n    sys.exit(0)\nelse:\n    owner.close()\n    sys.exit(9)\n'''
        result = subprocess.run([sys.executable, '-c', code, str(self.owner.path)],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        with self.owner.write() as (db, _):
            db.execute('CREATE TABLE owner_still_usable (id INTEGER)')


if __name__ == '__main__':
    unittest.main()
