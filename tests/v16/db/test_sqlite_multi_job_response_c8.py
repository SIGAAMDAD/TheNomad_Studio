"""RC-01/STOP-03: a response continues across real SQLite claims and keeps truthful cards."""
import sqlite3
import unittest

from studio_v16.adapters.response import run_response
from studio_v16.communications.service import CoordinationService, Scope
from studio_v16.db.store import GatewayContext
from tests.v16.db import test_sqlite_commands_c8 as fixture
from studio_v16.db.sqlite.types import utc_us


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 45), 'SQLite >= 3.45 required')
class SQLiteMultiJobResponseC8(unittest.TestCase):
    setUp = fixture.SQLiteCommandsC8.setUp

    def _init_second_job(self):
        with self.owner.write() as (db, now):
            db.execute("INSERT INTO jobs(job_id,parent_issue_ref,owner_head_id,state,approved_scope_hash,authority_revision,source_repo,source_base_sha,resource_key) VALUES('job_test_002','issue-2','head','READY',?,'rev_0001','SIGAAMDAD/TheNomad_Studio',?,'src/next')", (fixture.SCOPE,fixture.BASE))
            db.execute("INSERT INTO worker_assignments(job_id,worker_id,assignment_epoch,status) VALUES('job_test_002',?,1,'ACTIVE')",(fixture.WORKER,))
            db.execute("INSERT INTO decisions(decision_id,authority_principal_id,job_id,approved_scope_hash,action,proof_ref,authority_revision) VALUES('decision_claim_2','owner_test_001','job_test_002',?,'CLAIM','test-owner-proof','rev_0001')",(fixture.SCOPE,))
            db.execute("INSERT INTO grants(grant_id,principal_id,job_id,worker_id,repo_id,path_scope,operation,policy_version,authority_revision,approved_scope_hash,source_base_sha,assignment_epoch,expires_at,issuer_approval_ref) VALUES('grant_claim_2',?, 'job_test_002',?,'SIGAAMDAD/TheNomad_Studio','[\"studio_v16/db/\"]','CLAIM','policy_1','rev_0001',?,?,1,?,'decision_claim_2')", (fixture.PRINCIPAL,fixture.WORKER,fixture.SCOPE,fixture.BASE,utc_us()+1200000000))

            db.execute("INSERT INTO decisions(decision_id,authority_principal_id,job_id,approved_scope_hash,action,proof_ref,authority_revision) VALUES('decision_checkpoint_2','owner_test_001','job_test_002',?,'CHECKPOINT','test-owner-proof','rev_0001')",(fixture.SCOPE,))
            db.execute("INSERT INTO grants(grant_id,principal_id,job_id,worker_id,repo_id,path_scope,operation,policy_version,authority_revision,approved_scope_hash,source_base_sha,assignment_epoch,expires_at,issuer_approval_ref) VALUES('grant_checkpoint_2',?,'job_test_002',?,'SIGAAMDAD/TheNomad_Studio',?,'CHECKPOINT','policy_1','rev_0001',?,?,1,?,'decision_checkpoint_2')",(fixture.PRINCIPAL,fixture.WORKER,'["studio_v16/db/"]',fixture.SCOPE,fixture.BASE,utc_us()+1200000000))

    def test_response_claims_second_ready_job_without_stop_and_persists_unknown_free_cards(self):
        self._init_second_job()
        ctx = fixture.CTX
        coord = CoordinationService(self.store, policy={fixture.PRINCIPAL: Scope(frozenset({'design'}),frozenset({'RESPONSE'}))})
        cmds = [fixture.command('CLAIM',101,source_base_sha=fixture.BASE,resource_key='src/main',
                                expected_lease_generation=1,lease_expires_at=fixture.future(),
                                owner_decision_ref='decision_000',grant_id='grant_000'),
                fixture.command('CLAIM',102,'attempt_00000002',source_base_sha=fixture.BASE,resource_key='src/next',
                                expected_lease_generation=1,lease_expires_at=fixture.future(),
                                owner_decision_ref='decision_claim_2',grant_id='grant_claim_2')]
        cmds[1]['job_id'] = 'job_test_002'
        class Port:
            def __init__(self):
                self.claims = list(cmds)
            def start(self, response_id, worker_id, department):
                return coord.start_response(response_id, worker_id, department, ctx)
            def observe_control(self, response_id):
                return coord.observe_control(response_id, ctx)
            def next_claim(self, response_id):
                if not self.claims:
                    return None
                cmd = self.claims.pop(0)
                receipt = coord.claim_for_response(response_id, cmd, ctx)
                if receipt['result'] != 'COMMITTED':
                    raise AssertionError(receipt)
                return dict(cmd, claim_receipt=receipt)
            def record_job(self, response_id, attempt_id, acceptance_ids, evidence):
                return coord.record_response_job(response_id, attempt_id, acceptance_ids, evidence, ctx)
            def finish(self, response_id, *, stop, resume_capsule, failures):
                return coord.finish_response(response_id, ctx, stop=stop, resume_capsule=resume_capsule, failures=failures)
        port = Port()
        def execute(claim):
            step = int(claim['command_id'][-3:])
            cp = fixture.command('CHECKPOINT',200 + step, claim['attempt_id'],
                                 checkpoint_sha256='b'*64,artifact_ref='urn:test:durable',
                                 state='SUBMITTED' if claim['job_id']=='job_test_001' else 'YIELDED',
                                 elapsed_observed_seconds=3,resume_required=claim['job_id']!='job_test_001',
                                 source_base_sha=fixture.BASE)
            cp['job_id'] = claim['job_id']
            result = self.store.execute(cp,ctx)
            self.assertEqual(result['result'],'COMMITTED',result)
            return dict(acceptance_ids=['C8-TEST-JOB'],evidence={'commit_sequence':result['commit_sequence'],
                       'source_sha':fixture.BASE})
        card = run_response(port, 'resp-two','w_test_001','design',execute,
                            lambda:{'manual_resume_required':True}, max_jobs=5)
        self.assertEqual([r['job_id'] for r in card['jobs']],['job_test_001','job_test_002'])
        self.assertEqual([r['outcome'] for r in card['jobs']],['SUBMITTED','YIELDED'], card)
        self.assertEqual(card['termination'],'RESOURCE_BOUNDARY_YIELD')
        self.assertEqual(card['failures'],[])
        self.assertEqual(card['independent_qa'],'NOT_ATTESTED')
        self.assertEqual(port.claims,[])
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute("SELECT status FROM response_attempts WHERE response_id='resp-two'").fetchone(), ('YIELDED',))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM response_job_starts WHERE response_id='resp-two'").fetchone()[0],2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM response_attempt_jobs WHERE response_id='resp-two'").fetchone()[0],2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0],2)
            self.assertEqual(dict(db.execute('SELECT job_id,state FROM jobs')),{'job_test_001':'SUBMITTED','job_test_002':'YIELDED'})
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone(), ('ok',))


if __name__=='__main__':
    unittest.main()
