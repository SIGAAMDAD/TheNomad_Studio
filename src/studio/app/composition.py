"""Explicit composition root for project, release, recovery and integration commands.

The HTTP adapter owns authenticated actor conversion. Storage owns APSW
transactions. This module wires already constructed domain ports without
loading worker modules that have not been supplied, or auto-launching chats.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from studio.api.errors import ApiError
from studio.api.service import StudioOperatorService

from .errors import StudioError
from .lifecycle import StudioRuntime
from .integration_commands import IntegrationService
from .projects import Actor, ProjectService
from .releases import RecoveryCoordinator, ReleaseCoordinator


@dataclass(frozen=True)
class StudioComposition:
    projects: ProjectService
    releases: ReleaseCoordinator
    recovery: RecoveryCoordinator
    runtime: StudioRuntime
    integrations: IntegrationService | None = None
    operator_service: StudioOperatorService | None = None
    webhook_wsgi: Any | None = None

    # All M mutations use their own C-backed atomic state ports. J must not
    # open an unrelated outer transaction around project, release, recovery or
    # canonical integration commands; in particular, publication is an
    # independently journaled remote effect after its state intent.
    atomic_operations = frozenset({
        'createProject', 'configureProject', 'prepareRelease', 'publishRelease',
        'requestRestore', 'stageIntegration', 'promoteIntegration',
    })

    @property
    def operation_ids(self) -> tuple[str, ...]:
        return (
            "createProject", "listProjects", "getProject", "configureProject",
            "preflightProject", "prepareRelease", "publishRelease", "listReleases",
            "requestRestore", "observeRecovery",
            "stageIntegration", "promoteIntegration", "inspectIntegration",
        )

    def __getattr__(self, operation_id: str):
        """Expose the defined Studio operations to the API callable registry.

        No dynamic operation is created outside the explicit M operation catalog.
        Request validation and actor identity remain owned by J.
        """
        if operation_id not in self.operation_ids:
            raise AttributeError(operation_id)

        def handler(actor: Actor | Mapping[str, Any], **kwargs: Any) -> dict:
            permitted = {'project_id', 'release_id', 'intent_id', 'request'}
            unexpected = set(kwargs) - permitted
            if unexpected:
                raise ApiError('invalid', 'unknown Studio operation parameters')
            try:
                return self.invoke(operation_id, actor, **kwargs)
            except StudioError as error:
                raise ApiError(error.code, error.message) from error
        return handler

    def invoke(self, operation_id: str, actor: Actor | Mapping[str, Any],
               *, project_id: str | None = None,
               release_id: str | None = None,
               intent_id: str | None = None,
               request: dict[str, Any] | None = None) -> dict:
        """Bind M operations after converting J's authenticated identity."""
        actor = self._verified_actor(actor)
        targets = {
            "createProject": lambda: self.projects.createProject(actor, self._require_request(request)),
            "listProjects": lambda: self.projects.listProjects(actor),
            "getProject": lambda: self.projects.getProject(actor, self._require_project(project_id)),
            "configureProject": lambda: self.projects.configureProject(actor, self._require_project(project_id), self._require_request(request)),
            "preflightProject": lambda: self.projects.preflightProject(actor, self._require_project(project_id)),
            "prepareRelease": lambda: self.releases.prepareRelease(actor, self._require_project(project_id), self._require_request(request)),
            "publishRelease": lambda: self.releases.publishRelease(actor, self._require_project(project_id), self._require_release(release_id), self._require_request(request)),
            "listReleases": lambda: self.releases.listReleases(actor, self._require_project(project_id)),
            "requestRestore": lambda: self.recovery.requestRestore(actor, self._require_project(project_id), self._require_request(request)),
            "observeRecovery": lambda: self.recovery.observeRecovery(actor, self._require_project(project_id)),
            "stageIntegration": lambda: self._require_integrations().stageIntegration(actor, self._require_project(project_id), self._require_request(request)),
            "inspectIntegration": lambda: self._require_integrations().inspectIntegration(actor, self._require_project(project_id), self._require_intent(intent_id)),
            "promoteIntegration": lambda: self._require_integrations().promoteIntegration(actor, self._require_project(project_id), self._require_intent(intent_id), self._require_request(request)),
        }
        if operation_id not in targets:
            raise StudioError("invalid", "unknown Studio operation")
        return targets[operation_id]()

    @staticmethod
    def _verified_actor(actor: Actor | Mapping[str, Any]) -> Actor:
        """Bridge trusted J claims to the M domain's Actor contract.

        J creates these claims from an authenticated bearer and a live grants
        read. Missing/invalid proof cannot be inferred from a worker role.
        """
        if isinstance(actor, Actor):
            return actor
        if not isinstance(actor, Mapping):
            raise ApiError('forbidden', 'verified operator identity required')
        principal = actor.get('principal_id')
        issuer = actor.get('issuer')
        proof = actor.get('trusted_proof_ref')
        grants = actor.get('allowed_project_ids')
        owner = actor.get('owner_verified')
        if (any(not isinstance(value, str) or not value
                for value in (principal, issuer, proof))
                or type(owner) is not bool
                or not isinstance(grants, (list, tuple, set, frozenset))
                or any(not isinstance(project, str) or not project for project in grants)):
            raise ApiError('forbidden', 'incomplete authenticated actor')
        return Actor(principal, issuer, frozenset(grants), proof, owner)

    @staticmethod
    def _require_request(request: dict | None) -> dict:
        if request is None:
            raise StudioError("invalid", "request body required")
        return request

    @staticmethod
    def _require_project(project_id: str | None) -> str:
        if not project_id:
            raise StudioError("invalid", "project id required")
        return project_id

    @staticmethod
    def _require_release(release_id: str | None) -> str:
        if not release_id:
            raise StudioError("invalid", "release id required")
        return release_id

    def _require_integrations(self) -> IntegrationService:
        if self.integrations is None:
            raise StudioError("capability_unavailable", "C/E/G submission and QA integration ports unavailable")
        return self.integrations

    @staticmethod
    def _require_intent(intent_id: str | None) -> str:
        if not intent_id:
            raise StudioError("invalid", "integration intent id required")
        return intent_id
