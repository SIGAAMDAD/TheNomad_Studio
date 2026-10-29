"""Portable HR/WORKFORCE-RECOMMENDATION/1 DTOs; no Studio admission authority."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import FrozenSet, Optional, Tuple


class Lifecycle(str, Enum):
    JOINER = "JOINER"
    QUALIFY = "QUALIFY"
    PROBATION = "PROBATION"
    ACTIVE_REVIEW = "ACTIVE_REVIEW"
    ROLE_CHANGE = "ROLE_CHANGE"
    TEMP_RESTRICTION = "TEMP_RESTRICTION"
    REINSTATEMENT = "REINSTATEMENT"
    RETIREMENT = "RETIREMENT"


class Result(str, Enum):
    RECOMMENDED = "RECOMMENDED"
    DENIED = "DENIED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NOT_VERIFIED = "NOT_VERIFIED"
    UNSUPPORTED = "UNSUPPORTED"
    BLOCKED_INDEPENDENCE = "BLOCKED_INDEPENDENCE"
    NEEDS_POLICY = "NEEDS_POLICY"
    REVIEW_RECOMMENDED = "REVIEW_RECOMMENDED"


@dataclass(frozen=True)
class Role:
    role_id: str
    department_id: str
    privileges: FrozenSet[str]
    required_capabilities: FrozenSet[str] = frozenset()


@dataclass(frozen=True)
class Identity:
    worker_id: str
    role_id: str
    department_id: str
    identity_epoch: int
    assignment_epoch: int
    persona_revision: int
    persona_digest: str
    privileges: FrozenSet[str]
    active: bool
    retired: bool = False


@dataclass(frozen=True)
class CapabilityEvidence:
    receipt_id: str
    worker_id: str
    role_id: str
    identity_epoch: int
    capability: str
    source_digest: str
    issued_at: int
    expires_at: int


@dataclass(frozen=True)
class TrainingEvidence:
    record_id: str
    worker_id: str
    target_role_id: str
    evaluator_id: str
    source_digest: str
    objective_result: str
    completed_at: int
    expires_at: int


@dataclass(frozen=True)
class GrantClaim:
    grant_id: str
    worker_id: str
    identity_epoch: int
    action: Lifecycle
    target_role_id: str
    target_privileges: FrozenSet[str]
    policy_revision: str
    expires_at: int


@dataclass(frozen=True)
class QualificationRequest:
    request_id: str
    worker_id: str
    identity_epoch: int
    assignment_epoch: int
    current_role_id: str
    persona_revision: int
    persona_digest: str
    action: Lifecycle
    target_role_id: str
    policy_revision: str
    grant: Optional[GrantClaim]
    capabilities: Tuple[CapabilityEvidence, ...] = ()
    training: Tuple[TrainingEvidence, ...] = ()
    reason_digest: str = ""


@dataclass(frozen=True)
class Decision:
    recommendation_id: str
    worker_id: str
    action: Lifecycle
    status: Result
    reason_code: str
    source_identity_epoch: int
    source_assignment_epoch: int
    policy_revision: str
    evidence_digests: Tuple[str, ...] = ()
    valid_until: Optional[int] = None
    target_role_id: str = ""
    minimum_permissions: Tuple[str, ...] = ()
    # Explicitly distinguish independently checked artifacts from recommendations.
    verified_facts: Tuple[str, ...] = ()
    inferences: Tuple[str, ...] = ()


@dataclass(frozen=True)
class HandoverPlan:
    worker_id: str
    from_role_id: str
    to_role_id: str
    expected_identity_epoch: int
    expected_assignment_epoch: int
    proposed_next_assignment_epoch: int
    expected_persona_revision: int
    proposed_next_persona_revision: int
    revoke_old_tickets: bool
    fence_old_leases: bool
    require_current_persona: bool
    requires_studio_readback: bool
    status: Result


@dataclass(frozen=True)
class ReviewItem:
    intake_id: str
    subject_worker_id: str
    origin_principal: str
    category: str
    evidence_digest: str
    received_at: int
    priority: int
    specialty: str
    excluded_principals: FrozenSet[str] = frozenset()


@dataclass(frozen=True)
class Reviewer:
    principal_id: str
    specialty: FrozenSet[str]
    training_valid_until: int
    active: bool
    active_cases: int
    case_capacity: int
    conflicts: FrozenSet[str] = frozenset()


@dataclass(frozen=True)
class ReviewSuggestion:
    intake_id: str
    reviewer_id: Optional[str]
    status: Result
    reason_code: str


@dataclass(frozen=True)
class CoverageNeed:
    role_id: str
    department_id: str
    required: int
    # Supplied/approved policy target: never inferred by this module.


@dataclass(frozen=True)
class CoverageSuggestion:
    role_id: str
    department_id: str
    required: int
    available: int
    deficit: int
    worker_ids: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ScheduleSuggestion:
    status: Result
    worker_ids: Tuple[str, ...] = ()
    remaining: int = 0
    capacity_source: str = ""


@dataclass(frozen=True)
class Dossier:
    worker_id: str
    identity_epoch: int
    role_id: str
    persona_revision: int
    persona_digest: str
    records: Tuple[Decision, ...] = ()
    status: Result = Result.NOT_VERIFIED
