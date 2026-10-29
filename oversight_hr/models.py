"""Public, privacy-minimal input contracts for the provisional HR core."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Status(str, Enum):
    OK = 'OK'
    DUPLICATE = 'DUPLICATE'
    DENIED = 'DENIED'
    BLOCKED_PRIVATE_ACCESS = 'BLOCKED_PRIVATE_ACCESS'
    BLOCKED_INDEPENDENCE = 'BLOCKED_INDEPENDENCE'
    WAITING_EVIDENCE = 'WAITING_EVIDENCE'
    OWNER_PENDING = 'OWNER_PENDING'
    NOT_CONNECTED = 'NOT_CONNECTED'
    UNSUPPORTED = 'UNSUPPORTED'
    PENDING_UNKNOWN = 'PENDING_UNKNOWN'
    CONFLICT = 'CONFLICT'


@dataclass(frozen=True)
class Result:
    status: Status
    code: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SealedReport:
    event_id: str
    reporter_id: str
    subject_worker_id: str | None
    ciphertext_ref: str
    ciphertext_sha256: str
    source_ref: str
    occurred_at: str
    incident_category: str
    summary_class: str = 'RESTRICTED'


@dataclass(frozen=True)
class EvidenceRef:
    event_id: str
    source_ref: str
    source_revision: str
    byte_sha256: str
    collector_id: str
    evidence_class: str  # CLAIMED/OBSERVED/CORROBORATED/INFERRED/DISPUTED/INVALID_UNREADABLE


ALLOWED_ACTIONS = frozenset({
    'FLAG_FOR_OWNER', 'REQUIRE_INDEPENDENT_REVIEW', 'PAUSE_NEW_ASSIGNMENTS',
    'REQUEST_LEASE_FENCE', 'REQUEST_ACCESS_REVIEW',
})
EVIDENCE_CLASSES = frozenset({
    'CLAIMED', 'OBSERVED', 'CORROBORATED', 'INFERRED', 'DISPUTED', 'INVALID_UNREADABLE'
})
CASE_STATES = (
    'SEALED_RECEIVED', 'SCREENED', 'TRIAGED', 'INVESTIGATING', 'EVIDENCE_READY',
    'FINDINGS_DRAFT', 'SECOND_REVIEW', 'OWNER_DISPOSITION', 'CLOSED',
    'BLOCKED_ACCESS', 'BLOCKED_INDEPENDENCE', 'WAITING_EVIDENCE',
    'WAITING_OWNER', 'APPEALED', 'REOPENED', 'DISMISSED_NO_EVIDENCE',
)
TRANSITIONS = {
    'SEALED_RECEIVED': {'SCREENED', 'BLOCKED_ACCESS'},
    'BLOCKED_ACCESS': {'SCREENED'},
    'SCREENED': {'TRIAGED', 'DISMISSED_NO_EVIDENCE'},
    'TRIAGED': {'INVESTIGATING', 'WAITING_EVIDENCE', 'BLOCKED_INDEPENDENCE', 'DISMISSED_NO_EVIDENCE'},
    'BLOCKED_INDEPENDENCE': {'INVESTIGATING'},
    'WAITING_EVIDENCE': {'INVESTIGATING', 'DISMISSED_NO_EVIDENCE'},
    'INVESTIGATING': {'EVIDENCE_READY', 'WAITING_EVIDENCE', 'BLOCKED_ACCESS'},
    'EVIDENCE_READY': {'FINDINGS_DRAFT', 'WAITING_EVIDENCE'},
    'FINDINGS_DRAFT': {'SECOND_REVIEW'},
    'SECOND_REVIEW': {'OWNER_DISPOSITION', 'WAITING_OWNER'},
    'WAITING_OWNER': {'OWNER_DISPOSITION'},
    'OWNER_DISPOSITION': {'CLOSED'},
    'CLOSED': {'APPEALED', 'REOPENED'},
    'DISMISSED_NO_EVIDENCE': {'APPEALED', 'REOPENED'},
    'APPEALED': {'REOPENED', 'CLOSED'},
    'REOPENED': {'INVESTIGATING', 'BLOCKED_INDEPENDENCE'},
}
