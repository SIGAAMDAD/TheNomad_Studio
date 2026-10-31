"""Frozen WG-G operator binding to verified provider/CI/effect services."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from studio.api.errors import ApiError
from studio.integrations.api_ports import GOperatorPorts
from studio.integrations.common import IntegrationError, ProviderFailure, RemoteUnknown
from studio.integrations.effects import EffectIntent


class ProviderCommands:
    """J authenticates callers; G owns provider authorization and effect readback."""

    atomic_operations = frozenset({'attachConnector', 'runBuild', 'reconcileEffect'})
    operation_ids = (
        'listArtifacts', 'getArtifact', 'attachConnector',
        'runBuild', 'reconcileEffect', 'observeEffect',
    )

    def __init__(self, service: GOperatorPorts,
                 *, effect_readback: Callable[[EffectIntent], tuple[str, bytes] | None]):
        needed = ('list_artifacts', 'get_artifact', 'attach_connector',
                  'run_build', 'reconcile_effect', 'observe_effect')
        if any(not callable(getattr(service, n, None)) for n in needed):
            raise ValueError('WG-G provider operation ports are incomplete')
        if not callable(effect_readback):
            raise ValueError('trusted external-effect readback is required')
        self.service, self.effect_readback = service, effect_readback

    @staticmethod
    def _project(actor: Mapping[str, Any], project_id: str) -> str:
        if not isinstance(actor, Mapping):
            raise ApiError('forbidden', 'verified operator identity required')
        principal, issuer, proof = (actor.get('principal_id'), actor.get('issuer'),
                                    actor.get('trusted_proof_ref'))
        projects = actor.get('allowed_project_ids')
        if (any(not isinstance(v, str) or not v for v in (principal, issuer, proof))
                or not isinstance(projects, (list, tuple, set, frozenset))
                or any(not isinstance(p, str) or not p for p in projects)):
            raise ApiError('forbidden', 'unverified provider actor')
        if project_id not in projects:
            raise ApiError('forbidden', 'project unavailable')
        return project_id

    @staticmethod
    def _run(fn):
        try:
            return fn()
        except IntegrationError as error:
            raise ApiError(error.code, str(error)) from error
        except ProviderFailure as error:
            raise ApiError('provider_unavailable', 'provider operation unavailable') from error
        except RemoteUnknown as error:
            raise ApiError('unknown_effect', 'remote effect requires reconciliation') from error

    def listArtifacts(self, actor, project_id):
        project = self._project(actor, project_id)
        return self._run(lambda: self.service.list_artifacts(project_id=project))

    def getArtifact(self, actor, project_id, artifact_id):
        project = self._project(actor, project_id)
        return self._run(lambda: self.service.get_artifact(project_id=project,artifact_id=artifact_id))

    def attachConnector(self, actor, project_id, request):
        project = self._project(actor, project_id)
        return self._run(lambda: self.service.attach_connector(project_id=project,**request))

    def runBuild(self, actor, project_id, request):
        project = self._project(actor, project_id)
        return self._run(lambda: self.service.run_build(project_id=project,**request))

    def reconcileEffect(self, actor, project_id, effect_id, request):
        project = self._project(actor, project_id)
        return self._run(lambda: self.service.reconcile_effect(
            project_id=project,effect_id=effect_id,
            readback=self.effect_readback,**request))

    def observeEffect(self, actor, project_id, effect_id):
        project = self._project(actor, project_id)
        return self._run(lambda: self.service.observe_effect(
            project_id=project,effect_id=effect_id,readback=self.effect_readback))
