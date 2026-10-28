"""C8 real-file SQLite safety and disaster-recovery regression tests.

Runs without remote credentials; all databases live in isolated private directories.
"""
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from infra.sqlite.backup import restore, snapshot, verify_snapshot
from studio_v16.db.sqlite.connection import ConnectionOwner, Unavailable
from studio_v16.db.sqlite.migrate import migrate
from studio_v16.db.sqlite.store import SQLiteStore
from studio_v16.db.store import GatewayContext, Denied
from studio_v16.communications.service import CoordinationService, Scope


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 45), 'SQLite >=3.45 required')
class SQLiteC8Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / 'authority.db'
        self.owner = ConnectionOwner(self.db)
        self.addCleanup(self.owner.close)
        self.store = SQLiteStore(self.owner)

    def test_migration_idempotent_and_complete(self):
        self.assertEqual(migrate(self.owner), [])
        with self.owner.connection(readonly=True) as conn:
            versions = conn.execute('SELECT version FROM schema_migrations ORDER BY version').fetchall()
            self.assertEqual(len(versions), 8)
            self.assertEqual(versions[-1][0], '0008_response_claim_fence')
            self.assertEqual(conn.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            self.assertEqual(conn.execute('PRAGMA synchronous').fetchone()[0], 2)
            self.assertEqual(conn.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_migration_checksum_tamper_rejected(self):
        # Immutable applied migration rows must reject tampering, not just replays.
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'IMMUTABLE_EVIDENCE'):
            with self.owner.write() as (conn, _):
                conn.execute("UPDATE schema_migrations SET checksum=? WHERE version='0001_core_sqlite'", ('0'*64,))
        self.assertEqual(migrate(self.owner), [])

    def test_failed_migration_rolls_back_all_changes(self):
        migrations = self.root / 'migrations'
        migrations.mkdir()
        (migrations / '0001_bad.sql').write_text('CREATE TABLE should_rollback(id INTEGER);\nSELECT * FROM missing_table;\n')
        # Run against an empty database: an already-migrated ledger correctly
        # rejects unrelated scripts before it ever reaches the SQL error.
        other = self.root / 'migration_failure.db'
        with ConnectionOwner(other) as isolated:
            with self.assertRaises(Unavailable):
                migrate(isolated, directory=migrations)
            with isolated.connection(readonly=True) as conn:
                self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='should_rollback'").fetchone())

    def test_only_one_process_owner(self):
        with self.assertRaises((BlockingIOError, OSError)):
            ConnectionOwner(self.db)

    def test_one_writer_and_atomic_rollback(self):
        with self.owner.write() as (conn, _):
            conn.execute('CREATE TABLE c8_atomic(n INTEGER NOT NULL) STRICT')
        errors = []
        def worker():
            try:
                with self.owner.write() as (conn, _):
                    conn.execute('INSERT INTO c8_atomic VALUES(1)')
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=worker) for _ in range(24)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertFalse(errors)
        with self.assertRaisesRegex(RuntimeError, 'rollback'):
            with self.owner.write() as (conn, _):
                conn.execute('INSERT INTO c8_atomic VALUES(2)')
                raise RuntimeError('rollback')
        with self.owner.connection(readonly=True) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM c8_atomic').fetchone()[0], 24)

    def test_immutable_department_evidence_and_foreign_key(self):
        with self.owner.write() as (conn, now):
            conn.execute("INSERT INTO principals(principal_id,issuer,subject,kind,status) VALUES('p','i','s','WORKER','ACTIVE')")
            conn.execute("INSERT INTO event_journal(event_id,producer,transport_delivery_id,partition_key,payload_sha256,canonical_event_json) VALUES('ev','p','delivery','q',?,'{}')", ('a'*64,))
            conn.execute("INSERT INTO department_events(event_id,sender_principal_id,recipient_department,recipient_id,metadata_json,created_at) VALUES('ev','p','design','p','{}',?)", (now,))
        with self.assertRaisesRegex(sqlite3.DatabaseError, 'IMMUTABLE_EVIDENCE'):
            with self.owner.write() as (conn, _):
                conn.execute("DELETE FROM department_events WHERE event_id='ev'")
        with self.assertRaises(sqlite3.IntegrityError):
            with self.owner.write() as (conn, now):
                conn.execute("INSERT INTO department_events(event_id,sender_principal_id,recipient_department,recipient_id,metadata_json,created_at) VALUES('missing','p','design','p','{}',?)", (now,))

    def test_bad_command_fails_closed_and_does_not_journal(self):
        result = self.store.execute({'command_kind':'NOT_A_REAL_COMMAND'}, GatewayContext('untrusted','',False))
        self.assertEqual(result['result'], 'DENIED')
        with self.owner.connection(readonly=True) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM event_journal').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM command_results').fetchone()[0], 0)

    def test_backup_restore_roundtrip_and_shadow_mode(self):
        with self.owner.write() as (conn, _):
            conn.execute("INSERT INTO principals(principal_id,issuer,subject,kind,status) VALUES('p','i','s','WORKER','ACTIVE')")
        backup = self.root / 'snapshot.db'
        manifest = snapshot(self.db, backup)
        self.assertEqual(manifest['schema'], 'SQLITE-SNAPSHOT/1')
        verify_snapshot(backup)
        target = self.root / 'restored.db'
        receipt = restore(backup, target)
        self.assertEqual(receipt['mode'], 'SHADOW')
        with sqlite3.connect(target) as conn:
            self.assertEqual(conn.execute("SELECT status FROM principals WHERE principal_id='p'").fetchone()[0], 'ACTIVE')
            self.assertEqual(conn.execute('SELECT mode FROM authority_mode WHERE singleton=1').fetchone()[0], 'SHADOW')
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_corrupted_snapshot_refused(self):
        backup = self.root / 'tamper.db'
        snapshot(self.db, backup)
        os.chmod(backup, 0o600)
        with backup.open('ab') as out:
            out.write(b'c8-tamper')
        with self.assertRaisesRegex(ValueError, 'BACKUP_HASH_MISMATCH'):
            restore(backup, self.root / 'not_restored.db')
        self.assertFalse((self.root / 'not_restored.db').exists())

    def _coord(self):
        sender = GatewayContext('worker', 'trusted', True)
        recipient = GatewayContext('recipient', 'trusted', True)
        policy = {
            'worker': Scope(frozenset({'design'}), frozenset({'ROUTE', 'RESPONSE'})),
            'recipient': Scope(frozenset({'design'}), frozenset({'INBOX'})),
        }
        with self.owner.write() as (conn, _):
            conn.execute("INSERT INTO principals(principal_id,issuer,subject,kind,status) VALUES('worker','trusted','w','WORKER','ACTIVE')")
            conn.execute("INSERT INTO principals(principal_id,issuer,subject,kind,status) VALUES('recipient','trusted','r','WORKER','ACTIVE')")
            for name, number in [('e1', 'd1'), ('e2', 'd2')]:
                conn.execute('INSERT INTO event_journal(event_id,producer,transport_delivery_id,partition_key,payload_sha256,canonical_event_json) VALUES(?,?,?,?,?,?)', (name, 'worker', number, 'design', 'a'*64, '{}'))
        return CoordinationService(self.store, policy=policy), sender, recipient

    def test_department_inbox_requires_delivery_and_ordered_ack(self):
        coord, sender, recipient = self._coord()
        one = coord.route_event('e1', 'design', 'recipient', {'task':'one'}, sender)
        two = coord.route_event('e2', 'design', 'recipient', {'task':'two'}, sender)
        with self.assertRaises(Denied):
            coord.ack('design', one, recipient)
        self.assertEqual([r['event_id'] for r in coord.inbox('design', recipient)], ['e1', 'e2'])
        with self.assertRaises(Denied):
            coord.ack('design', two, recipient)
        self.assertEqual(coord.ack('design', one, recipient), one)
        self.assertEqual(coord.ack('design', two, recipient), two)
        self.assertEqual(coord.inbox('design', recipient), [])

    def test_department_routing_idempotency_and_conflict(self):
        coord, sender, recipient = self._coord()
        one = coord.route_event('e1', 'design', 'recipient', {'task':'one'}, sender)
        self.assertEqual(one, coord.route_event('e1', 'design', 'recipient', {'task':'one'}, sender))
        with self.assertRaises(Denied):
            coord.route_event('e1', 'design', 'recipient', {'task':'altered'}, sender)
        with self.assertRaises(Denied):
            coord.inbox('design', sender)
        with self.owner.connection(readonly=True) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM department_events').fetchone()[0], 1)

    def test_response_is_terminal_and_immutable(self):
        coord, worker, _ = self._coord()
        self.assertEqual(coord.start_response('resp', 'cw-1', 'design', worker), 'RUNNING')
        self.assertEqual(coord.start_response('resp', 'cw-1', 'design', worker), 'RUNNING')
        with self.assertRaises(Denied):
            coord.start_response('resp', 'cw-2', 'design', worker)
        with self.owner.write() as (conn, _):
            conn.execute("UPDATE response_attempts SET status='STOPPED' WHERE response_id='resp'")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'IMMUTABLE_RESPONSE_RECEIPT'):
            with self.owner.write() as (conn, _):
                conn.execute("UPDATE response_attempts SET status='RUNNING' WHERE response_id='resp'")

    def test_leader_epoch_and_document_generation_fences(self):
        with self.owner.write() as (conn, _):
            conn.execute("INSERT INTO principals(principal_id,issuer,subject,kind,status) VALUES('p','issuer','p','WORKER','ACTIVE')")
            conn.execute("INSERT INTO leader_state(department,principal_id,leader_epoch,updated_at) VALUES('design','p',2,1)")
            conn.execute("INSERT INTO document_refs(file_id,revision,exported_sha256,owner_principal_id,department,reader_scope_json,source_ref,created_at) VALUES('file','rev',?,'p','design',?,'drive',1)", ('a'*64, '[\"p\"]'))
            conn.execute("INSERT INTO document_heads(file_id,revision,leader_epoch,writer_generation) VALUES('file','rev',2,3)")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'STALE_LEADER'):
            with self.owner.write() as (conn, _):
                conn.execute("UPDATE leader_state SET leader_epoch=1 WHERE department='design'")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'STALE_DOCUMENT_WRITER'):
            with self.owner.write() as (conn, _):
                conn.execute("UPDATE document_heads SET writer_generation=2 WHERE file_id='file'")

    def test_owner_after_close_fails_closed(self):
        self.owner.close()
        with self.assertRaises(Unavailable):
            with self.owner.write():
                pass


if __name__ == '__main__':
    unittest.main()
