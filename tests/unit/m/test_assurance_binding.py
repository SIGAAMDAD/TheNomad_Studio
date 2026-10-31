"""WG-E QA/oversight HTTP binding tests with injected authoring service."""
import hashlib
import io
import json
from types import SimpleNamespace
from unittest.mock import Mock
from wsgiref.util import setup_testing_defaults

import pytest
from studio.api import (ConfiguredOpaqueVerifier, DomainRegistry, FrozenContracts,
                        StudioOperatorService, StudioWSGI, VerifiedPrincipal)
from studio.api.errors import ApiError
from studio.app.assurance_commands import AssuranceCommands
from studio.assurance.service import AssuranceError


def _fixture():
    service=SimpleNamespace(
        requestReview=Mock(return_value={'review_id':'review-1','state':'pending'}),
        submitReview=Mock(return_value={'review_id':'review-1','verdict':'pass'}),
        listReviews=Mock(return_value={'reviews':[]}),
        openOversightCase=Mock(return_value={'case_id':'case-1','state':'open'}),
        resolveOversightCase=Mock(return_value={'case_id':'case-1','state':'resolved'}),
        listOversightCases=Mock(return_value={'cases':[]}),
    )
    bridge=AssuranceCommands(service)
    token='e-worker-'+('w'*48)
    verify=ConfiguredOpaqueVerifier({hashlib.sha256(token.encode()).hexdigest():
        VerifiedPrincipal('worker','configured','subject','proof',False)})
    class Grants:
        def granted_project_ids(self,principal_id):return {'p1'}
    reg=DomainRegistry()
    reg.register_owner('E',bridge)
    app=StudioWSGI(StudioOperatorService(FrozenContracts('CONTRACTS'),verify,Grants(),reg,None))
    return app,token,service,bridge


def _call(app,token,method,path,body=None):
    env={}
    setup_testing_defaults(env)
    raw=json.dumps(body).encode() if body is not None else b''
    env.update(REQUEST_METHOD=method,PATH_INFO=path,QUERY_STRING='',
        CONTENT_LENGTH=str(len(raw)),CONTENT_TYPE='application/json',
        HTTP_AUTHORIZATION='Bearer '+token,**{'wsgi.input':io.BytesIO(raw)})
    calls=[]
    data=b''.join(app(env,lambda status,headers:calls.append(status)))
    return int(calls[0][:3]),json.loads(data)


def test_e_qa_oversight_calls_and_exact_payloads():
    app,token,service,bridge=_fixture()
    req={'request_id':'r1','submission_id':'sub-1','acceptance_digest':'a'*64}
    assert _call(app,token,'POST','/v1/projects/p1/reviews',req)==(
        200,{'review_id':'review-1','state':'pending'})
    assert service.requestReview.call_args.args[1]=='p1'
    assert service.requestReview.call_args.kwargs['request']==req
    assert _call(app,token,'GET','/v1/projects/p1/reviews')==(200,{'reviews':[]})
    assert _call(app,token,'POST','/v1/projects/p1/reviews/review-1/verdict',
        {'request_id':'r2','verdict':'pass','evidence_digests':[]})==(
        200,{'review_id':'review-1','verdict':'pass'})
    assert _call(app,token,'GET','/v1/projects/p1/oversight')==(200,{'cases':[]})
    assert _call(app,token,'POST','/v1/projects/p1/oversight',
        {'request_id':'r3','subject_id':'sub-1','evidence_digest':'a'*64})==(
        200,{'case_id':'case-1','state':'open'})
    assert _call(app,token,'POST','/v1/projects/p1/oversight/case-1/resolve',
        {'request_id':'r4','resolution_digest':'b'*64})==(
        200,{'case_id':'case-1','state':'resolved'})


def test_e_domain_errors_and_project_scope_are_preserved():
    app,token,service,bridge=_fixture()
    with pytest.raises(ApiError,match='verified assurance project actor'):
        bridge.listReviews({'principal_id':'fake','issuer':'test','trusted_proof_ref':'x',
                            'allowed_project_ids':['p2']},'p1')
    service.listReviews.side_effect=AssuranceError('qa_failed','evidence incomplete')
    status,result=_call(app,token,'GET','/v1/projects/p1/reviews')
    assert status==409 and result['code']=='qa_failed'


def test_e_frozen_contract_bindings_and_durable_ownership():
    app,token,service,bridge=_fixture()
    contract=FrozenContracts('CONTRACTS')
    assert set(bridge.operation_ids)=={o.name for o in contract.operations.values() if o.owner=='E'}
    assert bridge.atomic_operations=={
        'requestReview','submitReview','openOversightCase','resolveOversightCase',
    }
    with pytest.raises(ValueError,match='missing assurance operations'):
        AssuranceCommands(SimpleNamespace())
