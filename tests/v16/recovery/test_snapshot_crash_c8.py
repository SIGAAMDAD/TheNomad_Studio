"""Real-file C8 backup crash boundaries and filesystem failure injection.

These are same-host regression fixtures, not an independent off-host drill.
"""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from infra.sqlite import backup
from studio_v16.db.sqlite.connection import ConnectionOwner
from studio_v16.db.sqlite.migrate import migrate


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 45), 'SQLite >= 3.45 required')
class SnapshotCrashC8(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'authority.db'
        self.owner = ConnectionOwner(self.source)
        self.addCleanup(self.owner.close)
        migrate(self.owner)
        with self.owner.write() as (conn, _):
            conn.execute("INSERT INTO principals(principal_id,issuer,subject,kind,status) "
                         "VALUES('fault-principal','issuer','subject','WORKER','ACTIVE')")
        self.backup_path = self.root / 'snapshot.db'
        self.manifest = Path(str(self.backup_path) + '.manifest.json')

    def test_manifest_directory_fsync_failure_removes_both_files_and_allows_retry(self):
        real_sync = backup.sync_file

        def fail_manifest_sync(path):
            if Path(path) == self.manifest:
                raise OSError('injected manifest directory sync failure')
            return real_sync(path)

        with mock.patch.object(backup, 'sync_file', side_effect=fail_manifest_sync):
            with self.assertRaisesRegex(OSError, 'injected manifest directory'):
                backup.snapshot(self.source, self.backup_path)
        self.assertFalse(self.backup_path.exists())
        self.assertFalse(self.manifest.exists(), 'a stale manifest must not block a safe retry')
        backup.snapshot(self.source, self.backup_path)
        self.assertEqual(backup.verify_snapshot(self.backup_path)['schema'], 'SQLITE-SNAPSHOT/1')

    def test_manifest_file_fsync_failure_leaves_no_claimed_backup(self):
        original_fsync = os.fsync

        def fail_manifest_file_fsync(fd):
            if self.manifest.exists() and os.fstat(fd).st_ino == self.manifest.stat().st_ino:
                raise OSError('injected manifest content fsync failure')
            return original_fsync(fd)

        with mock.patch.object(backup.os, 'fsync', side_effect=fail_manifest_file_fsync):
            with self.assertRaisesRegex(OSError, 'manifest content fsync'):
                backup.snapshot(self.source, self.backup_path)
        self.assertFalse(self.backup_path.exists())
        self.assertFalse(self.manifest.exists())
        backup.snapshot(self.source, self.backup_path)
        backup.verify_snapshot(self.backup_path)

    def test_existing_manifest_is_never_deleted_by_failed_snapshot(self):
        self.manifest.write_text('previous immutable marker')
        with self.assertRaises(FileExistsError):
            backup.snapshot(self.source, self.backup_path)
        self.assertEqual(self.manifest.read_text(), 'previous immutable marker')
        self.assertFalse(self.backup_path.exists())

    def _kill_snapshot_child(self, when):
        code = r'''
import os, sys
from pathlib import Path
from infra.sqlite import backup
original = backup.sync_file
def kill_at_boundary(path):
    original(path)
    is_manifest = str(path).endswith('.manifest.json')
    if is_manifest == (sys.argv[3] == 'after_manifest'):
        os._exit(93)
backup.sync_file = kill_at_boundary
backup.snapshot(Path(sys.argv[1]), Path(sys.argv[2]))
'''
        result = subprocess.run([sys.executable, '-c', code, str(self.source),
                                 str(self.backup_path), when], check=False,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 93, result.stderr)

    def test_abrupt_exit_after_snapshot_file_sync_cannot_restore_without_manifest(self):
        self._kill_snapshot_child('before_manifest')
        self.assertTrue(self.backup_path.exists())
        self.assertFalse(self.manifest.exists())
        restored = self.root / 'refused.db'
        with self.assertRaises(FileNotFoundError):
            backup.restore(self.backup_path, restored)
        self.assertFalse(restored.exists())

    def test_abrupt_exit_after_manifest_sync_can_verify_and_restore_shadow(self):
        self._kill_snapshot_child('after_manifest')
        self.assertTrue(self.manifest.exists())
        evidence = backup.verify_snapshot(self.backup_path)
        self.assertFalse(evidence['off_host_verified'])
        restored = self.root / 'verified-restore.db'
        receipt = backup.restore(self.backup_path, restored)
        self.assertEqual(receipt['mode'], 'SHADOW')
        with sqlite3.connect(restored) as conn:
            self.assertEqual(conn.execute("SELECT status FROM principals "
                                          "WHERE principal_id='fault-principal'").fetchone()[0], 'ACTIVE')
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(conn.execute('SELECT mode FROM authority_mode WHERE singleton=1').fetchone()[0], 'SHADOW')


if __name__ == '__main__':
    unittest.main()
