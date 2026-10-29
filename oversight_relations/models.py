"""Provisional, project-neutral, private-domain relations DTOs (no case plaintext)."""
from __future__ import annotations
from dataclasses import dataclass, replace
from typing import Literal, Optional, Tuple

IssueKind = Literal['WORK_DISAGREEMENT', 'COMMUNICATION_FRICTION', 'TARGETED_CONDUCT', 'IMPERSONATION', 'SECURITY_RISK']
Route = Literal['PRODUCTION_HEAD', 'MEDIATION', 'HR_TRIAGE', 'INCIDENT_REVIEW', 'EMERGENCY_GATE_REVIEW', 'SECURITY_REVIEW_PENDING']
EvidenceClass = Literal['CLAIMED', 'OBSERVED', 'CORROBORATED', 'INFERRED', 'DISPUTED', 'INVALID_UNREADABLE']
Disposition = Literal['REFERRED', 'OPEN', 'BLOCKED_INDEPENDENCE', 'NOTICE_PENDING', 'READY', 'RESOLVED', 'UNRESOLVED', 'APPEALED']
TERMS = frozenset({'USE_SOURCE_REFERENCES', 'ACKNOWLEDGE_HANDOFF', 'ESCALATE_THROUGH_HEAD', 'STRUCTURED_REVIEW', 'USE_NEUTRAL_LANGUAGE', 'CHECK_REQUEST_SCOPE'})

@dataclass(frozen=True)
class EvidenceRef:
    """Only externally managed, opaque evidence handles and content digests."""
    handle: str
    sha256: str

@dataclass(frozen=True)
class IssueReport:
    case_id: str
    reporter_id: str
    subject_id: str
    responsible_head_id: str
    kind: IssueKind
    evidence: Tuple[EvidenceRef, ...] = ()

@dataclass(frozen=True)
class EvidenceAssessment:
    """Result of injected custody/evidence verifier, never accepted from requester fields."""
    classification: EvidenceClass
    verified_impact: bool = False
    recurring_events: int = 0
    identity_abuse_verified: bool = False
    security_active_verified: bool = False

@dataclass(frozen=True)
class GateDecision:
    preauthorized: bool
    authorization_ref: Optional[str] = None

@dataclass(frozen=True)
class Referral:
    route: Route
    reason_code: str
    assessment: EvidenceClass
    emergency_gate_proof_ref: Optional[str] = None

@dataclass(frozen=True)
class Mediator:
    worker_id: str
    qualified: bool
    active: bool
    authorized_cases: Tuple[str, ...]
    conflict_workers: Tuple[str, ...] = ()
    conflict_cases: Tuple[str, ...] = ()
    prior_case_roles: Tuple[str, ...] = ()

@dataclass(frozen=True)
class Response:
    worker_id: str
    evidence: EvidenceRef
    kind: Literal['COUNTERSTATEMENT', 'FACT_RECORD']

@dataclass(frozen=True)
class Appeal:
    appeal_id: str
    submitted_by: str
    new_evidence: EvidenceRef
    reviewer_id: str
    status: Literal['PENDING_PRIVATE_APPEAL_INTEGRATION'] = 'PENDING_PRIVATE_APPEAL_INTEGRATION'

@dataclass(frozen=True)
class AuditEntry:
    action: str
    actor_id: str
    revision: int
    reference: Optional[str] = None

@dataclass(frozen=True)
class Session:
    session_id: str
    request_id: str
    request_fingerprint: str
    issue: IssueReport
    referral: Referral
    status: Disposition = 'REFERRED'
    revision: int = 1
    mediator_id: Optional[str] = None
    participation: Tuple[Tuple[str, str], ...] = ()
    responses: Tuple[Response, ...] = ()
    working_terms: Tuple[str, ...] = ()
    followup_checkpoint_ref: Optional[str] = None
    notice_receipt_ref: Optional[str] = None
    escalation_route: Optional[Route] = None
    appeals: Tuple[Appeal, ...] = ()
    audit: Tuple[AuditEntry, ...] = ()

@dataclass(frozen=True)
class Result:
    status: Literal['OK', 'REPLAY', 'DENIED', 'UNSUPPORTED', 'CONFLICT', 'INVALID', 'BLOCKED_INDEPENDENCE', 'NOT_FOUND']
    reason: str
    session_id: Optional[str] = None
    revision: Optional[int] = None
    route: Optional[Route] = None
    # This is a private-only projection of metadata, never evidence text.
    private_session: Optional[Session] = None


def advanced(session: Session, action: str, actor_id: str, **changes: object) -> Session:
    revision = session.revision + 1
    return replace(session, revision=revision, audit=session.audit + (AuditEntry(action, actor_id, revision),), **changes)
