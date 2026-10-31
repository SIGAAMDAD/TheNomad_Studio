"""Frozen D-domain HTTP adapter.

Authentication is performed at J before this boundary. The domain owns its
transaction and state machine; this adapter only validates the actor projection,
maps the frozen path/body shape and converts typed domain errors to API errors.
"""
from __future__ import annotations

from collections.abc import Mapping

from studio.api.errors import ApiError
from studio.production.service import ProductionService, ProductionFault


class ProductionCommands:
    # These operations execute their own atomic state commands. Never nest a
    # separate J mutation transaction around those domain commands.
    atomic_operations = frozenset(['submitFeature','planFeature','cancelTask','dispatchTask','assignTask'])
    operation_ids = ('submitFeature', 'planFeature', 'listFeatures', 'listTasks', 'cancelTask', 'dispatchTask', 'assignTask')

    def __init__(self, service: ProductionService):
        missing = [name for name in self.operation_ids
                   if not callable(getattr(service, name, None))]
        if missing:
            raise ValueError('D domain operations are unavailable: ' + ', '.join(missing))
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
        except ProductionFault as exc:
            raise ApiError(exc.code, str(exc)) from exc

    def submitFeature(self, actor, project_id, request):
        return self._call('submitFeature', actor, project_id, request=request)

    def planFeature(self, actor, project_id, feature_id, request):
        return self._call('planFeature', actor, project_id, feature_id=feature_id, request=request)

    def listFeatures(self, actor, project_id):
        return self._call('listFeatures', actor, project_id)

    def listTasks(self, actor, project_id):
        return self._call('listTasks', actor, project_id)

    def cancelTask(self, actor, project_id, task_id, request):
        return self._call('cancelTask', actor, project_id, task_id=task_id, request=request)

    def dispatchTask(self, actor, project_id, task_id, request):
        return self._call('dispatchTask', actor, project_id, task_id=task_id, request=request)

    def assignTask(self, actor, project_id, task_id, request):
        return self._call('assignTask', actor, project_id, task_id=task_id, request=request)
