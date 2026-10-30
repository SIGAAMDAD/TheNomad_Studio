"""Release/recovery commands with verified policy, provenance and effect readback.

No external publication is assumed successful from a request or provider timeout.
The storage and provider ports below must be backed by WG-C, WG-A, WG-E,
WG-G and the authorized service configuration at assembly time.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .errors import StudioError
from .projects import Actor, require_sha, request_hash


@dataclass(frozen=True)
class ReleaseRecord:
    project_id: str
    release_id: str
    source_revision_id: str
    snapshot_id: str
    release_digest: str
    state: str


@dataclass(frozen=True)
class EffectRecord:
    effect_id: str
    project_id: str
    release_id: str
    effect_digest: str
    state: str  # prepared | dispatched | observed | unknown | failed


class ReleaseEvidencePort(Protocol):
    def verified_source_qa_ci(self, *, project_id: str, source_revision_id: str) -> bool: ...
    def verified_canon_snapshot(self, *, project_id: str, snapshot_id: str) -> bool: ...
    def verified_artifacts(self, *, project_id: str,
                           source_revision_id: str, artifact_ids: tuple[str, ...]) -> bool: ...
    def verified_release_digest(self, *, project_id: str, source_revision_id: str,
                                snapshot_id: str, artifact_ids: tuple[str, ...],
                                release_digest: str) -> bool: ...
    def approved_exact_effect(self, *, project_id: str, release_id: str,
                              decision_id: str, effect_digest: str,
                              source_revision_id: str, snapshot_id: str) -> bool: ...


class ReleaseTransactionPort(Protocol):
    def prepare_release(self, *, project_id: str, principal_id: str,
                        request_id: str, request_body_sha256: str,
                        source_revision_id: str, snapshot_id: str,
                        artifact_ids: tuple[str, ...], release_digest: str) -> ReleaseRecord: ...
    def get_release(self, *, project_id: str, release_id: str) -> ReleaseRecord | None: ...
    def list_releases(self, *, project_id: str) -> list[ReleaseRecord]: ...
    def prepare_publication(self, *, project_id: str, release_id: str,
                            decision_id: str, effect_digest: str,
                            request_id: str, request_body_sha256: str) -> EffectRecord: ...
    def observe_publication(self, *, project_id: str, effect_id: str,
                            verified_remote_digest: str) -> None: ...
    def mark_publication_unknown(self, *, project_id: str, effect_id: str) -> None: ...


class PublicationPort(Protocol):
    def observe_exact(self, *, project_id: str, release_id: str) -> str | None:
        """Digest from authenticated remote readback, or None if absent."""
        ...
    def publish(self, *, project_id: str, release_id: str, effect_digest: str,
                effect_id: str) -> None:
        """WG-G publish to configured existing ref only; no new Git branch."""
        ...


class ReleaseCoordinator:
    def __init__(self, state: ReleaseTransactionPort,
                 evidence: ReleaseEvidencePort, publisher: PublicationPort):
        self.state, self.evidence, self.publisher = state, evidence, publisher

    def prepareRelease(self, actor: Actor, project_id: str, request: dict) -> dict:
        actor.require_scope(project_id)
        ids = tuple(request["artifact_ids"])
        if not ids or len(set(ids)) != len(ids):
            raise StudioError("invalid", "artifacts must be nonempty and unique")
        release_digest = request["release_digest"]
        require_sha(release_digest)
        source, snapshot = request["source_revision_id"], request["snapshot_id"]
        if not self.evidence.verified_source_qa_ci(project_id=project_id,
                                                  source_revision_id=source):
            raise StudioError("qa_failed", "source QA/CI exact revision unverified")
        if not self.evidence.verified_canon_snapshot(project_id=project_id, snapshot_id=snapshot):
            raise StudioError("stale_snapshot", "Canon snapshot unverified")
        if not self.evidence.verified_artifacts(project_id=project_id,
                                                source_revision_id=source, artifact_ids=ids):
            raise StudioError("invalid", "artifact bytes or source association unverified")
        if not self.evidence.verified_release_digest(
                project_id=project_id, source_revision_id=source,
                snapshot_id=snapshot, artifact_ids=ids, release_digest=release_digest):
            raise StudioError("invalid", "release digest not proven by exact manifest")
        record = self.state.prepare_release(
            project_id=project_id, principal_id=actor.principal_id,
            request_id=request["request_id"],
            request_body_sha256=request_hash(source, snapshot, ids, release_digest),
            source_revision_id=source, snapshot_id=snapshot,
            artifact_ids=ids, release_digest=release_digest)
        return {"release_id": record.release_id, "state": record.state}

    def publishRelease(self, actor: Actor, project_id: str,
                       release_id: str, request: dict) -> dict:
        actor.require_scope(project_id)
        record = self.state.get_release(project_id=project_id, release_id=release_id)
        if record is None:
            raise StudioError("missing", "release missing")
        if record.state not in ("prepared", "unknown", "published"):
            raise StudioError("conflict", "release not publishable")
        if (request["source_revision_id"] != record.source_revision_id or
                request["snapshot_id"] != record.snapshot_id):
            raise StudioError("stale_revision", "release source/snapshot mismatch")
        require_sha(request["effect_digest"])
        if not self.evidence.approved_exact_effect(
                project_id=project_id, release_id=release_id,
                decision_id=request["decision_id"], effect_digest=request["effect_digest"],
                source_revision_id=record.source_revision_id,
                snapshot_id=record.snapshot_id):
            raise StudioError("requires_owner", "matching owner decision not verified")
        if not self.evidence.verified_source_qa_ci(
                project_id=project_id, source_revision_id=record.source_revision_id):
            raise StudioError("qa_failed", "release QA/CI no longer verified")
        effect = self.state.prepare_publication(
            project_id=project_id, release_id=release_id,
            decision_id=request["decision_id"], effect_digest=request["effect_digest"],
            request_id=request["request_id"],
            request_body_sha256=request_hash(record.source_revision_id,
                                             record.snapshot_id, request["effect_digest"],
                                             request["decision_id"]))
        if effect.project_id != project_id or effect.release_id != release_id or effect.effect_digest != request["effect_digest"]:
            raise StudioError("invalid", "effect intent does not match release")
        try:
            observed = self.publisher.observe_exact(project_id=project_id, release_id=release_id)
        except Exception as exc:
            self.state.mark_publication_unknown(project_id=project_id, effect_id=effect.effect_id)
            raise StudioError("unknown_effect", "remote effect readback unavailable") from exc
        if observed is not None:
            if observed != effect.effect_digest:
                raise StudioError("conflict", "remote release digest mismatch")
            self.state.observe_publication(project_id=project_id, effect_id=effect.effect_id,
                                           verified_remote_digest=observed)
            return {"release_id": release_id, "state": "published"}
        if effect.state != "prepared":
            # Unknown/dispatched requests cannot be replayed just because an
            # eventually consistent provider returned 'missing'.
            raise StudioError("unknown_effect", "publication requires reconciliation")
        try:
            self.publisher.publish(project_id=project_id, release_id=release_id,
                                   effect_digest=effect.effect_digest, effect_id=effect.effect_id)
            observed = self.publisher.observe_exact(project_id=project_id, release_id=release_id)
            if observed != effect.effect_digest:
                raise StudioError("unknown_effect", "remote readback not yet verified")
            self.state.observe_publication(project_id=project_id, effect_id=effect.effect_id,
                                           verified_remote_digest=observed)
        except Exception as exc:
            self.state.mark_publication_unknown(project_id=project_id, effect_id=effect.effect_id)
            if isinstance(exc, StudioError):
                raise
            raise StudioError("unknown_effect", "publish outcome uncertain; reconcile first") from exc
        return {"release_id": release_id, "state": "published"}

    def listReleases(self, actor: Actor, project_id: str) -> dict:
        actor.require_scope(project_id)
        records = self.state.list_releases(project_id=project_id)
        return {"releases": [{"release_id": record.release_id,
                              "source_revision_id": record.source_revision_id,
                              "state": record.state} for record in records]}


class RestorePort(Protocol):
    def project_revision(self, *, project_id: str) -> int: ...
    def backup_is_verified(self, *, project_id: str, backup_digest: str) -> bool: ...
    def outstanding_uncertain_effects(self, *, project_id: str) -> list[str]: ...
    def request_restore(self, *, project_id: str, principal_id: str,
                        request_id: str, request_body_sha256: str,
                        backup_digest: str, expected_revision: int) -> tuple[str, str]: ...
    def recovery_state(self, *, project_id: str) -> str: ...


class RecoveryCoordinator:
    def __init__(self, state: RestorePort):
        self.state = state

    def requestRestore(self, actor: Actor, project_id: str, request: dict) -> dict:
        actor.require_scope(project_id)
        actor.require_owner()
        expected = request["expected_revision"]
        require_sha(request["backup_digest"])
        if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
            raise StudioError("invalid", "expected project revision invalid")
        if self.state.project_revision(project_id=project_id) != expected:
            raise StudioError("stale_revision", "project changed since restore request")
        if not self.state.backup_is_verified(project_id=project_id,
                                             backup_digest=request["backup_digest"]):
            raise StudioError("invalid", "backup project/digest not verified")
        if self.state.outstanding_uncertain_effects(project_id=project_id):
            raise StudioError("unknown_effect", "reconcile uncertain provider effects before restore")
        recovery_id, state = self.state.request_restore(
            project_id=project_id, principal_id=actor.principal_id,
            request_id=request["request_id"],
            request_body_sha256=request_hash(request["backup_digest"], expected),
            backup_digest=request["backup_digest"], expected_revision=expected)
        return {"recovery_id": recovery_id, "state": state}

    def observeRecovery(self, actor: Actor, project_id: str) -> dict:
        actor.require_scope(project_id)
        outstanding = self.state.outstanding_uncertain_effects(project_id=project_id)
        return {"recovery_state": self.state.recovery_state(project_id=project_id),
                "outstanding_effect_ids": outstanding}
