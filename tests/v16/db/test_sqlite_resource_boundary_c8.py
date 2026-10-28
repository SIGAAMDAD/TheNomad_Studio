"""STOP-04: missing STOP at a response boundary leaves durable UNKNOWN/YIELD."""
import sqlite3
import unittest

from studio_v16.communications.service import CoordinationService
from studio_v16.db.sqlite.connection import ConnectionOwner
from studio_v16.db.sqlite.store import SQLiteStore
from studio_v16.db.sqlite.types import parse_json
from tests.v16.db import test_sqlite_missing_receipts_c8 as fixture


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 45), 'SQLite >= 3.45 required')
class SQLiteResourceBoundaryC8(unittest.TestCase):
    setUp = fixture.SQLiteMissingReceiptC8.setUp
    _start_job_without_receipt = fixture.SQLiteMissingReceiptC8._start_job_without_receipt

    def test_platform_boundary_preserves_unreported_job_and_manual_resume(self):
        self._start_job_without_receipt()
        resume = {'checkpoint': 'urn:nomad:durable:checkpoint',
                  'response_id': 'resp-missing', 'pending_attempt': 'attempt-missing',
                  'reason': 'platform-tool-boundary', 'manual_resume_required': True}
        card = self.coord.finish_response('resp-missing', self.worker_ctx,
                                          stop=False, resume_capsule=resume,
                                          failures=[{'stage': 'EXECUTION', 'error_type': 'Unavailable'}])
        self.assertEqual(card['termination'], 'RESOURCE_BOUNDARY_YIELD')
        self.assertEqual(card['jobs'][0]['outcome'], 'UNKNOWN')
        self.assertIsNone(card['control_observed'])
        path, policy = self.owner.path, self.coord._policy
        self.owner.close()
        with ConnectionOwner(path) as reopened:
            store = SQLiteStore(reopened)
            coord = CoordinationService(store, policy=policy)
            with reopened.connection(readonly=True) as db:
                row = db.execute("SELECT status,resume_json,report_json FROM response_attempts WHERE response_id='resp-missing'").fetchone()
                self.assertEqual(row[0], 'YIELDED')
                self.assertEqual(parse_json(row[1]), resume)
                self.assertEqual(parse_json(row[2]), card)
                self.assertEqual(db.execute("SELECT state FROM jobs WHERE job_id='job-missing'").fetchone(), ('CLAIMED',))
                self.assertEqual(db.execute('SELECT COUNT(*) FROM response_attempt_jobs').fetchone()[0], 0)
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone(), ('ok',))
            self.assertEqual(coord.finish_response('resp-missing',self.worker_ctx,
                                                   stop=False,resume_capsule=resume), card)
            self.assertEqual(coord.start_response('resp-manual-resume','worker-one','design',self.worker_ctx),'RUNNING')
            with reopened.connection(readonly=True) as db:
                self.assertEqual(db.execute("SELECT status FROM response_attempts WHERE response_id='resp-missing'").fetchone(), ('YIELDED',))
                self.assertEqual(db.execute("SELECT status FROM response_attempts WHERE response_id='resp-manual-resume'").fetchone(), ('RUNNING',))
                self.assertEqual(db.execute('SELECT COUNT(*) FROM response_attempt_jobs').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
