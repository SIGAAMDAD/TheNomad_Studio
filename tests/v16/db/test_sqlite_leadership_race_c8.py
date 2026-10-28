"""HR-FAULT-21 local competing owner-approved leadership transfer race."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import unittest

from studio_v16.communications.service import Scope, sha
from studio_v16.db.store import Denied, GatewayContext
from tests.v16.db import test_sqlite_controls_c8 as fixture


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 45), 'SQLite >= 3.45 required')
class SQLiteLeadershipRaceC8(unittest.TestCase):
    setUp = fixture.SQLiteControlsC8.setUp

    def _setup_handoffs(self):
        owner = GatewayContext(principal_id='owner', verified_gateway=True)
        self.coord._policy['owner'] = Scope(frozenset({'design'}), frozenset({'HANDOFF'}), owner=True)
        for candidate in ('candidate-a', 'candidate-b'):
            self.coord._policy[candidate] = Scope(frozenset({'design'}), frozenset({'CONTROL'}))
        with self.owner.write() as (db, now):
            for name, kind in (('owner','OWNER'),('candidate-a','WORKER'),('candidate-b','WORKER')):
                db.execute('INSERT INTO principals(principal_id,issuer,subject,kind,status) VALUES(?,?,?,?,?)',
                           (name,'fixture',name,kind,'ACTIVE'))
            db.execute('''INSERT INTO jobs(job_id,parent_issue_ref,owner_head_id,state,approved_scope_hash,
                authority_revision,source_repo,source_base_sha,resource_key)
                VALUES('handoff-job','fixture-issue','head','READY',?,'rev-1','SIGAAMDAD/TheNomad_Studio',?, 'design')''',
                       ('a'*64,'b'*40))
            for candidate, decision_id in [('candidate-a','handoff-a'),('candidate-b','handoff-b')]:
                binding = dict(department='design',new_principal_id=candidate,expected_epoch=1)
                db.execute('''INSERT INTO decisions(decision_id,authority_principal_id,job_id,
                    approved_scope_hash,action,proof_ref,authority_revision)
                    VALUES(?,'owner','handoff-job',?,'LEADER_HANDOFF','owner-fixture-proof','rev-1')''',
                    (decision_id,sha(binding)))
        return owner

    def test_competing_handoffs_have_one_epoch_winner_and_fence_old_head(self):
        owner = self._setup_handoffs()
        def attempt(candidate, decision_id):
            try:
                return ('ACCEPTED', candidate, self.coord.handoff('design',candidate,1,decision_id,owner))
            except Denied as err:
                return ('DENIED', candidate, str(err))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda pair: attempt(*pair),
                                    [('candidate-a','handoff-a'),('candidate-b','handoff-b')]))
        winners = [r for r in results if r[0]=='ACCEPTED']
        losers = [r for r in results if r[0]=='DENIED']
        self.assertEqual(len(winners),1,results)
        self.assertEqual(len(losers),1,results)
        self.assertEqual(winners[0][2],2)
        leader = winners[0][1]
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT principal_id,leader_epoch FROM leader_state WHERE department=\'design\'').fetchone(),(leader,2))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM leader_handoffs').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT old_principal_id,old_epoch,new_epoch,approver_principal_id FROM leader_handoffs').fetchone(),('leader',1,2,'owner'))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM response_controls').fetchone()[0],0)
        with self.assertRaises(Denied):
            self.coord.control('response-c8','STOP',0,'old-head-stop-nonce-01',1,self.leader)
        with self.assertRaises(Denied):
            self.coord.control('response-c8','STOP',0,'loser-head-stop-nonce',2,
                               GatewayContext(principal_id=losers[0][1],verified_gateway=True))
        winner_ctx = GatewayContext(principal_id=leader,verified_gateway=True)
        self.assertEqual(self.coord.control('response-c8','STOP',0,'new-head-stop-nonce-01',2,winner_ctx),1)

    def test_succession_audit_cannot_be_mutated_or_deleted(self):
        owner = self._setup_handoffs()
        self.assertEqual(self.coord.handoff('design','candidate-a',1,'handoff-a',owner),2)
        for sql in ("UPDATE leader_handoffs SET new_principal_id='candidate-b'", "DELETE FROM leader_handoffs"):
            with self.subTest(sql=sql), self.assertRaisesRegex(sqlite3.IntegrityError,'IMMUTABLE_EVIDENCE'):
                with self.owner.write() as (db, _):
                    db.execute(sql)
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT new_principal_id,new_epoch FROM leader_handoffs').fetchone(),('candidate-a',2))
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])


if __name__ == '__main__':
    unittest.main()
