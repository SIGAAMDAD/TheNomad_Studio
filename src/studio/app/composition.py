"""Explicit composition root for WG-M command adapters and runtime services.

WG-J owns HTTP routes and authenticated actor conversion. WG-C owns APSW
transactions. This module wires already constructed domain ports without
loading worker modules that have not been supplied, or auto-launching chats.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import StudioError
from .lifecycle import StudioRuntime
from .integration_commands import IntegrationService
from .projects import Actor, ProjectService
from .releases import RecoveryCoordinator, ReleaseCoordinator


@dataclass(frozen=True)
class MComposition:
    projects: ProjectService
    releases: ReleaseCoordinator
    recovery: RecoveryCoordinator
    runtime: StudioRuntime
    integrations: IntegrationService | None = None

    @property
    def operation_ids(self) -> tuple[str, ...]:
        return (
            "createProject", "listProjects", "getProject", "configureProject",
            "preflightProject", "prepareRelease", "publishRelease", "listReleases",
            "requestRestore", "observeRecovery",
            "stageIntegration", "promoteIntegration", "inspectIntegration",
        )

    def invoke(self, operation_id: str, actor: Actor,
               *, project_id: str | None = None,
               release_id: str | None = None,
               intent_id: str | None = None,
               request: dict[str, Any] | None = None) -> dict:
        """Binds owned operations; J validates the frozen OpenAPI request schema."""
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
            raise StudioError("invalid", "unknown WG-M command")
        return targets[operation_id]()

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
