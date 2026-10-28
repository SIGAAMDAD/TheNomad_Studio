"""Trusted adapter contracts. WG-C owns the durable implementation; WG-G owns providers.

Never populate these objects from a worker's payload. A real authority adapter must
load them through an isolated server credential and re-check state when reserving.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol


@dataclass(frozen=True)
class Principal:
    principal_id: str
    # Created by verified mTLS transport only in deployed service.
    auth_method: str = "MTLS"


@dataclass(frozen=True)
class VerifiedQA:
    review_id: str
    reviewer_principal_id: str
    subject_sha256: str
    source_sha: str
    evidence_ref: str
    verdict: str = "PASS"


@dataclass(frozen=True)
class State:
    grant: Mapping
    grant_issuer_verified: bool
    owner_approved: bool
    approved_scope_sha256: str
    policy_revision: str
    authority_revision: str
    assignment_epoch: int
    lease_generation: int
    holder_attempt_id: str
    work_branch: str
    source_base_sha: str
    revoked: bool
    budget_authorized: bool
    source_writes_enabled: bool
    qa: VerifiedQA | None
    # Immutable, trusted revision used as optimistic concurrency precondition.
    state_revision: str
    drive_file_id: str | None = None
    drive_document_owner_ref: str | None = None
    drive_expected_revision: str | None = None
    document_writes_enabled: bool = False
    # Independently verified CI/test evidence digest from trusted authority.
    evidence_sha256: str | None = None


@dataclass(frozen=True)
class GitEntry:
    path: str
    mode: str  # Git native 100644 / 100755 / 120000 / 160000 / 040000


@dataclass(frozen=True)
class SourceSnapshot:
    repo_id: str
    branch: str
    head_sha: str
    entries: tuple[GitEntry, ...]


@dataclass(frozen=True)
class InspectedPatch:
    # These MUST be computed by an isolated provider from actual patch bytes,
    # and include both rename/delete source and destination paths.
    patch_sha256: str
    proposed_tree_sha256: str
    changed_paths: tuple[str, ...]
    proposed_entries: tuple[GitEntry, ...]


class Authority(Protocol):
    def load(self, job_id: str, grant_id: str) -> State: ...
    def reserve(self, intent: dict, principal_id: str, state_revision: str,
                payload_sha256: str) -> str: ...
    # COMMITTED or DUPLICATE on exact payload, otherwise return denial code;
    # within one DB transaction revalidate owner decision, revocation, fences,
    # QA, scope, active policy and source publication enabled.
    def recorded(self, publication_id: str, idempotency_key: str) -> dict | None: ...
    def reconcile(self, publication_id: str, idempotency_key: str,
                  state_revision: str, receipt: dict) -> bool: ...
    # Transactionally verify reserved payload, current fences and readback.


class GitSource(Protocol):
    def snapshot(self, repo_id: str, branch: str) -> SourceSnapshot: ...
    def inspect_patch(self, repo_id: str, base_sha: str, patch: bytes) -> InspectedPatch: ...
    def readback(self, intent: dict) -> dict | None: ...
    # Trusted provider readback only; no client-supplied observed facts.


class DriveSource(Protocol):
    def revision(self, file_id: str) -> str: ...
    def readback(self, intent: dict) -> dict | None: ...


class AuditSink(Protocol):
    def emit(self, record: dict) -> None: ...
