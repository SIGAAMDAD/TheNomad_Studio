"""TX-01/TX-03/TX-04: real-file transactional fencing and no side effects."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import sqlite3
import unittest

from tests.v16.db import test_sqlite_commands_c8 as fixture


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 45), 'SQLite >=3.45 required')
class SQLiteTransactionFencesC8(unittest.TestCase):
    setUp = fixture.SQLiteCommandsC8.setUp
    claim = fixture.SQLiteCommandsC8.claim

    def _counts(self):
        with self.owner.connection(readonly=True) as db:
            return {table: db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
                    for table in ('attempts','resource_leases','event_journal','command_results','outbox')}

    def test_distinct_concurrent_claims_have_one_lease_holder_and_one_effect(self):
        with ThreadPoolExecutor(max_workers=20) as pool:
            results=list(pool.map(lambda n: self.store.execute(self.claim(n, f'attempt_{n:08d}'), fixture.CTX),
                                  range(101, 201)))
        self.assertEqual(sum(x['result']=='COMMITTED' for x in results),1)
        self.assertEqual(sum(x['result']=='DENIED' for x in results),99)
        self.assertTrue(all(x['denial_code']=='RETRYABLE_BLOCKED'
                            for x in results if x['result']=='DENIED'))
        self.assertEqual(self._counts(), dict(attempts=1,resource_leases=1,
                                              event_journal=1,command_results=1,outbox=0))
        with self.owner.connection(readonly=True) as db:
            holder=db.execute('SELECT holder_attempt_id,lease_generation FROM resource_leases').fetchone()
            self.assertEqual(holder[1],1)
            self.assertIsNotNone(db.execute('SELECT 1 FROM attempts WHERE attempt_id=?',(holder[0],)).fetchone())

    def test_stale_assignment_and_lease_rejected_with_atomic_empty_journal(self):
        cases=[(dict(assignment_epoch=2),'STALE_ASSIGNMENT'),
               (dict(payload={**self.claim()['payload'],'expected_lease_generation':2}),'STALE_LEASE')]
        for number,(changes, reason) in enumerate(cases, start=1):
            with self.subTest(reason=reason):
                cmd={**self.claim(number),**changes}
                denied=self.store.execute(cmd,fixture.CTX)
                self.assertEqual(denied['result'],'DENIED')
                self.assertEqual(denied['denial_code'],reason)
                self.assertEqual(self._counts(),dict(attempts=0,resource_leases=0,
                                                     event_journal=0,command_results=0,outbox=0))
        result=self.store.execute(self.claim(10),fixture.CTX)
        self.assertEqual(result['result'],'COMMITTED')
        before=self._counts()
        stale=fixture.command('CHECKPOINT', 11, checkpoint_sha256='a'*64,
                              artifact_ref='urn:test:stale',state='HEARTBEAT',
                              elapsed_observed_seconds=1,resume_required=True,
                              source_base_sha=fixture.BASE)
        stale['assignment_epoch']=2
        result=self.store.execute(stale,fixture.CTX)
        self.assertEqual(result['result'],'DENIED')
        self.assertEqual(result['denial_code'],'STALE_ASSIGNMENT')
        self.assertEqual(self._counts(),before)

    def test_revocation_before_claim_denies_atomically(self):
        with self.owner.write() as (db, now):
            db.execute("UPDATE grants SET revoked_at=? WHERE grant_id='grant_000'", (now,))
        denied=self.store.execute(self.claim(),fixture.CTX)
        self.assertEqual(denied['result'],'DENIED')
        self.assertEqual(denied['denial_code'],'REVOKED_GRANT')
        self.assertEqual(self._counts(),dict(attempts=0,resource_leases=0,
                                             event_journal=0,command_results=0,outbox=0))

    def test_concurrent_revocation_cannot_split_claim_transaction(self):
        # Pause after authorization while the write transaction holds its mutex.
        # Revocation is serialized after the committed claim and must fence every
        # subsequent attempt to mutate under the revoked grant.
        entered, release, revocation_started = Event(), Event(), Event()
        original=self.store._authority
        def pending_authority(conn,cmd,ctx,operation,now,**kwargs):
            value=original(conn,cmd,ctx,operation,now,**kwargs)
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test revocation barrier timeout')
            return value
        self.store._authority=pending_authority
        self.addCleanup(setattr,self.store,'_authority',original)
        def revoke():
            revocation_started.set()
            with self.owner.write() as (db, now):
                db.execute("UPDATE grants SET revoked_at=? WHERE grant_id='grant_001'",(now,))
        with ThreadPoolExecutor(max_workers=2) as pool:
            claim=pool.submit(self.store.execute,self.claim(),fixture.CTX)
            self.assertTrue(entered.wait(5),'claim did not reach transaction barrier')
            revocation=pool.submit(revoke)
            self.assertTrue(revocation_started.wait(5),'revoker did not enter concurrent path')
            release.set()
            self.assertEqual(claim.result(timeout=10)['result'],'COMMITTED')
            revocation.result(timeout=10)
        before=self._counts()
        self.assertEqual(before['attempts'],1)
        self.assertEqual(before['resource_leases'],1)
        stale=fixture.command('CHECKPOINT', 2, checkpoint_sha256='a'*64,
                              artifact_ref='urn:test:revoked',state='HEARTBEAT',
                              elapsed_observed_seconds=1,resume_required=True,
                              source_base_sha=fixture.BASE)
        denied=self.store.execute(stale,fixture.CTX)
        self.assertEqual(denied['result'],'DENIED')
        self.assertEqual(denied['denial_code'],'REVOKED_GRANT')
        self.assertEqual(self._counts(),before)

    def test_reassignment_advances_both_epoch_and_lease_and_fences_old_writer(self):
        self.assertEqual(self.store.execute(self.claim(),fixture.CTX)['result'],'COMMITTED')
        with self.owner.connection(readonly=True) as db:
            old_epoch=db.execute('SELECT assignment_epoch FROM jobs WHERE job_id=?',(fixture.JOB,)).fetchone()[0]
            old_generation=db.execute('SELECT lease_generation FROM resource_leases').fetchone()[0]
        command=fixture.command('REASSIGN',2,new_worker_id='w_test_002',new_assignment_epoch=old_epoch+1,
                                new_lease_generation=old_generation+1,transfer_checkpoint_sha256=None,
                                revocation_decision_ref='decision_003')
        self.assertEqual(self.store.execute(command,fixture.CTX)['result'],'COMMITTED')
        before=self._counts()
        with self.owner.connection(readonly=True) as db:
            epoch=db.execute('SELECT assignment_epoch FROM jobs WHERE job_id=?',(fixture.JOB,)).fetchone()[0]
            generation=db.execute('SELECT lease_generation FROM resource_leases').fetchone()[0]
            active=db.execute("SELECT worker_id,assignment_epoch FROM worker_assignments WHERE status='ACTIVE'").fetchall()
            old=db.execute('SELECT status,revoked_at FROM worker_assignments WHERE worker_id=?',(fixture.WORKER,)).fetchone()
        self.assertEqual(epoch,old_epoch+1)
        self.assertEqual(generation,old_generation+1)
        self.assertEqual(active,[('w_test_002',epoch)])
        self.assertEqual(old[0],'REVOKED')
        self.assertIsNotNone(old[1])
        stale=fixture.command('CHECKPOINT',3,checkpoint_sha256='a'*64,
                              artifact_ref='urn:test:old-writer',state='HEARTBEAT',
                              elapsed_observed_seconds=1,resume_required=True,
                              source_base_sha=fixture.BASE)
        denied=self.store.execute(stale,fixture.CTX)
        self.assertEqual(denied['result'],'DENIED')
        self.assertEqual(denied['denial_code'],'STALE_ASSIGNMENT')
        self.assertEqual(self._counts(),before)


if __name__=='__main__':
    unittest.main()
