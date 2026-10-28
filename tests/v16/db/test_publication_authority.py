"""Offline authority-contract checks; real database tests run separately with a DSN."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from studio_v16.adapters.authority import PostgresAuthority
from studio_v16.db.store import PostgresStore, Denied
from studio_v16.gateway.contracts import canonical_hash, validate
from studio_v16.gateway.interfaces import State


class Evidence:
    def __init__(self,state): self.state=state
    def load(self,job_id,grant_id):
        if (job_id,grant_id)!=('job','grant'): raise LookupError('no grant')
        return self.state


class RecordingStore:
    def __init__(self): self.calls=[]
    def verify_gateway_state(self,*args): self.calls.append(('verify',args))
    def reserve_gateway_intent(self,*args): self.calls.append(('reserve',args));return 'COMMITTED'
    def recorded_gateway_intent(self,*args):self.calls.append(('recorded',args));return {'job_id':'job','grant_id':'grant'}
    def reconcile_gateway_intent(self,*args):self.calls.append(('reconcile',args));return True
    def authorize_gateway_effect(self,*args):self.calls.append(('effect',args));return True


class PublicationAuthorityOffline(unittest.TestCase):
    def test_default_fails_closed(self):
        authority=PostgresAuthority(RecordingStore())
        with self.assertRaises(LookupError):authority.load('job','grant')
        self.assertEqual(authority.reserve({'job_id':'job','grant_id':'grant'},'who','state','hash'),'GATEWAY_UNAVAILABLE')
        self.assertFalse(authority.authorize_effect({'job_id':'job','grant_id':'grant'}))

    def test_external_evidence_must_be_typed_and_live_rechecked(self):
        store=RecordingStore()
        a=PostgresAuthority(store,authoritative_evidence=Evidence({'unverified':'claim'}))
        with self.assertRaises(LookupError):a.load('job','grant')
        self.assertFalse(store.calls)
        # The type is necessary; DB also checks revocation/approval/QA and fences.
        from tests.v16.gateway.test_gateway import make_suite
        gateway,*_=make_suite()
        state=gateway.authority.state
        a=PostgresAuthority(store,authoritative_evidence=Evidence(state))
        self.assertIs(a.load('job','grant'),state)
        self.assertEqual(store.calls[0][0],'verify')
        self.assertEqual(a.reserve({'job_id':'job','grant_id':'grant'},'p','r','h'),'COMMITTED')
        self.assertTrue(a.authorize_effect({'job_id':'job','grant_id':'grant'}))
        self.assertTrue(a.reconcile('publication','key','r',{}))

    def test_live_database_denials_preserve_reason_and_block_all_actions(self):
        from studio_v16.db.publication import PublicationDenied
        from studio_v16.gateway.gateway import AdmissionError
        from tests.v16.gateway.test_gateway import make_suite

        gateway, *_ = make_suite()
        state = gateway.authority.state

        class RevokedStore(RecordingStore):
            def verify_gateway_state(self, *args):
                raise PublicationDenied('REVOKED_GRANT')

        adapter = PostgresAuthority(RevokedStore(), authoritative_evidence=Evidence(state))
        with self.assertRaises(AdmissionError) as denied:
            adapter.load('job', 'grant')
        self.assertEqual(denied.exception.code, 'REVOKED_GRANT')
        intent = {'job_id': 'job', 'grant_id': 'grant'}
        self.assertEqual(adapter.reserve(intent, 'principal', 'revision', 'hash'), 'REVOKED_GRANT')
        self.assertFalse(adapter.authorize_effect(intent))
        self.assertFalse(adapter.reconcile('pub', 'key', 'revision', {}))
        self.assertFalse(adapter.mark_conflict('pub', 'key', 'revision', {}))

        # The real gateway must preserve the database denial in its signed-
        # shape admission receipt, not report a fabricated backend outage.
        class AnyEvidence:
            def load(self, job_id, grant_id):
                return state
        gateway, *_parts = make_suite()
        gateway.authority = PostgresAuthority(RevokedStore(), authoritative_evidence=AnyEvidence())
        from studio_v16.gateway.interfaces import Principal
        receipt = gateway.evaluate(_parts[-2], Principal('principal-worker-001'))
        self.assertEqual(receipt['denial_code'], 'REVOKED_GRANT')

    def test_partial_db_commands_cannot_authorize_publication(self):
        s=PostgresStore(lambda:None)
        for action in (s._reserve,s._reconcile):
            with self.assertRaises(Denied) as error:action(None,{},None)
            self.assertEqual(error.exception.reason,'GATEWAY_UNAVAILABLE')

    def test_migration_additive_and_separates_digests(self):
        source=(ROOT/'studio_v16/db/publication.py').read_text()
        migration=(ROOT/'studio_v16/db/migrations/0002_publication_authority.up.sql').read_text()
        store=(ROOT/'studio_v16/db/store.py').read_text()
        self.assertIn('ADD COLUMN patch_sha256',migration)
        self.assertIn('proposed_tree_sha256 char(64)',migration)
        self.assertIn('expected_revision text',migration)
        self.assertIn('intent_json jsonb NOT NULL',migration)
        self.assertIn('protect_publication_intent',migration)
        self.assertIn("target['patch_sha256'],",source)
        self.assertIn("target['proposed_tree_sha256']",source)
        self.assertIn("target['expected_revision']",source)
        self.assertIn('FOR UPDATE',source)
        self.assertIn("kind NOT IN ('GITHUB_EFFECT','DRIVE_EFFECT')",store)
        self.assertIn("raise Denied('GATEWAY_UNAVAILABLE')",store)
        before=(ROOT/'studio_v16/db/migrations/0001_core.up.sql').read_bytes()
        self.assertGreater(len(before),100)
        self.assertIn('schema_migrations',before.decode())

    def test_signed_shape_is_not_just_a_hash(self):
        source=(ROOT/'studio_v16/db/publication.py').read_text()
        for name in ('state_revision','owner_decision_ref','qa_review_id','lease_generation',
                     'evidence_sha256','approved_scope_sha256','AUTH-V16/GRANT/1',
                     'PUBLISH-V16/INTENT/1','PUBLISH-V16/READBACK/1'):
            self.assertIn(name,source)
        self.assertNotEqual(canonical_hash({'artifact_sha256':'a'*64}),
                            canonical_hash({'artifact_sha256':'b'*64}))

if __name__=='__main__':unittest.main()
