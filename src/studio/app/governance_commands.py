"""Frozen A-domain HTTP adapter.

Authentication is performed at J before this boundary. The domain owns its
transaction and state machine; this adapter only validates the actor projection,
maps the frozen path/body shape and converts typed domain errors to API errors.
"""
from __future__ import annotations

from collections.abc import Mapping

from studio.api.errors import ApiError
from studio.governance.service import Governance, GovernanceError


class GovernanceCommands:
    # These operations execute their own atomic state commands. Never nest a
    # separate J mutation transaction around those domain commands.
    atomic_operations = frozenset(['createBible','stageBible','publishBible','requestDecision','resolveDecision','setProjectPolicy','grantProjectAccess','revokeProjectAccess'])
    operation_ids = ('listBibles', 'createBible', 'stageBible', 'diffBible', 'publishBible', 'getCanon', 'requestDecision', 'resolveDecision', 'listDecisions', 'getProjectPolicy', 'setProjectPolicy', 'listProjectGrants', 'grantProjectAccess', 'revokeProjectAccess')

    def __init__(self, service: Governance):
        missing = [name for name in self.operation_ids
                   if not callable(getattr(service, name, None))]
        if missing:
            raise ValueError('A domain operations are unavailable: ' + ', '.join(missing))
        self.service = service

    @staticmethod
    def _check_actor(actor: Mapping, project_id: str) -> None:
        if not isinstance(actor, Mapping):
            raise ApiError('forbidden', 'authenticated actor required')
        if any(not isinstance(actor.get(k), str) or not actor[k]
               for k in ('principal_id', 'issuer', 'trusted_proof_ref')):
            raise ApiError('forbidden', 'authenticated actor incomplete')
        projects = actor.get('allowed_project_ids')
        if (not isinstance(projects, (tuple, list, set, frozenset))
                or any(not isinstance(p, str) or not p for p in projects)
                or project_id not in projects):
            raise ApiError('forbidden', 'project unavailable')

    def _call(self, name: str, actor: Mapping, project_id: str, *,
              request: dict | None = None, **path: str) -> dict:
        self._check_actor(actor, project_id)
        try:
            if name in self.atomic_operations:
                if not isinstance(request, dict):
                    raise ApiError('invalid', 'request body required')
                return getattr(self.service, name)(actor, project_id, **path, request=request)
            if request is not None:
                raise ApiError('invalid', 'read operation cannot accept mutation body')
            return getattr(self.service, name)(actor, project_id, **path)
        except GovernanceError as exc:
            raise ApiError(exc.code, str(exc)) from exc

    def listBibles(self, actor, project_id):
        return self._call('listBibles', actor, project_id)

    def createBible(self, actor, project_id, request):
        return self._call('createBible', actor, project_id, request=request)

    def stageBible(self, actor, project_id, bible_id, request):
        return self._call('stageBible', actor, project_id, bible_id=bible_id, request=request)

    def diffBible(self, actor, project_id, bible_id):
        return self._call('diffBible', actor, project_id, bible_id=bible_id)

    def publishBible(self, actor, project_id, bible_id, request):
        return self._call('publishBible', actor, project_id, bible_id=bible_id, request=request)

    def getCanon(self, actor, project_id):
        return self._call('getCanon', actor, project_id)

    def requestDecision(self, actor, project_id, request):
        return self._call('requestDecision', actor, project_id, request=request)

    def resolveDecision(self, actor, project_id, decision_id, request):
        return self._call('resolveDecision', actor, project_id, decision_id=decision_id, request=request)

    def listDecisions(self, actor, project_id):
        return self._call('listDecisions', actor, project_id)

    def getProjectPolicy(self, actor, project_id):
        return self._call('getProjectPolicy', actor, project_id)

    def setProjectPolicy(self, actor, project_id, request):
        return self._call('setProjectPolicy', actor, project_id, request=request)

    def listProjectGrants(self, actor, project_id):
        return self._call('listProjectGrants', actor, project_id)

    def grantProjectAccess(self, actor, project_id, request):
        return self._call('grantProjectAccess', actor, project_id, request=request)

    def revokeProjectAccess(self, actor, project_id, request):
        return self._call('revokeProjectAccess', actor, project_id, request=request)
