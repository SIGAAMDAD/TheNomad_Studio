"""Data-only Oversight operations contracts; private source adapters are not bundled."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import FrozenSet, Optional
import re


SHA256 = re.compile(r"^[a-f0-9]{64}$")
CASE_ID = re.compile(r"^c_[a-f0-9]{32}$")


def digest_ok(value: str) -> bool:
    return isinstance(value, str) and SHA256.fullmatch(value) is not None


def opaque_case_id(value: str) -> bool:
    return isinstance(value, str) and CASE_ID.fullmatch(value) is not None


class ResultCode(str, Enum):
    ACCEPTED = "ACCEPTED"
    PENDING = "PENDING"
    DENIED = "DENIED"
    CONFLICT = "CONFLICT"
    BACKPRESSURE = "BACKPRESSURE"
    UNSUPPORTED = "UNSUPPORTED"
    BLOCKED_PRIVATE_ACCESS = "BLOCKED_PRIVATE_ACCESS"
    BLOCKED_INDEPENDENCE = "BLOCKED_INDEPENDENCE"
    NOT_VERIFIED = "NOT_VERIFIED"
    QUARANTINED = "QUARANTINED"


@dataclass(frozen=True)
class Result:
    code: ResultCode
    ref: str = ""
    reason: str = ""


@dataclass(frozen=True)
class SealedIntake:
    """No plaintext, reporter identity, narrative, or evidence bytes are accepted."""
    case_id: str
    event_id: str
    sealed_receipt_sha256: str
    ciphertext_sha256: str
    retention_class: str
    severity: int
    received_at: int
    subject_token: str = ""  # opaque normalized pseudonym, never ordinary worker ID
    policy_trigger: str = ""  # category from deterministic observed evidence

    def valid(self) -> bool:
        return (opaque_case_id(self.case_id) and bool(re.fullmatch(r"ev_[a-f0-9]{32}", self.event_id))
                and digest_ok(self.sealed_receipt_sha256) and digest_ok(self.ciphertext_sha256)
                and self.retention_class in ("STANDARD", "RESTRICTED", "LEGAL_HOLD")
                and isinstance(self.severity, int) and 0 <= self.severity <= 4
                and isinstance(self.received_at, int) and self.received_at >= 0
                and (not self.subject_token or bool(re.fullmatch(r"sub_[a-f0-9]{32}", self.subject_token)))
                and self.policy_trigger in ("", "CREDENTIAL_ABUSE", "SOURCE_TAMPERING",
                                            "CONFIDENTIAL_DISCLOSURE", "EVIDENCE_TAMPERING"))


@dataclass
class CaseRecord:
    sealed: SealedIntake
    state: str = "PENDING"
    custody_verified: bool = False
    assigned_investigator: Optional[str] = None
    assigned_epoch: int = 0
    last_checked_at: int = 0
    custody_hold: bool = True
    archive_id: Optional[str] = None


@dataclass(frozen=True)
class Investigator:
    investigator_id: str
    epoch: int
    active: bool
    clearance: FrozenSet[str]
    conflicted_subjects: FrozenSet[str] = frozenset()
    load_limit: int = 1
    current_load: int = 0


@dataclass(frozen=True)
class RetentionRule:
    retention_class: str
    minimum_age_seconds: int
    policy_revision: str
    policy_digest: str
    approved: bool = False


@dataclass(frozen=True)
class ArchiveManifest:
    archive_id: str
    case_id: str
    ciphertext_sha256: str
    manifest_sha256: str
    retention_class: str
    retention_policy_sha256: str
    staged_at: int
    signature_ref: Optional[str] = None
    # signature_ref is descriptive only until verified by independent signature port.


@dataclass
class ArchiveState:
    manifest: ArchiveManifest
    ciphertext: bytes = field(repr=False)
    staged_by: str = ""
    signature_verified: bool = False
    restore_verified: bool = False
    quarantined: bool = False


@dataclass(frozen=True)
class RestoreReceipt:
    archive_id: str
    manifest_sha256: str
    ciphertext_sha256: str
    witness_ref: str
    witness_epoch: int
    restored_ok: bool


@dataclass(frozen=True)
class P0Proof:
    case_id: str
    trigger: str
    evidence_sha256: str
    policy_revision: str
    proposed_action: str
    target_scope: str
    owner_approved_action_ref: str
    expires_at: int


@dataclass
class Alert:
    alert_id: str
    kind: str
    created_at: int
    state: str = "PENDING"  # PENDING, SENT_UNACKNOWLEDGED, ACKNOWLEDGED
    attempts: int = 0


@dataclass(frozen=True)
class AuditSummary:
    total_cases: int
    pending_cases: int
    unassigned_cases: int
    oldest_case_age_seconds: int
    overdue_checks: int
    high_risk_cases: int
    investigator_coverage_pct: float
    intake_count: int
    intake_rate_per_hour: float
    pending_alerts: int
    fault_counts: dict[str, int]
    statuses: tuple[str, ...]
    time_to_triage_p50_ms: Optional[float]
    time_to_triage_p95_ms: Optional[float]
    time_to_triage_p99_ms: Optional[float]


@dataclass(frozen=True)
class CapacityReport:
    logical_workers: int
    synthetic_events: int
    elapsed_ms: float
    ingestion_p50_ms: float
    ingestion_p95_ms: float
    ingestion_p99_ms: float
    maximum_queue_depth: int
    backpressured: int
    accepted: int
    queue_cap: int
    bottleneck: str
    actual_host_sessions_observed: bool = False
