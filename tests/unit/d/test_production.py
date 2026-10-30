"""Offline SQLite reference integration, not production APSW or live GitHub proof."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
from studio.production import ProductionService, ProductionFault, TaskGraph, GraphFault
from studio.production.service import _digest

SCHEMA = Path(os.environ.get('R5_CONTRACT_SCHEMA', str(ROOT / 'CONTRACTS' / 'initial_schema.sql')))


class RefTx:
    def __init__(self, connection): self.db = connection
    def one(self, sql, params=()):
        c = self.db.execute(sql, params); r = c.fetchone()
        return dict(r) if r else None
    def all(self, sql, params=()): return [dict(r) for r in self.db.execute(sql, params)]
    def change(self, sql, params=()): return self.db.execute(sql, params).rowcount


class RefCStore:
    """Reference-only C transaction/idempotency semantics using actual frozen SQL."""
    def __init__(self):
        self.db = sqlite3.connect(':memory:', isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA.read_text())
        self.db.execute('PRAGMA foreign_keys=ON')
        self.commands = 0
    def read(self, project_id, fn): return fn(RefTx(self.db))
    def command(self, project_id, operation, request_id, payload_sha256, fn):
        db = self.db; db.execute('BEGIN IMMEDIATE')
        try:
            row = db.execute('SELECT payload_digest,status,result_digest FROM idempotency WHERE project_id=? AND action_owner=? AND request_id=?',
                             (project_id, operation, request_id)).fetchone()
            if row:
                if row['payload_digest'] != payload_sha256:
                    raise ProductionFault('conflict', 'same idempotency key, divergent payload')
                if row['status'] != 'finished':
                    raise ProductionFault('conflict', 'request has unknown outcome')
                out = json.loads(row['result_digest'])
            else:
                db.execute('INSERT INTO idempotency VALUES (?,?,?,?,?,?)', (project_id, operation, request_id, payload_sha256, 'started', None))
                self.commands += 1
                out = fn(RefTx(db))
                db.execute('UPDATE idempotency SET status=?,result_digest=? WHERE project_id=? AND action_owner=? AND request_id=?',
                           ('finished', json.dumps(out, sort_keys=True), project_id, operation, request_id))
            db.execute('COMMIT'); return out
        except BaseException:
            db.execute('ROLLBACK'); raise


class ExplicitAuthorization:
    def __init__(self): self.denied = set()
    def require(self, actor, project_id, operation, subject_id=''):
        if (project_id, operation) in self.denied or actor['principal_id'] != 'owner':
            raise ProductionFault('forbidden', 'policy or authenticated role denies action')


class VerifiedBriefReference:
    def __init__(self): self.blobs = {}
    def add(self, body, project_id='p1'):
        raw = json.dumps(body, sort_keys=True, separators=(',', ':')).encode()
        digest = hashlib.sha256(raw).hexdigest(); self.blobs[(project_id,digest)] = raw
        return digest
    def get_verified(self, *, project_id, content_sha256): return self.blobs.get((project_id,content_sha256), b'')


class ReferenceFBudgets:
    def reserve(self, tx, *, project_id, operation_id, resource_amounts):
        for resource_key, amount in resource_amounts:
            row = tx.one('''SELECT budget_id FROM budgets WHERE project_id=? AND resource_key=?
                            AND reserved_amount+spent_amount+?<=limit_amount ORDER BY budget_id LIMIT 1''',
                         (project_id, resource_key, amount))
            if row is None: raise ProductionFault('conflict', 'budget exhausted')
            tx.change('UPDATE budgets SET reserved_amount=reserved_amount+? WHERE project_id=? AND budget_id=?',
                      (amount, project_id, row['budget_id']))
            tx.change('INSERT INTO reservations VALUES (?,?,?,?,?,?)',
                      (project_id, _digest(operation_id, resource_key), row['budget_id'], operation_id, amount, 'reserved'))
    def release(self, tx, *, project_id, operation_id):
        rows = tx.all("SELECT reservation_id,budget_id,amount FROM reservations WHERE project_id=? AND operation_id=? AND state='reserved'", (project_id, operation_id))
        for row in rows:
            tx.change("UPDATE reservations SET state='released' WHERE project_id=? AND reservation_id=? AND state='reserved'", (project_id, row['reservation_id']))
            tx.change('UPDATE budgets SET reserved_amount=reserved_amount-? WHERE project_id=? AND budget_id=?',
                      (row['amount'], project_id, row['budget_id']))


class DenyWithoutReceipt:
    def require_accepted(self, tx, *, project_id, task_id, lease_generation, evidence_digest):
        raise ProductionFault('qa_failed', 'independent QA proof absent')


class VerifiedProgressReference:
    def __init__(self): self.allowed = set()
    def require_stage(self, tx, *, project_id, task_id, lease_generation, next_state, evidence_digest):
        if (project_id, task_id, lease_generation, next_state, evidence_digest) not in self.allowed:
            raise ProductionFault('forbidden', 'unverified relay/stage receipt')


class VerifiedIndependentQA:
    def __init__(self): self.passed = set(); self.failed = set()
    def require_accepted(self, tx, *, project_id, task_id, lease_generation, evidence_digest):
        if (project_id, task_id, lease_generation, evidence_digest) not in self.passed:
            raise ProductionFault('qa_failed','independent QA receipt mismatch')
    def require_failed(self, tx, *, project_id, task_id, evidence_digest):
        if (project_id, task_id, evidence_digest) not in self.failed:
            raise ProductionFault('qa_failed','independent failed-task receipt mismatch')


class StopSink:
    def __init__(self): self.notified = []
    def notify_task_canceled(self, **kw): self.notified.append(kw)


def fixture_db(db):
    db.execute('INSERT INTO principals VALUES (?,?,?,?)', ('owner','trusted', 'owner','human'))
    for project in ('p1', 'p2'):
        db.execute('INSERT INTO projects(project_id,name,charter_digest,state) VALUES (?,?,?,?)',
                   (project, project,'ch','active'))
        db.execute('INSERT INTO project_grants VALUES (?,?,?)', (project,'owner','owner'))
        for kind in ('design','technical'):
            blob = f'{project}-{kind}-blob'
            bib = f'{project}-{kind}'
            dec = f'dec-{bib}'; effect = f'effect-{bib}'
            db.execute('INSERT INTO blobs VALUES (?,?,?,?)', (blob,10, 'store/'+blob, 'verified'))
            for purpose in ('bible-content','bible-manifest'):
                db.execute('INSERT INTO project_blobs VALUES (?,?,?)', (project,blob,purpose))
            db.execute('INSERT INTO bibles VALUES (?,?,?,?)', (project,bib,kind,bib))
            db.execute('INSERT INTO decision_proposals(project_id,proposal_id,subject_type,subject_id,action,effect_digest,requester_principal_id,state,created_at) VALUES (?,?,?,?,?,?,?,?,?)',
                       (project,dec,'bible',bib,'bible.publish',effect,'owner','pending','now'))
            db.execute('INSERT INTO owner_decisions VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (project,dec,'bible',bib,'bible.publish',effect,None,'owner','proof','approved'))
            db.execute('INSERT INTO effect_intents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (project,effect,'bible',bib,'bible.publish',effect,None,dec,1,'prepared','github',None,'r-'+effect))
            db.execute('INSERT INTO approval_consumptions VALUES (?,?,?,?)', (project,dec,effect,'now'))
            db.execute('INSERT INTO bible_editions VALUES (?,?,?,?,?,?,?,?,?)',
                       (project,bib,'v1',kind,blob,blob,dec,effect,'now'))
            db.execute('INSERT INTO active_bible_editions VALUES (?,?,?,?)', (project,bib,'v1',kind))
        db.execute('INSERT INTO canon_snapshots VALUES (?,?,?,?,?,?,?,?,?,?)',
                   (project,'s1',f'{project}-design','v1','design',f'{project}-technical','v1','technical','pol','manifest'))
        db.execute('INSERT INTO project_repositories VALUES (?,?,?,?,?)', (project,'repo','git','main',None))
        db.execute('INSERT INTO source_revisions VALUES (?,?,?,?,?,?)', (project,'rev1','repo','sha','tree','canonical'))
        db.execute('INSERT INTO specialist_roles(role_id,role_revision,role_manifest_digest,state) VALUES (?,?,?,?)',
                   (f'role-{project}','1','roledigest','ready'))
        for cabal in (f'{project}-cabal', f'{project}-reserve'):
            db.execute('INSERT INTO cabals VALUES (?,?,?,?,?)', (project,cabal,cabal,'charter','active'))
            for member in ('w1', 'w2'):
                db.execute('INSERT INTO cabal_members VALUES (?,?,?,?,?)',
                           (project,cabal,member,f'role-{project}','perms'))
        db.execute('INSERT INTO package_revisions VALUES (?,?,?,?,?,?)',
                   (f'pack-{project}',f'pkg-{project}','1','pkg-manifest','tool','installed'))
        db.execute('INSERT INTO project_packages VALUES (?,?,?,?)',
                   (project,f'pack-{project}','config',1))
        db.execute('INSERT INTO tool_entrypoints VALUES (?,?,?,?,?,?)',
                   (f'pack-{project}','build','binary','args','output','sandbox'))
        db.execute('INSERT INTO tool_grants VALUES (?,?,?,?,?,?,?)',
                   (project,f'grant-{project}',f'pack-{project}','build','ws','grants',0))
        db.execute('INSERT INTO budgets VALUES (?,?,?,?,?,?)', (project,'cpu-budget','cpu',4,0,0))


def brief(*, tasks=None, source='rev1'):
    return {'acceptance_digest': 'accept-feature', 'source_revision_id': source, 'tasks': tasks or [
        {'key':'compile', 'cabal_id':'p1-cabal', 'role_id':'role-p1', 'acceptance_digest':'acc-compile',
         'depends_on':[], 'priority':10,'duration_units':2,'resources':{'cpu':3},
         'package_digest':'pack-p1','entrypoint_id':'build','grant_id':'grant-p1'},
        {'key':'verify', 'cabal_id':'p1-cabal','role_id':'role-p1','acceptance_digest':'acc-verify',
         'depends_on':['compile'], 'priority':8,'duration_units':1,
         'package_digest':'pack-p1','entrypoint_id':'build','grant_id':'grant-p1'}]}


class ProductionTests(unittest.TestCase):
    def setUp(self):
        self.storage = RefCStore(); fixture_db(self.storage.db)
        self.auth = ExplicitAuthorization(); self.blobs=VerifiedBriefReference()
        self.budgets=ReferenceFBudgets(); self.stop=StopSink()
        self.progress = VerifiedProgressReference();self.acceptance=VerifiedIndependentQA()
        self.now=datetime(2026,10,30,tzinfo=timezone.utc)
        self.svc=ProductionService(storage=self.storage, authorization=self.auth,
                                   briefs=self.blobs, reservations=self.budgets,
                                   acceptance=self.acceptance, progress=self.progress, stop=self.stop,
                                   clock=lambda: self.now, lease_seconds=20)
        self.actor={'principal_id':'owner','issuer':'trusted','allowed_project_ids':['p1'],'trusted_proof_ref':'verified'}
    def tearDown(self):
        self.storage.db.close()
    def submit(self, body=None, rid='sf1'):
        digest = self.blobs.add(body or brief())
        return self.svc.submitFeature(self.actor,'p1',{'request_id':rid,'brief_digest':digest,
                    'snapshot_id':'s1','acceptance_digest':'accept-feature'})['feature_id']
    def plan(self, feature_id, rid='pf1'):
        return self.svc.planFeature(self.actor,'p1',feature_id, {'request_id':rid,'snapshot_id':'s1','expected_revision':1})['task_ids']
    def assertFault(self, code, fn):
        with self.assertRaises(ProductionFault) as err: fn()
        self.assertEqual(err.exception.code,code)
    def test_full_dag_plan_sql_and_read(self):
        feature=self.submit(); tasks=self.plan(feature)
        self.assertEqual(len(tasks),2)
        self.assertEqual({r['state'] for r in self.svc.listTasks(self.actor,'p1')['tasks']},{'ready','pending'})
        self.assertEqual(self.svc.ready_candidates(self.actor,'p1')[0]['task_id'],tasks[0])
        self.assertEqual(self.storage.db.execute('SELECT COUNT(*) FROM task_dependencies').fetchone()[0],1)
        self.assertEqual(self.svc.listFeatures(self.actor,'p1')['features'][0]['state'],'planned')
        self.assertEqual(tasks, self.plan_replay(feature))
    def plan_replay(self, feature):
        return self.svc.planFeature(self.actor,'p1',feature, {'request_id':'pf1','snapshot_id':'s1','expected_revision':1})['task_ids']
    def test_idempotency_and_stale_cas(self):
        feature=self.submit()
        self.assertEqual(feature,self.submit(rid='sf1'))
        self.assertFault('conflict',lambda:self.submit(body=brief(tasks=[dict(brief()['tasks'][0])]),rid='sf1'))
        self.plan(feature)
        self.assertFault('stale_revision',lambda:self.svc.planFeature(self.actor,'p1',feature,
                       {'request_id':'new','snapshot_id':'s1','expected_revision':1}))
    def test_cross_project_and_auth_rejection(self):
        f=self.submit(); self.plan(f)
        bad=dict(self.actor,allowed_project_ids=['p2'])
        self.assertFault('forbidden',lambda:self.svc.listTasks(bad,'p1'))
        self.assertFault('missing',lambda:self.svc.planFeature(self.actor,'p1','foreign',
                           {'request_id':'bad','snapshot_id':'s1','expected_revision':1}))
        self.assertFault('missing',lambda:self.svc.assignTask(self.actor,'p1','cross',
                           {'request_id':'bad','cabal_id':'p2-cabal','expected_revision':1}))
        self.auth.denied.add(('p1','dispatchTask'))
        self.assertFault('forbidden',lambda:self.svc.ready_candidates(self.actor,'p2'))
    def test_revision_assign_and_reject_cross_cabal(self):
        task=self.plan(self.submit())[0]
        self.assertFault('missing',lambda:self.svc.assignTask(self.actor,'p1',task,
                            {'request_id':'cross','cabal_id':'p2-cabal','expected_revision':1}))
        out=self.svc.assignTask(self.actor,'p1',task,
                         {'request_id':'assign','cabal_id':'p1-reserve','expected_revision':1})
        self.assertEqual(out['revision'],2)
        self.assertFault('stale_revision',lambda:self.svc.assignTask(self.actor,'p1',task,
                            {'request_id':'assign2','cabal_id':'p1-cabal','expected_revision':1}))
    def test_dispatch_budget_pins_no_browser_execution(self):
        task=self.plan(self.submit())[0]
        req={'request_id':'dispatch1','expected_revision':1,'worker_id':'w1'}
        out=self.svc.dispatchTask(self.actor,'p1',task,req)
        self.assertEqual(out['lease_generation'],1)
        self.assertEqual(out,self.svc.dispatchTask(self.actor,'p1',task,req))
        self.assertEqual(self.storage.db.execute("SELECT reserved_amount FROM budgets WHERE project_id='p1'").fetchone()[0],3)
        prepared=self.svc.prepared_assignment(self.actor,'p1',task,1)
        self.assertEqual((prepared.worker_id, prepared.source_revision_id),('w1','rev1'))
        self.assertEqual(self.storage.db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0],0)
        self.assertEqual(self.storage.db.execute('SELECT COUNT(*) FROM relay_messages').fetchone()[0],0)
        self.assertFault('stale_revision',lambda:self.svc.dispatchTask(self.actor,'p1',task,
                         {'request_id':'dispatch2','expected_revision':1,'worker_id':'w2'}))
        self.assertFault('conflict',lambda:self.svc.prepared_assignment(self.actor,'p1',task,2))
    def test_budget_oversubscription_rollback(self):
        source=brief(tasks=[{**brief()['tasks'][0], 'key':'a'},
                             {**brief()['tasks'][0], 'key':'b'}])
        tasks=self.plan(self.submit(source))
        self.svc.dispatchTask(self.actor,'p1',tasks[0],{'request_id':'d1','expected_revision':1,'worker_id':'w1'})
        self.assertFault('conflict',lambda:self.svc.dispatchTask(self.actor,'p1',tasks[1],
                            {'request_id':'d2','expected_revision':1,'worker_id':'w2'}))
        self.assertEqual(self.storage.db.execute('SELECT COUNT(*) FROM leases').fetchone()[0],1)
        self.assertEqual(self.storage.db.execute('SELECT reserved_amount FROM budgets').fetchone()[0],3)
    def test_cancellation_advisory_and_reservation_release(self):
        task=self.plan(self.submit())[0]
        self.svc.dispatchTask(self.actor,'p1',task,{'request_id':'d1','expected_revision':1,'worker_id':'w1'})
        result=self.svc.cancelTask(self.actor,'p1',task,{'request_id':'cancel','expected_revision':2,'reason':'owner held task'})
        self.assertEqual(result['state'],'canceled')
        self.assertEqual(self.storage.db.execute('SELECT reserved_amount FROM budgets').fetchone()[0],0)
        self.assertEqual(self.storage.db.execute('SELECT status FROM leases').fetchone()[0],'released')
        self.assertEqual(self.stop.notified[0]['lease_generation'],1)
        self.assertFault('conflict',lambda:self.svc.dispatchTask(self.actor,'p1',task,
                    {'request_id':'new','expected_revision':3,'worker_id':'w2'}))
    def test_verify_acceptance_rejects_forged_evidence(self):
        task=self.plan(self.submit())[0]
        self.storage.db.execute("UPDATE tasks SET state='review' WHERE project_id='p1' AND task_id=?",(task,))
        self.assertFault('qa_failed',lambda:self.svc.mark_accepted(self.actor,'p1',task,generation=1,
                          evidence_digest='fake',expected_revision=1,request_id='forged'))
        self.assertEqual(self.storage.db.execute('SELECT state FROM tasks WHERE task_id=?',(task,)).fetchone()[0],'review')
    def test_invalid_graph_and_unverified_brief(self):
        cyclic=brief(tasks=[{**brief()['tasks'][0],'key':'a','depends_on':['b']},
                            {**brief()['tasks'][0],'key':'b','depends_on':['a']}])
        f=self.submit(cyclic)
        self.assertFault('invalid',lambda:self.plan(f))
        self.assertEqual(self.storage.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],0)
        bad=self.blobs.add(brief());self.blobs.blobs[('p1',bad)]=b'tampered'
        self.assertFault('conflict',lambda:self.svc.submitFeature(self.actor,'p1',
            {'request_id':'bad','brief_digest':bad,'snapshot_id':'s1','acceptance_digest':'accept-feature'}))
    def test_stale_source_and_tool_grant_rejection(self):
        f=self.submit();self.storage.db.execute("UPDATE source_revisions SET state='superseded' WHERE project_id='p1'")
        self.assertFault('conflict',lambda:self.plan(f))
    def test_bounded_read_and_priority(self):
        a=self.plan(self.submit())
        self.assertEqual(self.svc.ready_candidates(self.actor,'p1',limit=1)[0]['task_id'],a[0])
        self.assertFault('invalid',lambda:self.svc.ready_candidates(self.actor,'p1',limit=10000))
    def test_lease_expiry_generational_fence(self):
        task=self.plan(self.submit())[0]
        self.svc.dispatchTask(self.actor,'p1',task,{'request_id':'d1','expected_revision':1,'worker_id':'w1'})
        self.now += timedelta(seconds=21)
        rows=self.svc.reconcile_expired(self.actor,'p1',limit=10,request_id='exp1')
        self.assertEqual(rows,[{'task_id':task,'generation':1,'new_state':'ready'}])
        self.assertFault('conflict',lambda:self.svc.prepared_assignment(self.actor,'p1',task,1))
        self.assertEqual(self.storage.db.execute('SELECT reserved_amount FROM budgets WHERE project_id=?',('p1',)).fetchone()[0],0)
        new=self.svc.dispatchTask(self.actor,'p1',task,{'request_id':'d2','expected_revision':3,'worker_id':'w2'})
        self.assertEqual(new['lease_generation'],2)
        self.assertFault('conflict',lambda:self.svc.prepared_assignment(self.actor,'p1',task,1))

    def test_verified_progress_and_qa_unlocks_dependency(self):
        first,second=self.plan(self.submit())
        self.svc.dispatchTask(self.actor,'p1',first,{'request_id':'d1','expected_revision':1,'worker_id':'w1'})
        self.assertFault('forbidden',lambda:self.svc.record_verified_progress(self.actor,'p1',first,
               generation=1,next_state='running',evidence_digest='fake',expected_revision=2,request_id='n1'))
        for state,revision,proof in [('running',2,'hello'),('submitted',3,'handoff'),('review',4,'review-receipt')]:
            self.progress.allowed.add(('p1',first,1,state,proof))
            out=self.svc.record_verified_progress(self.actor,'p1',first,generation=1,
                 next_state=state,evidence_digest=proof,expected_revision=revision,request_id='progress-'+state)
            self.assertEqual(out['state'],state)
        self.assertFault('qa_failed',lambda:self.svc.mark_accepted(self.actor,'p1',first,
               generation=1,evidence_digest='invented',expected_revision=5,request_id='accept1'))
        self.acceptance.passed.add(('p1',first,1,'qa-original'))
        result=self.svc.mark_accepted(self.actor,'p1',first,generation=1,evidence_digest='qa-original',
                                      expected_revision=5,request_id='accept2')
        self.assertEqual(result['ready_task_ids'],[second])
        self.assertEqual(self.storage.db.execute('SELECT state FROM tasks WHERE task_id=?',(second,)).fetchone()[0],'ready')
        self.assertEqual(self.storage.db.execute('SELECT reserved_amount FROM budgets WHERE project_id=?',('p1',)).fetchone()[0],0)

    def test_repair_requires_failed_task_and_verified_qa_evidence(self):
        task=self.plan(self.submit())[0]
        self.storage.db.execute("UPDATE tasks SET state='failed' WHERE project_id='p1' AND task_id=?",(task,))
        repair=brief(tasks=[{**brief()['tasks'][0], 'key':'repair-1'}])
        repair['repair_of_task_id']=task;repair['failure_evidence_digest']='qa-failure'
        self.assertFault('qa_failed',lambda:self.submit(repair,rid='repair1'))
        self.acceptance.failed.add(('p1',task,'qa-failure'))
        feature=self.submit(repair,rid='repair2')
        self.assertEqual(len(self.plan(feature,rid='repair-plan')),1)

    def test_revoked_package_and_member_capacity(self):
        task=self.plan(self.submit())[0]
        self.storage.db.execute("UPDATE tool_grants SET revoked=1 WHERE project_id='p1'")
        self.assertFault('capability_unavailable',lambda:self.svc.dispatchTask(self.actor,'p1',task,
            {'request_id':'d1','expected_revision':1,'worker_id':'w1'}))
        self.storage.db.execute("UPDATE tool_grants SET revoked=0 WHERE project_id='p1'")
        self.assertFault('forbidden',lambda:self.svc.dispatchTask(self.actor,'p1',task,
            {'request_id':'d2','expected_revision':1,'worker_id':'foreign'}))

    def test_running_expired_blocks_not_auto_completed(self):
        task=self.plan(self.submit())[0]
        self.svc.dispatchTask(self.actor,'p1',task,{'request_id':'d1','expected_revision':1,'worker_id':'w1'})
        self.progress.allowed.add(('p1',task,1,'running','hello'))
        self.svc.record_verified_progress(self.actor,'p1',task,generation=1,next_state='running',
                                           evidence_digest='hello',expected_revision=2,request_id='p1')
        self.now += timedelta(seconds=21)
        affected=self.svc.reconcile_expired(self.actor,'p1',limit=10,request_id='e1')
        self.assertEqual(affected[0]['new_state'],'blocked')
        self.assertEqual(self.storage.db.execute('SELECT state FROM tasks WHERE task_id=?',(task,)).fetchone()[0],'blocked')

    def test_keyset_lists_all_rows_over_page_boundary(self):
        f=self.submit(); self.plan(f)
        for i in range(620):
            fid=f'extra-{i:04d}'
            self.storage.db.execute('''INSERT INTO features(project_id,feature_id,brief_digest,snapshot_id,source_revision_id,state)
                         VALUES (?,?,?,?,?,?)''',('p1',fid,'verified-brief','s1','rev1','proposed'))
            self.storage.db.execute('''INSERT INTO tasks(project_id,task_id,feature_id,cabal_id,snapshot_id,role_id,acceptance_digest,priority,state)
                         VALUES (?,?,?,?,?,?,?,?,?)''',('p1',f'extra-task-{i:04d}',fid,'p1-cabal','s1','role-p1','accept',i%9,'pending'))
        features=self.svc.listFeatures(self.actor,'p1')['features']
        tasks=self.svc.listTasks(self.actor,'p1')['tasks']
        self.assertEqual(len(features),621)
        self.assertEqual(len(tasks),622)
        self.assertEqual([t['task_id'] for t in tasks],sorted(t['task_id'] for t in tasks))
        self.assertEqual(len({t['task_id'] for t in tasks}),len(tasks))

    def test_superseded_progress_proof_rejected_even_with_stored_evidence(self):
        task=self.plan(self.submit())[0]
        self.svc.dispatchTask(self.actor,'p1',task,{'request_id':'d1','expected_revision':1,'worker_id':'w1'})
        self.progress.allowed.add(('p1',task,1,'running','old-hello'))
        self.now += timedelta(seconds=30)
        self.svc.reconcile_expired(self.actor,'p1',limit=10,request_id='expired')
        self.svc.dispatchTask(self.actor,'p1',task,{'request_id':'d2','expected_revision':3,'worker_id':'w2'})
        self.assertFault('conflict', lambda:self.svc.record_verified_progress(self.actor,'p1',task,
                          generation=1,next_state='running',evidence_digest='old-hello',
                          expected_revision=4,request_id='stale-hello'))

    def test_missing_f_budget_release_cannot_drop_reservation(self):
        task=self.plan(self.submit())[0]
        self.svc.dispatchTask(self.actor,'p1',task,{'request_id':'d1','expected_revision':1,'worker_id':'w1'})
        self.svc.reservations = None
        self.assertFault('capability_unavailable',lambda:self.svc.cancelTask(self.actor,'p1',task,
                    {'request_id':'c1','expected_revision':2,'reason':'pause'}))
        self.assertEqual(self.storage.db.execute('SELECT reserved_amount FROM budgets WHERE project_id=?',('p1',)).fetchone()[0],3)
        self.assertEqual(self.storage.db.execute('SELECT status FROM leases').fetchone()[0],'active')

    def test_query_plan_uses_project_task_primary_key(self):
        rows=self.storage.db.execute("EXPLAIN QUERY PLAN SELECT task_id,feature_id,state,priority,revision FROM tasks WHERE project_id=? AND task_id>? ORDER BY task_id LIMIT ?",('p1','',256)).fetchall()
        descriptions=' '.join(str(r['detail']) for r in rows)
        self.assertIn('INDEX', descriptions)
        self.assertNotIn('TEMP B-TREE', descriptions)

    def test_deep_dag_non_recursive(self):
        n=4096
        dependencies={f't{i:04d}':frozenset([f't{i-1:04d}']) if i else frozenset()
                      for i in range(n)}
        graph=TaskGraph(dependencies)
        self.assertEqual(len(graph.order()),n)
        self.assertEqual(graph.critical_path({key:1 for key in dependencies})[0],n)
        self.assertEqual(graph.ready(set(),limit=1),('t0000',))

    def test_graph_topology_critical_path_and_cycles(self):
        graph=TaskGraph({'a':frozenset(), 'b':frozenset({'a'}),'c':frozenset({'a'}),
                         'd':frozenset({'b','c'})})
        self.assertEqual(graph.order(),('a','b','c','d'))
        self.assertEqual(graph.ready({'a'}),('b','c'))
        self.assertEqual(graph.critical_path({'a':3,'b':5,'c':2,'d':4}), (12,('a','b','d')))
        with self.assertRaises(GraphFault): TaskGraph({'a':frozenset({'b'}),'b':frozenset({'a'})})
        with self.assertRaises(GraphFault): TaskGraph({'a':frozenset({'missing'})})


if __name__=='__main__': unittest.main()
