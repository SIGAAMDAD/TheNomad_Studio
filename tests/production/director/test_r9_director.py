"""CAB-J isolated authority-gated tests; provider observations here are simulated."""
from __future__ import annotations
import contextlib, json, sqlite3, time
import pytest
from nomad_studio.production.director.api import DirectorService
from nomad_studio.production.director.commands import CommandService
from nomad_studio.production.director.wire import Result, digest
from nomad_studio.production.status.api import ProductionStatusService

D = 'a'*64
C = 'b'*64
H = 'c'*64
SHA = 'a'*40
SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE v1_projects(project_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL);
CREATE TABLE v1_briefs(project_id TEXT NOT NULL,brief_id TEXT PRIMARY KEY,feature_id TEXT NOT NULL UNIQUE,original_digest TEXT NOT NULL);
CREATE TABLE v1_feature_contracts(project_id TEXT NOT NULL,feature_id TEXT NOT NULL,contract_digest TEXT NOT NULL UNIQUE,bible_pair_digest TEXT NOT NULL,original_brief_digest TEXT NOT NULL);
CREATE TABLE v1_feature_runs(project_id TEXT NOT NULL,feature_id TEXT PRIMARY KEY,brief_id TEXT NOT NULL,active_contract_digest TEXT NOT NULL,bible_pair_digest TEXT NOT NULL,phase TEXT NOT NULL,revision INTEGER NOT NULL,graph_revision INTEGER NOT NULL,qa_branch_ref TEXT,updated_at_us INTEGER NOT NULL);
CREATE TABLE v1_graph_revisions(feature_id TEXT NOT NULL,graph_revision INTEGER NOT NULL,contract_digest TEXT NOT NULL,bible_pair_digest TEXT NOT NULL);
CREATE TABLE v1_outbox(message_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL);
CREATE TABLE v1_operator_receipts(feature_id TEXT NOT NULL,receipt_id TEXT PRIMARY KEY,phase TEXT NOT NULL,subject_digest TEXT NOT NULL,evidence_ref TEXT NOT NULL,issued_at_us INTEGER NOT NULL);
CREATE TABLE v1_dashboard_projections(project_id TEXT NOT NULL,feature_id TEXT NOT NULL,projection_digest TEXT NOT NULL,payload_json TEXT NOT NULL,updated_at_us INTEGER NOT NULL,PRIMARY KEY(project_id,feature_id));
CREATE TABLE v1_owner_commands(command_id TEXT PRIMARY KEY,project_id TEXT,feature_id TEXT,owner_principal_id TEXT NOT NULL,request_digest TEXT NOT NULL,result_digest TEXT NOT NULL,idempotency_key TEXT NOT NULL UNIQUE,created_at_us INTEGER NOT NULL);
CREATE TABLE v1_independent_qa(feature_id TEXT NOT NULL,commit_sha TEXT NOT NULL,verdict TEXT NOT NULL);
CREATE TABLE v1_owner_decisions(project_id TEXT NOT NULL,decision_id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,action TEXT NOT NULL,subject_digest TEXT NOT NULL,proof_ref TEXT,expires_at_us INTEGER,consumed_at_us INTEGER);
CREATE TABLE v1_qa_ready_receipts(feature_id TEXT NOT NULL,receipt_id TEXT PRIMARY KEY,commit_sha TEXT NOT NULL,contract_digest TEXT NOT NULL,manifest_digest TEXT NOT NULL,quality_digest TEXT NOT NULL,quality_signer TEXT NOT NULL,created_at_us INTEGER NOT NULL);
CREATE TABLE v1_test_executions(feature_id TEXT NOT NULL,execution_id TEXT PRIMARY KEY,commit_sha TEXT NOT NULL,test_id TEXT NOT NULL,exit_code INTEGER NOT NULL,log_digest TEXT NOT NULL,adapter_digest TEXT NOT NULL,toolchain_digest TEXT NOT NULL,input_digest TEXT NOT NULL,runner_principal_id TEXT NOT NULL,observed_at_us INTEGER NOT NULL);
CREATE TABLE v1_tasks(feature_id TEXT NOT NULL,task_id TEXT PRIMARY KEY,graph_revision INTEGER NOT NULL,state TEXT NOT NULL,budget_limit_units INTEGER NOT NULL);
CREATE TABLE v1_dependencies(feature_id TEXT NOT NULL,parent_id TEXT NOT NULL,child_id TEXT NOT NULL);
CREATE TABLE v1_task_attempts(task_id TEXT NOT NULL,started_us INTEGER,finished_us INTEGER,status TEXT NOT NULL);
CREATE TABLE v1_budget_reservations(feature_id TEXT NOT NULL,reservation_id TEXT PRIMARY KEY,reserved_units INTEGER NOT NULL,measured_units INTEGER NOT NULL);
CREATE TABLE v1_budget_settlements(settlement_id TEXT PRIMARY KEY,reservation_id TEXT NOT NULL,measured_units INTEGER NOT NULL);
CREATE TABLE v1_review_assignments(feature_id TEXT NOT NULL,review_id TEXT PRIMARY KEY);
CREATE TABLE v1_review_verdicts(review_id TEXT NOT NULL,verdict TEXT NOT NULL);
'''

class Writer:
    def __init__(self,conn):self.conn=conn
    @contextlib.contextmanager
    def transaction(self):
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            yield self.conn
        except BaseException:
            self.conn.rollback(); raise
        else:self.conn.commit()

class Outbox:
    def append(self, tx, msg):
        tx.execute('INSERT INTO v1_outbox VALUES (?,?)',(msg['idempotency_key'],json.dumps(msg,sort_keys=True)))

@pytest.fixture
def env():
    conn=sqlite3.connect(':memory:')
    conn.executescript(SCHEMA)
    conn.execute('INSERT INTO v1_projects VALUES (?,?)',('p','owner'))
    conn.execute('INSERT INTO v1_briefs VALUES (?,?,?,?)',('p','brief','f',H))
    conn.execute('INSERT INTO v1_feature_contracts VALUES (?,?,?,?,?)',('p','f',C,D,H))
    conn.execute('INSERT INTO v1_graph_revisions VALUES (?,?,?,?)',('f',1,C,D))
    conn.commit()
    writer=Writer(conn)
    receipts={}
    def observe(kind):
        def run(feature,snapshot,evidence):
            return Result('OK','OK',{
                'project_id':'p', 'feature_id':'f', 'bible_pair_digest':D,
                'feature_contract_digest':C, 'revision':1,
                'source_commit':SHA, 'tested_commit':SHA,
                'artifact_manifest_digest': H, 'trusted_quality_signer':'quality_e',
            }, evidence.get('receipt_ref', kind+':verified'))
        return run
    observers={k:observe(k) for k in ('context','contract','graph','scheduler','repository','adapter','quality')}
    director=DirectorService(writer,outbox=Outbox(),observations=observers,clock_us=lambda:1000)
    yield conn,director,writer
    conn.close()

def start(env):
    conn,director,writer=env
    assert director.open_run({'project_id':'p','feature_id':'f','brief_id':'brief','original_digest':H},{'project_id':'p','feature_id':'f','brief_id':'brief','original_brief_digest':H,'bible_pair_digest':D,'contract_digest':C}).status=='OK'
    return director

def advance(director,state,receipt=None):
    rev=director.get_feature_status('f','owner').record['revision']
    if receipt is None and state in ('CONTEXT_PINNED','CONTRACT_READY','PLANNED','IN_PRODUCTION','INTEGRATING','VERIFYING','QA_READY','QA_IN_PROGRESS','QA_PASSED','MERGE_REQUESTED','MERGED'):
        kind={'CONTEXT_PINNED':'context','CONTRACT_READY':'contract','PLANNED':'graph','IN_PRODUCTION':'scheduler','INTEGRATING':'repository','VERIFYING':'adapter','QA_READY':'quality','QA_IN_PROGRESS':'quality','QA_PASSED':'quality','MERGE_REQUESTED':'repository','MERGED':'repository'}[state]
        receipt={'receipt_ref':kind+':verified', **({'source_commit':SHA} if state in ('INTEGRATING','VERIFYING','QA_READY') else {})}
    return director.advance_run('f',rev,state,receipt or {})

def test_open_cas_idempotency_and_outbox(env):
    d=start(env)
    assert env[0].execute('SELECT COUNT(*) FROM v1_outbox').fetchone()[0]==1
    assert d.open_run({'project_id':'p','feature_id':'f','brief_id':'brief','original_digest':H},{'project_id':'p','feature_id':'f','brief_id':'brief','original_brief_digest':H,'bible_pair_digest':D,'contract_digest':C}).status=='OK'
    assert d.advance_run('f',0,'PLANNED',{}).code=='INVALID_INPUT'
    assert advance(d,'QA_READY').status=='REJECTED'
    for state in ('CONTEXT_PINNED','CONTRACT_READY','PLANNED','IN_PRODUCTION','INTEGRATING','VERIFYING'):
        assert advance(d,state).status=='OK'
    assert d.advance_run('f',7,'QA_READY',{'source_commit':SHA, 'receipt_ref':'quality:verified'}).status=='BLOCKED_LOCAL'
    assert d.advance_run('f',7,'QA_READY',{'source_commit':SHA, 'receipt_ref':'quality:verified'}).code=='EVIDENCE_INVALID'
    assert env[0].execute('SELECT phase FROM v1_feature_runs').fetchone()[0]=='VERIFYING'
    assert d.advance_run('f',4,'PAUSED',{'reason':'stale'}).code=='REVISION_CONFLICT'

def test_qa_exact_commit_and_resume(env):
    d=start(env)
    for state in ('CONTEXT_PINNED','CONTRACT_READY','PLANNED','IN_PRODUCTION','INTEGRATING','VERIFYING'):
        assert advance(d,state).status=='OK'
    conn=env[0]
    conn.execute('INSERT INTO v1_qa_ready_receipts VALUES (?,?,?,?,?,?,?,?)',('f','quality:verified',SHA,C,H,D,'quality_e',1))
    conn.execute('INSERT INTO v1_test_executions VALUES (?,?,?,?,?,?,?,?,?,?,?)',('f','t1',SHA,'build',0,D,D,D,D,'runner',1))
    conn.commit()
    assert advance(d,'QA_READY',{'source_commit':'b'*40, 'receipt_ref':'quality:verified'}).code=='EVIDENCE_INVALID'
    assert advance(d,'QA_READY',{'source_commit':SHA, 'receipt_ref':'quality:verified'}).status=='OK'
    assert d.get_feature_status('f','intruder').code=='PERMISSION_DENIED'
    assert advance(d,'PAUSED',{'reason':'owner_pause'}).status=='OK'
    assert d.resume_target('f')=='QA_READY'
    assert advance(d,'QA_PASSED').code=='REVISION_CONFLICT'
    assert advance(d,'QA_READY').status=='OK'
    assert d.resume_target('f') is None
    assert advance(d,'CANCELLED',{'reason':'owner_cancel'}).status=='OK'
    assert advance(d,'PLANNED').status=='REJECTED'

def test_owner_command_strict_identity_and_idempotency(env):
    conn,d,writer=env
    status=ProductionStatusService(writer)
    class Dummy:pass
    service=CommandService(writer,outbox=Outbox(),status=status,director=d,workflow=Dummy(),payload_reader=Dummy(),clock_us=lambda:10)
    command={'command_id':'c1','operation':'studio.status','owner_principal_id':'owner','request_digest':D,'auth_proof_ref':'proof', 'idempotency_key':'key','issued_at_us':1}
    signed=Result('OK','OK',{'principal_id':'owner','principal_kind':'OWNER','action_digest':D,'simulated':False},'proof')
    assert service.submit_owner_command(command,Result('OK','OK',{'principal_id':'owner'},'proof')).code=='UNAUTHENTICATED'
    assert service.submit_owner_command({**command,'payload':{'foo':'bar'}},signed).code=='INVALID_INPUT'
    assert service.submit_owner_command(command,signed).status=='ACCEPTED'
    assert service.submit_owner_command(command,signed).status=='ACCEPTED'
    assert service.submit_owner_command({**command,'request_digest':H},Result('OK','OK',{**signed.record,'action_digest':H},'proof')).code=='IDEMPOTENCY_CONFLICT'
    assert conn.execute('SELECT COUNT(*) FROM v1_outbox').fetchone()[0]==1
    assert service.execute_pending(command,signed).status=='OK'
    replay=service.execute_pending(command,signed)
    assert replay.status=='OK' and replay.record['processed_result_digest'] == conn.execute('SELECT result_digest FROM v1_owner_commands WHERE command_id=?', ('c1',)).fetchone()[0]
    assert service.submit_owner_command(command,signed).status=='OK'
    assert service.get_studio_status('intruder',None).record['features']==[]

def test_restart_resume_from_stored_projection(env):
    d=start(env)
    assert advance(d,'CONTEXT_PINNED').status=='OK'
    assert advance(d,'PAUSED',{'reason':'owner_pause'}).status=='OK'
    recovered=DirectorService(env[2],outbox=Outbox(),observations=d.observations,clock_us=lambda:2000)
    assert recovered.resume_target('f')=='CONTEXT_PINNED'
    assert advance(recovered,'CONTRACT_READY').code=='REVISION_CONFLICT'
    assert advance(recovered,'CONTEXT_PINNED').status=='OK'

def test_one_brief_plan_qa_trace_rework_and_metrics(env):
    """Simulated cross-CAB ports; CAB-J computations consume real local DB rows."""
    from types import SimpleNamespace
    from nomad_studio.production.director.workflow import ProductionWorkflow
    conn,d,writer=env
    class Brief:
        def admit_brief(self,command,identity):
            return Result('OK','OK',{'brief_id':'brief','project_id':'p','feature_id':'f','owner_principal_id':'owner','owner_command_ref':'owner_cmd','original_digest':H},'b:admitted')
        def amend_brief(self,feature,amendment,identity):
            return Result('OK','OK',{'project_id':'p','feature_id':'f','brief_id':'brief','old_scope_digest':D, 'affected_dependencies':['B']},'b:amended')
        def get_original_brief(self,brief,project):
            return Result('OK','OK',{'project_id':'p','feature_id':'f','brief_id':'brief','original_digest':H},'b:brief')
    class Bibles:
        def snapshot_active_pair(self,project,scope):
            return Result('OK','OK',{'project_id':'p','pair_digest':D,'design_edition_id':'design','technical_edition_id':'tech','design_manifest_digest':D,'technical_manifest_digest':D,'design_epoch':1,'technical_epoch':1},'d:pair')
    class Context:
        def build_bundle(self,project,brief,pair,fence):
            return Result('OK','OK',{'project_id':'p','bundle_digest':D},'i:bundle')
    class Contract:
        def compile_contract(self,brief,pair,bundle):
            return Result('OK','OK',{'project_id':'p','feature_id':'f','brief_id':'brief','bible_pair_digest':D,'contract_digest':C,'original_brief_digest':H,'required_capabilities':['code']},'b:contract')
        def revise_contract(self,feature,change,original,pair):
            conn.execute('INSERT INTO v1_feature_contracts VALUES (?,?,?,?,?)',('p','f','d'*64,D,H));conn.commit()
            return Result('OK','OK',{'project_id':'p','feature_id':'f','brief_id':'brief','bible_pair_digest':D,'contract_digest':'d'*64,'scope_digest':'d'*64},'b:revised')
    class Capabilities:
        def resolve_capabilities(self,project,skills,pair):
            return Result('OK','OK',{'project_id':'p','capacity':1},'g:capacity')
    class Graph:
        def plan_feature(self,contract,snapshot):
            return Result('OK','OK',{'project_id':'p','feature_id':'f','feature_contract_digest':C,'bible_pair_digest':D,'revision':1},'c:planned')
        def validate_graph(self,graph,contract):
            return SimpleNamespace(status='ALLOW',code='OK',allowed=True)
        def create_rework(self,feature,change,revision):
            conn.execute('INSERT INTO v1_graph_revisions VALUES (?,?,?,?)',('f',revision+1,'d'*64,D));conn.commit()
            return Result('OK','OK',{'project_id':'p','feature_id':'f','revision':revision+1},'c:rework')
    class Schedule:
        def enqueue_ready(self,feature,revision,scoped):
            return Result('OK','OK',{'project_id':'p','feature_id':'f','graph_revision':revision},'scheduler:verified')
    class PayloadReader:
        def read_request(self,command_id,request_digest):
            return {'original_text': 'Implement a quest'}
    from nomad_studio.production.director.wire import digest
    workflow=ProductionWorkflow(director=d, brief_port=Brief(), bible_port=Bibles(), contract_port=Contract(), context_port=Context(), graph_port=Graph(), capability_port=Capabilities(), scheduling_port=Schedule(), payload_reader=PayloadReader())
    admission=workflow.submit({'command_id':'owner_cmd','project_id':'p','owner_principal_id':'owner','request_digest':digest({'original_text':'Implement a quest'})}, Result('OK','OK',{'principal_id':'owner'},'proof'))
    assert admission.status=='OK'
    assert admission.record['status']=='IN_PRODUCTION'
    for state in ('INTEGRATING','VERIFYING'):
        assert advance(d,state).status=='OK'
    conn.execute('INSERT INTO v1_qa_ready_receipts VALUES (?,?,?,?,?,?,?,?)',('f','quality:verified',SHA,C,H,D,'quality_e',1))
    conn.execute('INSERT INTO v1_test_executions VALUES (?,?,?,?,?,?,?,?,?,?,?)',('f','test',SHA,'build',0,D,D,D,D,'runner',1))
    conn.execute('INSERT INTO v1_tasks VALUES (?,?,?,?,?)',('f','A',1,'DONE',10))
    conn.execute('INSERT INTO v1_tasks VALUES (?,?,?,?,?)',('f','B',1,'DONE',10))
    conn.execute('INSERT INTO v1_tasks VALUES (?,?,?,?,?)',('f','C',1,'DONE',10))
    conn.execute('INSERT INTO v1_dependencies VALUES (?,?,?)',('f','A','B'))
    conn.execute('INSERT INTO v1_dependencies VALUES (?,?,?)',('f','A','C'))
    for task,end in (('A',100),('B',200),('C',300)):
        conn.execute('INSERT INTO v1_task_attempts VALUES (?,?,?,?)',(task,1,end,'VERIFIED'))
    conn.execute('INSERT INTO v1_budget_reservations VALUES (?,?,?,?)',('f','reservation',40,27))
    conn.execute('INSERT INTO v1_budget_settlements VALUES (?,?,?)',('settlement','reservation',27))
    conn.commit()
    assert advance(d,'QA_READY').status=='OK'
    status=ProductionStatusService(writer).get_feature('owner','f')
    assert status.record['status']=='QA_READY'
    assert status.record['critical_path_task_ids']==['A','C']
    assert status.record['critical_path_elapsed_us']==398
    assert status.record['settled_units']==27 and status.record['cost_complete']
    assert status.record['qa_ready_receipt_ref']=='quality:verified'
    amended=workflow.replan_changes('f','owner',{'scope':'change-B'},Result('OK','OK',{},'owner:verified'))
    assert amended.status=='OK' and amended.record['status']=='REWORK_REQUIRED'
    assert amended.record['feature_contract_digest']=='d'*64
    assert ProductionStatusService(writer).get_feature('owner','f').record['source_commit'] is None
    assert ProductionStatusService(writer).get_feature('owner','f').record['rework_rejections']==0
