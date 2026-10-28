"""Additional publication identity checks against a real SQLite file."""
import copy
import sqlite3
import unittest

from studio_v16.gateway.contracts import canonical_hash
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


if __name__ == '__main__':
    unittest.main()
