import hashlib
import io
import json
from types import SimpleNamespace
from unittest.mock import Mock
from wsgiref.util import setup_testing_defaults

import pytest

from studio.app.composition import StudioComposition
from studio.app.projects import Actor, Project, ProjectService
from studio.api import ConfiguredOpaqueVerifier, FrozenContracts, StudioOperatorService, StudioWSGI, VerifiedPrincipal
from studio.api.errors import ApiError
from studio.api.service import DomainRegistry


def _service(*names):
    return SimpleNamespace(**{name: Mock(return_value={"ok": True}) for name in names})


def test_studio_exposes_every_frozen_operation_as_registry_callable():
    m = StudioComposition(
        projects=_service('createProject','listProjects','getProject','configureProject','preflightProject'),
        releases=_service('prepareRelease','publishRelease','listReleases'),
        recovery=_service('requestRestore','observeRecovery'),
        runtime=object(),
        integrations=_service('stageIntegration','promoteIntegration','inspectIntegration'),
    )
    registry = DomainRegistry()
    registry.register_owner('M', m)
    for name in m.operation_ids:
        handler = registry.get(SimpleNamespace(owner='M', name=name))
        assert callable(handler), name
    assert registry.missing(SimpleNamespace(operations={name: SimpleNamespace(owner='M', name=name) for name in m.operation_ids})) == []
    assert registry.get(SimpleNamespace(owner='M', name='listProjects'))(Actor('test','configured',frozenset(),'proof')) == {'ok':True}


def test_studio_refuses_undefined_operation_and_unexpected_parameters():
    m = StudioComposition(projects=_service('listProjects'), releases=object(), recovery=object(), runtime=object())
    assert not hasattr(m, 'unapprovedAction')
    h = m.listProjects
    with pytest.raises(ApiError, match='unknown Studio operation'):
        h({}, invented='parameter')


class _Projects:
    """Reference project port; only HTTP identity integration is under test."""
    def __init__(self):
        self.record = Project('p1', 'Studio', 1, 'active')

    def bootstrap_project(self, *, principal_id, request_id, request_body_sha256, name, charter_digest):
        assert principal_id == 'owner'
        return self.record

    def list_granted_projects(self, *, principal_id, limit):
        return [self.record]

    def get_project(self, *, project_id, principal_id):
        return self.record if project_id == 'p1' else None

    def configure_project(self, *, project_id, principal_id, request_id,
                          request_body_sha256, config_digest, expected_revision):
        from studio.app.errors import StudioError
        if expected_revision != self.record.revision:
            raise StudioError('stale_revision', 'project revision changed')
        self.record = Project('p1', 'Studio', expected_revision + 1, 'active')
        return self.record


class _Capabilities:
    def probe(self, *, project_id):
        return {'relay': True, 'tool_host': False}


class _Grants:
    def granted_project_ids(self, principal_id):
        return {'p1'} if principal_id in ('owner', 'worker') else set()


class _Atomic:
    """Local HTTP adapter probe, not a production transaction store."""
    def execute_idempotent(self, *, owner, project_id, principal_id, request_id,
                           payload_digest, operation_id, command):
        return command()


def _call(wsgi, path, token, *, method='GET', body=None):
    environ = {}
    setup_testing_defaults(environ)
    raw = json.dumps(body).encode() if body is not None else b''
    environ.update(REQUEST_METHOD=method, PATH_INFO=path, QUERY_STRING='',
                   CONTENT_LENGTH=str(len(raw)), CONTENT_TYPE='application/json',
                   HTTP_AUTHORIZATION='Bearer ' + token, **{'wsgi.input': io.BytesIO(raw)})
    reported = []
    data = b''.join(wsgi(environ, lambda status, headers: reported.append(status)))
    return int(reported[0][:3]), json.loads(data)


def test_authenticated_http_identity_reaches_actual_m_domain_and_owner_boundary():
    owner_token = 'owner.' + 'o'*48
    worker_token = 'worker.' + 'w'*48
    verifier = ConfiguredOpaqueVerifier({
        hashlib.sha256(owner_token.encode()).hexdigest():
            VerifiedPrincipal('owner','configured','owner-subject','proof-owner',True),
        hashlib.sha256(worker_token.encode()).hexdigest():
            VerifiedPrincipal('worker','configured','worker-subject','proof-worker',False),
    })
    domain = StudioComposition(
        projects=ProjectService(_Projects(), _Capabilities()),
        releases=object(), recovery=object(), runtime=object())
    registry = DomainRegistry()
    registry.register_owner('M', domain)
    api = StudioOperatorService(FrozenContracts('CONTRACTS'),verifier,_Grants(),registry,_Atomic())
    app = StudioWSGI(api)
    path = '/v1/projects'
    create = {'request_id':'owner-create','name':'Studio','charter_digest':'a'*64}
    status, result = _call(app,path,worker_token,method='POST',body=create)
    assert status == 403 and result['code'] == 'requires_owner'
    status, result = _call(app,path,owner_token,method='POST',body=create)
    assert (status,result) == (200,{'project_id':'p1','revision':1})
    status, result = _call(app,'/v1/projects/p1',worker_token)
    assert status == 200 and result['name'] == 'Studio'
    status, result = _call(app,'/v1/projects/p1/preflight',worker_token)
    assert status == 200 and result['missing_capabilities'] == ['tool_host']
    change = {'request_id':'owner-update','config_digest':'b'*64,'expected_revision':1}
    status, result = _call(app,'/v1/projects/p1/config',worker_token,method='PUT',body=change)
    assert (status,result) == (200,{'project_id':'p1','revision':2})
    status, result = _call(app,'/v1/projects/p1/config',worker_token,method='PUT',
                           body={**change,'request_id':'stale','expected_revision':1})
    assert status == 409 and result['code'] == 'stale_revision'


def test_m_domain_rejects_unverified_actor_projection():
    domain = StudioComposition(projects=_service('listProjects'),releases=object(),
                               recovery=object(),runtime=object())
    with pytest.raises(ApiError, match='incomplete authenticated actor'):
        domain.listProjects({'principal_id':'untrusted','issuer':'x',
                             'trusted_proof_ref':'x','allowed_project_ids':['p1']})
    with pytest.raises(ApiError, match='incomplete authenticated actor'):
        domain.listProjects({'principal_id':'untrusted','issuer':'x',
                             'trusted_proof_ref':'x','allowed_project_ids':['p1'],
                             'owner_verified':'yes'})
