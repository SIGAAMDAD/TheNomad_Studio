"""PUB-07: terminal provider conflict is atomic and prevents future dispatch."""
import sqlite3
import unittest

from studio_v16.db.publication import PublicationDenied
from studio_v16.gateway.contracts import canonical_hash
from tests.v16.db import test_sqlite_publication_c8 as fixture


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 45), 'SQLite >=3.45 required')
class SQLitePublicationConflictC8(unittest.TestCase):
    setUp = fixture.SQLitePublicationC8.setUp
    setup_intent = fixture.SQLitePublicationC8.setup_intent
    activate_test = fixture.SQLitePublicationC8.activate_test
    receipt = staticmethod(fixture.SQLitePublicationC8.receipt)

    def _prepared(self):
        state, intent = self.setup_intent()
        self.activate_test()
        self.assertEqual(self.store.reserve_gateway_intent(
            intent, fixture.PRINCIPAL, state, state.state_revision,
            canonical_hash(intent)), 'COMMITTED')
        dispatch = self.store.begin_effect_dispatch(intent, state)
        self.assertTrue(dispatch.startswith('dispatch_'))
        receipt = self.receipt(intent, status='CONFLICT')
        receipt['denial_code'] = 'SOURCE_SHA_MISMATCH'
        receipt['observed'] = None
        receipt['verified_at'] = None
        return state, intent, dispatch, receipt

    def _states(self):
        with self.owner.connection(readonly=True) as db:
            return (db.execute('SELECT status FROM source_publications').fetchone()[0],
                    db.execute('SELECT status FROM publication_intents').fetchone()[0],
                    db.execute('SELECT status,dead_letter_reason FROM outbox').fetchone(),
                    db.execute('SELECT COUNT(*) FROM effect_dispatches').fetchone()[0])

    def test_conflict_blocks_publication_and_outbox_atomically(self):
        state, intent, _dispatch, receipt = self._prepared()
        result = self.store.mark_gateway_conflict(intent['publication_id'],
                    intent['idempotency_key'], state, state.state_revision, receipt)
        self.assertTrue(result)
        self.assertEqual(self._states(),
                         ('CONFLICT','CONFLICT',('BLOCKED','PROVIDER_READBACK_CONFLICT'),1))
        with self.owner.connection(readonly=True) as db:
            recorded = db.execute('SELECT conflict_receipt_json FROM publication_intents').fetchone()[0]
        self.assertIn('SOURCE_SHA_MISMATCH', recorded)
        with self.assertRaises(PublicationDenied):
            self.store.begin_effect_dispatch(intent, state)
        self.assertFalse(self.store.reconcile_gateway_intent(intent['publication_id'],
                         intent['idempotency_key'],state,state.state_revision,self.receipt(intent)))
        self.assertEqual(self._states(),
                         ('CONFLICT','CONFLICT',('BLOCKED','PROVIDER_READBACK_CONFLICT'),1))

    def test_outbox_update_failure_rolls_back_all_terminal_conflict_writes(self):
        state, intent, _dispatch, receipt = self._prepared()
        self.assertEqual(self._states(), ('PREPARED','PREPARED',('IN_FLIGHT',None),1))
        with self.owner.write() as (db, _):
            db.execute('''CREATE TRIGGER injected_outbox_conflict_failure
                BEFORE UPDATE ON outbox WHEN NEW.status='BLOCKED'
                BEGIN SELECT RAISE(ABORT,'INJECTED_OUTBOX_BLOCK_FAILURE'); END''')
        self.assertFalse(self.store.mark_gateway_conflict(intent['publication_id'],
                         intent['idempotency_key'],state,state.state_revision,receipt))
        self.assertEqual(self._states(), ('PREPARED','PREPARED',('IN_FLIGHT',None),1))
        with self.owner.write() as (db, _):
            db.execute('DROP TRIGGER injected_outbox_conflict_failure')
        self.assertTrue(self.store.mark_gateway_conflict(intent['publication_id'],
                        intent['idempotency_key'],state,state.state_revision,receipt))
        self.assertEqual(self._states(),
                         ('CONFLICT','CONFLICT',('BLOCKED','PROVIDER_READBACK_CONFLICT'),1))
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone(),('ok',))
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])


if __name__ == '__main__':
    unittest.main()
