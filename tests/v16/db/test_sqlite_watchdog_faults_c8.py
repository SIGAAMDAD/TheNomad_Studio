"""HR-FAULT-13/25 local real-file watchdog evidence and owner fallback view."""
import sqlite3
import unittest

from studio_v16.communications.service import Scope
from studio_v16.db.store import GatewayContext, Denied
from tests.v16.db import test_sqlite_watchdog_c8 as fixture


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3,45), 'SQLite >=3.45 required')
class SQLiteWatchdogFaultsC8(unittest.TestCase):
    setUp = fixture.SQLiteWatchdogC8.setUp

    def _owner(self):
        ctx = GatewayContext(principal_id='owner',verified_gateway=True)
        with self.owner.write() as (db, _):
            db.execute("INSERT INTO principals(principal_id,issuer,subject,kind,status) "
                       "VALUES('owner','fixture','owner','OWNER','ACTIVE')")
        self.coord._policy['owner'] = Scope(frozenset({'design'}),frozenset({'CONTROLLER'}),owner=True)
        return ctx

    def test_invalid_audit_digest_stays_degraded_and_creates_recovery_packet(self):
        self.watch.record_audit('design', self.audit_ctx)
        with self.owner.write() as (db, now):
            db.execute("INSERT INTO supervisory_observations(observer_principal_id,subject_id,kind,"
                       "evidence_sha256,evidence_json,observed_at) VALUES('auditor','design','AUDIT',?, '{}',?)",
                       ('f'*64,now))
        result = self.watch.inspect('design',self.watch_ctx)
        self.assertEqual(result['status'],'DEGRADED')
        self.assertEqual(len(result['cases']),1)
        with self.owner.connection(readonly=True) as db:
            case=db.execute('SELECT reason,status,owner_packet_json FROM recovery_cases').fetchone()
        self.assertEqual(case[0],'AUDIT_INVALID')
        self.assertEqual(case[1],'ALERT_DELIVERY_PENDING')
        self.assertIn('Review exact receipts',case[2])

    def test_missing_audit_digest_is_rejected_by_storage(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with self.owner.write() as (db, now):
                db.execute("INSERT INTO supervisory_observations(observer_principal_id,subject_id,kind,"
                           "evidence_sha256,evidence_json,observed_at) VALUES('auditor','design','AUDIT',NULL,'{}',?)",
                           (now,))
        self.assertEqual(self.watch.inspect('design',self.watch_ctx)['status'],'DEGRADED')
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM supervisory_observations').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT reason FROM recovery_cases').fetchone(),('AUDIT_UNAVAILABLE',))

    def test_stale_audit_timestamp_is_degraded_not_all_clear(self):
        self.watch.record_audit('design', self.audit_ctx)
        with self.owner.write() as (db, now):
            db.execute("INSERT INTO supervisory_observations(observer_principal_id,subject_id,kind,"
                       "evidence_sha256,evidence_json,observed_at) VALUES('auditor','design','AUDIT',?, '{}',?)",
                       ('e'*64,now-7200*1000000))
        result=self.watch.inspect('design',self.watch_ctx,stale_seconds=60)
        self.assertEqual(result['status'],'DEGRADED')
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT reason FROM recovery_cases').fetchone(),('AUDIT_UNAVAILABLE',))

    def test_failed_alert_delivery_remains_bounded_and_visible_to_real_owner(self):
        owner_ctx=self._owner()
        case=self.watch.inspect('design',self.watch_ctx)['cases'][0]
        def unavailable(case_id, packet):
            raise OSError('email gateway unavailable')
        for attempt in range(1,4):
            receipt=self.watch.deliver_pending('design',self.watch_ctx,unavailable,max_attempts=3)
            self.assertEqual(receipt,[{'case_id':case,'status':'ALERT_DELIVERY_PENDING','attempts':attempt}])
            with self.owner.write() as (db, now):
                db.execute('UPDATE recovery_cases SET next_retry_at=? WHERE case_id=?',(now,case))
        self.assertEqual(self.watch.deliver_pending('design',self.watch_ctx,unavailable,max_attempts=3),[])
        queue=self.watch.owner_pending_alerts('design',owner_ctx)
        self.assertEqual(len(queue),1)
        self.assertEqual(queue[0]['case_id'],case)
        self.assertEqual(queue[0]['status'],'ALERT_DELIVERY_PENDING')
        self.assertEqual(queue[0]['alert_attempts'],3)
        self.assertEqual(queue[0]['reason'],'AUDIT_UNAVAILABLE')
        self.assertEqual(queue[0]['owner_packet']['owner_action'],
                         'Review exact receipts and authorize recovery; no automatic promotion')
        with self.owner.connection(readonly=True) as db:
            self.assertIsNone(db.execute('SELECT delivery_receipt_json FROM recovery_cases').fetchone()[0])
        # Even an incorrectly broad injected policy cannot turn a WORKER into an OWNER.
        self.coord._policy['worker'] = Scope(frozenset({'design'}),frozenset({'CONTROLLER'}),owner=True)
        for ctx in (self.worker_ctx,self.watch_ctx,GatewayContext(principal_id='owner',verified_gateway=False)):
            with self.subTest(principal=ctx.principal_id,verified=ctx.verified_gateway),self.assertRaises(Denied):
                self.watch.owner_pending_alerts('design',ctx)


if __name__=='__main__':
    unittest.main()
