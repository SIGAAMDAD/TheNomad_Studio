"""SQLITE-01/TX-08 real-file acceptance checks at the source authority boundary.

Only observable local behavior is claimed. Provider and independent QA are separate.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from infra.sqlite.backup import logical_manifest, snapshot, verify_snapshot
from infra.sqlite.transfer import TABLES
from studio_v16.db.sqlite.connection import ConnectionOwner
from studio_v16.db.sqlite.migrate import MIGRATIONS, migrate
from tests.v16.db import test_sqlite_commands_c8 as fixture


CORE_SCHEMA_DIGEST = 'f360a865944b4c893ddd647d739c00e64a97d34768d363b34f8f47c4e45c59fb'


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 45), 'SQLite >=3.45 required')
class SQLiteSchemaC8(unittest.TestCase):
    def test_25_core_tables_constraints_and_ordered_migrations_replay_noop(self):
        self.assertEqual(len(TABLES), 25)
        with tempfile.TemporaryDirectory() as temp:
            with ConnectionOwner(Path(temp) / 'migration.db') as owner:
                paths = sorted(MIGRATIONS.glob('[0-9][0-9][0-9][0-9]_*.sql'))
                expected = [(p.stem, hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths]
                self.assertEqual(len(expected), 8)
                migrate(owner)
                with owner.connection(readonly=True) as conn:
                    ledger = conn.execute('SELECT version,checksum FROM schema_migrations ORDER BY version').fetchall()
                    self.assertEqual(ledger, expected)
                    all_rows = conn.execute('SELECT type,name,tbl_name,sql FROM sqlite_master '
                                            "WHERE type IN ('table','index','trigger') "
                                            'ORDER BY type,name').fetchall()
                    core_rows = [r for r in all_rows if r[2] in TABLES]
                    schema_sha256 = hashlib.sha256(json.dumps(core_rows, sort_keys=True,
                                                 separators=(',', ':')).encode()).hexdigest()
                    self.assertEqual(schema_sha256, CORE_SCHEMA_DIGEST)
                    self.assertEqual(len(core_rows), 102)
                    table_flags = {r[1]: r[5] for r in conn.execute('PRAGMA table_list')}
                    self.assertTrue(all(table_flags[name] == 1 for name in TABLES))
                    self.assertEqual(sum(len(conn.execute('PRAGMA foreign_key_list("' + name + '")').fetchall())
                                         for name in TABLES), 36)
                    self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
                self.assertEqual(migrate(owner), [])
                with owner.connection(readonly=True) as conn:
                    self.assertEqual(conn.execute('SELECT version,checksum FROM schema_migrations '
                                                  'ORDER BY version').fetchall(), expected)


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 45), 'SQLite >=3.45 required')
class SQLiteBusyC8(unittest.TestCase):
    setUp = fixture.SQLiteCommandsC8.setUp
    claim = fixture.SQLiteCommandsC8.claim

    def test_busy_claim_denies_retryably_without_mutation_or_false_receipt(self):
        backup_path = Path(self.temp.name) / 'before_busy.db'
        original_manifest = snapshot(self.owner.path, backup_path)
        with self.owner.connection(readonly=True) as conn:
            before = logical_manifest(conn)
        lock = sqlite3.connect(self.owner.path, isolation_level=None, timeout=1)
        try:
            lock.execute('BEGIN IMMEDIATE')
            lock.execute("UPDATE jobs SET state='CLAIMED' WHERE job_id='job_test_001'")
            outcome = self.store.execute(self.claim(), fixture.CTX)
            self.assertEqual(outcome['result'], 'DENIED')
            self.assertEqual(outcome['denial_code'], 'RETRYABLE_BLOCKED')
            lock.rollback()
        finally:
            lock.close()
        with self.owner.connection(readonly=True) as conn:
            self.assertEqual(logical_manifest(conn), before)
            for table in ('attempts', 'resource_leases', 'command_results', 'response_job_starts', 'outbox'):
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT state FROM jobs WHERE job_id='job_test_001'").fetchone()[0], 'READY')
        self.assertEqual(verify_snapshot(backup_path)['file_sha256'], original_manifest['file_sha256'])
        after = self.store.execute(self.claim(), fixture.CTX)
        self.assertEqual(after['result'], 'COMMITTED')
        with self.owner.connection(readonly=True) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM command_results').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
