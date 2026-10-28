"""Additional publication identity checks against a real SQLite file."""
import copy
import sqlite3
import unittest

from studio_v16.gateway.contracts import canonical_hash
from studio_v16.db.sqlite.recovery import TrustedRecoveryIdentity
from tests.v16.db import test_sqlite_publication_c8 as fixture


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 45), 'SQLite >=3.45 required')
class PublicationIdentityExtraC8(unittest.TestCase):
    setUp = fixture.SQLitePublicationC8.setUp
    setup_intent = fixture.SQLitePublicationC8.setup_intent
    activate_test = fixture.SQLitePublicationC8.activate_test

    def test_changed_payload_with_same_publication_identity_is_rejected(self):
        state, intent = self.setup_intent()
        self.activate_test()
        digest = canonical_hash(intent)
        self.assertEqual(self.store.reserve_gateway_intent(intent, fixture.PRINCIPAL, state,
                                                          state.state_revision, digest), 'COMMITTED')
        self.assertEqual(self.store.reserve_gateway_intent(intent, fixture.PRINCIPAL, state,
                                                          state.state_revision, digest), 'DUPLICATE')
        changed = copy.deepcopy(intent)
        changed['requested_at'] = fixture.future(-2)
        self.assertNotEqual(canonical_hash(changed), digest)
        self.assertEqual(self.store.reserve_gateway_intent(changed, fixture.PRINCIPAL, state,
                                                          state.state_revision, canonical_hash(changed)),
                         'IDEMPOTENCY_CONFLICT')
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM publication_intents').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0], 1)
        self.assertEqual(self.store.recorded_gateway_intent(intent['publication_id'], intent['idempotency_key']), intent)

    def test_duplicate_publication_ids_and_keys_are_rejected(self):
        state, intent = self.setup_intent()
        self.activate_test()
        self.assertEqual(self.store.reserve_gateway_intent(intent, fixture.PRINCIPAL, state,
                                                          state.state_revision, canonical_hash(intent)), 'COMMITTED')
        duplicate_id = (intent['publication_id'], 'd' * 64, fixture.JOB, 'e' * 64)
        duplicate_key = ('other_pub_id', intent['idempotency_key'], fixture.JOB, 'f' * 64)
        for values in (duplicate_id, duplicate_key):
            with self.subTest(values=values), self.assertRaises(sqlite3.IntegrityError):
                with self.owner.write() as (db, _):
                    db.execute('INSERT INTO publication_keys(publication_id,idempotency_key,job_id,artifact_sha256) VALUES(?,?,?,?)', values)
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM publication_keys').fetchone()[0], 1)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_verified_publication_receipt_cannot_be_changed(self):
        state, intent = self.setup_intent()
        self.activate_test()
        self.assertEqual(self.store.reserve_gateway_intent(intent, fixture.PRINCIPAL, state,
                                                          state.state_revision, canonical_hash(intent)), 'COMMITTED')
        dispatch = self.store.begin_effect_dispatch(intent, state)
        trusted = TrustedRecoveryIdentity('recovery_test_001', verified_gateway=True, provider_readback_verified=True)
        receipt = fixture.SQLitePublicationC8.receipt(intent)
        self.assertEqual(self.store.reconcile_dispatched_effect(intent['publication_id'], intent['idempotency_key'],
                                                                dispatch, receipt, trusted), 'VERIFIED')
        with self.owner.write() as (db, _):
            db.execute('CREATE TABLE terminal_probe (id INTEGER) STRICT')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'INVALID_TERMINAL_RECEIPT'):
            with self.owner.write() as (db, _):
                db.execute('INSERT INTO terminal_probe VALUES (1)')
                db.execute("UPDATE publication_intents SET status='CONFLICT' WHERE publication_id=?", (intent['publication_id'],))
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT status FROM publication_intents').fetchone()[0], 'VERIFIED')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM terminal_probe').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
