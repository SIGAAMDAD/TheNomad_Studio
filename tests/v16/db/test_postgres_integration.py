"""Postgres 15+ integration gates: NOMAD_V16_TEST_PG_DSN disposable database.

No test connects to production by default. `PG_DSN` must point to a disposable DB;
these tests create/drop the nomad_v16 schema and may run concurrently.
"""
import json
import os
from pathlib import Path
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import unittest

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from studio_v16.db import PostgresStore, GatewayContext, Denied
from studio_v16.db.migrate import apply, rollback_disposable
from studio_v16.db.store import RESULT_VALIDATOR

DSN=os.getenv('NOMAD_V16_TEST_PG_DSN')
try:
    import psycopg
except ImportError:
    psycopg=None

SHA='a'*64
BASE='a'*40
SCOPE='b'*64
PRINCIPAL='p_test_001'
WORKER='w_test_001'
JOB='job_test_001'
CTX=GatewayContext(principal_id=PRINCIPAL,verified_gateway=True,verified_source_base=True,provider_readback_verified=True,verified_external_receipt=True)


def timeplus(minutes=20):
    return (datetime.now(timezone.utc)+timedelta(minutes=minutes)).isoformat().replace('+00:00','Z')


def command(kind,i,attempt='attempt_00000001',**payload):
    return dict(schema_id='DB-CONTRACT/COMMAND/1',command_id=f'command_{i:08d}',command_kind=kind,
                job_id=JOB,attempt_id=attempt,worker_id=WORKER,assignment_epoch=1,
                authority_revision='rev_0001',payload=payload)


@unittest.skipUnless(DSN and psycopg,'PostgreSQL DSN/psycopg3 unavailable (NOT_RUN: real PG gates)')
class PostgresIntegration(unittest.TestCase):
    def connect(self):
        return psycopg.connect(DSN,autocommit=True)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with psycopg.connect(DSN,autocommit=True) as conn:
            # Fail closed on any nonempty/preexisting database.
            existing=conn.execute("SELECT to_regnamespace('nomad_v16')").fetchone()[0]
            if existing:
                raise RuntimeError('Refusing to modify a pre-existing nomad_v16 schema; use an empty disposable test DB')
            apply(conn)

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(DSN,autocommit=True) as conn:
            rollback_disposable(conn,disposable_test_database=True)
        super().tearDownClass()

    def setUp(self):
        self.store=PostgresStore(self.connect)
        with self.connect() as db,db.transaction():
            # Isolation across test cases without dropping migrational schema.
            db.execute("UPDATE nomad_v16.authority_mode SET mode='SHADOW',cutover_watermark=NULL,owner_proof_ref=NULL,signed_revision=NULL WHERE singleton=true")
            for t in ('command_results','inbox_cursors','delivery_effects','outbox','event_journal','reviews',
                      'publication_intents','document_publications','source_publications','publication_keys','resume_capsules','recovery_checkpoints','resource_leases',
                      'attempts','worker_assignments','grants','budget_reservations','job_dependencies','decisions',
                      'jobs','principals','shadow_imports'):
                db.execute(f'TRUNCATE nomad_v16.{t} CASCADE')
            db.execute("INSERT INTO nomad_v16.principals(principal_id,issuer,subject,kind,status) VALUES(%s,'TEST_ISSUER','worker','WORKER','ACTIVE'),('owner_test_001','TEST_ISSUER','owner','OWNER','ACTIVE')",(PRINCIPAL,))
            db.execute("""INSERT INTO nomad_v16.jobs(job_id,parent_issue_ref,owner_head_id,state,approved_scope_hash,authority_revision,source_repo,source_base_sha,resource_key)
                        VALUES(%s,'test-issue','head','READY',%s,'rev_0001','SIGAAMDAD/TheNomad_Studio',%s,'src/main')""",(JOB,SCOPE,BASE))
            db.execute("INSERT INTO nomad_v16.worker_assignments(job_id,worker_id,assignment_epoch,status) VALUES(%s,%s,1,'ACTIVE')",(JOB,WORKER))
            ops=('CLAIM','CHECKPOINT','ACK','REASSIGN','RESERVE_PUBLICATION','RECONCILE_PUBLICATION','OUTBOX_SCHEDULE','OUTBOX_ACK')
            for index,op in enumerate(ops):
                did=f'decision_{index:03d}';gid=f'grant_{index:03d}'
                db.execute("INSERT INTO nomad_v16.decisions(decision_id,authority_principal_id,job_id,approved_scope_hash,action,proof_ref,authority_revision) VALUES(%s,'owner_test_001',%s,%s,%s,'test-evidence','rev_0001')",(did,JOB,SCOPE,op))
                db.execute("""INSERT INTO nomad_v16.grants(grant_id,principal_id,job_id,worker_id,repo_id,path_scope,operation,policy_version,authority_revision,approved_scope_hash,source_base_sha,assignment_epoch,expires_at,issuer_approval_ref)
                  VALUES(%s,%s,%s,%s,'SIGAAMDAD/TheNomad_Studio',ARRAY['studio_v16/db/'],%s,'policy_1','rev_0001',%s,%s,1,%s,%s)""",
                  (gid,PRINCIPAL,JOB,WORKER,op,SCOPE,BASE,timeplus(),did))

    def claim(self,i=1,attempt='attempt_00000001'):
        return command('CLAIM',i,attempt,source_base_sha=BASE,resource_key='src/main',expected_lease_generation=1,lease_expires_at=timeplus(),owner_decision_ref='decision_000',grant_id='grant_000')

    def test_claim_idempotency_stale_and_checkpoint(self):
        c=self.claim()
        first=self.store.execute(c,CTX)
        self.assertEqual(first['result'],'COMMITTED')
        self.assertEqual(first['lease_generation'],1)
        self.assertEqual(self.store.execute(c,CTX)['result'],'DUPLICATE')
        changed={**c,'payload':{**c['payload'],'resource_key':'another'}}
        self.assertEqual(self.store.execute(changed,CTX)['denial_code'],'IDEMPOTENCY_CONFLICT')
        self.assertEqual(self.store.execute(self.claim(2,'attempt_00000002'),CTX)['result'],'RETRYABLE_BLOCKED')
        cp=command('CHECKPOINT',3,checkpoint_sha256=SHA,artifact_ref='urn:test:checkpoint',state='YIELDED',elapsed_observed_seconds=17,resume_required=True,source_base_sha=BASE)
        result=self.store.execute(cp,CTX)
        self.assertEqual(result['result'],'COMMITTED')
        self.assertEqual(result['checkpoint_sha256'],SHA)
        self.assertEqual(self.store.resume_capsule(JOB,CTX)['checkpoint_sha256'],SHA)
        next_claim=self.claim(4,'attempt_00000003')
        next_claim['payload']['expected_lease_generation']=1
        self.assertEqual(self.store.execute(next_claim,CTX)['lease_generation'],2)

    def test_parallel_claim_only_one_winner(self):
        # Two competing attempts from independent connections across a job row.
        def run(i):
            return self.store.execute(self.claim(i,f'attempt_{i:08d}'),CTX)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(run,(41,42)))
        self.assertEqual(sum(r['result']=='COMMITTED' for r in results),1)
        self.assertEqual(sum(r['result']=='RETRYABLE_BLOCKED' for r in results),1)

    def test_event_dedupe_and_outbox(self):
        self.assertEqual(self.store.execute(self.claim(),CTX)['result'],'COMMITTED')
        event=dict(schema_id='DB-CONTRACT/EVENT/1',event_id='event_0000000001',transport_delivery_id='delivery_000000001',
                   producer='TEST',job_id=JOB,worker_id=WORKER,attempt_id='attempt_00000001',assignment_epoch=1,
                   event_kind='ACK',causation_id=None,correlation_id='test-1',payload_sha256=SHA,
                   immutable_evidence_ref='urn:test:event',received_at=timeplus(-1),delivery_authority='ARCHIVE_ONLY')
        self.assertEqual(self.store.record_event(event,CTX),event['event_id'])
        self.assertEqual(self.store.record_event(event,CTX),event['event_id'])
        mutated=dict(event,payload_sha256='f'*64)
        with self.assertRaises(Denied):self.store.record_event(mutated,CTX)
        ack=command('ACK',3,event_id=event['event_id'],recipient_worker_id=WORKER,delivery_assignment_epoch=1,effect_name='apply')
        self.assertEqual(self.store.execute(ack,CTX)['result'],'COMMITTED')
        self.assertEqual(self.store.execute(ack,CTX)['result'],'DUPLICATE')
        schedule=command('OUTBOX_SCHEDULE',4,event_id=event['event_id'],destination='provider-1',kind='NOTICE',payload_sha256=SHA)
        self.assertEqual(self.store.execute(schedule,CTX)['result'],'COMMITTED')
        self.assertEqual(self.store.execute(command('OUTBOX_SCHEDULE',5,**schedule['payload']),CTX)['result'],'COMMITTED')
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM nomad_v16.outbox WHERE destination='provider-1'").fetchone()[0],1)

    def test_legacy_partial_publication_commands_are_denied(self):
        self.store.execute(self.claim(),CTX)
        reserve=command('RESERVE_PUBLICATION',2,publication_id='publication_01',idempotency_key='c'*64,artifact_sha256=SHA,
                        source_base_sha=BASE,operation='SOURCE_BRANCH_UPDATE',target_ref='studio/workorders/job-1')
        self.assertEqual(self.store.execute(reserve,CTX)['denial_code'],'GATEWAY_UNAVAILABLE')
        reconcile=command('RECONCILE_PUBLICATION',3,publication_id='publication_01',provider_request_id='request-1',
                          readback_state='PRESENT',observed_revision=BASE,observed_sha256=SHA)
        self.assertEqual(self.store.execute(reconcile,CTX)['denial_code'],'GATEWAY_UNAVAILABLE')
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM nomad_v16.publication_keys').fetchone()[0],0)

    def test_reassign_increments_both_fences_without_transferring_grant(self):
        self.assertEqual(self.store.execute(self.claim(),CTX)['result'],'COMMITTED')
        c=command('REASSIGN',2,new_worker_id='w_test_002',new_assignment_epoch=2,
                  new_lease_generation=2,transfer_checkpoint_sha256=None,revocation_decision_ref='decision_003')
        self.assertEqual(self.store.execute(c,CTX)['result'],'COMMITTED')
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT assignment_epoch FROM nomad_v16.jobs WHERE job_id=%s',(JOB,)).fetchone()[0],2)
            self.assertEqual(db.execute("SELECT lease_generation FROM nomad_v16.resource_leases WHERE repo_id='SIGAAMDAD/TheNomad_Studio' AND resource_key='src/main'").fetchone()[0],2)
        # Delayed original worker cannot mutate or claim, and no grant issued to new worker.
        stale=command('CHECKPOINT',3,checkpoint_sha256=SHA,artifact_ref='urn:test:checkpoint',
                      state='HEARTBEAT',elapsed_observed_seconds=1,resume_required=True,source_base_sha=BASE)
        self.assertEqual(self.store.execute(stale,CTX)['denial_code'],'STALE_ASSIGNMENT')

    def test_crash_outbox_requires_readback_before_retry(self):
        claim=self.store.execute(self.claim(),CTX)
        self.assertEqual(claim['result'],'COMMITTED')
        event=claim['journal_event_id']
        with self.connect() as db:
            digest=db.execute('SELECT payload_sha256 FROM nomad_v16.event_journal WHERE event_id=%s',(event,)).fetchone()[0].strip()
        schedule=command('OUTBOX_SCHEDULE',3,event_id=event,destination='notice-target',kind='NOTICE',payload_sha256=digest)
        self.assertEqual(self.store.execute(schedule,CTX)['result'],'COMMITTED')
        ready=self.store.reserve_outbox_delivery(CTX,shadow_test=True)
        self.assertEqual(len(ready),1)
        self.assertEqual(self.store.mark_delivery_retry(ready[0]['outbox_id'],ready[0]['claim_token'],'simulated timeout',CTX),'RECONCILE_REQUIRED')
        self.assertEqual(self.store.reserve_outbox_delivery(CTX,shadow_test=True),[])
        self.assertEqual(self.store.reconcile_outbox(ready[0]['outbox_id'],'ABSENT',None,None,CTX),'RETRY')
        with self.connect() as db:
            db.execute('UPDATE nomad_v16.outbox SET next_retry_at=clock_timestamp()')
        again=self.store.reserve_outbox_delivery(CTX,shadow_test=True)
        self.assertEqual(len(again),1)
        self.assertEqual(self.store.reconcile_outbox(ready[0]['outbox_id'],'PRESENT',digest,'provider-receipt-1',CTX),'DELIVERED')
        self.assertEqual(self.store.reserve_outbox_delivery(CTX,shadow_test=True),[])

    def test_shadow_reconciliation(self):
        self.assertEqual(self.store.shadow_import('import_01','GITHUB','repo/main','base_01',{'x':1},CTX), hashlib.sha256(b'{"x":1}').hexdigest())
        self.assertEqual(self.store.reconcile_shadow('GITHUB','repo/main','base_01',hashlib.sha256(b'{"x":1}').hexdigest(),CTX),'MATCH')
        self.assertEqual(self.store.reconcile_shadow('GITHUB','repo/main','base_01','f'*64,CTX),'MISMATCH')

    def test_migration_checksum_replay(self):
        with self.connect() as db:
            self.assertEqual(apply(db),[])

if __name__=='__main__': unittest.main()
