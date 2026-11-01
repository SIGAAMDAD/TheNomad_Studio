"""Actual in-process pump lifecycle and signed webhook input boundary checks."""
import hashlib
import hmac
import io
import json
import sqlite3
import weakref
import uuid
from types import SimpleNamespace

import pytest
from studio.app.command_delivery import CommandDeliveryPump
from studio.app.github_webhooks import GitHubWebhookHints, GitHubWebhookBinding, WebhookMuxWSGI
from studio.storage.workforce import WorkforceStorage
from studio.workforce import Workforce


class WorkforceStub(Workforce):
    def publish_pending(self, actor, project, *, provider, limit):
        return {'published':1,'unknown':0,'rejected':0}


@pytest.fixture
def pump():
    db=sqlite3.connect(':memory:',check_same_thread=False)
    db.execute('CREATE TABLE command_outbox(project_id TEXT,state TEXT)')
    writer=WorkforceStorage(db)
    pump = CommandDeliveryPump(writer=writer,workforce=WorkforceStub(writer,require_action=lambda *_:None),
                               actor_for_project=lambda p:{'principal_id':'owner'},
                               github_for_project=lambda p:object(), interval_seconds=0.25)
    try:
        yield pump
    finally:
        writer.close()


def test_publisher_real_thread_start_stop_is_idempotent(pump):
    pump.start()
    with pytest.raises(RuntimeError,match='already started'):
        pump.start()
    pump.request_reconciliation()
    pump.stop()
    assert pump._thread is None
    pump.stop()


def test_publisher_does_not_issue_provider_calls_after_stop(pump):
    calls=[]
    pump.writer._connection.execute("INSERT INTO command_outbox VALUES('p','queued')")
    pump.actor_for_project=lambda p:calls.append(p) or {}
    pump._stop.set()
    assert pump.poll_once()=={'published':0,'unknown':0,'rejected':0}
    assert calls==[]


def test_publisher_reports_inflight_shutdown_timeout(pump):
    thread=SimpleNamespace(join=lambda timeout: None,is_alive=lambda: True)
    pump._thread=thread
    with pytest.raises(RuntimeError,match='did not stop'):
        pump.stop()
    assert pump._thread is thread


def test_publisher_loop_records_internal_exception_and_recovers(pump):
    calls=[]
    def crash_once():
        calls.append(1)
        pump._stop.set()
        raise RuntimeError('database unavailable')
    pump.poll_once=crash_once
    pump._stop.clear()
    pump._loop()
    assert isinstance(pump.last_error,RuntimeError)
    assert calls==[1]


class RecordingC(WorkforceStorage):
    def __init__(self):
        super().__init__(sqlite3.connect(':memory:'))
        weakref.finalize(self, self._connection.close)
        self.events=[]
    def command(self,project,operation,request,digest,fn):
        self.events.append((project,operation,request,digest))
        return fn(None)


def receiver():
    c=RecordingC()
    binding=GitHubWebhookBinding('project','owner','repo','main','comms',b'authentication-secret-long')
    return GitHubWebhookHints(c,(binding,),lambda:None),c


def invoke(receiver,raw=b'{"repository":{"full_name":"owner/repo"},"ref":"refs/heads/main"}',
           headers=None):
    signature='sha256='+hmac.new(b'authentication-secret-long',raw,hashlib.sha256).hexdigest()
    env={'REQUEST_METHOD':'POST','PATH_INFO':'/webhooks/github',
         'CONTENT_LENGTH':str(len(raw)),'CONTENT_TYPE':'application/json',
         'HTTP_X_HUB_SIGNATURE_256':signature,
         'HTTP_X_GITHUB_DELIVERY':str(uuid.UUID(int=1)),
         'HTTP_X_GITHUB_EVENT':'push','wsgi.input':io.BytesIO(raw)}
    env.update(headers or {})
    statuses=[]
    receiver(env,lambda status,headers:statuses.append(int(status.split()[0])))
    return statuses[0]


@pytest.mark.parametrize('headers,code',[
    ({'REQUEST_METHOD':'GET'},405),
    ({'CONTENT_LENGTH':'garbage'},400),
    ({'CONTENT_LENGTH':'0'},400),
    ({'CONTENT_LENGTH':str(1024*1024+1)},413),
    ({'CONTENT_TYPE':'text/plain'},415),
    ({'HTTP_X_HUB_SIGNATURE_256':'wrong'},401),
    ({'HTTP_X_GITHUB_DELIVERY':'not-a-uuid'},400),
    ({'HTTP_X_GITHUB_EVENT':'unrelated'},204),
    ({'wsgi.input':object()},400),
])
def test_signed_webhook_rejects_bad_envelope_before_mutation(headers,code):
    endpoint,c=receiver()
    assert invoke(endpoint,headers=headers)==code
    assert c.events==[]


@pytest.mark.parametrize('payload',[
    b'not-json',b'NaN',b'[]',
    b'{"repository":{"full_name":"owner/repo"},"ref":"refs/heads/main","ref":"duplicate"}',
    b'{"repository":{"full_name":"foreign/repo"},"ref":"refs/heads/main"}',
])
def test_signed_webhook_rejects_invalid_payload_or_foreign_repository(payload):
    endpoint,c=receiver()
    assert invoke(endpoint,raw=payload) in (400,401)
    assert c.events==[]


def test_signed_webhook_rejects_short_payload_stream_and_invalid_bindings():
    endpoint,c=receiver()
    assert invoke(endpoint,headers={'CONTENT_LENGTH':'100000'})==400
    with pytest.raises(ValueError,match='signing secret'):
        GitHubWebhookBinding('project','owner','repo','main','comms',b'bad')
    with pytest.raises(ValueError,match='binding'):
        GitHubWebhookBinding('','owner','repo','main','comms',b'authentication-secret-long')
    with pytest.raises(TypeError):
        GitHubWebhookHints(c,(),lambda:None)
    with pytest.raises(TypeError):
        GitHubWebhookHints(object(),(),lambda:None)


def test_webhook_mux_requires_trusted_receiver():
    with pytest.raises(TypeError):WebhookMuxWSGI(lambda *a:None,object())