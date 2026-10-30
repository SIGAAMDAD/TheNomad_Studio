"""Exact external-effect intents and readback reconciliation; no optimistic success."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from .common import IntegrationError, ProjectBinding, ProviderFailure, RemoteUnknown, digest, identifier, sha256


@dataclass(frozen=True)
class EffectIntent:
    effect_id: str
    project_id: str
    provider: str
    identity: str
    path: str
    expected_sha256: str
    request_id: str

    def __post_init__(self):
        for value in (self.effect_id, self.project_id, self.provider, self.request_id):
            identifier(value)
        digest(self.expected_sha256)
        if not self.identity or not self.path:
            raise IntegrationError("invalid", "effect identity and path are mandatory")


@dataclass(frozen=True)
class EffectObservation:
    effect_id: str
    state: str
    remote_identity: str | None = None
    body_sha256: str | None = None


class IntentRepository(Protocol):
    """C-owned durable adapter: persist before dispatch, exact idempotency enforced in C."""
    def load(self, project_id: str, effect_id: str) -> EffectIntent: ...
    def store_observation(self, project_id: str, observation: EffectObservation, expected_state: str | None) -> None: ...


class EffectReconciler:
    def __init__(self, binding: ProjectBinding, intents: IntentRepository):
        self.binding = binding
        self.intents = intents

    def observe_effect(self, *, project_id: str, effect_id: str, readback: Callable[[EffectIntent], tuple[str, bytes] | None]) -> EffectObservation:
        self.binding.require(project_id)
        intent = self.intents.load(project_id, identifier(effect_id))
        if intent.project_id != project_id:
            raise IntegrationError("forbidden", "cross-project effect")
        try:
            observed = readback(intent)
        except (RemoteUnknown, ProviderFailure, OSError):
            return EffectObservation(effect_id, "UNKNOWN")
        if observed is None:
            return EffectObservation(effect_id, "UNKNOWN")
        remote_identity, data = observed
        actual = sha256(data)
        if actual != intent.expected_sha256:
            return EffectObservation(effect_id, "CONFLICT", remote_identity, actual)
        return EffectObservation(effect_id, "VERIFIED", remote_identity, actual)

    def reconcile_effect(self, *, project_id: str, effect_id: str, readback: Callable[[EffectIntent], tuple[str, bytes] | None], expected_state: str | None = None) -> EffectObservation:
        self.binding.require(project_id)
        if expected_state is not None and expected_state not in ("UNKNOWN", "PENDING", "VERIFIED", "CONFLICT"):
            raise IntegrationError("invalid", "unknown expected effect state")
        observation = self.observe_effect(project_id=project_id, effect_id=effect_id, readback=readback)
        # C service performs expected-state CAS, persisted in its own scoped transaction.
        self.intents.store_observation(project_id, observation, expected_state)
        return observation
