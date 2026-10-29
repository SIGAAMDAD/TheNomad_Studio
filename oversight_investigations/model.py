"""Local, metadata-only HR investigation contracts (PROVISIONAL_SOURCE).

No cryptography, provider authority, case decryption or live notice issuance lives here.
Callers must inject authenticated identity, authorization, seal and source verifiers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Protocol, TypeVar


T = TypeVar('T')

STATES = frozenset({
    'SEALED_RECEIVED', 'SCREENED', 'TRIAGED', 'INVESTIGATING',
    'EVIDENCE_READY', 'FINDINGS_DRAFT', 'SECOND_REVIEW',
    'OWNER_DISPOSITION', 'CLOSED', 'BLOCKED_ACCESS',
    'BLOCKED_INDEPENDENCE', 'WAITING_EVIDENCE', 'WAITING_OWNER',
    'APPEALED', 'REOPENED', 'DISMISSED_NO_EVIDENCE',
})
CLASSES = frozenset({
    'CLAIMED', 'OBSERVED', 'CORROBORATED', 'INFERRED',
    'DISPUTED', 'INVALID_UNREADABLE',
})
APPEAL_OUTCOMES = frozenset({
    'CONFIRM', 'AMEND', 'OVERTURN', 'INSUFFICIENT_EVIDENCE', 'NEEDS_OWNER',
})


class InvestigationError(Exception):
    """Error codes do not contain case text, identities or ciphertext paths."""
    code = 'INVESTIGATION_ERROR'

    def __str__(self) -> str:
        return self.code


class Denied(InvestigationError):
    code = 'DENIED'


class Conflict(InvestigationError):
    code = 'CONFLICT'


class Unsupported(InvestigationError):
    code = 'UNSUPPORTED'


class IntegrityFailure(InvestigationError):
    code = 'INTEGRITY_FAILURE'


class Invalid(InvestigationError):
    code = 'INVALID'


@dataclass(frozen=True)
class Principal:
    identity: str
    authorization_epoch: int
    valid_until: datetime


@dataclass(frozen=True)
class SourceReceipt:
    """Result from a trusted source verifier, never a caller-provided assertion."""
    evidence_id: str
    content_sha256: str
    source_ref: str
    source_revision: str
    source_event_id: str
    source_occurred_at: datetime
    origin_ref: str
    verifier_id: str
    author_id: str
    access_scope: str
    classification: str
    valid_until: datetime


@dataclass(frozen=True)
class EvidenceSubmission:
    evidence_id: str
    content_sha256: str
    manifest_sha256: str
    ciphertext_ref: str
    source_ref: str
    source_revision: str
    source_event_id: str
    source_occurred_at: datetime
    origin_ref: str
    access_scope: str
    author_id: str
    counter_to: str | None = None
    corrects: str | None = None


@dataclass(frozen=True)
class IntakeReceipt:
    """Verified intake metadata, independently bound to a sealed report receipt."""
    intake_id: str
    ciphertext_ref: str
    manifest_sha256: str
    subject_worker_id: str | None
    reporter_id: str | None
    policy_revision: int
    source_occurred_at: datetime
    verifier_id: str
    valid_until: datetime


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    content_sha256: str
    manifest_sha256: str
    ciphertext_ref: str
    source_ref: str
    source_revision: str
    source_event_id: str
    source_occurred_at: datetime
    received_at: datetime
    origin_ref: str
    verifier_id: str
    access_scope: str
    author_id: str
    classification: str
    counter_to: str | None
    corrects: str | None


@dataclass(frozen=True)
class Finding:
    conclusion: str
    policy_clause: str
    policy_revision: int
    fact_refs: tuple[str, ...]
    inference_refs: tuple[str, ...]
    allegation_refs: tuple[str, ...]
    counter_refs: tuple[str, ...]
    sealed_reason_sha256: str
    author_id: str
    sufficiency: str


@dataclass(frozen=True)
class Appeal:
    appeal_id: str
    appellant_id: str
    new_evidence_refs: tuple[str, ...]
    sealed_ground_sha256: str
    filed_at: datetime
    original_state: str
    reviewer_id: str | None = None
    outcome: str | None = None
    policy_revision: int | None = None
    ratified: bool = False


@dataclass(frozen=True)
class LedgerEntry:
    entry_id: str
    kind: str
    actor_id: str
    occurred_at: datetime
    metadata_sha256: str
    previous_sha256: str
    record_sha256: str
    state: str


@dataclass
class Case:
    case_id: str
    state: str
    subject_worker_id: str | None
    reporter_id: str | None
    intake_id: str
    intake_seal_sha256: str
    created_at: datetime
    intake_source_occurred_at: datetime
    intake_verifier_id: str
    investigator_id: str | None = None
    second_reviewer_id: str | None = None
    finding: Finding | None = None
    evidence: dict[str, Evidence] = field(default_factory=dict)
    appeals: dict[str, Appeal] = field(default_factory=dict)
    events: list[LedgerEntry] = field(default_factory=list)
    custody: list[LedgerEntry] = field(default_factory=list)
    operations: dict[str, tuple[str, dict]] = field(default_factory=dict)
    suspended_state: str | None = None
    pending_appeal_id: str | None = None
    owner_decision_id: str | None = None
    policy_revision: int = 1


class IdentityPort(Protocol):
    def resolve(self, opaque_credential: object) -> Principal | None: ...


class AuthorizationPort(Protocol):
    def allow(self, principal: Principal, operation: str, case_id: str | None,
              access_scope: str | None, now: datetime) -> bool: ...


class ClockPort(Protocol):
    def now(self) -> datetime: ...


class SealedCustodyPort(Protocol):
    def verify_seal(self, ciphertext_ref: str, manifest_sha256: str,
                    principal: Principal, now: datetime) -> bool: ...


class IntakeVerificationPort(Protocol):
    def verify_intake(self, *, intake_id: str, ciphertext_ref: str,
                      manifest_sha256: str, subject_worker_id: str | None,
                      reporter_id: str | None, policy_revision: int,
                      principal: Principal, now: datetime) -> IntakeReceipt | None: ...


class SourceVerificationPort(Protocol):
    def verify_source(self, submitted: EvidenceSubmission,
                      principal: Principal, now: datetime) -> SourceReceipt | None: ...


class EligibilityPort(Protocol):
    def eligible(self, case_id: str, candidate_id: str, role: str,
                 now: datetime) -> bool: ...


class OwnerDecisionPort(Protocol):
    def verify_decision(self, *, decision_ref: str, case_id: str,
                        case_chain_sha256: str, action: str, policy_revision: int,
                        now: datetime) -> bool: ...


class CaseStorePort(Protocol):
    """P2 replacement must be atomic, durable, confidential and authorization-scoped."""
    def create(self, intake_id: str, fingerprint: str,
               new: Callable[[], Case]) -> tuple[Case, bool]: ...

    def read(self, case_id: str) -> Case: ...

    def update(self, case_id: str, fn: Callable[[Case], T]) -> T: ...

    def aggregate(self) -> dict[str, int]: ...
