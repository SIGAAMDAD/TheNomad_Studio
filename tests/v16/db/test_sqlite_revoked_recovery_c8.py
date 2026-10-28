"""PUB-05: revoked publisher cannot initiate writes, recovery verifies only old dispatch."""
from copy import deepcopy
import sqlite3
import unittest

from studio_v16.db.publication import PublicationDenied
from studio_v16.db.sqlite.recovery import TrustedRecoveryIdentity
from studio_v16.gateway.contracts import canonical_hash, validate
from tests.v16.db import test_sqlite_publication_c8 as fixture


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 45), 'SQLite >=3.45 required')
class SQLiteRevokedRecoveryC8(unittest.TestCase):
    setUp = fixture.SQLitePublicationC8.setUp
    setup_intent = fixture.SQLitePublicationC8.setup_intent
    activate_test = fixture.SQLitePublicationC8.activate_test
    receipt = staticmethod(fixture.SQLitePublicationC8.receipt)

    def _prior_dispatch_then_revoke(self):
        state, intent = self.setup_intent()
        self.activate_test()
        self.assertEqual(self.store.reserve_gateway_intent(intent, fixture.PRINCIPAL,
                         state, state.state_revision, canonical_hash(intent)), 'COMMITTED')
        dispatch = self.store.begin_effect_dispatch(intent, state)
        with self.owner.write() as (db, now):
            db.execute('UPDATE grants SET revoked_at=? WHERE grant_id=?', (now, 'grant_pub'))
            db.execute("UPDATE authority_mode SET mode='SHADOW',cutover_watermark=NULL,"
                       "owner_proof_ref=NULL,signed_revision=NULL WHERE singleton=1")
        return state, intent, dispatch

    def test_new_write_denied_but_independent_trusted_recovery_verifies_exact_prior_dispatch(self):
        state, intent, dispatch = self._prior_dispatch_then_revoke()
        with self.assertRaises(PublicationDenied):
            self.store.begin_effect_dispatch(intent,state)
        fresh = deepcopy(intent)
        fresh['publication_id'] = 'pub_after_revoke'
        fresh['idempotency_key'] = 'f'*64
        validate(fresh,'PUBLISH-V16/INTENT/1')
        self.assertEqual(self.store.reserve_gateway_intent(fresh, fixture.PRINCIPAL,
                         state,state.state_revision,canonical_hash(fresh)), 'REVOKED_GRANT')
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM effect_dispatches').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM publication_intents').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT status FROM publication_intents').fetchone(),('PREPARED',))
        trusted = TrustedRecoveryIdentity('recovery_test_001',verified_gateway=True,
                                           provider_readback_verified=True)
        self.assertEqual(self.store.reconcile_dispatched_effect(intent['publication_id'],
                         intent['idempotency_key'],dispatch,self.receipt(intent),trusted),'VERIFIED')
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT status FROM publication_intents').fetchone(),('VERIFIED',))
            self.assertEqual(db.execute('SELECT status FROM outbox').fetchone(),('DELIVERED',))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM effect_dispatches').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT mode FROM authority_mode WHERE singleton=1').fetchone(),('SHADOW',))


    def test_shadow_mode_alone_denies_new_dispatch_and_preserves_prior_effect(self):
        state, intent = self.setup_intent()
        self.activate_test()
        self.assertEqual(self.store.reserve_gateway_intent(intent, fixture.PRINCIPAL,
                         state,state.state_revision,canonical_hash(intent)), 'COMMITTED')
        dispatch = self.store.begin_effect_dispatch(intent,state)
        with self.owner.write() as (db, now):
            db.execute("UPDATE authority_mode SET mode='SHADOW',cutover_watermark=NULL,"
                       "owner_proof_ref=NULL,signed_revision=NULL WHERE singleton=1")
        with self.assertRaises(PublicationDenied):
            self.store.begin_effect_dispatch(intent,state)
        recovered = TrustedRecoveryIdentity('recovery_test_001',True,True)
        self.assertEqual(self.store.reconcile_dispatched_effect(intent['publication_id'],
                         intent['idempotency_key'],dispatch,self.receipt(intent),recovered),'VERIFIED')
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM effect_dispatches').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT status FROM outbox').fetchone(),('DELIVERED',))
            self.assertEqual(db.execute('SELECT mode FROM authority_mode WHERE singleton=1').fetchone(),('SHADOW',))

    def test_recovery_cannot_be_self_authenticated_or_reconcile_different_dispatch(self):
        state, intent, dispatch = self._prior_dispatch_then_revoke()
        receipt = self.receipt(intent)
        identities = [TrustedRecoveryIdentity(fixture.PRINCIPAL,True,True),
                      TrustedRecoveryIdentity('recovery_test_001',False,True),
                      TrustedRecoveryIdentity('recovery_test_001',True,False)]
        for identity in identities:
            with self.subTest(identity=identity), self.assertRaises(PublicationDenied):
                self.store.reconcile_dispatched_effect(intent['publication_id'],
                         intent['idempotency_key'],dispatch,receipt,identity)
        independent = TrustedRecoveryIdentity('recovery_test_001',True,True)
        with self.assertRaises(PublicationDenied):
            self.store.reconcile_dispatched_effect(intent['publication_id'],
                     intent['idempotency_key'],'forged-dispatch-id',receipt,independent)
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT status FROM publication_intents').fetchone(),('PREPARED',))
            self.assertEqual(db.execute('SELECT status FROM outbox').fetchone(),('IN_FLIGHT',))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM effect_dispatches').fetchone()[0],1)


if __name__=='__main__':
    unittest.main()
