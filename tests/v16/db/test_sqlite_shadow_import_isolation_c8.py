"""PUB-10: historical observations cannot become active publication authority."""
import sqlite3
import unittest

from studio_v16.db.store import Denied, GatewayContext
from tests.v16.db import test_sqlite_c8 as fixtures
from tests.v16.db import test_sqlite_commands_c8 as commands


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 45), 'SQLite >=3.45 required')
class SQLiteShadowPublicationIsolationC8(unittest.TestCase):
    setUp = fixtures.SQLiteC8Tests.setUp

    def _worker(self):
        with self.owner.write() as (db, _):
            db.execute("INSERT INTO principals(principal_id,issuer,subject,kind,status) "
                       "VALUES ('historical_importer','fixture','worker','WORKER','ACTIVE')")
        return GatewayContext(principal_id='historical_importer', verified_gateway=True)

    def test_historical_publication_creates_no_grants_or_outbound_effect(self):
        context = self._worker()
        historical = {'publication_id': 'pub_historical_01', 'provider': 'GITHUB',
                      'status': 'VERIFIED', 'revision': 'deadbeef', 'grant_id': 'forged_grant',
                      'worker_id': 'historical_importer', 'effect': 'SOURCE_BRANCH_UPDATE',
                      'claimed_owner_approved': True}
        digest = self.store.shadow_import('import_historical_01', 'GITHUB',
                                          'owner/repo/branch', 'revision-1', historical, context)
        self.assertEqual(self.store.shadow_import('import_historical_01', 'GITHUB',
                                                  'owner/repo/branch', 'revision-1', historical, context), digest)
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT mode FROM authority_mode WHERE singleton=1').fetchone(), ('SHADOW',))
            self.assertEqual(db.execute('SELECT source_kind,source_ref,content_sha256 FROM shadow_imports').fetchone(),
                             ('GITHUB', 'owner/repo/branch', digest))
            for table in ('grants', 'decisions', 'publication_intents', 'publication_keys',
                          'source_publications', 'document_publications', 'effect_dispatches', 'outbox'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0, table)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone(), ('ok',))

    def test_historical_publication_cannot_activate_or_grant_on_import_replay(self):
        context = self._worker()
        historical = {'kind': 'OWNER_APPROVAL', 'status': 'PASS', 'provider_effect': 'GITHUB',
                      'grant_id': 'historical_grant', 'publication_id': 'historical_pub'}
        self.store.shadow_import('import_historical_02', 'LEDGER', 'legacy/publish', 'v2', historical, context)
        # A change to the source payload under the same immutable identity is rejected.
        with self.assertRaises(Denied):
            self.store.shadow_import('import_historical_02', 'LEDGER', 'legacy/publish', 'v2',
                                     {**historical, 'status': 'NEW'}, context)
        # Even if an independent operator later activates the empty test database,
        # the imported observations never become rows in authority tables.
        with self.owner.write() as (db, _):
            db.execute("UPDATE authority_mode SET mode='ACTIVE',cutover_watermark='TEST_ONLY',"
                       "owner_proof_ref='TEST_ONLY',signed_revision='TEST_ONLY' WHERE singleton=1")
        with self.owner.connection(readonly=True) as db:
            for table in ('grants', 'decisions', 'jobs', 'worker_assignments',
                          'publication_intents', 'effect_dispatches', 'outbox'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0, table)
        result = self.store.execute(commands.command('CLAIM', 1,
                     source_base_sha=commands.BASE, resource_key='src/main',
                     expected_lease_generation=1, lease_expires_at=commands.future(),
                     owner_decision_ref='forged_historical_decision',
                     grant_id='historical_grant'), context)
        self.assertEqual(result['result'], 'DENIED')
        self.assertEqual(result['denial_code'], 'ACCESS_REQUIRED')


if __name__ == '__main__':
    unittest.main()
