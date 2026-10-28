"""Operator restore rehearsal receipts are never automatically off-host QA."""
import sqlite3
from pathlib import Path
import socket
import tempfile
import unittest

from infra.sqlite.backup import snapshot
from infra.sqlite.offhost_drill import run_drill
from studio_v16.db.sqlite.connection import ConnectionOwner
from studio_v16.db.sqlite.migrate import migrate


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 45), 'SQLite >=3.45 required')
class OperatorRestoreDrillC8(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'primary.db'
        self.owner = ConnectionOwner(self.source)
        self.addCleanup(self.owner.close)
        migrate(self.owner)
        self.backup_path = self.root / 'snapshot.db'
        snapshot(self.source, self.backup_path)

    def test_same_host_receipt_and_shadow_never_claim_offhost_proof(self):
        target = self.root / 'restore.db'
        receipt = run_drill(self.backup_path, target, claimed_source_host=socket.gethostname())
        self.assertEqual(receipt['schema'], 'SQLITE-RESTORE-DRILL/1')
        self.assertEqual(receipt['host_comparison'], 'SAME_HOST_REPORTED')
        self.assertFalse(receipt['off_host_verified'])
        self.assertFalse(receipt['cutover_authorized'])
        self.assertEqual(receipt['independent_qa'], 'NOT_RUN')
        self.assertEqual(receipt['restore_mode'], 'SHADOW')
        self.assertEqual(receipt['rpo_seconds'], 'UNMEASURED')
        self.assertEqual(receipt['foreign_key_violations'], 0)
        self.assertTrue(receipt['snapshot_sha256'])
        self.assertTrue(receipt['manifest_sha256'])

    def test_reported_other_host_remains_unattested_and_no_overwrite(self):
        target = self.root / 'restore.db'
        receipt = run_drill(self.backup_path, target, claimed_source_host='unverified-external-host')
        self.assertEqual(receipt['host_comparison'], 'DIFFERENT_HOST_REPORTED_UNATTESTED')
        self.assertFalse(receipt['off_host_verified'])
        with self.assertRaises(FileExistsError):
            run_drill(self.backup_path, target, claimed_source_host='unverified-external-host')
        self.assertEqual(receipt['restore_mode'], 'SHADOW')


if __name__ == '__main__':
    unittest.main()
