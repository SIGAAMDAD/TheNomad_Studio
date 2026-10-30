"""All A/D operation bindings are exact frozen routes, without fake domain success."""
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
from studio.app.governance_commands import GovernanceCommands
from studio.app.production_commands import ProductionCommands
from studio.governance.model import GovernanceError
from studio.production.service import ProductionFault


def _service(operations):
    return SimpleNamespace(**{name: Mock(return_value={}) for name in operations})


def _http(own, domain):
    token = 'verified-' + 'x' * 48
    verifier = ConfiguredOpaqueVerifier({
        hashlib.sha256(token.encode()).hexdigest():
            VerifiedPrincipal('principal', 'configured', 'subject', 'proof', True)})
    class Grants:
        def granted_project_ids(self, principal_id):
            return {'p1'}
    registry = DomainRegistry()
    registry.register_owner(own, domain)
    return StudioWSGI(StudioOperatorService(
        FrozenContracts('CONTRACTS'), verifier, Grants(), registry, None)), token


def _call(app, token, method, path, body=None):
    env = {}
    setup_testing_defaults(env)
    raw = json.dumps(body).encode() if body is not None else b''
    env.update(REQUEST_METHOD=method, PATH_INFO=path, QUERY_STRING='',
               CONTENT_LENGTH=str(len(raw)), CONTENT_TYPE='application/json',
               HTTP_AUTHORIZATION='Bearer ' + token, **{'wsgi.input': io.BytesIO(raw)})
    reports = []
    response = b''.join(app(env, lambda status, headers: reports.append(status)))
    return int(reports[0][:3]), json.loads(response)


@pytest.mark.parametrize('owner,bridge', [('A', GovernanceCommands), ('D', ProductionCommands)])
def test_frozen_operation_catalog_matches_domain_methods(owner, bridge):
    contracts = FrozenContracts('CONTRACTS')
    names = {op.name for op in contracts.operations.values() if op.owner == owner}
    service = _service(names)
    adapted = bridge(service)
    assert set(adapted.operation_ids) == names
    assert adapted.atomic_operations == {
        op.name for op in contracts.operations.values() if op.owner == owner and op.mutation}
    assert all(callable(getattr(adapted, name)) for name in names)
    with pytest.raises(ValueError, match='unavailable'):
        bridge(SimpleNamespace())


def test_a_routes_actual_canon_and_owner_decision_calls():
    service = _service(GovernanceCommands.operation_ids)
    service.listBibles.return_value = {'bibles': []}
    service.resolveDecision.return_value = {'decision_id':'d1', 'state':'approved'}
    adapter = GovernanceCommands(service)
    app, token = _http('A', adapter)
    assert _call(app, token, 'GET', '/v1/projects/p1/bibles') == (200, {'bibles': []})
    request = {'request_id':'r1', 'decision':'approve',
               'expected_effect_digest':'a'*64, 'expected_revision':1}
    assert _call(app, token, 'POST', '/v1/projects/p1/decisions/d1/resolve',
                 request) == (200, {'decision_id':'d1', 'state':'approved'})
    assert service.resolveDecision.call_args.kwargs == {'decision_id':'d1', 'request':request}
    assert service.resolveDecision.call_args.args[0]['owner_verified'] is True
    assert service.resolveDecision.call_args.args[1] == 'p1'


def test_d_dispatch_preserves_worker_lease_and_outbox_result():
    service = _service(ProductionCommands.operation_ids)
    service.dispatchTask.return_value = {'task_id':'t1', 'lease_generation':3,
        'command_id':'cmd1','shift_id':'s1','delivery_state':'queued'}
    app, token = _http('D', ProductionCommands(service))
    request = {'request_id':'r1','worker_id':'w1','expected_revision':2}
    assert _call(app,token,'POST','/v1/projects/p1/tasks/t1/dispatch', request) == (
        200,service.dispatchTask.return_value)
    assert service.dispatchTask.call_args.kwargs == {'task_id':'t1', 'request':request}


@pytest.mark.parametrize('bridge,error', [
    (GovernanceCommands,GovernanceError),
    (ProductionCommands,ProductionFault),
])
def test_domain_errors_are_typed_and_scope_is_fail_closed(bridge,error):
    svc = _service(bridge.operation_ids)
    op = 'listBibles' if bridge is GovernanceCommands else 'listFeatures'
    adapted = bridge(svc)
    actor = {'principal_id':'person','issuer':'verified','trusted_proof_ref':'proof',
             'allowed_project_ids':['other'],'owner_verified':False}
    with pytest.raises(ApiError, match='project unavailable'):
        getattr(adapted, op)(actor, 'p1')
    assert svc.__dict__[op].call_count == 0
    svc.__dict__[op].side_effect = error('conflict', 'durable state conflict')
    app, token = _http('A' if bridge is GovernanceCommands else 'D', adapted)
    route = '/v1/projects/p1/bibles' if bridge is GovernanceCommands else '/v1/projects/p1/features'
    status, response = _call(app,token,'GET',route)
    assert status == 409 and response['code'] == 'conflict'
