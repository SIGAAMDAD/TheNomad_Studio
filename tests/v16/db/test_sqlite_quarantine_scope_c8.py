"""HR-FAULT-23: only the selected dependency subtree is quarantined."""
import sqlite3
import unittest

from studio_v16.communications.service import Scope, sha
from studio_v16.controller.views import ControllerViews
from studio_v16.db.store import GatewayContext, Denied
from tests.v16.db import test_sqlite_watchdog_c8 as fixture


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 45), 'SQLite >= 3.45 required')
class SQLiteQuarantineScopeC8(unittest.TestCase):
    setUp = fixture.SQLiteWatchdogC8.setUp

    def _init_graph(self, *, foreign_child=False):
        self.owner_ctx = GatewayContext(principal_id='human-owner', verified_gateway=True)
        self.coord._policy['human-owner'] = Scope(frozenset({'design'}), frozenset({'QUARANTINE','CONTROLLER'}), owner=True)
        self.controller = ControllerViews(self.coord)
        specs = [('root','design'), ('left','design'), ('right','design'),
                 ('leaf','other' if foreign_child else 'design'), ('unrelated','design')]
        with self.owner.write() as (conn, now):
            conn.execute("INSERT INTO principals(principal_id,issuer,subject,kind,status) VALUES('human-owner','test','human-owner','OWNER','ACTIVE')")
            for job, department in specs:
                conn.execute('''INSERT INTO jobs(job_id,parent_issue_ref,owner_head_id,state,
                    approved_scope_hash,authority_revision,source_repo,source_base_sha,resource_key)
                    VALUES(?,?,?,?,?,'rev','SIGAAMDAD/TheNomad_Studio',?,?)''',
                    (job,'issue-'+job,'head','READY','a'*64,'b'*40,'work/'+job))
                decision='bind-'+job
                conn.execute('''INSERT INTO decisions(decision_id,authority_principal_id,job_id,
                    approved_scope_hash,action,proof_ref,authority_revision)
                    VALUES(?,'human-owner',?,?,'JOB_DEPARTMENT','owner-proof','rev')''',
                    (decision,job,sha(dict(job_id=job,department=department))))
                conn.execute('INSERT INTO job_departments VALUES(?,?,?,?)',
                             (job,department,decision,now))
            for child, parent in [('left','root'),('right','root'),('leaf','left'),('leaf','right')]:
                conn.execute('INSERT INTO job_dependencies VALUES(?,?)',(child,parent))
            binding=dict(root_job_id='root',affected_jobs=['leaf','left','right','root'])
            conn.execute('''INSERT INTO decisions(decision_id,authority_principal_id,job_id,
                approved_scope_hash,action,proof_ref,authority_revision)
                VALUES('quarantine-approval','human-owner','root',?,'SUBTREE_QUARANTINE',
                       'owner-proof','rev')''',(sha(binding),))
        return self.owner_ctx

    def test_approved_diamond_quarantine_blocks_only_affected_subtree(self):
        owner=self._init_graph()
        self.assertEqual(self.controller.quarantine_subtree('root','quarantine-approval',owner),
                         ['leaf','left','right','root'])
        with self.owner.connection(readonly=True) as conn:
            states=dict(conn.execute('SELECT job_id,state FROM jobs ORDER BY job_id'))
            self.assertEqual(states,{'leaf':'BLOCKED','left':'BLOCKED','right':'BLOCKED',
                                     'root':'BLOCKED','unrelated':'READY'})
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM decisions WHERE revoked_at IS NOT NULL').fetchone()[0],0)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.assertEqual([item['job_id'] for item in self.controller.ready('design',owner)],['unrelated'])

    def test_missing_other_department_owner_scope_rolls_back_every_job(self):
        owner=self._init_graph(foreign_child=True)
        with self.assertRaises(Denied):
            self.controller.quarantine_subtree('root','quarantine-approval',owner)
        with self.owner.connection(readonly=True) as conn:
            self.assertEqual(dict(conn.execute('SELECT job_id,state FROM jobs')),
                             dict.fromkeys(['root','left','right','leaf','unrelated'],'READY'))
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.assertEqual(set(item['job_id'] for item in self.controller.ready('design',owner)),
                         {'root','unrelated'})


if __name__=='__main__':
    unittest.main()
