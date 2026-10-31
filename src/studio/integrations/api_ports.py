"""Frozen WG-G HTTP-operation payload shapes; J owns HTTP, C owns durable records.

Routes/serialization/authentication belong to WG-J. The trusted J/A caller passes
project identity after checking its grants. C owns idempotency and effect records.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Callable

from .artifacts import ArtifactStore
from .ci import GitHubActions, BuildObservation
from .common import IntegrationError, ProjectBinding, ProviderFailure, RemoteUnknown, identifier, git_sha
from .connectors import ConnectorService
from .effects import EffectReconciler, EffectIntent


@dataclass(frozen=True)
class PreparedBuild:
    project_id: str
    ci_id: str
    effect_id: str
    source_sha: str
    target_id: str
    intent_id: str


class BuildRepository(Protocol):
    """C-owned durable, project/idempotency-key/body-hash constrained persistence."""
    def prepare(self, *, project_id: str, request_id: str, source_revision_id: str, build_target: str) -> PreparedBuild: ...
    def claim_first_dispatch(self, *, project_id: str, effect_id: str) -> bool: ...
    def observe(self, *, project_id: str, ci_id: str, run: BuildObservation | None) -> None: ...


class GOperatorPorts:
    def __init__(self, binding: ProjectBinding, artifacts: ArtifactStore, connectors: ConnectorService,
                 actions: GitHubActions, builds: BuildRepository, effects: EffectReconciler,
                 relay=None):
        self.binding, self.artifacts, self.connectors = binding, artifacts, connectors
        self.actions, self.builds, self.effects = actions, builds, effects
        # The communications relay has different Git refs from source CI.
        self.relay = relay

    def list_artifacts(self, *, project_id: str) -> dict:
        self.binding.require(project_id)
        return {"artifacts": self.artifacts.list_artifacts(project_id=project_id)}

    def get_artifact(self, *, project_id: str, artifact_id: str) -> dict:
        self.binding.require(project_id)
        item = self.artifacts.get_artifact(project_id=project_id, artifact_id=artifact_id)
        return item.summary()

    def attach_connector(self, *, project_id: str, request_id: str, provider_key: str, credential_ref: str) -> dict:
        self.binding.require(project_id)
        result = self.connectors.attach(project_id=project_id, request_id=request_id,
                                        provider_key=provider_key, credential_ref=credential_ref)
        return {"account_id": result.account_id, "state": result.state}

    def run_build(self, *, project_id: str, request_id: str, source_revision_id: str, build_target: str) -> dict:
        self.binding.require(project_id)
        for value in (request_id, source_revision_id, build_target):
            identifier(value)
        reserved = self.builds.prepare(project_id=project_id, request_id=request_id,
                                       source_revision_id=source_revision_id, build_target=build_target)
        if reserved.project_id != project_id or reserved.target_id != build_target:
            raise IntegrationError("forbidden", "build reservation does not match project/target")
        git_sha(reserved.source_sha)
        # Durable claim must commit BEFORE remote dispatch. Repeat request does not dispatch.
        if self.builds.claim_first_dispatch(project_id=project_id, effect_id=reserved.effect_id):
            try:
                self.actions.dispatch(project_id=project_id, target_id=build_target,
                                      source_sha=reserved.source_sha, intent_id=reserved.intent_id,
                                      effect_intent_recorded=True)
            except (RemoteUnknown, ProviderFailure):
                return {"ci_id": reserved.ci_id, "state": "UNKNOWN"}
        try:
            run = self.actions.observe(project_id=project_id, target_id=build_target,
                                       source_sha=reserved.source_sha, intent_id=reserved.intent_id)
        except (RemoteUnknown, ProviderFailure):
            return {"ci_id": reserved.ci_id, "state": "UNKNOWN"}
        self.builds.observe(project_id=project_id, ci_id=reserved.ci_id, run=run)
        return {"ci_id": reserved.ci_id, "state": run.status if run else "UNKNOWN"}

    def observe_effect(self, *, project_id: str, effect_id: str,
                       readback: Callable[[EffectIntent], tuple[str, bytes] | None]) -> dict:
        self.binding.require(project_id)
        result = self.effects.observe_effect(project_id=project_id, effect_id=effect_id, readback=readback)
        return {"effect_id": result.effect_id, "state": result.state}

    def reconcile_effect(self, *, project_id: str, effect_id: str, request_id: str,
                         readback: Callable[[EffectIntent], tuple[str, bytes] | None],
                         expected_state: str | None = None) -> dict:
        self.binding.require(project_id)
        identifier(request_id)
        result = self.effects.reconcile_effect(project_id=project_id, effect_id=effect_id,
                                               readback=readback, expected_state=expected_state)
        return {"effect_id": result.effect_id, "state": result.state}
