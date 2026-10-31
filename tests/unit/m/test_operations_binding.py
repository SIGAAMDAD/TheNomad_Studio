"""WG-F operator bridge tests; C storage and real provider remain separate gates."""
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
from studio.app.operations_commands import OperationsCommands
from studio.operations.core import Scope, DomainError


def _fixture():
    ops = SimpleNamespace(
        configure_budget=Mock(return_value={'budget_id':'budget-1','limit_amount':64}),
        get_budgets=Mock(return_value={'budgets':[]}),
        list_reports=Mock(return_value={'reports':[]}),
        list_notifications=Mock(return_value={'notifications':[]}),
        ack_notification=Mock(return_value={'notification_id':'n1','acknowledged':True}),
    )
    profiles=SimpleNamespace(load_verified=lambda project_id,digest:b'{}')
    blobs=SimpleNamespace(store_verified=lambda project_id,body,purpose:'' )
    bridge=OperationsCommands(ops,profiles=profiles,blobs=blobs)
    token='f-owner-'+('p'*48)
    verifier=ConfiguredOpaqueVerifier({hashlib.sha256(token.encode()).hexdigest():
        VerifiedPrincipal('owner','configured','owner-subject','proof',True)})
    class Grants:
        def granted_project_ids(self, principal_id):return {'p1'}
    reg=DomainRegistry()
    reg.register_owner('F',bridge)
    api=StudioOperatorService(FrozenContracts('CONTRACTS'),verifier,Grants(),reg,None)
    return StudioWSGI(api),token,ops,bridge


def _call(app, token, method, path, body=None):
    env={}
    setup_testing_defaults(env)
    raw=json.dumps(body).encode() if body is not None else b''
    env.update(REQUEST_METHOD=method,PATH_INFO=path,CONTENT_LENGTH=str(len(raw)),
        CONTENT_TYPE='application/json',QUERY_STRING='',HTTP_AUTHORIZATION='Bearer '+token,
        **{'wsgi.input':io.BytesIO(raw)})
    recorded=[]
    result=b''.join(app(env,lambda status,headers:recorded.append(status)))
    return int(recorded[0][:3]),json.loads(result)


def test_f_routes_real_domain_methods_without_separate_http_transaction():
    app,token,ops,bridge=_fixture()
    assert len(bridge.operation_ids)==6
    assert _call(app,token,'GET','/v1/projects/p1/budgets') == (200,{'budgets':[]})
    assert _call(app,token,'GET','/v1/projects/p1/reports') == (200,{'reports':[]})
    assert _call(app,token,'GET','/v1/projects/p1/notifications') == (200,{'notifications':[]})
    payload={'request_id':'r1','limit_amount':64,'expected_revision':0}
    assert _call(app,token,'PUT','/v1/projects/p1/budgets/budget-1',payload) == (
        200,{'budget_id':'budget-1','limit_amount':64})
    assert ops.configure_budget.call_count==1
    scope=ops.configure_budget.call_args.args[0]
    assert isinstance(scope,Scope) and scope.project_id=='p1' and scope.principal_id=='owner'
    assert ops.configure_budget.call_args.kwargs == {
        'budget_id':'budget-1','request_id':'r1','limit_amount':64,'expected_revision':0}
    assert _call(app,token,'POST','/v1/projects/p1/notifications/n1/ack',
                 {'request_id':'r2','expected_state':'queued'}) == (
        200,{'notification_id':'n1','acknowledged':True})
    assert ops.ack_notification.call_args.kwargs['notification_id']=='n1'


def test_f_scope_denial_and_domain_errors_preserve_frozen_statuses():
    app,token,ops,bridge=_fixture()
    with pytest.raises(ApiError,match='invalid verified F actor'):
        bridge.getBudgets({'principal_id':'x','issuer':'configured',
                           'trusted_proof_ref':'proof','allowed_project_ids':'p1'},'p1')
    with pytest.raises(ApiError,match='project scope'):
        bridge.getBudgets({'principal_id':'x','issuer':'configured',
                           'trusted_proof_ref':'proof','allowed_project_ids':['other']},'p1')
    ops.get_budgets.side_effect=DomainError('provider_unavailable','service offline')
    status,result=_call(app,token,'GET','/v1/projects/p1/budgets')
    assert status==503 and result['code']=='provider_unavailable'


def test_f_all_six_handlers_exist_and_profiles_are_required():
    app,token,ops,bridge=_fixture()
    contracts=FrozenContracts('CONTRACTS')
    assert set(bridge.operation_ids)=={op.name for op in contracts.operations.values() if op.owner=='F'}
    with pytest.raises(ValueError,match='must be connected'):
        OperationsCommands(SimpleNamespace(),profiles=object(),blobs=object())
