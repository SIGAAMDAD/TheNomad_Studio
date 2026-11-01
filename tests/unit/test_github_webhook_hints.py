"""Webhooks can persist authenticated hints but never authorize external effects."""
import hashlib
import hmac
import io
import json
import sqlite3
import weakref
import uuid

from studio.app.github_webhooks import GitHubWebhookBinding, GitHubWebhookHints
from studio.storage.workforce import WorkforceStorage
from studio.workforce import WorkforceFault


class RecordingC(WorkforceStorage):
    def __init__(self):
        super().__init__(sqlite3.connect(':memory:'))
        weakref.finalize(self, self._connection.close)
        self.events={}
    def command(self, project, operation, request, digest, callback):
        key=(project,request)
        existing=self.events.get(key)
        if existing is not None:
            if existing[0]!=digest:
                raise WorkforceFault('conflict','changed payload for delivery')
            return existing[1]
        result=callback(None)
        self.events[key]=(digest,result)
        return result


def rig():
    writer=RecordingC()
    wake=[]
    binding=GitHubWebhookBinding('p1','owner','repo','main','studio/comms',b'webhook-secret-value')
    receiver=GitHubWebhookHints(writer,(binding,),lambda:wake.append('wake'))
    return writer,receiver,wake


def request(receiver, payload, *, secret=b'webhook-secret-value',
            event='push', ref='refs/heads/studio/comms',
            delivery=None, content_type='application/json'):
    doc=dict(payload)
    doc.setdefault('repository',{'full_name':'owner/repo'})
    if event=='push':
        doc.setdefault('ref',ref)
    raw=json.dumps(doc,sort_keys=True).encode()
    sign='sha256='+hmac.new(secret,raw,hashlib.sha256).hexdigest()
    env={'REQUEST_METHOD':'POST','PATH_INFO':'/webhooks/github',
         'CONTENT_LENGTH':str(len(raw)),'CONTENT_TYPE':content_type,
         'HTTP_X_GITHUB_EVENT':event,
         'HTTP_X_GITHUB_DELIVERY':delivery or str(uuid.UUID(int=1)),
         'HTTP_X_HUB_SIGNATURE_256':sign,
         'wsgi.input':io.BytesIO(raw)}
    status=[]
    receiver(env,lambda st,headers:status.append(st))
    return int(status[0].split(' ')[0])


def test_valid_delivery_persists_once_but_only_sends_nonblocking_hints():
    writer,receiver,wake=rig()
    assert request(receiver,{})==202
    assert request(receiver,{})==202
    assert len(writer.events)==1
    assert wake==['wake','wake']
    record=next(iter(writer.events.values()))[1]
    assert record['project_id']=='p1'
    assert record['state']=='recorded'


def test_rejects_bad_signature_foreign_repo_wrong_ref_and_changed_guid_content():
    writer,receiver,wake=rig()
    assert request(receiver,{},secret=b'wrong-signature-value')==401
    assert request(receiver,{'repository':{'full_name':'attacker/repo'}})==401
    assert request(receiver,{},ref='refs/heads/unrelated')==401
    assert writer.events=={}
    assert request(receiver,{})==202
    assert request(receiver,{'different':1})==409
    assert len(writer.events)==1
    assert len(wake)==1


def test_workflow_run_event_requires_bound_source_branch():
    _,receiver,wake=rig()
    assert request(receiver,{'workflow_run':{'head_branch':'main'}},event='workflow_run')==202
    assert request(receiver,{'workflow_run':{'head_branch':'other'}},event='workflow_run')==401
    assert wake==['wake']


def test_invalid_requests_never_record_delivery():
    writer,receiver,wake=rig()
    assert request(receiver,{},content_type='text/plain')==415
    assert request(receiver,{},event='unrecognized')==204
    assert writer.events=={}
    assert wake==[]


def test_multiplexer_preserves_operator_routes_and_only_diverts_signed_ingress():
    from studio.app.github_webhooks import WebhookMuxWSGI
    _,receiver,_=rig()
    traces=[]
    def operator(environ,start_response):
        traces.append(environ['PATH_INFO'])
        start_response('200 OK',[])
        return [b'operator']
    mux=WebhookMuxWSGI(operator,receiver)
    statuses=[]
    result=mux({'PATH_INFO':'/api/v1/operations'},lambda s,h:statuses.append(s))
    assert result==[b'operator']
    assert traces==['/api/v1/operations']
    assert statuses==['200 OK']
    env={'PATH_INFO':'/webhooks/github','REQUEST_METHOD':'GET'}
    result=mux(env,lambda s,h:statuses.append(s))
    assert result==[b'']
    assert statuses[-1].startswith('405 ')
    assert traces==['/api/v1/operations']


def test_wake_failure_cannot_undo_durable_webhook_receipt():
    writer,_,_=rig()
    def broken_signal():
        raise RuntimeError('pump unavailable during shutdown')
    receiver=GitHubWebhookHints(writer,(GitHubWebhookBinding(
        'p1','owner','repo','main','studio/comms',b'webhook-secret-value'),),broken_signal)
    assert request(receiver,{})==202
    assert len(writer.events)==1
