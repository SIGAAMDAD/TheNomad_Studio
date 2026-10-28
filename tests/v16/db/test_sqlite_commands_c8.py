"""C8 real SQLite authority command, lease, and outbox integration checks."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from studio_v16.db import GatewayContext
from studio_v16.db.sqlite.connection import ConnectionOwner
from studio_v16.db.sqlite.store import SQLiteStore
from studio_v16.db.sqlite.types import utc_us

JOB = 'job_test_001'
WORKER = 'w_test_001'
PRINCIPAL = 'p_test_001'
BASE = 'a' * 40
SCOPE = 'b' * 64
CTX = GatewayContext(principal_id=PRINCIPAL, verified_gateway=True,
                     verified_source_base=True, provider_readback_verified=True,
                     verified_external_receipt=True)


def future(minutes=20):
    return (datetime.now(timezone.utc)+timedelta(minutes=minutes)).isoformat().replace('+00:00','Z')


def command(kind, number, attempt='attempt_00000001', **payload):
    return dict(schema_id='DB-CONTRACT/COMMAND/1', command_id=f'command_{number:08d}',
                command_kind=kind, job_id=JOB, attempt_id=attempt, worker_id=WORKER,
                assignment_epoch=1, authority_revision='rev_0001', payload=payload)


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 45), 'SQLite >=3.45 required')
class SQLiteCommandsC8(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.owner = ConnectionOwner(Path(self.temp.name)/'authority.sqlite')
        self.addCleanup(self.owner.close)
        self.store = SQLiteStore(self.owner)
        with self.owner.write() as (db, _):
            db.execute("INSERT INTO principals(principal_id,issuer,subject,kind,status) VALUES(?,'TEST_ISSUER','worker','WORKER','ACTIVE')", (PRINCIPAL,))
            db.execute("INSERT INTO principals(principal_id,issuer,subject,kind,status) VALUES('owner_test_001','TEST_ISSUER','owner','OWNER','ACTIVE')")
            db.execute("INSERT INTO jobs(job_id,parent_issue_ref,owner_head_id,state,approved_scope_hash,authority_revision,source_repo,source_base_sha,resource_key) VALUES(?,'test-issue','head','READY',?,'rev_0001','SIGAAMDAD/TheNomad_Studio',?,'src/main')", (JOB,SCOPE,BASE))
            db.execute("INSERT INTO worker_assignments(job_id,worker_id,assignment_epoch,status) VALUES(?,?,1,'ACTIVE')", (JOB, WORKER))
            for n, operation in enumerate(('CLAIM','CHECKPOINT','ACK','REASSIGN','OUTBOX_SCHEDULE','OUTBOX_ACK')):
                decision, grant = f'decision_{n:03d}', f'grant_{n:03d}'
                db.execute("INSERT INTO decisions(decision_id,authority_principal_id,job_id,approved_scope_hash,action,proof_ref,authority_revision) VALUES(?,'owner_test_001',?,?,?,'test-evidence','rev_0001')", (decision,JOB,SCOPE,operation))
                db.execute("INSERT INTO grants(grant_id,principal_id,job_id,worker_id,repo_id,path_scope,operation,policy_version,authority_revision,approved_scope_hash,source_base_sha,assignment_epoch,expires_at,issuer_approval_ref) VALUES(?,?,?,?,'SIGAAMDAD/TheNomad_Studio',?,?, 'policy_1','rev_0001',?,?,1,?,?)",
                           (grant, PRINCIPAL, JOB, WORKER, '["studio_v16/db/"]', operation, SCOPE, BASE, utc_us() + 20*60*1000000, decision))

    def claim(self, number=1, attempt='attempt_00000001'):
        return command('CLAIM',number,attempt,source_base_sha=BASE,resource_key='src/main',
                       expected_lease_generation=1,lease_expires_at=future(),
                       owner_decision_ref='decision_000',grant_id='grant_000')

    def test_atomic_claim_idempotency_and_mutation(self):
        cmd = self.claim()
        self.assertEqual(self.store.execute(cmd, CTX)['result'], 'COMMITTED')
        self.assertEqual(self.store.execute(cmd, CTX)['result'], 'DUPLICATE')
        changed = {**cmd, 'payload': {**cmd['payload'], 'resource_key':'elsewhere'}}
        self.assertEqual(self.store.execute(changed, CTX)['denial_code'], 'IDEMPOTENCY_CONFLICT')
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], 1)

    def test_concurrent_claim_one_winner(self):
        def attempt(i):
            return self.store.execute(self.claim(i, f'attempt_{i:08d}'), CTX)
        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(attempt, range(41,57)))
        self.assertEqual(sum(v['result']=='COMMITTED' for v in results),1)
        self.assertEqual(sum(v['denial_code']=='RETRYABLE_BLOCKED' for v in results),15)
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM resource_leases').fetchone()[0],1)

    def test_claim_checkpoint_yield_and_resume(self):
        self.assertEqual(self.store.execute(self.claim(),CTX)['result'],'COMMITTED')
        cp=command('CHECKPOINT',2,checkpoint_sha256='a'*64,artifact_ref='urn:test:checkpoint',
                   state='YIELDED',elapsed_observed_seconds=17,resume_required=True,source_base_sha=BASE)
        result=self.store.execute(cp,CTX)
        self.assertEqual(result['result'],'COMMITTED')
        self.assertEqual(self.store.resume_capsule(JOB,CTX)['checkpoint_sha256'],'a'*64)
        self.assertEqual(self.store.execute(self.claim(3,'attempt_00000003'),CTX)['lease_generation'],2)

    def test_stale_revision_denied(self):
        bad={**self.claim(), 'authority_revision':'obsolete'}
        self.assertEqual(self.store.execute(bad,CTX)['denial_code'],'AUTHORITY_REVISION_MISMATCH')
        self.assertEqual(self.store.execute(self.claim(),CTX)['result'],'COMMITTED')

    def test_legacy_publication_commands_fail_closed(self):
        self.store.execute(self.claim(),CTX)
        cmd=command('RESERVE_PUBLICATION',2,publication_id='publication_01',idempotency_key='c'*64,artifact_sha256='a'*64,
                    source_base_sha=BASE,operation='SOURCE_BRANCH_UPDATE',target_ref='studio/workorders/job-1')
        self.assertEqual(self.store.execute(cmd,CTX)['denial_code'],'GATEWAY_UNAVAILABLE')
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM publication_keys').fetchone()[0],0)

    def test_reassign_fences_old_worker(self):
        self.assertEqual(self.store.execute(self.claim(),CTX)['result'],'COMMITTED')
        cmd=command('REASSIGN',2,new_worker_id='w_test_002',new_assignment_epoch=2,new_lease_generation=2,
                    transfer_checkpoint_sha256=None,revocation_decision_ref='decision_003')
        self.assertEqual(self.store.execute(cmd,CTX)['result'],'COMMITTED')
        stale=command('CHECKPOINT',3,checkpoint_sha256='a'*64,artifact_ref='urn:test:checkpoint',
                      state='HEARTBEAT',elapsed_observed_seconds=1,resume_required=True,source_base_sha=BASE)
        self.assertEqual(self.store.execute(stale,CTX)['denial_code'],'STALE_ASSIGNMENT')


if __name__ == '__main__':
    unittest.main()
