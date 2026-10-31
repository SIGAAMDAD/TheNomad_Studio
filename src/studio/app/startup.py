"""Reconcile durable relay observations and uncertain external effects on restart.

GitHub ref verification and file readback are WG-G responsibilities. Worker
browser sessions are never woken, inferred running, or programmatically resumed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .errors import StudioError


@dataclass(frozen=True)
class ChannelCursor:
    project_id: str
    channel_id: str
    verified_ref_sha: str | None


@dataclass(frozen=True)
class RelayObservation:
    project_id: str
    channel_id: str
    remote_path: str
    commit_sha: str
    body_sha256: str
    verified: bool


class RecoveryStatePort(Protocol):
    def channels(self) -> list[ChannelCursor]: ...
    def outstanding_effects(self, *, project_id: str) -> list[str]: ...
    def persist_verified_cursor(self, *, project_id: str, channel_id: str,
                                expected_ref_sha: str | None,
                                new_ref_sha: str) -> bool: ...
    def hold_channel_unknown(self, *, project_id: str, channel_id: str, reason: str) -> None: ...


class GitHubReadbackPort(Protocol):
    def scan_since(self, *, project_id: str, channel_id: str,
                   verified_ref_sha: str | None) -> tuple[list[RelayObservation], str, bool]:
        """Return exact observations, new head SHA, and ancestry continuity."""
        ...

    def reconcile_external_effect(self, *, project_id: str, effect_id: str) -> bool:
        """Verify remote exact identity/readback; NEVER retry an unknown effect."""
        ...


class ObservationProjectionPort(Protocol):
    def project_verified(self, observation: RelayObservation) -> None:
        """WG-I idempotent, content-verified projection into C state."""
        ...


class StartupRecovery:
    def __init__(self, state: RecoveryStatePort, github: GitHubReadbackPort,
                 projector: ObservationProjectionPort):
        self.state, self.github, self.projector = state, github, projector

    def recover(self) -> dict[str, int]:
        """Replay verified GitHub observations before any active poller starts.

        A rewritten ref isolates that channel; an unknown provider effect stays
        unresolved and cannot be repeated. Healthy projects can still start.
        """
        projected = 0
        held = 0
        affected_projects: set[str] = set()
        # Use one stable channel snapshot for scans and unknown-effect recovery.
        cursors=tuple(self.state.channels())
        for cursor in cursors:
            if (not cursor.project_id or not cursor.channel_id or
                    cursor.verified_ref_sha is not None and len(cursor.verified_ref_sha) != 40):
                raise StudioError("invalid", "invalid persisted relay cursor")
            try:
                observations, head, ancestor = self.github.scan_since(
                    project_id=cursor.project_id, channel_id=cursor.channel_id,
                    verified_ref_sha=cursor.verified_ref_sha)
                if not ancestor or len(head) != 40:
                    raise StudioError("unknown_effect", "remote relay ref rewound or rewritten")
                for observation in observations:
                    if (not observation.verified or
                            observation.project_id != cursor.project_id or
                            observation.channel_id != cursor.channel_id or
                            len(observation.body_sha256) != 64):
                        raise StudioError("invalid", "relay observation lacks authenticated byte verification")
                    self.projector.project_verified(observation)
                    projected += 1
                if not self.state.persist_verified_cursor(
                        project_id=cursor.project_id, channel_id=cursor.channel_id,
                        expected_ref_sha=cursor.verified_ref_sha, new_ref_sha=head):
                    raise StudioError("stale_revision", "relay cursor CAS failed")
            except Exception as exc:
                self.state.hold_channel_unknown(project_id=cursor.project_id,
                                               channel_id=cursor.channel_id, reason=str(exc))
                held += 1
                affected_projects.add(cursor.project_id)
        for project_id in {c.project_id for c in cursors}:
            for effect_id in self.state.outstanding_effects(project_id=project_id):
                try:
                    reconciled = self.github.reconcile_external_effect(
                        project_id=project_id, effect_id=effect_id)
                except Exception:
                    reconciled = False
                if not reconciled:
                    affected_projects.add(project_id)
        return {"observations_projected": projected, "channels_held": held,
                "projects_requiring_reconciliation": len(affected_projects)}
