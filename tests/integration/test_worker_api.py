"""Real HTTP adapter -> owner-gated worker operations -> SQLite storage state.

The database uses the production DDL and writer; sqlite3 is an offline driver
substitute. An APSW backed boot and browser/GitHub observations require CI/live
provider acceptance and are never asserted here.
"""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
from pathlib import Path
from wsgiref.util import setup_testing_defaults

import pytest

from studio.agents.operations import WorkerOperations
from studio.api import (ConfiguredOpaqueVerifier, DomainRegistry, FrozenContracts,
                        StudioOperatorService, StudioWSGI, VerifiedPrincipal)
from studio.storage.workforce import WorkforceStorage
from studio.workforce import Workforce

ROOT = Path(__file__).resolve().parents[2]


class Grants:
    def granted_project_ids(self, principal_id):
        return ('p',) if principal_id == 'owner' else ()


@pytest.fixture
def application(tmp_path):
    conn = sqlite3.connect(tmp_path / 'worker-api.sqlite', isolation_level=None)
    conn.execute('PRAGMA foreign_keys=ON')
    conn.executescript((ROOT / 'schema/initial_schema.sql').read_text())
    conn.execute("INSERT INTO principals VALUES ('owner','trusted','owner','human')")
    conn.execute("INSERT INTO projects(project_id,name,charter_digest,state) VALUES ('p','project','digest','active')")
    conn.execute("INSERT INTO specialist_roles(role_id,role_revision,role_manifest_digest,state) VALUES ('coder','1','digest','ready')")
    token = 'owner.' + 'z' * 48
    verifier = ConfiguredOpaqueVerifier({hashlib.sha256(token.encode()).hexdigest():
        VerifiedPrincipal('owner','test','owner-subject','verified-owner',True)})
    store = WorkforceStorage(conn)
    def authorize(actor, project, operation):
        if actor.get('principal_id') != 'owner' or project != 'p':
            raise ValueError('access denied')
    evidence = {'accept': False}
    operations = WorkerOperations(Workforce(store, require_action=authorize),
         verify_receipt=lambda _kind, _project, _body: evidence['accept'])
    registry = DomainRegistry()
    registry.register_owner('I', operations)
    api = StudioOperatorService(FrozenContracts(ROOT/'CONTRACTS'),verifier,Grants(),registry,None)
    yield StudioWSGI(api), operations, token, conn, evidence
    store.close()


def invoke(app, token, method, path, body=None):
    environ={}
    setup_testing_defaults(environ)
    raw=json.dumps(body, separators=(',',':')).encode() if body is not None else b''
    environ.update({'REQUEST_METHOD':method,'PATH_INFO':path,'QUERY_STRING':'',
                    'CONTENT_LENGTH':str(len(raw)),'CONTENT_TYPE':'application/json',
                    'HTTP_AUTHORIZATION':'Bearer '+token,'wsgi.input':io.BytesIO(raw)})
    events=[]
    response=json.loads(b''.join(app(environ,lambda status,headers:events.append(status))).decode())
    return int(events[0][:3]),response


def test_worker_registration_shift_observation_and_stop_are_durable(application):
    app, ops, token, conn, evidence = application
    path='/v1/projects/p/workers'
    enrollment={'request_id':'register-1','worker_id':'worker-1','browser_chat_ref':'existing-chat','role_id':'coder'}
    assert invoke(app,token,'POST',path,enrollment)==(200,{'worker_id':'worker-1','state':'registered'})
    assert invoke(app,token,'POST',path,enrollment)[0]==200
    assert invoke(app,token,'POST',path,{**enrollment,'role_id':'changed'})[0]==409
    assert invoke(app,token,'GET',path)==(200,{'items':[{'worker_id':'worker-1','role_id':'coder','state':'registered'}]})
    shift_path=path+'/worker-1/shifts'
    assert invoke(app,token,'POST',shift_path,{'request_id':'start-1','shift_id':'shift-1'})==(200,
            {'worker_id':'worker-1','shift_id':'shift-1','state':'awaiting_hello'})
    assert invoke(app,token,'POST',shift_path,{'request_id':'start-2','shift_id':'shift-2'})[0]==409
    hello={'request_id':'hello-1','worker_id':'worker-1','shift_id':'shift-1',
           'receipt_commit_sha':'a'*40,'receipt_digest':'b'*64}
    actor={'principal_id':'owner'}
    with pytest.raises(Exception,match='verified HELLO'):
        ops.observeHello(actor,'p',hello)
    assert conn.execute("SELECT state FROM worker_shifts WHERE shift_id='shift-1'").fetchone()[0]=='awaiting_hello'
    evidence['accept']=True
    assert ops.observeHello(actor,'p',hello)['state']=='active'
    assert invoke(app,token,'POST',shift_path+'/shift-1/stop',
                 {'request_id':'stop-1','reason':'cooperative stop'})==(200,
                 {'shift_id':'shift-1','state':'stopping'})
    assert conn.execute('SELECT kind,state FROM command_outbox').fetchone()==('STOP','queued')
    # The pending STOP has not been observed and acknowledged through GitHub;
    # an HTTP caller cannot manufacture completion with a final receipt field.
    end={'request_id':'end-1','receipt_commit_sha':'c'*40,'receipt_digest':'d'*64}
    status,data=invoke(app,token,'POST','/v1/projects/p/shifts/shift-1/end',end)
    assert (status,data['code'])==(409,'conflict')


def test_invalid_project_and_forged_http_receipt_fail_closed(application):
    app, _, token, _, _ = application
    request={'request_id':'r','worker_id':'w','role_id':'coder','browser_chat_ref':'chat'}
    assert invoke(app,token,'POST','/v1/projects/foreign/workers',request)[0]==403
    status,data=invoke(app,token,'POST','/v1/projects/p/commands/unknown/receipts',{
        'request_id':'r','receipt_id':'receipt','worker_id':'w','shift_id':'s',
        'kind':'completed','result_digest':'a'*64,'receipt_commit_sha':'b'*40})
    assert status==403 and data['code']=='forbidden'


def test_stop_receipt_then_final_receipt_allows_new_shift(application):
    from types import SimpleNamespace
    app, ops, token, conn, evidence = application
    evidence['accept'] = True
    assert invoke(app,token,'POST','/v1/projects/p/workers',{
        'request_id':'reg-next','worker_id':'worker-1','browser_chat_ref':'already-enrolled-chat',
        'role_id':'coder'})[0] == 200
    assert invoke(app,token,'POST','/v1/projects/p/workers/worker-1/shifts',{
        'request_id':'begin-first','shift_id':'shift-1'})[0] == 200
    assert ops.observeHello({'principal_id':'owner'},'p',{
        'request_id':'hello-first','worker_id':'worker-1','shift_id':'shift-1',
        'receipt_commit_sha':'a'*40,'receipt_digest':'b'*64})['state']=='active'
    assert invoke(app,token,'POST','/v1/projects/p/workers/worker-1/shifts/shift-1/stop',{
        'request_id':'stop-first','reason':'owner stop'})[0]==200
    command=conn.execute('SELECT command_id,remote_path,envelope_digest,envelope_json FROM command_outbox').fetchone()
    class ObservedGitHub:
        def publish(self, path, body, *, expected_digest, effect_intent_recorded):
            assert path==command[1] and hashlib.sha256(body).hexdigest()==expected_digest
            assert effect_intent_recorded and body==command[3].encode()
            return SimpleNamespace(verified=True,path=path,body_sha256=expected_digest,
                                   body=body,commit_sha='c'*40)
    result=ops._workforce.publish_pending({'principal_id':'owner'},'p',provider=ObservedGitHub())
    assert result=={'published':1,'unknown':0,'rejected':0}
    status,response=invoke(app,token,'POST','/v1/projects/p/commands/'+command[0]+'/receipts',{
        'request_id':'stop-receipt','receipt_id':'receipt-1','worker_id':'worker-1',
        'shift_id':'shift-1','kind':'stopped','result_digest':'d'*64,
        'receipt_commit_sha':'e'*40})
    assert (status,response)==(200,{'command_id':command[0],'state':'completed'})
    status,response=invoke(app,token,'POST','/v1/projects/p/shifts/shift-1/end',{
        'request_id':'final-first','receipt_commit_sha':'f'*40,'receipt_digest':'a'*64})
    assert (status,response)==(200,{'shift_id':'shift-1','state':'finished'})
    # Same enrolled conversation can take another separately human-started response.
    status,response=invoke(app,token,'POST','/v1/projects/p/workers/worker-1/shifts',{
        'request_id':'begin-second','shift_id':'shift-2'})
    assert status==200 and response['state']=='awaiting_hello'
    assert conn.execute('SELECT COUNT(*) FROM worker_conversations').fetchone()[0]==1
    assert conn.execute('SELECT COUNT(*) FROM worker_shifts').fetchone()[0]==2
