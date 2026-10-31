"""D domain rejects forged actor/brief/lease inputs before touching C storage."""
import hashlib
import json
from datetime import datetime,timezone

import pytest
from studio.production.service import (
    ProductionService,ProductionFault,_request,_text,_int,_utc,
)
from studio.production.models import TaskSpec


class Authorize:
    def __init__(self):self.calls=[]
    def require(self,actor,project,action,subject=''):
        self.calls.append((project,action,subject))


class Briefs:
    def __init__(self,raw=None):self.raw=raw
    def get_verified(self,*,project_id,content_sha256):return self.raw


class NeverWrite:
    def read(self,*args,**kwargs):raise AssertionError('C read unexpectedly reached')
    def command(self,*args,**kwargs):raise AssertionError('C write unexpectedly reached')


def service(raw=None):
    return ProductionService(storage=NeverWrite(),authorization=Authorize(),briefs=Briefs(raw))


def actor(**changes):
    result={'principal_id':'owner','allowed_project_ids':['p'],'trusted_proof_ref':'verified'}
    result.update(changes)
    return result


def fault(code,call):
    with pytest.raises(ProductionFault) as err:call()
    assert err.value.code==code


@pytest.mark.parametrize('payload,keys',[
    (None,{'request_id'}),({}, {'request_id'}),
    ({'request_id':''},{'request_id'}),
    ({'request_id':'x'*201},{'request_id'}),
    ({'request_id':'valid','extra':1},{'request_id'}),
])
def test_request_contract_exact_fields_and_idempotency_id(payload,keys):
    fault('invalid',lambda:_request('submitFeature',payload,keys))


@pytest.mark.parametrize('value',[None,'',42,'x'*513])
def test_required_text(value):
    fault('invalid',lambda:_text(value,'field'))


@pytest.mark.parametrize('value',[0,-1,True,None,1.5,'1'])
def test_positive_generation_cannot_be_forged(value):
    fault('invalid',lambda:_int(value,'generation',positive=True))
    assert _int(1,'generation',positive=True)==1


def test_clock_requires_aware_instant():
    fault('internal',lambda:_utc(datetime(2026,10,30)))
    assert _utc(datetime(2026,10,30,tzinfo=timezone.utc)).tzinfo==timezone.utc


@pytest.mark.parametrize('seconds',[0,86401,True,'3',2.2])
def test_lease_seconds_is_bounded(seconds):
    with pytest.raises(ValueError):ProductionService(storage=NeverWrite(),
        authorization=Authorize(),briefs=Briefs(),lease_seconds=seconds)


@pytest.mark.parametrize('claims',[
    None,{},actor(principal_id=''),actor(principal_id=None),
    actor(allowed_project_ids=[]),actor(trusted_proof_ref=''),
])
def test_authorization_never_trusts_unverified_principal_or_scope(claims):
    fault('forbidden',lambda:service()._authorize(claims,'p','listTasks'))


def test_valid_authorization_delegates_to_trusted_port():
    svc=service()
    svc._authorize(actor(),'p','listTasks')
    assert svc.authorization.calls==[('p','listTasks','')]


@pytest.mark.parametrize('identity', ['',None,'f'*63,'A'*64,'nonnumeric'])
def test_bad_brief_digest_rejected_before_blob_read(identity):
    fault('invalid',lambda:service()._brief('p',identity))


def test_unavailable_and_mismatched_brief_bytes_are_conflicts():
    fault('conflict',lambda:service()._brief('p','a'*64))
    fault('conflict',lambda:service(b'wrong')._brief('p','a'*64))


def test_malformed_source_brief_fails_validation_after_verified_hash():
    for bad in (b'not json',b'{}',b'[]'):
        svc=service(bad)
        fault('invalid',lambda:svc._brief('p',hashlib.sha256(bad).hexdigest()))


def test_valid_brief_reads_verified_content_and_contract():
    task={'key':'t1','cabal_id':'c','role_id':'coder',
          'acceptance_digest':'a'*64,'depends_on':[],
          'package_digest':'b'*64,'entrypoint_id':'run','grant_id':'grant'}
    body=json.dumps({'acceptance_digest':'c'*64,'source_revision_id':'rev',
                     'tasks':[task]}).encode()
    result=service(body)._brief('p',hashlib.sha256(body).hexdigest())
    assert result.tasks[0].key=='t1'


class EmptyTx:
    def one(self,*args):return None


def test_critical_project_pins_fail_closed_when_not_present_in_c():
    svc=service()
    for operation,code in (
        (lambda:svc._canonical(EmptyTx(),'p','snapshot'),'stale_snapshot'),
        (lambda:svc._source(EmptyTx(),'p','rev'),'conflict'),
        (lambda:svc._feature(EmptyTx(),'p','id'),'missing'),
        (lambda:svc._task(EmptyTx(),'p','id'),'missing'),
    ):
        fault(code,operation)


def test_unbound_package_grant_rejected():
    svc=service()
    valid={'key':'t','cabal_id':'c','role_id':'coder','acceptance_digest':'a'*64,
           'depends_on':[], 'package_digest':'b'*64,'entrypoint_id':'entry','grant_id':'grant'}
    spec=TaskSpec.parse(valid)
    fault('capability_unavailable',lambda:svc._package_grant(EmptyTx(),'p',spec))


def test_missing_acceptance_port_cannot_mark_completion():
    fault('capability_unavailable',lambda:service().mark_accepted(
        actor(),'p','t',generation=1,evidence_digest='a'*64,expected_revision=1,
        request_id='request'))


def test_submit_rejects_request_before_changing_database():
    for body in ({}, {'request_id':'r','brief_digest':'a'*64},
                 {'request_id':'r','brief_digest':'a'*64,'snapshot_id':'s','acceptance_digest':''}):
        fault('invalid',lambda:service().submitFeature(actor(),'p',body))