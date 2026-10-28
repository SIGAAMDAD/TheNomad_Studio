"""C8 watchdog fault isolation: independent identity, truthful absence, alert readback."""
import sqlite3
import tempfile
from pathlib import Path
import unittest

from studio_v16.communications.service import CoordinationService, Scope
from studio_v16.controller.views import ControllerViews
from studio_v16.watchdog.service import WatchdogService
from studio_v16.db.store import GatewayContext, Denied
from studio_v16.db.sqlite.connection import ConnectionOwner
from studio_v16.db.sqlite.store import SQLiteStore


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 45), 'SQLite >=3.45 required')
class SQLiteWatchdogC8(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.owner = ConnectionOwner(Path(self.tmp.name) / 'control.sqlite')
        self.addCleanup(self.owner.close)
        self.store = SQLiteStore(self.owner)
        self.audit_ctx = GatewayContext(principal_id='auditor', verified_gateway=True)
        self.watch_ctx = GatewayContext(principal_id='watchdog', verified_gateway=True)
        self.worker_ctx = GatewayContext(principal_id='worker', verified_gateway=True)
        with self.owner.write() as (db, _):
            for principal, kind in [('auditor', 'AUDITOR'), ('watchdog', 'WATCHDOG'), ('worker','WORKER')]:
                db.execute('INSERT INTO principals(principal_id,issuer,subject,kind,status) VALUES(?,?,?,?,?)',
                           (principal, 'fixture', principal, kind, 'ACTIVE'))
        self.coord = CoordinationService(self.store, policy={
            'auditor': Scope(frozenset({'design'}), frozenset({'AUDIT', 'CONTROLLER'})),
            'watchdog': Scope(frozenset({'design'}), frozenset({'WATCHDOG'})),
            'worker': Scope(frozenset({'design'}), frozenset({'RESPONSE'})),
        })
        self.views = ControllerViews(self.coord)
        self.watch = WatchdogService(self.coord, self.views,
                                     auditor_principal='auditor', watchdog_principal='watchdog',
                                     source_sha='a'*40)

    def test_distinct_watchdog_identity_required(self):
        with self.assertRaises(ValueError):
            WatchdogService(self.coord, self.views, auditor_principal='auditor',
                            watchdog_principal='auditor', source_sha='a'*40)
        with self.assertRaises(Denied):
            self.watch.inspect('design', self.audit_ctx)

    def test_missing_audit_is_degraded_not_silent_success(self):
        outcome = self.watch.inspect('design', self.watch_ctx)
        self.assertEqual(outcome['status'], 'DEGRADED')
        self.assertEqual(outcome['auto_restart'], 'UNSUPPORTED')
        self.assertEqual(outcome['independent_host_monitor'], 'NOT_ATTESTED')
        self.assertEqual(len(outcome['cases']), 1)
        again = self.watch.inspect('design', self.watch_ctx)
        self.assertEqual(again['cases'], outcome['cases'])
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM recovery_cases').fetchone()[0], 1)

    def test_observed_audit_clears_missing_audit_signal(self):
        audit = self.watch.record_audit('design', self.audit_ctx)
        self.assertEqual(audit['verdict'], 'OBSERVED')
        status = self.watch.inspect('design', self.watch_ctx)
        self.assertEqual(status['status'], 'OBSERVED')
        self.assertEqual(status['cases'], [])

    def test_unverified_alert_remains_pending_until_readback(self):
        pending = self.watch.inspect('design', self.watch_ctx)
        case = pending['cases'][0]
        attempted = self.watch.deliver_pending('design', self.watch_ctx,
                                               lambda case_id, packet: {'case_id':case_id,'accepted':True})
        self.assertEqual(attempted[0]['status'], 'ALERT_DELIVERY_PENDING')
        with self.owner.write() as (db, now):
            db.execute('UPDATE recovery_cases SET next_retry_at=? WHERE case_id=?', (now,case))
        confirmed = self.watch.deliver_pending('design', self.watch_ctx,
                                               lambda case_id, packet: {'case_id':case_id,'readback_verified':True})
        self.assertEqual(confirmed[0]['status'],'ALERTED')
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT status FROM recovery_cases WHERE case_id=?',(case,)).fetchone()[0],'ALERTED')

    def test_unobserved_response_not_misreported_as_crash(self):
        self.assertEqual(self.coord.start_response('response-1','cw-1','design',self.worker_ctx),'RUNNING')
        self.watch.record_audit('design',self.audit_ctx)
        with self.owner.write() as (db, now):
            db.execute('UPDATE response_attempts SET started_at=? WHERE response_id=?', (now - 5000*1000000,'response-1'))
        status=self.watch.inspect('design',self.watch_ctx, response_stale_seconds=60)
        self.assertEqual(status['status'],'DEGRADED')
        with self.owner.connection(readonly=True) as db:
            reason=db.execute("SELECT reason FROM recovery_cases WHERE subject_id='response-1'").fetchone()
            self.assertEqual(reason,('RESPONSE_UNOBSERVED',))


if __name__ == '__main__':
    unittest.main()
