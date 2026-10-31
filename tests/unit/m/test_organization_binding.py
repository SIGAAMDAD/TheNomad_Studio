"""WG-B real operator-to-domain interface tests; backend transactions are injected."""
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
from studio.app.organization_commands import OrganizationCommands
from studio.organization.service import TrustedActor
from studio.organization.verification import OrganizationError


def _b_service():
    names = OrganizationCommands.operation_ids
    service = SimpleNamespace(**{name: Mock() for name in names})

    def create(actor, project_id, **kw):
        assert isinstance(actor, TrustedActor)
        actor.require(project_id, owner=True)
        assert kw == {'request_id': 'r1', 'name': 'A', 'charter_digest': 'a'*64}
        return {'cabal_id': 'cab-1', 'revision': 1}

    def list_cabals(actor, project_id):
        actor.require(project_id)
        return {'cabals': []}

    def revoke(actor, project_id, grant_id, **kw):
        actor.require(project_id, owner=True)
        return {'grant_id': grant_id, 'revoked': True}

    service.createCabal.side_effect = create
    service.listCabals.side_effect = list_cabals
    service.revokeToolGrant.side_effect = revoke
    return service


class _Grants:
    def granted_project_ids(self, principal_id):
        return {'p1'} if principal_id in ('owner','worker') else set()


def _app():
    owner = 'b-owner-' + 'o'*48
    worker = 'b-worker-' + 'w'*48
    verifier = ConfiguredOpaqueVerifier({
        hashlib.sha256(owner.encode()).hexdigest():
            VerifiedPrincipal('owner','configured','owner','owner-proof',True),
        hashlib.sha256(worker.encode()).hexdigest():
            VerifiedPrincipal('worker','configured','worker','worker-proof',False),
    })
    backend = _b_service()
    bridge = OrganizationCommands(backend)
    reg = DomainRegistry()
    reg.register_owner('B', bridge)
    api = StudioOperatorService(FrozenContracts('CONTRACTS'),verifier,_Grants(),reg,None)
    return StudioWSGI(api), owner, worker, backend, bridge


def _request(app,token,path,*,method='GET',body=None):
    env = {}
    setup_testing_defaults(env)
    raw = json.dumps(body).encode() if body is not None else b''
    env.update(REQUEST_METHOD=method, PATH_INFO=path, QUERY_STRING='',
               CONTENT_LENGTH=str(len(raw)), CONTENT_TYPE='application/json',
               HTTP_AUTHORIZATION='Bearer '+token, **{'wsgi.input':io.BytesIO(raw)})
    replies = []
    data = b''.join(app(env,lambda status,headers:replies.append(status)))
    return int(replies[0][:3]),json.loads(data)


def test_b_bridge_routes_real_cabal_methods_and_uses_b_durable_mutations():
    app,owner,worker,backend,bridge = _app()
    assert bridge.atomic_operations == {
        'enablePack','disablePack','createCabal','staffCabal','importRoles',
        'createDeliberation','revokeToolGrant',
    }
    body={'request_id':'r1','name':'A','charter_digest':'a'*64}
    assert _request(app,owner,'/v1/projects/p1/cabals',method='POST',body=body) == (
        200,{'cabal_id':'cab-1','revision':1})
    assert _request(app,worker,'/v1/projects/p1/cabals') == (200,{'cabals':[]})
    status, result = _request(app,worker,'/v1/projects/p1/cabals',method='POST',body=body)
    assert status == 403 and result['code'] == 'requires_owner'
    assert backend.createCabal.call_count == 2
    assert backend.listCabals.call_count == 1


def test_b_revocation_rejects_nonowner_and_maps_domain_failure_to_http():
    app,owner,worker,backend,_ = _app()
    body={'request_id':'r2','expected_state':'active'}
    path='/v1/projects/p1/tools/grants/g1/revoke'
    status,result=_request(app,worker,path,method='POST',body=body)
    assert status == 403 and result['code'] == 'requires_owner'
    assert _request(app,owner,path,method='POST',body=body) == (
        200,{'grant_id':'g1','revoked':True})
    backend.revokeToolGrant.side_effect=OrganizationError('conflict','stale grant')
    status,result=_request(app,owner,path,method='POST',body={'request_id':'r3','expected_state':'active'})
    assert status == 409 and result['code']=='conflict'


def test_b_bridge_requires_actual_backend_and_authenticated_owner_bit():
    with pytest.raises(ValueError,match='unimplemented operations'):
        OrganizationCommands(SimpleNamespace())
    backend=_b_service()
    bridge=OrganizationCommands(backend)
    with pytest.raises(ApiError,match='unverified organization actor'):
        bridge.listCabals({'principal_id':'owner','allowed_project_ids':['p1'],
                           'trusted_proof_ref':'proof','issuer':'configured'},'p1')
    with pytest.raises(ApiError,match='unverified organization actor'):
        bridge.listCabals({'principal_id':'owner','allowed_project_ids':['p1'],
                           'trusted_proof_ref':'proof','issuer':'configured',
                           'owner_verified':'true'},'p1')


def test_b_all_thirteen_frozen_contract_handlers_are_explicit():
    app,owner,worker,backend,bridge=_app()
    contracts=FrozenContracts('CONTRACTS')
    registered={op.name for op in contracts.operations.values() if op.owner=='B'}
    assert set(bridge.operation_ids)==registered
    assert all(callable(getattr(bridge,name,None)) for name in registered)
