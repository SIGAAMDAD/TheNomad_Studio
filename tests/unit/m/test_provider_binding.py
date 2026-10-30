"""WG-G provider adapters through actual frozen HTTP envelopes."""
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
from studio.app.provider_commands import ProviderCommands
from studio.integrations.common import IntegrationError


def _fixture():
    reads=[]
    def readback(intent):
        reads.append(intent)
        return None
    service=SimpleNamespace(
        list_artifacts=Mock(return_value={'artifacts': []}),
        get_artifact=Mock(return_value={'artifact_id':'x','content_digest':'a'*64,'kind':'doc'}),
        attach_connector=Mock(return_value={'account_id':'account-1','state':'attached'}),
        run_build=Mock(return_value={'ci_id':'ci-1','state':'UNKNOWN'}),
        reconcile_effect=Mock(return_value={'effect_id':'ef-1','state':'UNKNOWN'}),
        observe_effect=Mock(return_value={'effect_id':'ef-1','state':'UNKNOWN'}),
    )
    bridge=ProviderCommands(service,effect_readback=readback)
    token='g-worker-'+('w'*48)
    verifier=ConfiguredOpaqueVerifier({hashlib.sha256(token.encode()).hexdigest():
        VerifiedPrincipal('worker','configured','subject','proof',False)})
    class Grants:
        def granted_project_ids(self,principal_id):return {'p1'}
    reg=DomainRegistry()
    reg.register_owner('G',bridge)
    api=StudioOperatorService(FrozenContracts('CONTRACTS'),verifier,Grants(),reg,None)
    return StudioWSGI(api),token,service,bridge,readback


def _call(app,token,method,path,body=None):
    env={}
    setup_testing_defaults(env)
    raw=json.dumps(body).encode() if body is not None else b''
    env.update(REQUEST_METHOD=method,PATH_INFO=path,QUERY_STRING='',
               CONTENT_LENGTH=str(len(raw)),CONTENT_TYPE='application/json',
               HTTP_AUTHORIZATION='Bearer '+token,**{'wsgi.input':io.BytesIO(raw)})
    replies=[]
    data=b''.join(app(env,lambda status,headers:replies.append(status)))
    return int(replies[0][:3]),json.loads(data)


def test_g_routes_six_operations_to_correct_provider_ports():
    app,token,service,bridge,readback=_fixture()
    assert _call(app,token,'GET','/v1/projects/p1/artifacts')==(200,{'artifacts':[]})
    assert _call(app,token,'GET','/v1/projects/p1/artifacts/x')==(
        200,{'artifact_id':'x','content_digest':'a'*64,'kind':'doc'})
    assert _call(app,token,'POST','/v1/projects/p1/connectors',{'request_id':'r1',
        'provider_key':'github','credential_ref':'cred'})==(
        200,{'account_id':'account-1','state':'attached'})
    assert _call(app,token,'POST','/v1/projects/p1/builds',{'request_id':'r2',
        'source_revision_id':'s1','build_target':'godot'})==(
        200,{'ci_id':'ci-1','state':'UNKNOWN'})
    assert _call(app,token,'GET','/v1/projects/p1/effects/ef-1')==(
        200,{'effect_id':'ef-1','state':'UNKNOWN'})
    assert _call(app,token,'POST','/v1/projects/p1/effects/ef-1/reconcile',{'request_id':'r3'})==(
        200,{'effect_id':'ef-1','state':'UNKNOWN'})
    assert service.observe_effect.call_args.kwargs['readback'] is readback
    assert service.reconcile_effect.call_args.kwargs['readback'] is readback
    assert bridge.atomic_operations == {'attachConnector','runBuild','reconcileEffect'}


def test_g_identity_and_provider_errors_are_fail_closed():
    app,token,service,bridge,readback=_fixture()
    with pytest.raises(ApiError,match='unverified provider actor'):
        bridge.listArtifacts({'principal_id':'p','issuer':'i','trusted_proof_ref':'x',
                              'allowed_project_ids':'p1'},'p1')
    with pytest.raises(ApiError,match='project unavailable'):
        bridge.listArtifacts({'principal_id':'p','issuer':'i','trusted_proof_ref':'x',
                              'allowed_project_ids':['other']},'p1')
    service.list_artifacts.side_effect=IntegrationError('conflict','source revision mismatch')
    status,response=_call(app,token,'GET','/v1/projects/p1/artifacts')
    assert status==409 and response['code']=='conflict'


def test_g_contract_is_complete_and_readback_mandatory():
    app,token,service,bridge,readback=_fixture()
    contracts=FrozenContracts('CONTRACTS')
    assert set(bridge.operation_ids)=={o.name for o in contracts.operations.values() if o.owner=='G'}
    with pytest.raises(ValueError,match='readback is required'):
        ProviderCommands(service,effect_readback=None)
    with pytest.raises(ValueError,match='incomplete'):
        ProviderCommands(SimpleNamespace(),effect_readback=readback)
