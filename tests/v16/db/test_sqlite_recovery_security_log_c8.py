"""PUB-06: denied recovery is logged and cannot fabricate verified readback."""
import sqlite3
import unittest

from studio_v16.db.publication import PublicationDenied
from studio_v16.db.sqlite.recovery import TrustedRecoveryIdentity
from studio_v16.gateway.contracts import canonical_hash
from tests.v16.db import test_sqlite_publication_c8 as fixture


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 45), 'SQLite >=3.45 required')
class SQLiteRecoverySecurityLogC8(unittest.TestCase):
    setUp = fixture.SQLitePublicationC8.setUp
    setup_intent = fixture.SQLitePublicationC8.setup_intent
    activate_test = fixture.SQLitePublicationC8.activate_test
    receipt = staticmethod(fixture.SQLitePublicationC8.receipt)

    def _dispatch(self):
        state,intent = self.setup_intent()
        self.activate_test()
        self.assertEqual(self.store.reserve_gateway_intent(intent,fixture.PRINCIPAL,
                         state,state.state_revision,canonical_hash(intent)),'COMMITTED')
        return state,intent,self.store.begin_effect_dispatch(intent,state)

    def test_forged_recovery_identity_is_logged_and_cannot_reconcile(self):
        _state,intent,dispatch = self._dispatch()
        for identity in (TrustedRecoveryIdentity(fixture.PRINCIPAL,True,True),
                         TrustedRecoveryIdentity('recovery_test_001',False,True),
                         TrustedRecoveryIdentity('recovery_test_001',True,False)):
            with self.subTest(identity=identity), self.assertLogs('studio_v16.db.sqlite.recovery',level='WARNING') as logs:
                with self.assertRaises(PublicationDenied):
                    self.store.reconcile_dispatched_effect(intent['publication_id'],
                             intent['idempotency_key'],dispatch,self.receipt(intent),identity)
            self.assertTrue(any('SQLITE_RECOVERY_SECURITY_DENIAL code=IDENTITY_UNVERIFIED' in line
                                for line in logs.output))
            self.assertFalse(any(identity.principal_id in line for line in logs.output))
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT status FROM publication_intents').fetchone(),('PREPARED',))
            self.assertEqual(db.execute('SELECT status FROM outbox').fetchone(),('IN_FLIGHT',))
            self.assertIsNone(db.execute('SELECT verified_receipt_json FROM publication_intents').fetchone()[0])

    def test_missing_dispatch_and_forged_readback_emit_denial_without_effect(self):
        _state,intent,dispatch = self._dispatch()
        trusted = TrustedRecoveryIdentity('recovery_test_001',True,True)
        with self.assertLogs('studio_v16.db.sqlite.recovery',level='WARNING') as logs:
            with self.assertRaises(PublicationDenied):
                self.store.reconcile_dispatched_effect(intent['publication_id'],
                         intent['idempotency_key'],'fake-dispatch-id',self.receipt(intent),trusted)
        self.assertTrue(any('SQLITE_RECOVERY_SECURITY_DENIAL code=ACCESS_REQUIRED' in line for line in logs.output))
        with self.assertLogs('studio_v16.db.sqlite.recovery',level='WARNING') as logs:
            with self.assertRaises(PublicationDenied):
                self.store.recorded_dispatch(intent['publication_id'],intent['idempotency_key'],
                                             TrustedRecoveryIdentity('unverified-recovery',False))
        self.assertTrue(any('SQLITE_RECOVERY_SECURITY_DENIAL code=IDENTITY_UNVERIFIED' in line for line in logs.output))
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM effect_dispatches').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT status FROM publication_intents').fetchone(),('PREPARED',))
            self.assertEqual(db.execute('SELECT status FROM outbox').fetchone(),('IN_FLIGHT',))


if __name__=='__main__':
    unittest.main()
