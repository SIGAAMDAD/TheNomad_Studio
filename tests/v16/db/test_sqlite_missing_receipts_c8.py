"""Real SQLite evidence for missing response-job receipts and derived status.

These cases do not attest a deployed auditor or an external owner alert.
"""
import sqlite3
import unittest

from tests.v16.db import test_sqlite_watchdog_c8 as fixture


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 45), 'SQLite >= 3.45 required')
class SQLiteMissingReceiptC8(unittest.TestCase):
    setUp = fixture.SQLiteWatchdogC8.setUp

    def _start_job_without_receipt(self, *, aged=False):
        self.assertEqual(self.coord.start_response('resp-missing', 'worker-one', 'design',
                                                   self.worker_ctx), 'RUNNING')
        with self.owner.write() as (conn, now):
            conn.execute('''INSERT INTO jobs(job_id,parent_issue_ref,owner_head_id,state,
                         approved_scope_hash,authority_revision,source_repo,source_base_sha,resource_key)
                         VALUES('job-missing','issue-missing','head','CLAIMED',?, 'rev',?,?,?)''',
                         ('a'*64, 'SIGAAMDAD/TheNomad_Studio', 'b'*40, 'src/missing'))
            conn.execute("INSERT INTO worker_assignments(job_id,worker_id,assignment_epoch,status) VALUES('job-missing','worker-one',1,'ACTIVE')")
            conn.execute('''INSERT INTO attempts(attempt_id,job_id,worker_id,principal_id,
                         assignment_epoch,status,source_base_sha)
                         VALUES('attempt-missing','job-missing','worker-one','worker',1,'STARTED',?)''',
                         ('b'*40,))
            started = now - 120*1000000 if aged else now
            conn.execute('''INSERT INTO response_job_starts(response_id,job_id,attempt_id,started_at)
                         VALUES('resp-missing','job-missing','attempt-missing',?)''', (started,))

    def test_reported_terminal_response_does_not_erase_derived_missing_job(self):
        self._start_job_without_receipt()
        card = self.coord.finish_response('resp-missing', self.worker_ctx,
                                          resume_capsule={'reason': 'resource-boundary'})
        self.assertEqual(card['termination'], 'RESOURCE_BOUNDARY_YIELD')
        self.assertEqual(card['jobs'][0]['outcome'], 'UNKNOWN')
        view = self.views.snapshot('design', self.audit_ctx)
        self.assertEqual(view['responses'][0]['observation'], 'YIELDED')
        self.assertEqual(view['missing_job_reports'], [dict(
            response_id='resp-missing', job_id='job-missing',
            attempt_id='attempt-missing', status='UNKNOWN')])
        self.assertEqual(view['job_states'], {})  # Unbound work is omitted from projection.
        audit = self.watch.record_audit('design', self.audit_ctx)
        self.assertEqual(audit['verdict'], 'DEGRADED')
        inspected = self.watch.inspect('design', self.watch_ctx)
        self.assertEqual(inspected['status'], 'DEGRADED')
        with self.owner.connection(readonly=True) as conn:
            found = conn.execute('''SELECT subject_id,status,owner_packet_json
                       FROM recovery_cases WHERE reason='JOB_REPORT_MISSING' ''').fetchall()
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0][:2], ('attempt-missing', 'ALERT_DELIVERY_PENDING'))
            for item in ('resp-missing', 'job-missing', 'attempt-missing'):
                self.assertIn(item, found[0][2])
            self.assertEqual(conn.execute("SELECT state FROM jobs WHERE job_id='job-missing'").fetchone(),
                             ('CLAIMED',))

    def test_fresh_inflight_job_receipt_grace_and_stale_recovery_packet(self):
        self._start_job_without_receipt(aged=True)
        self.watch.record_audit('design', self.audit_ctx)
        self.watch.inspect('design', self.watch_ctx, response_stale_seconds=1200)
        with self.owner.connection(readonly=True) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM recovery_cases WHERE reason='JOB_REPORT_MISSING'").fetchone()[0], 0)
        result = self.watch.inspect('design', self.watch_ctx, response_stale_seconds=60)
        self.assertEqual(result['status'], 'DEGRADED')
        with self.owner.connection(readonly=True) as conn:
            packet = conn.execute("SELECT owner_packet_json FROM recovery_cases WHERE reason='JOB_REPORT_MISSING'").fetchone()
            self.assertIsNotNone(packet)
            self.assertIn('attempt-missing', packet[0])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM response_attempt_jobs").fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
