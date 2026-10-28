"""PUB-04/PUB-08 real SQLite dispatch restart, unknown effect and absence-proof fencing."""
import sqlite3
import unittest

from studio_v16.db.publication import PublicationDenied
from studio_v16.db.sqlite.connection import ConnectionOwner
from studio_v16.db.sqlite.store import SQLiteStore
from studio_v16.db.sqlite.recovery import TrustedRecoveryIdentity
from studio_v16.gateway.contracts import canonical_hash, validate
from tests.v16.db import test_sqlite_publication_c8 as fixture


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3,45),'SQLite >=3.45 required')
class SQLiteDispatchRestartC8(unittest.TestCase):
    setUp = fixture.SQLitePublicationC8.setUp
    setup_intent = fixture.SQLitePublicationC8.setup_intent
    activate_test = fixture.SQLitePublicationC8.activate_test
    receipt = staticmethod(fixture.SQLitePublicationC8.receipt)

    def _new_store_after_dispatch(self):
        state, intent = self.setup_intent()
        self.activate_test()
        self.assertEqual(self.store.reserve_gateway_intent(intent,fixture.PRINCIPAL,
                         state,state.state_revision,canonical_hash(intent)),'COMMITTED')
        dispatch = self.store.begin_effect_dispatch(intent,state)
        self.owner.close()
        resumed = ConnectionOwner(self.owner.path)
        self.addCleanup(resumed.close)
        resumed_store = SQLiteStore(resumed, recovery_principals=('recovery_test_001',))
        return resumed,resumed_store,state,intent,dispatch

    def _unobserved(self,intent):
        result = self.receipt(intent,status='EXTERNAL_OBSERVED',observed=None)
        result.update(denial_code=None,observed=None,provider_request_id=None,verified_at=None)
        validate(result,'PUBLISH-V16/READBACK/1')
        return result

    def test_durable_dispatch_is_recovered_before_any_retry_after_restart(self):
        owner,store,state,intent,dispatch = self._new_store_after_dispatch()
        with owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT dispatch_id FROM effect_dispatches').fetchone(),(dispatch,))
            self.assertEqual(db.execute('SELECT status FROM outbox').fetchone(),('IN_FLIGHT',))
        with self.assertRaises(PublicationDenied):
            store.begin_effect_dispatch(intent,state)
        recovery = TrustedRecoveryIdentity('recovery_test_001',True,True)
        observed,prior = store.recorded_dispatch(intent['publication_id'],intent['idempotency_key'],recovery)
        self.assertEqual(observed,intent)
        self.assertEqual(prior,dispatch)
        self.assertEqual(store.reconcile_dispatched_effect(intent['publication_id'],
                         intent['idempotency_key'],dispatch,self._unobserved(intent),recovery),
                         'RECONCILE_REQUIRED')
        with self.assertRaises(PublicationDenied):
            store.begin_effect_dispatch(intent,state)
        with owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT status FROM publication_intents').fetchone(),('PREPARED',))
            self.assertEqual(db.execute('SELECT status FROM outbox').fetchone(),('RECONCILE_REQUIRED',))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM effect_dispatches').fetchone()[0],1)

    def test_retry_requires_exact_absence_proof_then_new_transactional_dispatch(self):
        owner,store,state,intent,dispatch = self._new_store_after_dispatch()
        recovery = TrustedRecoveryIdentity('recovery_test_001',True,True)
        with self.assertRaises(PublicationDenied):
            store.reconcile_dispatched_effect(intent['publication_id'],intent['idempotency_key'],
                         dispatch,self._unobserved(intent),recovery,
                         absence_evidence={'repo_id':'wrong'})
        with owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM effect_absence_proofs').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT status FROM outbox').fetchone(),('IN_FLIGHT',))
        target=intent['target']
        expected=dict(repo_id=target['repo_id'],work_branch=target['work_branch'],head_sha=target['source_base_sha'])
        self.assertEqual(store.reconcile_dispatched_effect(intent['publication_id'],
                         intent['idempotency_key'],dispatch,self._unobserved(intent),recovery,
                         absence_evidence=expected), 'RECONCILE_REQUIRED')
        with owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM effect_absence_proofs').fetchone()[0],1)
        next_dispatch=store.begin_effect_dispatch(intent,state)
        self.assertNotEqual(next_dispatch,dispatch)
        with owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM effect_dispatches').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT status,attempts FROM outbox').fetchone(),('IN_FLIGHT',2))
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])


if __name__=='__main__':
    unittest.main()
