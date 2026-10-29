"""CAB-C deterministic unit and negative tests on pinned R8 physical schema.

Verifiers are simulated trusted service fakes, NOT production provider evidence.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json
import sqlite3

import re
import pytest

from nomad_studio.production.workgraph.api import WorkGraphService
from nomad_studio.production.workgraph.core import Decision, hash_record, make_graph
from nomad_studio.scheduler.api import SchedulingService

HERE=Path(__file__).parent
SCHEMA=json.loads((HERE/'fixtures/r8_records.schema.json').read_text())
DB_SCHEMA=(HERE/'fixtures/r8_initial_schema_v1.sql').read_text()


def assert_frozen_schema(value, name):
    """Minimal stdlib $defs checker for R8 records, independent of dev deps."""
    def walk(v, schema):
        if '$ref' in schema:
            return walk(v, SCHEMA['$defs'][schema['$ref'].split('/')[-1]])
        if 'enum' in schema: assert v in schema['enum']
        typ=schema.get('type')
        if typ=='object':
            assert isinstance(v,dict)
            assert set(schema.get('required',()))<=set(v)
            if schema.get('additionalProperties') is False:
                assert set(v)<=set(schema.get('properties',{}))
            for key,item in v.items():
                if key in schema.get('properties',{}): walk(item,schema['properties'][key])
        if typ=='array':
            assert type(v) is list
            assert len(v)>=schema.get('minItems',0)
            assert len(v)<=schema.get('maxItems',10**9)
            if schema.get('uniqueItems'): assert len(set(json.dumps(i,sort_keys=True) for i in v))==len(v)
            for item in v: walk(item,schema['items'])
        if typ=='string':
            assert type(v) is str
            assert len(v)>=schema.get('minLength',0)
            assert len(v)<=schema.get('maxLength',10**9)
            if 'pattern' in schema: assert re.fullmatch(schema['pattern'],v)
        if typ=='integer':
            assert type(v) is int
            assert v>=schema.get('minimum',float('-inf'))
        if typ=='boolean': assert type(v) is bool
    walk(value,SCHEMA['$defs'][name])
D='a'*64
E='b'*64
F='c'*64


def approved(ref='feature:r1:build'):
    return Decision(True,'ALLOW','OK',ref,('simulated:proof',))


class FakeWriter:
    """Transaction fake. P2 must inject authoritative CAB-C table-limited writer."""
    def __init__(self):
        self.conn=sqlite3.connect(':memory:', isolation_level=None)
        self.conn.row_factory=sqlite3.Row
        self.conn.executescript(DB_SCHEMA)
        assert self.conn.execute('PRAGMA foreign_keys').fetchone()[0]==1

    def write(self, fn):
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            result=fn(self.conn)
            self.conn.commit()
            return result
        except BaseException:
            self.conn.rollback()
            raise

    def read(self, fn): return fn(self.conn)


def contract():
    return {'project_id':'prj','feature_id':'feature','brief_id':'brief',
            'original_brief_digest':D,'bible_pair_digest':E,'revision':1,
            'contract_digest':F,'scope_digest':D,'budget_limit_units':12,
            'acceptance_ids':['accept1','accept2'],
            'required_capabilities':['code','test'], 'unresolved_boundary_refs':[],
            'deliverables':[
                {'deliverable_id':k,'kind':'CODE','description':k,'requirement_refs':[],
                 'acceptance_ids':['accept1'],'required':True} for k in ('outa','outb','outc')]}


def plans():
    return {'project_id':'prj','feature_id':'feature','bible_pair_digest':E,
            'feature_contract_digest':F,
            'tasks':[
               {'task_id':'build','cabal_id':'cabal','dependencies':[],
                'required_skills':['code'],'owned_resources':['src/one'],
                'input_digests':[D],'outputs':['outa'],'acceptance_ids':['accept1'],
                'budget_limit_units':3},
               {'task_id':'check','cabal_id':'cabal','dependencies':['build'],
                'required_skills':['test'],'owned_resources':['src/one'],
                'input_digests':[D],'outputs':['outb'],'acceptance_ids':['accept2'],
                'budget_limit_units':3},
               {'task_id':'docs','cabal_id':'cabal','dependencies':[],
                'required_skills':['code'],'owned_resources':['docs/other'],
                'input_digests':[D],'outputs':['outc'],'acceptance_ids':['accept1'],
                'budget_limit_units':2}]}


def provision(writer):
    c=contract(); conn=writer.conn
    conn.execute('INSERT INTO v1_projects VALUES(?,?,?,?,?)',('prj','owner',D,'ACTIVE',1))
    conn.execute('INSERT INTO v1_briefs VALUES(?,?,?,?,?,?,?,?)',('prj','brief','feature','owner','original',D,'cmd',1))
    conn.execute('INSERT INTO v1_feature_contracts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                 ('prj','feature',1,'brief',D,E,F,D,D,D,12,json.dumps(c)))
    conn.execute('INSERT INTO v1_feature_runs VALUES(?,?,?,?,?,?,?,?,?,?)',
                 ('prj','feature','brief',F,E,'PLANNED',1,0,None,1))


class Harness:
    def __init__(self):
        self.writer=FakeWriter(); provision(self.writer)
        # Explicit simulated CAB-H readback state, not a real provider session.
        self.writer.conn.execute('INSERT INTO v1_host_sessions VALUES(?,?,?,?,?,?)',
                                 ('simulated:host:session','simulator','worker',D,1,1))
        self.now=100_000_000
        self.graph=WorkGraphService(self.writer, rework_guard=lambda r: approved('rework'),
                                    plan_guard=lambda c,s: approved('plan'))
        self.scheduler=SchedulingService(self.writer,clock=lambda: self.now,
            allocation_guard=lambda f,r,c: approved('allocation'),
            placement_guard=lambda t,w,c: approved(t['task_id']),
            checkpoint_guard=lambda r: approved('checkpoint'),
            completion_guard=lambda t,r: approved(t['task_id']),
            recovery_guard=lambda r: approved('recovery'),
            ttl_us=1000,retry_base_us=10)

    def plan(self):
        res=self.graph.plan_feature(contract(),plans()); assert res.status=='OK',res
        assert_frozen_schema(res.record,'WorkGraph')
        self.writer.conn.execute('UPDATE v1_feature_runs SET graph_revision=? WHERE feature_id=?',
                                 (res.record['revision'],'feature')) # P2/CAB-J activation simulation
        return res.record

    def lease(self,logical='build'):
        tid=self.id(logical)
        assert self.scheduler.enqueue_ready('feature',self.revision(),{}).status=='OK'
        res=self.scheduler.lease_task(tid,'worker',approved(tid))
        assert res.status=='OK',res
        return res.record['lease']

    def id(self,logical):return f'feature:r{self.revision()}:{logical}'
    def revision(self):return self.writer.conn.execute('SELECT graph_revision FROM v1_feature_runs WHERE feature_id=?',('feature',)).fetchone()[0]

    def finish(self,lease,status='SUCCEEDED'):
        return self.scheduler.finish_attempt(lease['task_id'],lease['lease_epoch'],
            {'task_id':lease['task_id'],'lease_epoch':lease['lease_epoch'],
             'status':status,'result_digest':D,
             'manifest_ref':'manifest:'+lease['task_id'] if status=='SUCCEEDED' else None,
             'evidence_refs':['simulated:verification']})


@pytest.fixture
def h(): return Harness()


def test_graph_exact_schema_and_persisted_dependencies(h):
    g=h.plan()
    assert len(g['tasks'])==3
    assert h.graph.validate_graph(g,F).allowed
    assert h.writer.conn.execute('SELECT COUNT(*) FROM v1_dependencies').fetchone()[0]==1
    assert [t['task_id'] for t in h.graph.get_ready_tasks('feature',1)]==['feature:r1:build','feature:r1:docs']
    assert [r['remaining_critical_path'] for r in h.graph.readiness_trace('feature',1).record['critical_path']]==[2,1,1]


@pytest.mark.parametrize('mutate',[
    lambda p:p['tasks'][0]['dependencies'].append('check'),
    lambda p:p['tasks'][1]['dependencies'].append('check'),
    lambda p:p['tasks'][1].update(dependencies=['foreign:r1:build']),
    lambda p:p['tasks'][1].update(outputs=[]),
    lambda p:p['tasks'][2].update(outputs=['outb']),
    lambda p:p['tasks'][2].update(owned_resources=['src/one/file']),
    lambda p:p['tasks'][2].update(owned_resources=['SRC/ONE']),
    lambda p:p['tasks'][0].update(budget_limit_units=15),
    lambda p:p['tasks'][0].update(required_skills=['manager']),
])
def test_reject_cycles_self_cross_feature_missing_output_collision_budget_skill(mutate):
    h=Harness(); p=plans();mutate(p)
    res=h.graph.plan_feature(contract(),p)
    assert res.status in ('REJECTED','CONFLICT'),res
    assert h.writer.conn.execute('SELECT COUNT(*) FROM v1_graph_revisions').fetchone()[0]==0


def test_reject_wrong_project_and_stale_bible_and_stale_revision(h):
    p=plans();p['project_id']='other'
    assert h.graph.plan_feature(contract(),p).code=='WRONG_PROJECT'
    p=plans();p['bible_pair_digest']=D
    assert h.graph.plan_feature(contract(),p).code=='STALE_BIBLE'
    h.plan()
    assert h.graph.plan_feature(contract(),plans()).status=='OK'
    assert h.graph.get_ready_tasks('feature',99)==()


def test_worker_fencing_checkpoint_and_expiration(h):
    h.plan();lease=h.lease()
    tid=lease['task_id'];epoch=lease['lease_epoch']
    assert h.scheduler.lease_task(tid,'worker',approved(tid)).status=='CONFLICT'
    checkpoint={'task_id':tid,'lease_epoch':epoch,'host_receipt_ref':'host:checkpoint',
                'content_digest':D,'session_id':lease['session_id']}
    assert h.scheduler.record_checkpoint(tid,epoch,checkpoint).status=='OK'
    assert h.scheduler.record_checkpoint(tid,epoch,checkpoint).status=='OK'
    changed={**checkpoint,'content_digest':F}
    assert h.scheduler.record_checkpoint(tid,epoch,changed).code=='IDEMPOTENCY_CONFLICT'
    h.now+=1000
    assert h.scheduler.fence_expired('prj',h.now).record['fenced'][0]['epoch']==epoch
    assert h.scheduler.record_checkpoint(tid,epoch,checkpoint).code=='LEASE_FENCED'
    assert h.finish(lease).code=='LEASE_FENCED'
    assert h.writer.conn.execute('SELECT state FROM v1_tasks WHERE task_id=?',(tid,)).fetchone()[0]=='RECOVERY_BLOCKED'


def test_restart_reconcile_absent_and_applied(h):
    h.plan();lease=h.lease();h.now+=1000;h.scheduler.fence_expired('prj',h.now)
    record=lambda state:{'task_id':lease['task_id'],'lease_epoch':lease['lease_epoch'],
       'provider_state':state,'manifest_ref':('manifest:provider' if state=='APPLIED' else None),
       'result_digest':(D if state=='APPLIED' else None),'evidence_refs':['simulated:readback']}
    assert h.scheduler.reconcile_expired(lease['task_id'],lease['lease_epoch'],record('UNKNOWN')).status=='UNKNOWN_EXTERNAL'
    assert h.scheduler.reconcile_expired(lease['task_id'],lease['lease_epoch'],record('ABSENT')).status=='OK'
    assert h.scheduler.lease_task(lease['task_id'],'worker',approved(lease['task_id'])).status=='OK'
    h.now+=1000;h.scheduler.fence_expired('prj',h.now)
    assert h.scheduler.reconcile_expired(lease['task_id'],2,record('APPLIED')).code=='LEASE_FENCED'
    newer={**record('APPLIED'),'lease_epoch':2}
    assert h.scheduler.reconcile_expired(lease['task_id'],2,newer).status=='OK'
    assert h.writer.conn.execute('SELECT output_manifest_ref FROM v1_tasks WHERE task_id=?',(lease['task_id'],)).fetchone()[0]=='manifest:provider'


def test_transient_retries_bounded_semantic_failure_no_auto_retries(h):
    h.plan();lease=h.lease()
    assert h.finish(lease,'INFRA_TRANSIENT').record['task_state']=='RETRY_WAIT'
    assert h.scheduler.enqueue_ready('feature',1,{}).record['promoted']==[]
    h.now+=10
    assert lease['task_id'] in h.scheduler.enqueue_ready('feature',1,{}).record['promoted']
    lease2=h.scheduler.lease_task(lease['task_id'],'worker',approved(lease['task_id'])).record['lease']
    assert h.finish(lease2,'SEMANTIC_FEATURE').record['task_state']=='FAILED'
    assert lease['task_id'] not in h.scheduler.enqueue_ready('feature',1,{}).record['promoted']


def test_rework_affected_subgraph_and_retained_manifest(h):
    h.plan();a=h.lease('build'); assert h.finish(a).status=='OK'
    d=h.lease('docs'); assert h.finish(d).status=='OK'
    a2=h.lease('check'); assert h.finish(a2,'SEMANTIC_FEATURE').status=='OK'
    receipt={'trigger_task_ids':[a2['task_id']],'new_input_digests':{a2['task_id']:[F]},
             'evidence_refs':['simulated:testfailure'],'reason':'FAILED_TEST'}
    res=h.graph.create_rework('feature',receipt,1)
    assert res.status=='OK',res
    g=res.record['graph'];assert g['revision']==2
    assert len(res.record['retained_output_refs'])==2
    h.writer.conn.execute('UPDATE v1_feature_runs SET graph_revision=2 WHERE feature_id=\'feature\'')
    assert [t['task_id'] for t in h.graph.get_ready_tasks('feature',2)]==['feature:r2:check']
    assert h.writer.conn.execute("SELECT state FROM v1_tasks WHERE task_id='feature:r2:build'").fetchone()[0]=='SUCCEEDED'
    assert h.writer.conn.execute("SELECT output_manifest_ref FROM v1_tasks WHERE task_id='feature:r2:docs'").fetchone()[0]=='manifest:feature:r1:docs'


def test_rework_fences_old_active_and_rejects_stale(h):
    h.plan();lease=h.lease()
    receipt={'trigger_task_ids':[lease['task_id']],'new_input_digests':{},
             'evidence_refs':['simulated:ownerchange'],'reason':'OWNER_CHANGE'}
    assert h.graph.create_rework('feature',receipt,1).status=='OK'
    h.writer.conn.execute("UPDATE v1_feature_runs SET graph_revision=2 WHERE feature_id='feature'")
    assert h.finish(lease).code in ('LEASE_FENCED','REVISION_CONFLICT')
    # Previous revision cannot create new rework once new graph is activated.
    h.writer.conn.execute('UPDATE v1_feature_runs SET graph_revision=2 WHERE feature_id=\'feature\'')
    assert h.graph.create_rework('feature',receipt,1).code=='REVISION_CONFLICT'


def test_failure_without_trusted_verifiers_blocks_not_success(h):
    h.plan();s=SchedulingService(h.writer,clock=lambda:h.now)
    tid=h.id('build')
    assert s.enqueue_ready('feature',1,{}).status=='BLOCKED_LOCAL'
    assert s.lease_task(tid,'worker',approved(tid)).status=='BLOCKED_LOCAL'
    work=WorkGraphService(h.writer)
    assert work.create_rework('feature',{},1).status=='BLOCKED_LOCAL'


@pytest.mark.parametrize('resource', ['../escape','src/../escape','src//foo','src/.git/HEAD',
                                      '/absolute','src/./thing','src/thing.','src\\thing'])
def test_invalid_resource_path_refused(resource):
    h=Harness();p=plans();p['tasks'][0]['owned_resources']=[resource]
    assert h.graph.plan_feature(contract(),p).status=='REJECTED'


def test_missing_graph_revision_and_untrusted_plan_fail_closed(h):
    g=make_graph(contract(),plans(),3)
    assert h.graph.validate_graph(g,F).code=='NOT_FOUND'
    untrusted=WorkGraphService(h.writer)
    assert untrusted.plan_feature(contract(),plans()).status=='BLOCKED_LOCAL'
    assert h.writer.conn.execute('SELECT COUNT(*) FROM v1_graph_revisions').fetchone()[0]==0


def test_tampering_and_stale_clock_do_not_mutate(h):
    g=h.plan()
    forged=deepcopy(g)
    forged['tasks'][0]['outputs']=['outc']
    assert not h.graph.validate_graph(forged,F).allowed
    lease=h.lease()
    assert h.scheduler.fence_expired('prj',h.now+1000).code=='EVIDENCE_INVALID'
    assert h.finish(lease).status=='OK'
    assert h.finish(lease).status=='CONFLICT'


def test_bounded_retry_ends_failed_after_three_observed_attempts(h):
    h.plan();tid=h.id('build')
    for i in range(3):
        lease=h.lease()
        result=h.finish(lease,'INFRA_TRANSIENT')
        assert result.status=='OK'
        assert result.record['task_state']==('RETRY_WAIT' if i<2 else 'FAILED')
        h.now+=10*(2**i)
    assert tid not in h.scheduler.enqueue_ready('feature',1,{}).record['promoted']
    assert h.writer.conn.execute('SELECT COUNT(*) FROM v1_task_attempts WHERE task_id=?',(tid,)).fetchone()[0]==3


def test_deep_dag_without_recursion_overflow_and_no_cross_feature_dependency():
    from nomad_studio.production.workgraph.core import validate_graph, critical_path
    c=contract();p=plans()
    n=1350
    p['tasks']=[{'task_id':f't{i}','cabal_id':'cabal','dependencies':[f't{i-1}'] if i else [],
                  'required_skills':['code'],'owned_resources':[f'src/unique{i}'],
                  'input_digests':[D],'outputs':['outa'] if i==n-1 else [],
                  'acceptance_ids':[],'budget_limit_units':0} for i in range(n)]
    c['deliverables']=[c['deliverables'][0]]
    graph=make_graph(c,p,1)
    trace=critical_path(graph,set())
    assert len(trace)==n and trace[0]['remaining_critical_path']==n
    validate_graph(graph,F,deliverables={'outa'},budget=12)
    # An external parent must never be accepted merely because it exists in DB.
    p['tasks'][42]['dependencies']=['another_feature:r1:t41']
    with pytest.raises(Exception): make_graph(c,p,1)


def test_rework_only_changed_input_descendants(h):
    h.plan()
    for logical in ('build','docs'):
        assert h.finish(h.lease(logical)).status=='OK'
    receipt={'trigger_task_ids':[h.id('build')], 'new_input_digests':{h.id('build'):[E]},
             'evidence_refs':['simulated:inputchange'],'reason':'CHANGED_INPUT'}
    result=h.graph.create_rework('feature',receipt,1)
    assert result.status=='OK'
    assert set(result.record['affected'])=={'feature:r2:build','feature:r2:check'}
    assert result.record['retained_output_refs']==['manifest:feature:r1:docs']
    assert h.graph.validate_graph(result.record['graph'],F).allowed


def test_exact_frozen_cab_c_table_ownership_and_foreign_keys():
    owners=json.loads((HERE/'fixtures/r8_schema_table_owners.json').read_text())['owner_per_table']
    expected={'v1_graph_revisions','v1_tasks','v1_dependencies','v1_worker_leases',
              'v1_task_attempts','v1_task_checkpoints'}
    assert {k for k,v in owners.items() if v=='CAB-C'}==expected
    writer=FakeWriter()
    tables={r[0] for r in writer.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(owners)==tables
    fk=lambda table:{r[2] for r in writer.conn.execute(f'PRAGMA foreign_key_list({table})')}
    assert {'v1_feature_runs','v1_graph_revisions'}<=fk('v1_tasks')
    assert 'v1_tasks' in fk('v1_worker_leases')
    assert 'v1_tasks' in fk('v1_task_attempts')
    assert 'v1_tasks' in fk('v1_task_checkpoints')
    assert 'v1_tasks' in fk('v1_dependencies')
    indexes={r[0] for r in writer.conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert 'v1_ready_task_lookup' in indexes and 'v1_job_attempt_lookup' in indexes


def test_paused_run_rejects_dispatch_and_unobserved_host_session(h):
    h.plan()
    h.writer.conn.execute("UPDATE v1_feature_runs SET phase='PAUSED' WHERE feature_id='feature'")
    assert h.scheduler.enqueue_ready('feature',1,{}).status=='BLOCKED_LOCAL'
    assert h.graph.readiness_trace('feature',1).status=='OK'
    h.writer.conn.execute("UPDATE v1_feature_runs SET phase='PLANNED' WHERE feature_id='feature'")
    assert h.scheduler.enqueue_ready('feature',1,{}).status=='OK'
    h.writer.conn.execute("UPDATE v1_host_sessions SET observed_real=0 WHERE authenticated_principal='worker'")
    assert h.scheduler.lease_task('feature:r1:build','worker',approved('feature:r1:build')).status=='BLOCKED_LOCAL'
    assert h.writer.conn.execute("SELECT COUNT(*) FROM v1_worker_leases").fetchone()[0]==0


def test_stale_lease_epoch_after_replacement(h):
    h.plan()
    first=h.lease()
    h.now+=1000
    h.scheduler.fence_expired('prj',h.now)
    absent={'task_id':first['task_id'],'lease_epoch':first['lease_epoch'],
        'provider_state':'ABSENT','result_digest':None,'manifest_ref':None,
        'evidence_refs':['simulated:native-readback']}
    assert h.scheduler.reconcile_expired(first['task_id'],1,absent).status=='OK'
    second=h.scheduler.lease_task(first['task_id'],'worker',approved(first['task_id'])).record['lease']
    assert second['lease_epoch']==first['lease_epoch']+1
    cp={'task_id':first['task_id'],'lease_epoch':first['lease_epoch'],
       'host_receipt_ref':'host:checkpoint','content_digest':D,'session_id':first['session_id']}
    assert h.scheduler.record_checkpoint(first['task_id'],1,cp).code=='LEASE_FENCED'
    assert h.finish(second).status=='OK'
