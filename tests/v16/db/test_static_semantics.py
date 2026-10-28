"""Contract regressions checked offline without substituting for actual PG tests."""
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from studio_v16.db.store import PostgresStore, EVENT_VALIDATOR

class RecordingConnection:
    def __init__(self):
        self.last=None
    def execute(self,sql,values):
        self.last=(sql,values)

class OfflineSemantics(unittest.TestCase):
    def test_generated_command_event_is_wg_a_schema_valid(self):
        fixture=json.loads((ROOT/'tests/v16/contracts/fixtures/positive.json').read_text())
        for key in ('db-command','db-command-checkpoint','db-command-ack','db-command-reserve_publication'):
            with self.subTest(key=key):
                conn=RecordingConnection()
                event_id=PostgresStore._journal_internal(conn,fixture[key])
                self.assertTrue(event_id.startswith('evt_'))
                event=json.loads(conn.last[1][-1])
                EVENT_VALIDATOR.validate(event)
                self.assertEqual(event['delivery_authority'],'ARCHIVE_ONLY')
                self.assertEqual(event['payload_sha256'],conn.last[1][-2])

    def test_frozen_a_contracts_unmodified(self):
        manifest=json.loads((ROOT/'studio/v16/contracts/FREEZE_MANIFEST.v1.json').read_text())
        self.assertEqual(len(manifest['files']),19)
        for entry in manifest['files']:
            with self.subTest(path=entry['path']):
                self.assertEqual(hashlib.sha256((ROOT/entry['path']).read_bytes()).hexdigest(),entry['sha256'])

    def test_sql_claim_and_retry_use_pg_row_locking(self):
        source=(ROOT/'studio_v16/db/store.py').read_text()
        for fragment in ('FOR UPDATE SKIP LOCKED','pg_advisory_xact_lock','IDEMPOTENCY_CONFLICT',
                         "status='RECONCILE_REQUIRED'", "readback_state=='ABSENT'", 'provider_readback_verified'):
            with self.subTest(fragment=fragment): self.assertIn(fragment,source)

if __name__=='__main__': unittest.main()
