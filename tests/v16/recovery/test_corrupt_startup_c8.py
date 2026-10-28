"""C8 TX-10 fail-closed startup and backup-source validation regressions.

Real local SQLite files; these do not attest off-host RPO/RTO or WAL corruption
protection outside SQLite's integrity-check implementation.
"""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from infra.sqlite.backup import restore, snapshot, verify_snapshot
from studio_v16.db.sqlite.connection import ConnectionOwner, Unavailable
from studio_v16.db.sqlite.migrate import migrate


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 45), 'SQLite >=3.45 required')
class CorruptStartupAndRestoreC8(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'authority.db'
        self.backup = self.root / 'snapshot.db'
        self.target = self.root / 'recovered.db'
        self.owner = ConnectionOwner(self.source)
        self.addCleanup(self.owner.close)
        migrate(self.owner)
        with self.owner.write() as (conn, now):
            conn.execute("INSERT INTO principals(principal_id,issuer,subject,kind,status) "
                         "VALUES('tx10', 'fixture', 'source', 'WORKER', 'ACTIVE')")

    def test_corrupt_authority_file_fails_startup_without_replacing_it(self):
        self.owner.close()
        self.source.write_bytes(b'broken-sqlite-header-and-contents')
        before = self.source.read_bytes()
        with self.assertRaises((Unavailable, sqlite3.DatabaseError)):
            ConnectionOwner(self.source)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse(self.target.exists())

    def test_missing_snapshot_or_manifest_refuses_restore_without_target(self):
        with self.assertRaises(FileNotFoundError):
            restore(self.backup, self.target)
        self.assertFalse(self.target.exists())
        snapshot(self.source, self.backup)
        Path(str(self.backup) + '.manifest.json').unlink()
        with self.assertRaises(FileNotFoundError):
            restore(self.backup, self.target)
        self.assertFalse(self.target.exists())

    def test_snapshot_digest_mismatch_refuses_restore_and_preserves_source(self):
        manifest = snapshot(self.source, self.backup)
        original_sha = manifest['file_sha256']
        manifest_path = Path(str(self.backup) + '.manifest.json')
        altered = json.loads(manifest_path.read_text())
        altered['file_sha256'] = '0'*64
        manifest_path.write_text(json.dumps(altered))
        with self.assertRaisesRegex(ValueError, 'BACKUP_HASH_MISMATCH'):
            restore(self.backup, self.target)
        self.assertFalse(self.target.exists())
        with self.owner.connection(readonly=True) as conn:
            self.assertEqual(conn.execute("SELECT status FROM principals WHERE principal_id='tx10'").fetchone(), ('ACTIVE',))
        self.assertEqual(manifest['file_sha256'], original_sha)

    def test_verified_snapshot_recovers_shadow_without_second_active_authority(self):
        snapshot(self.source, self.backup)
        evidence = verify_snapshot(self.backup)
        self.assertFalse(evidence['off_host_verified'])
        receipt = restore(self.backup, self.target)
        self.assertEqual(receipt['mode'], 'SHADOW')
        self.assertEqual(receipt['activation'], 'OWNER_GATED')
        with sqlite3.connect(self.target) as conn:
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone(), ('ok',))
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(conn.execute("SELECT mode FROM authority_mode WHERE singleton=1").fetchone(), ('SHADOW',))
            self.assertEqual(conn.execute("SELECT status FROM principals WHERE principal_id='tx10'").fetchone(), ('ACTIVE',))
        with self.owner.connection(readonly=True) as source_conn:
            self.assertEqual(source_conn.execute("SELECT mode FROM authority_mode WHERE singleton=1").fetchone(), ('SHADOW',))


if __name__ == '__main__':
    unittest.main()
