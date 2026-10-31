"""Frozen HTTP adapter for WG-B's transactional organization service.

J owns bearer verification; B owns package/cabal/grant authorization and
durable organization mutations. This bridge neither creates browser workers
nor invents success for unavailable backend operations.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from studio.api.errors import ApiError
from studio.organization.service import OrganizationService, TrustedActor
from studio.organization.verification import OrganizationError


class OrganizationCommands:
    """Expose WG-B's 13 frozen operations using J's request-body convention."""

    atomic_operations = frozenset({
        'enablePack', 'disablePack', 'createCabal', 'staffCabal',
        'importRoles', 'createDeliberation', 'revokeToolGrant',
    })
    operation_ids = (
        'listPacks', 'enablePack', 'disablePack', 'createCabal', 'staffCabal',
        'listCabals', 'importRoles', 'listRoles', 'listDepartments',
        'createDeliberation', 'listDeliberations', 'listToolGrants',
        'revokeToolGrant',
    )

    def __init__(self, service: OrganizationService):
        missing = [name for name in self.operation_ids
                   if not callable(getattr(service, name, None))]
        if missing:
            raise ValueError('organization service has unimplemented operations: '
                             + ', '.join(missing))
        self.service = service

    @staticmethod
    def _actor(claims: Mapping[str, Any]) -> TrustedActor:
        if not isinstance(claims, Mapping):
            raise ApiError('forbidden', 'authenticated identity required')
        principal = claims.get('principal_id')
        proof = claims.get('trusted_proof_ref')
        issuer = claims.get('issuer')
        allowed = claims.get('allowed_project_ids')
        owner = claims.get('owner_verified')
        if (not isinstance(principal, str) or not principal
                or not isinstance(issuer, str) or not issuer
                or not isinstance(proof, str) or not proof
                or type(owner) is not bool
                or not isinstance(allowed, (list, tuple, set, frozenset))
                or any(not isinstance(item, str) or not item for item in allowed)):
            raise ApiError('forbidden', 'unverified organization actor')
        projects = frozenset(allowed)
        return TrustedActor(principal, projects,
                            projects if owner else frozenset(), proof)

    def _call(self, name: str, claims: Mapping[str, Any], project_id: str,
              *, request: dict[str, Any] | None = None, **path: str) -> dict:
        actor = self._actor(claims)
        if not isinstance(project_id, str) or not project_id:
            raise ApiError('invalid', 'project_id required')
        kwargs: dict[str, Any] = dict(path)
        if name in self.atomic_operations:
            if not isinstance(request, dict) or not request:
                raise ApiError('invalid', 'request body required')
            kwargs.update(request)
        elif request is not None:
            raise ApiError('invalid', 'read-only operation does not accept request')
        try:
            return getattr(self.service, name)(actor, project_id, **kwargs)
        except OrganizationError as error:
            raise ApiError(error.code, str(error)) from error

    def listPacks(self, actor, project_id):
        return self._call('listPacks', actor, project_id)

    def enablePack(self, actor, project_id, request):
        return self._call('enablePack', actor, project_id, request=request)

    def disablePack(self, actor, project_id, request):
        return self._call('disablePack', actor, project_id, request=request)

    def createCabal(self, actor, project_id, request):
        return self._call('createCabal', actor, project_id, request=request)

    def staffCabal(self, actor, project_id, cabal_id, request):
        return self._call('staffCabal', actor, project_id, cabal_id=cabal_id, request=request)

    def listCabals(self, actor, project_id):
        return self._call('listCabals', actor, project_id)

    def importRoles(self, actor, project_id, request):
        return self._call('importRoles', actor, project_id, request=request)

    def listRoles(self, actor, project_id):
        return self._call('listRoles', actor, project_id)

    def listDepartments(self, actor, project_id):
        return self._call('listDepartments', actor, project_id)

    def createDeliberation(self, actor, project_id, cabal_id, request):
        return self._call('createDeliberation', actor, project_id, cabal_id=cabal_id, request=request)

    def listDeliberations(self, actor, project_id, cabal_id):
        return self._call('listDeliberations', actor, project_id, cabal_id=cabal_id)

    def listToolGrants(self, actor, project_id):
        return self._call('listToolGrants', actor, project_id)

    def revokeToolGrant(self, actor, project_id, grant_id, request):
        return self._call('revokeToolGrant', actor, project_id, grant_id=grant_id, request=request)
