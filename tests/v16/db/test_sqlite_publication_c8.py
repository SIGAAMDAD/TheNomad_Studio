"""C8 publication authority and trusted post-revocation readback using real SQLite."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from studio_v16.db.sqlite.connection import ConnectionOwner
from studio_v16.db.sqlite.store import SQLiteStore
from studio_v16.db.sqlite.recovery import TrustedRecoveryIdentity
from studio_v16.db.publication import PublicationDenied
from studio_v16.db.sqlite.types import utc_us
from studio_v16.gateway.contracts import canonical_hash, validate
from studio_v16.gateway.interfaces import State, VerifiedQA
from tests.v16.db.test_sqlite_commands_c8 import (JOB, WORKER, PRINCIPAL, BASE, SCOPE, CTX,
                                                  command, future)

BRANCH = f'studio/workorders/{JOB}/publication'
PATCH = 'a'*64
TREE = 'd'*64


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3,45), 'SQLite >=3.45 required')
class SQLitePublicationC8(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.owner = ConnectionOwner(Path(self.temp.name)/'publication.sqlite')
        self.addCleanup(self.owner.close)
        self.store = SQLiteStore(self.owner, recovery_principals=('recovery_test_001',))
        with self.owner.write() as (db, now):
            for pid, kind in ((PRINCIPAL,'WORKER'),('owner_test_001','OWNER'),('issuer_test_001','ISSUER'),
                              ('reviewer_test_001','QA'),('recovery_test_001','RECOVERY_READBACK')):
                db.execute('INSERT INTO principals(principal_id,issuer,subject,kind,status) VALUES(?,?,?,?,?)',
                           (pid,'TEST',pid,kind,'ACTIVE'))
            db.execute("INSERT INTO jobs(job_id,parent_issue_ref,owner_head_id,state,approved_scope_hash,authority_revision,source_repo,source_base_sha,resource_key) VALUES(?,'test-issue','head','READY',?,'rev_0001','SIGAAMDAD/TheNomad_Studio',?,'src/main')", (JOB,SCOPE,BASE))
            db.execute("INSERT INTO worker_assignments(job_id,worker_id,assignment_epoch,status) VALUES(?,?,1,'ACTIVE')",(JOB,WORKER))
            for decision, grant, operation in (('decision_claim','grant_claim','CLAIM'),('decision_pub','grant_pub','SOURCE_BRANCH_UPDATE')):
                db.execute("INSERT INTO decisions(decision_id,authority_principal_id,job_id,approved_scope_hash,action,proof_ref,authority_revision) VALUES(?,'owner_test_001',?,?,?,'test-owner-proof','rev_0001')",(decision,JOB,SCOPE,operation))
                db.execute("INSERT INTO grants(grant_id,principal_id,job_id,worker_id,repo_id,path_scope,operation,policy_version,authority_revision,approved_scope_hash,source_base_sha,assignment_epoch,expires_at,issuer_approval_ref) VALUES(?,?,?,?,'SIGAAMDAD/TheNomad_Studio',?,?,'policy_1','rev_0001',?,?,1,?,?)",
                           (grant,PRINCIPAL,JOB,WORKER,'["studio_v16/db/"]',operation,SCOPE,BASE,now+20*60*1000000,decision))
            db.execute("INSERT INTO reviews(review_id,subject_publication_id,reviewer_principal_id,subject_sha,verdict,evidence_ref) VALUES('review_pub','pub_authority_001','reviewer_test_001',?,'PASS','urn:test:qa')",(TREE,))
        claim = command('CLAIM',1,source_base_sha=BASE,resource_key='src/main',
                        expected_lease_generation=1,lease_expires_at=future(),
                        owner_decision_ref='decision_claim',grant_id='grant_claim')
        self.assertEqual(self.store.execute(claim,CTX)['result'],'COMMITTED')

    def setup_intent(self):
        grant=dict(schema_id='AUTH-V16/GRANT/1',grant_id='grant_pub',job_id=JOB,principal_id=PRINCIPAL,
                   worker_id=WORKER,issuer_principal_id='issuer_test_001',owner_decision_ref='decision_pub',
                   authority_revision='rev_0001',policy_revision='policy_1',assignment_epoch=1,lease_generation=1,
                   repo_id='SIGAAMDAD/TheNomad_Studio',owned_path_prefixes=['studio_v16/db/'],
                   allowed_operations=['SOURCE_BRANCH_UPDATE'],source_base_sha=BASE,issued_at=future(-5),
                   expires_at=future(),revoked_at=None,approved_scope_sha256=SCOPE,issuer_proof_ref='issuer-proof')
        validate(grant,'AUTH-V16/GRANT/1')
        qa=VerifiedQA('review_pub','reviewer_test_001',TREE,BASE,'urn:test:qa')
        state=State(grant=grant,grant_issuer_verified=True,owner_approved=True,
                    approved_scope_sha256=SCOPE,policy_revision='policy_1',authority_revision='rev_0001',
                    assignment_epoch=1,lease_generation=1,holder_attempt_id='attempt_00000001',
                    work_branch=BRANCH,source_base_sha=BASE,revoked=False,budget_authorized=True,
                    source_writes_enabled=True,qa=qa,state_revision='sqlite:2:grant_pub:1:1',
                    evidence_sha256='e'*64)
        target=dict(provider='GITHUB',operation='SOURCE_BRANCH_UPDATE',repo_id='SIGAAMDAD/TheNomad_Studio',
                    work_branch=BRANCH,source_base_sha=BASE,patch_sha256=PATCH,
                    proposed_tree_sha256=TREE,changed_paths=['studio_v16/db/publication.py'])
        intent=dict(schema_id='PUBLISH-V16/INTENT/1',publication_id='pub_authority_001',
                    idempotency_key='c'*64,gateway_mode='ISOLATED_GATEWAY',principal_id=PRINCIPAL,
                    grant_id='grant_pub',owner_decision_ref='decision_pub',job_id=JOB,
                    attempt_id='attempt_00000001',worker_id=WORKER,assignment_epoch=1,lease_generation=1,
                    authority_revision='rev_0001',policy_revision='policy_1',approved_scope_sha256=SCOPE,
                    artifact_sha256=PATCH,evidence_sha256='e'*64,
                    independent_qa=dict(review_id=qa.review_id,reviewer_principal_id=qa.reviewer_principal_id,
                                        subject_sha256=qa.subject_sha256,source_sha=qa.source_sha,
                                        evidence_ref=qa.evidence_ref,verdict='PASS'),target=target,requested_at=future(-1))
        validate(intent,'PUBLISH-V16/INTENT/1')
        return state,intent

    def activate_test(self):
        with self.owner.write() as (db, now):
            db.execute("UPDATE authority_mode SET mode='ACTIVE',cutover_watermark='TEST_ONLY',owner_proof_ref='TEST_ONLY',signed_revision='TEST_ONLY' WHERE singleton=1")

    @staticmethod
    def receipt(intent, status='VERIFIED', observed=None):
        return dict(schema_id='PUBLISH-V16/READBACK/1',publication_id=intent['publication_id'],
                    idempotency_key=intent['idempotency_key'],provider='GITHUB',status=status,denial_code=None,
                    observed=observed if observed is not None else
                    dict(repo_id=intent['target']['repo_id'],work_branch=BRANCH,
                         tree_sha256=TREE,artifact_sha256=PATCH,commit_sha='b'*40),
                    provider_request_id='provider-test-01',verified_at=future(-1),audit_event_id='event_pub_test_001')

    def test_shadow_denies_publication(self):
        state,intent=self.setup_intent()
        self.assertEqual(self.store.reserve_gateway_intent(intent,PRINCIPAL,state,state.state_revision,canonical_hash(intent)),'CUTOVER_NOT_APPROVED')
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM publication_intents').fetchone()[0],0)

    def test_reserve_exact_idempotency_and_separate_hashes(self):
        state,intent=self.setup_intent(); self.activate_test()
        digest=canonical_hash(intent)
        self.assertEqual(self.store.reserve_gateway_intent(intent,PRINCIPAL,state,state.state_revision,digest),'COMMITTED')
        self.assertEqual(self.store.reserve_gateway_intent(intent,PRINCIPAL,state,state.state_revision,digest),'DUPLICATE')
        self.assertEqual(self.store.recorded_gateway_intent(intent['publication_id'],intent['idempotency_key']),intent)
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT patch_sha256,proposed_tree_hash FROM source_publications').fetchone(),(PATCH,TREE))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM publication_intents').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],1)

    def test_concurrent_reservations_one_result(self):
        state,intent=self.setup_intent(); self.activate_test(); digest=canonical_hash(intent)
        def reserve(_):
            return self.store.reserve_gateway_intent(intent,PRINCIPAL,state,state.state_revision,digest)
        with ThreadPoolExecutor(max_workers=12) as pool:
            values=list(pool.map(reserve,range(12)))
        self.assertEqual(values.count('COMMITTED'),1)
        self.assertEqual(values.count('DUPLICATE'),11)

    def test_no_duplicate_dispatch_without_verified_absence(self):
        state,intent=self.setup_intent(); self.activate_test()
        self.assertEqual(self.store.reserve_gateway_intent(intent,PRINCIPAL,state,state.state_revision,canonical_hash(intent)),'COMMITTED')
        first=self.store.begin_effect_dispatch(intent,state)
        self.assertTrue(first.startswith('dispatch_'))
        with self.assertRaises(PublicationDenied):
            self.store.begin_effect_dispatch(intent,state)
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM effect_dispatches').fetchone()[0],1)

    def test_trusted_recovery_after_revocation_and_shadow(self):
        state,intent=self.setup_intent(); self.activate_test()
        self.assertEqual(self.store.reserve_gateway_intent(intent,PRINCIPAL,state,state.state_revision,canonical_hash(intent)),'COMMITTED')
        dispatch=self.store.begin_effect_dispatch(intent,state)
        with self.owner.write() as (db, now):
            db.execute('UPDATE grants SET revoked_at=? WHERE grant_id=?',(now,'grant_pub'))
            db.execute("UPDATE authority_mode SET mode='SHADOW',cutover_watermark=NULL,owner_proof_ref=NULL,signed_revision=NULL WHERE singleton=1")
        trusted=TrustedRecoveryIdentity('recovery_test_001',verified_gateway=True,provider_readback_verified=True)
        self.assertEqual(self.store.reconcile_dispatched_effect(intent['publication_id'],intent['idempotency_key'],
                                                                dispatch,self.receipt(intent),trusted),'VERIFIED')
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT status FROM publication_intents').fetchone()[0],'VERIFIED')

    def test_untrusted_recovery_cannot_complete(self):
        state,intent=self.setup_intent(); self.activate_test()
        self.assertEqual(self.store.reserve_gateway_intent(intent,PRINCIPAL,state,state.state_revision,canonical_hash(intent)),'COMMITTED')
        dispatch=self.store.begin_effect_dispatch(intent,state)
        with self.assertRaises(PublicationDenied):
            self.store.reconcile_dispatched_effect(intent['publication_id'],intent['idempotency_key'],
                                                   dispatch,self.receipt(intent),TrustedRecoveryIdentity('recovery_test_001'))
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT status FROM publication_intents').fetchone()[0],'PREPARED')

    def test_wrong_tree_readback_rejected(self):
        state,intent=self.setup_intent(); self.activate_test()
        self.assertEqual(self.store.reserve_gateway_intent(intent,PRINCIPAL,state,state.state_revision,canonical_hash(intent)),'COMMITTED')
        dispatch=self.store.begin_effect_dispatch(intent,state)
        trusted=TrustedRecoveryIdentity('recovery_test_001',verified_gateway=True,provider_readback_verified=True)
        bad=self.receipt(intent);bad['observed']['tree_sha256']='f'*64
        with self.assertRaises(PublicationDenied):
            self.store.reconcile_dispatched_effect(intent['publication_id'],intent['idempotency_key'],dispatch,bad,trusted)


if __name__=='__main__':
    unittest.main()
