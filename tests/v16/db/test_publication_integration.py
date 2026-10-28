"""Actual PostgreSQL 15+ publisher authority and crash/replay acceptance gate.

Run only with NOMAD_V16_TEST_PG_DSN pointing at an EMPTY disposable database.
Independent external provider, TLS and credential proofs require separate WG-H QA.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from studio_v16.db import PostgresStore
from studio_v16.db.migrate import apply, rollback_disposable
from studio_v16.gateway.contracts import canonical_hash, validate
from studio_v16.gateway.interfaces import State, VerifiedQA
from tests.v16.db.test_postgres_integration import JOB, WORKER, PRINCIPAL, BASE, SCOPE, command, CTX, timeplus

DSN=os.getenv('NOMAD_V16_TEST_PG_DSN')
try:
 import psycopg
except ImportError:
 psycopg=None

BRANCH='studio/workorders/job_test_001/publication'
PATCH='a'*64
TREE='d'*64
EXPORT='f'*64


@unittest.skipUnless(DSN and psycopg,'PG15+/psycopg unavailable: real publication tests NOT_RUN')
class PublicationIntegration(unittest.TestCase):
    def connect(self):
        return psycopg.connect(DSN,autocommit=True)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with psycopg.connect(DSN,autocommit=True) as c:
            if c.execute("SELECT to_regnamespace('nomad_v16')").fetchone()[0]:
                raise RuntimeError('Refusing to alter a populated/non-disposable nomad_v16 schema')
            apply(c)

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(DSN, autocommit=True) as c:
            # Publication tests deliberately exercise ACTIVE; the destructive
            # disposable-only rollback requires SHADOW and no active writer.
            with c.transaction():
                c.execute("UPDATE nomad_v16.authority_mode SET mode='SHADOW', "
                          "cutover_watermark=NULL,owner_proof_ref=NULL,signed_revision=NULL "
                          "WHERE singleton=true")
            rollback_disposable(c, disposable_test_database=True)
        super().tearDownClass()

    def setUp(self):
        self.store=PostgresStore(self.connect)
        with self.connect() as c,c.transaction():
            for table in ('publication_intents','outbox','event_journal','command_results','reviews',
                         'source_publications','document_publications','publication_keys',
                         'resource_leases','attempts','worker_assignments','grants',
                         'budget_reservations','job_dependencies','decisions','jobs','principals'):
                c.execute('TRUNCATE nomad_v16.'+table+' CASCADE')
            c.execute("UPDATE nomad_v16.authority_mode SET mode='SHADOW',cutover_watermark=NULL,owner_proof_ref=NULL,signed_revision=NULL WHERE singleton=true")
            c.execute("""INSERT INTO nomad_v16.principals(principal_id,issuer,subject,kind,status) VALUES
              (%s,'TEST','worker','WORKER','ACTIVE'),('owner_test_001','TEST','owner','OWNER','ACTIVE'),
              ('issuer_test_001','TEST','issuer','ISSUER','ACTIVE'),('reviewer_test_001','TEST','reviewer','QA','ACTIVE')""",(PRINCIPAL,))
            c.execute("""INSERT INTO nomad_v16.jobs(job_id,parent_issue_ref,owner_head_id,state,approved_scope_hash,
                authority_revision,source_repo,source_base_sha,resource_key)
                VALUES(%s,'test-issue','head','READY',%s,'rev_0001','SIGAAMDAD/TheNomad_Studio',%s,'src/main')""",(JOB,SCOPE,BASE))
            c.execute("INSERT INTO nomad_v16.worker_assignments(job_id,worker_id,assignment_epoch,status) VALUES(%s,%s,1,'ACTIVE')",(JOB,WORKER))
            c.execute("""INSERT INTO nomad_v16.decisions(decision_id,authority_principal_id,job_id,approved_scope_hash,
                action,proof_ref,authority_revision)
                VALUES('decision_claim','owner_test_001',%s,%s,'CLAIM','urn:test:owner','rev_0001')""",(JOB,SCOPE))
            c.execute("""INSERT INTO nomad_v16.grants(grant_id,principal_id,job_id,worker_id,repo_id,
                path_scope,operation,policy_version,authority_revision,approved_scope_hash,
                source_base_sha,assignment_epoch,expires_at,issuer_approval_ref)
                VALUES('grant_claim',%s,%s,%s,'SIGAAMDAD/TheNomad_Studio',ARRAY['studio_v16/db/'],
                'CLAIM','policy_1','rev_0001',%s,%s,1,%s,'decision_claim')""",(PRINCIPAL,JOB,WORKER,SCOPE,BASE,timeplus()))
        claim=command('CLAIM',1,source_base_sha=BASE,resource_key='src/main',
                      expected_lease_generation=1,lease_expires_at=timeplus(),
                      owner_decision_ref='decision_claim',grant_id='grant_claim')
        self.assertEqual(self.store.execute(claim,CTX)['result'],'COMMITTED')

    def _setup_intent(self, *, provider='GITHUB', pub='pub_authority_001'):
        operation='SOURCE_BRANCH_UPDATE' if provider=='GITHUB' else 'DRIVE_REVISION_UPDATE'
        grant_id='grant_pub'
        with self.connect() as c,c.transaction():
            c.execute("""INSERT INTO nomad_v16.decisions(decision_id,authority_principal_id,job_id,approved_scope_hash,
                action,proof_ref,authority_revision) VALUES('decision_pub','owner_test_001',%s,%s,%s,'urn:test:owner','rev_0001')""",
                (JOB,SCOPE,operation))
            c.execute("""INSERT INTO nomad_v16.grants(grant_id,principal_id,job_id,worker_id,repo_id,
                path_scope,operation,policy_version,authority_revision,approved_scope_hash,
                source_base_sha,assignment_epoch,expires_at,issuer_approval_ref)
                VALUES(%s,%s,%s,%s,'SIGAAMDAD/TheNomad_Studio',ARRAY['studio_v16/db/'],
                %s,'policy_1','rev_0001',%s,%s,1,%s,'decision_pub')""",
                (grant_id,PRINCIPAL,JOB,WORKER,operation,SCOPE,BASE,timeplus()))
            c.execute("""INSERT INTO nomad_v16.reviews(review_id,subject_publication_id,reviewer_principal_id,
                subject_sha,verdict,evidence_ref) VALUES('review_pub',%s,'reviewer_test_001',%s,'PASS','urn:test:qa')""",
                (pub,TREE if provider=='GITHUB' else EXPORT))
            c.execute("UPDATE nomad_v16.authority_mode SET mode='ACTIVE',cutover_watermark='TEST_ONLY',owner_proof_ref='TEST_ONLY',signed_revision='TEST_ONLY' WHERE singleton=true")
        grant=dict(schema_id='AUTH-V16/GRANT/1',grant_id=grant_id,job_id=JOB,principal_id=PRINCIPAL,
            worker_id=WORKER,issuer_principal_id='issuer_test_001',owner_decision_ref='decision_pub',
            authority_revision='rev_0001',policy_revision='policy_1',assignment_epoch=1,lease_generation=1,
            repo_id='SIGAAMDAD/TheNomad_Studio',owned_path_prefixes=['studio_v16/db/'],
            allowed_operations=[operation],source_base_sha=BASE,issued_at=timeplus(-5),expires_at=timeplus(),
            revoked_at=None,approved_scope_sha256=SCOPE,issuer_proof_ref='issuer-proof')
        validate(grant,'AUTH-V16/GRANT/1')
        qa=VerifiedQA('review_pub','reviewer_test_001',TREE if provider=='GITHUB' else EXPORT,
                      BASE,'urn:test:qa')
        state=State(grant=grant,grant_issuer_verified=True,owner_approved=True,
            approved_scope_sha256=SCOPE,policy_revision='policy_1',authority_revision='rev_0001',
            assignment_epoch=1,lease_generation=1,holder_attempt_id='attempt_00000001',
            work_branch=BRANCH,source_base_sha=BASE,revoked=False,budget_authorized=True,
            source_writes_enabled=True,qa=qa,state_revision='pg:2:grant_pub:1:1',
            drive_file_id='drive-file-1',drive_document_owner_ref='owner_test_001',
            drive_expected_revision='rev-17',document_writes_enabled=True,evidence_sha256='e'*64)
        target=({'provider':'GITHUB','operation':operation,'repo_id':'SIGAAMDAD/TheNomad_Studio',
                 'work_branch':BRANCH,'source_base_sha':BASE,'patch_sha256':PATCH,
                 'proposed_tree_sha256':TREE,'changed_paths':['studio_v16/db/publication.py']}
             if provider=='GITHUB' else
                {'provider':'GOOGLE_DRIVE','operation':operation,'file_id':'drive-file-1',
                 'document_owner_ref':'owner_test_001','expected_revision':'rev-17','exported_sha256':EXPORT})
        intent=dict(schema_id='PUBLISH-V16/INTENT/1',publication_id=pub,idempotency_key='c'*64,
            gateway_mode='ISOLATED_GATEWAY',principal_id=PRINCIPAL,grant_id=grant_id,
            owner_decision_ref='decision_pub',job_id=JOB,attempt_id='attempt_00000001',worker_id=WORKER,
            assignment_epoch=1,lease_generation=1,authority_revision='rev_0001',policy_revision='policy_1',
            approved_scope_sha256=SCOPE,artifact_sha256=PATCH if provider=='GITHUB' else EXPORT,
            evidence_sha256='e'*64,independent_qa=dict(review_id=qa.review_id,
            reviewer_principal_id=qa.reviewer_principal_id,subject_sha256=qa.subject_sha256,
            source_sha=qa.source_sha,evidence_ref=qa.evidence_ref,verdict='PASS'),
            target=target,requested_at=timeplus(-1))
        validate(intent,'PUBLISH-V16/INTENT/1')
        return state,intent

    def test_source_patch_tree_atomic_and_exact_replay(self):
        state,intent=self._setup_intent()
        sha=canonical_hash(intent)
        self.assertEqual(self.store.reserve_gateway_intent(intent,PRINCIPAL,state,state.state_revision,sha),'COMMITTED')
        self.assertEqual(self.store.reserve_gateway_intent(intent,PRINCIPAL,state,state.state_revision,sha),'DUPLICATE')
        self.assertEqual(self.store.recorded_gateway_intent(intent['publication_id'],intent['idempotency_key']),intent)
        with self.connect() as c:
            self.assertEqual(c.execute('''SELECT patch_sha256,proposed_tree_hash FROM nomad_v16.source_publications''').fetchone(),(PATCH,TREE))
        bad=dict(intent,artifact_sha256='b'*64,target=dict(intent['target'],patch_sha256='b'*64))
        self.assertEqual(self.store.reserve_gateway_intent(bad,PRINCIPAL,state,state.state_revision,canonical_hash(bad)),'IDEMPOTENCY_CONFLICT')
        receipt=dict(schema_id='PUBLISH-V16/READBACK/1',publication_id=intent['publication_id'],
            idempotency_key=intent['idempotency_key'],provider='GITHUB',status='VERIFIED',
            denial_code=None,observed={'repo_id':intent['target']['repo_id'],'work_branch':BRANCH,
                'tree_sha256':TREE,'artifact_sha256':PATCH,'commit_sha':'b'*40},
            provider_request_id=None,verified_at=timeplus(-1),audit_event_id='event_pub_test_001')
        validate(receipt,'PUBLISH-V16/READBACK/1')
        mismatch=dict(receipt,observed={**receipt['observed'],'tree_sha256':'e'*64})
        self.assertFalse(self.store.reconcile_gateway_intent(intent['publication_id'],intent['idempotency_key'],state,state.state_revision,mismatch))
        self.assertTrue(self.store.reconcile_gateway_intent(intent['publication_id'],intent['idempotency_key'],state,state.state_revision,receipt))
        replay=dict(receipt,verified_at=timeplus(),audit_event_id='event_pub_test_002')
        self.assertTrue(self.store.reconcile_gateway_intent(intent['publication_id'],intent['idempotency_key'],state,state.state_revision,replay))
        with self.connect() as c:
            self.assertEqual(c.execute('SELECT status FROM nomad_v16.publication_intents').fetchone()[0],'VERIFIED')
            self.assertEqual(c.execute("SELECT status FROM nomad_v16.outbox WHERE kind='GITHUB_EFFECT'").fetchone()[0],'DELIVERED')

    def test_drive_cas_revision_persisted_and_fenced(self):
        state,intent=self._setup_intent(provider='GOOGLE_DRIVE')
        sha=canonical_hash(intent)
        self.assertEqual(self.store.reserve_gateway_intent(intent,PRINCIPAL,state,state.state_revision,sha),'COMMITTED')
        with self.connect() as c:
            self.assertEqual(c.execute('SELECT expected_revision,exported_sha256 FROM nomad_v16.document_publications').fetchone(),('rev-17',EXPORT))
        receipt=dict(schema_id='PUBLISH-V16/READBACK/1',publication_id=intent['publication_id'],
            idempotency_key=intent['idempotency_key'],provider='GOOGLE_DRIVE',status='VERIFIED',denial_code=None,
            observed={'file_id':'drive-file-1','revision':'rev-17','exported_sha256':EXPORT},
            provider_request_id=None,verified_at=timeplus(-1),audit_event_id='event_pub_test_001')
        self.assertFalse(self.store.reconcile_gateway_intent(intent['publication_id'],intent['idempotency_key'],state,state.state_revision,receipt))
        receipt['observed']['revision']='rev-18'
        self.assertTrue(self.store.reconcile_gateway_intent(intent['publication_id'],intent['idempotency_key'],state,state.state_revision,receipt))

    def test_concurrent_identical_reservations_one_effect(self):
        state,intent=self._setup_intent()
        digest=canonical_hash(intent)
        def run(_):
            return self.store.reserve_gateway_intent(intent,PRINCIPAL,state,state.state_revision,digest)
        with ThreadPoolExecutor(max_workers=10) as pool:
            values=list(pool.map(run,range(10)))
        self.assertEqual(values.count('COMMITTED'),1)
        self.assertEqual(values.count('DUPLICATE'),9)
        with self.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM nomad_v16.publication_intents').fetchone()[0],1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM nomad_v16.outbox WHERE kind='GITHUB_EFFECT'").fetchone()[0],1)

    def test_owner_issuer_and_worker_must_be_distinct(self):
        state,intent=self._setup_intent()
        compromised_grant=dict(state.grant,issuer_principal_id='owner_test_001')
        compromised=replace(state,grant=compromised_grant)
        self.assertEqual(self.store.reserve_gateway_intent(intent,PRINCIPAL,compromised,
                        compromised.state_revision,canonical_hash(intent)),'ACCESS_REQUIRED')

    def test_shadow_revocation_and_lease_fencing(self):
        state,intent=self._setup_intent()
        sha=canonical_hash(intent)
        with self.connect() as c:
            c.execute("UPDATE nomad_v16.authority_mode SET mode='SHADOW',cutover_watermark=NULL,owner_proof_ref=NULL,signed_revision=NULL")
        self.assertEqual(self.store.reserve_gateway_intent(intent,PRINCIPAL,state,state.state_revision,sha),'CUTOVER_NOT_APPROVED')
        with self.connect() as c:
            c.execute("UPDATE nomad_v16.authority_mode SET mode='ACTIVE',cutover_watermark='TEST_ONLY',owner_proof_ref='TEST_ONLY',signed_revision='TEST_ONLY'")
            c.execute("UPDATE nomad_v16.grants SET revoked_at=clock_timestamp() WHERE grant_id='grant_pub'")
        self.assertEqual(self.store.reserve_gateway_intent(intent,PRINCIPAL,state,state.state_revision,sha),'REVOKED_GRANT')

if __name__=='__main__':unittest.main()
