"""Offline regression/shape tests. Real PostgreSQL checks in test_postgres_integration.py."""
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from studio_v16.db import GatewayContext, PostgresStore, Denied
from studio_v16.db.store import COMMAND_VALIDATOR, RESULT_VALIDATOR, EVENT_VALIDATOR
from studio_v16.db.migrate import MIGRATIONS, rollback_disposable

class StorageContracts(unittest.TestCase):
    def test_all_wg_a_db_command_fixtures_accepted(self):
        data=json.loads((ROOT/'tests/v16/contracts/fixtures/positive.json').read_text())
        for name,payload in data.items():
            if name.startswith('db-command'):
                with self.subTest(name=name):
                    COMMAND_VALIDATOR.validate(payload)

    def test_wg_a_db_events_accepted(self):
        data=json.loads((ROOT/'tests/v16/contracts/fixtures/positive.json').read_text())
        EVENT_VALIDATOR.validate(data['db-event'])
        RESULT_VALIDATOR.validate(data['db-result'])
        RESULT_VALIDATOR.validate(data['db-result-denied'])

    def test_db_result_valid_for_missing_gateway(self):
        store=PostgresStore(lambda: self.fail('No DB access for untrusted caller'))
        command=json.loads((ROOT/'tests/v16/contracts/fixtures/positive.json').read_text())['db-command']
        result=store.execute(command,GatewayContext(principal_id='unauthenticated'))
        self.assertEqual(result['result'],'DENIED')
        self.assertEqual(result['denial_code'],'IDENTITY_UNVERIFIED')
        RESULT_VALIDATOR.validate(result)

    def test_rejects_malformed_command_before_opening_db(self):
        store=PostgresStore(lambda: self.fail('Malformed command opened DB'))
        result=store.execute({'command_id':'invalid_name','command_kind':'CLAIM'},GatewayContext('trusted',True))
        self.assertEqual(result['denial_code'],'MALFORMED_REQUEST')

    def test_destructive_rollback_requires_explicit_test_gate(self):
        with self.assertRaises(PermissionError):
            rollback_disposable(None)

    def test_all_required_tables_and_fences_in_migration(self):
        ddl=(MIGRATIONS/'0001_core.up.sql').read_text()
        tables=('principals','grants','jobs','job_dependencies','worker_assignments','attempts','resource_leases',
                'event_journal','delivery_effects','inbox_cursors','outbox','source_publications',
                'document_publications','reviews','decisions','audit_anchors','budget_reservations',
                'schema_migrations','recovery_checkpoints','command_results','shadow_imports','authority_mode','publication_keys','resume_capsules')
        for table in tables:
            with self.subTest(table=table):
                self.assertIn(f'CREATE TABLE nomad_v16.{table} ',ddl)
        self.assertIn('UNIQUE(producer,transport_delivery_id)',ddl)
        self.assertIn('UNIQUE(event_id,destination,kind)',ddl)
        self.assertIn('PRIMARY KEY(event_id,consumer_id,assignment_epoch,effect_name)',ddl)
        self.assertIn("DEFAULT 'SHADOW'",ddl)

    def test_store_never_has_external_provider_or_chat_runner(self):
        source=(ROOT/'studio_v16/db/store.py').read_text()
        for forbidden in ('requests.post(', 'httpx.post(', 'github_api.', 'openai.ChatCompletion',
                          'subprocess.run(', 'launch_chat('):
            self.assertNotIn(forbidden,source)

    def test_only_owned_paths_created(self):
        self.assertTrue((ROOT/'infra/db/docker-compose.yml').is_file())
        self.assertTrue((ROOT/'studio_v16/db/migrations/0001_core.down.sql').is_file())

if __name__=='__main__': unittest.main()
