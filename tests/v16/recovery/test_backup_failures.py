"""Local snapshot and restore failure handling; no off-host guarantees."""
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from infra.sqlite.backup import restore, snapshot, verify_snapshot
from studio_v16.db.sqlite.connection import ConnectionOwner
from studio_v16.db.sqlite.migrate import migrate


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 45), 'SQLite >=3.45 required')
class LocalBackupFailureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / 'authority.db'
        self.owner = ConnectionOwner(self.db)
        self.addCleanup(self.owner.close)
        migrate(self.owner)
        self.snapshot = self.root / 'backup.db'
        self.manifest = Path(str(self.snapshot) + '.manifest.json')

    def test_missing_snapshot_or_manifest_never_creates_restore(self):
        target = self.root / 'restore.db'
        with self.assertRaises(FileNotFoundError):
            restore(self.snapshot, target)
        self.assertFalse(target.exists())
        snapshot(self.db, self.snapshot)
        self.manifest.unlink()
        with self.assertRaises(FileNotFoundError):
            restore(self.snapshot, target)
        self.assertFalse(target.exists())

    def test_logical_manifest_tamper_is_rejected(self):
        snapshot(self.db, self.snapshot)
        manifest = json.loads(self.manifest.read_text())
        manifest['logical']['tables']['principals']['rows'] += 1
        self.manifest.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'BACKUP_LOGICAL_MISMATCH'):
            verify_snapshot(self.snapshot)
        target = self.root / 'restore.db'
        with self.assertRaisesRegex(ValueError, 'BACKUP_LOGICAL_MISMATCH'):
            restore(self.snapshot, target)
        self.assertFalse(target.exists())

    def test_existing_destination_is_never_overwritten(self):
        snapshot(self.db, self.snapshot)
        target = self.root / 'restore.db'
        target.write_bytes(b'existing evidence')
        with self.assertRaises(FileExistsError):
            restore(self.snapshot, target)
        self.assertEqual(target.read_bytes(), b'existing evidence')

    def test_online_snapshot_is_consistent_during_writer_activity(self):
        with self.owner.write() as (db, _):
            db.execute('CREATE TABLE backup_writes (id INTEGER PRIMARY KEY) STRICT')
        started = threading.Event()
        stop = threading.Event()
        failures = []
        def writer():
            try:
                number = 0
                while not stop.is_set():
                    with self.owner.write() as (db, _):
                        db.execute('INSERT INTO backup_writes VALUES (?)', (number,))
                    number += 1
                    started.set()
            except Exception as exc:
                failures.append(repr(exc))
                started.set()
        thread = threading.Thread(target=writer)
        thread.start()
        try:
            self.assertTrue(started.wait(10))
            self.assertEqual(failures, [])
            manifest = snapshot(self.db, self.snapshot)
        finally:
            stop.set()
            thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(json.dumps(verify_snapshot(self.snapshot)['logical'], sort_keys=True),
                         json.dumps(manifest['logical'], sort_keys=True))
        with self.owner.connection(readonly=True) as db:
            live_count = db.execute('SELECT COUNT(*) FROM backup_writes').fetchone()[0]
        backed_up = manifest['logical']['tables']['backup_writes']['rows']
        self.assertGreaterEqual(backed_up, 1)
        self.assertLessEqual(backed_up, live_count)
        target = self.root / 'restore.db'
        self.assertEqual(restore(self.snapshot, target)['mode'], 'SHADOW')
        with sqlite3.connect(target) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM backup_writes').fetchone()[0], backed_up)

    def test_restore_remains_shadow_and_never_attests_offhost(self):
        manifest = snapshot(self.db, self.snapshot)
        self.assertFalse(manifest['off_host_verified'])
        self.assertEqual((manifest['rpo'], manifest['rto']), ('UNMEASURED', 'UNMEASURED'))
        target = self.root / 'restore.db'
        receipt = restore(self.snapshot, target)
        self.assertEqual((receipt['mode'], receipt['activation']), ('SHADOW', 'OWNER_GATED'))
        with sqlite3.connect(target) as db:
            self.assertEqual(db.execute('SELECT mode FROM authority_mode').fetchone()[0], 'SHADOW')
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])


if __name__ == '__main__':
    unittest.main()
