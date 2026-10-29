"""Injected private-authority interfaces; no host, secret or provider implementation."""
from __future__ import annotations
from typing import Protocol, Optional, Sequence
from .models import IssueReport, EvidenceAssessment, GateDecision, Mediator, Session

class CaseAccess(Protocol):
    def permit(self, authenticated_principal: str, case_id: str, operation: str) -> bool: ...

class EvidenceAuthority(Protocol):
    def assess(self, issue: IssueReport) -> EvidenceAssessment: ...

class EmergencyGate(Protocol):
    def validate(self, issue: IssueReport, assessment: EvidenceAssessment) -> GateDecision: ...

class MediatorRegistry(Protocol):
    def candidates(self, case_id: str) -> Sequence[Mediator]: ...

class NoticeAuthority(Protocol):
    def verify_delivery(self, case_id: str, notice_receipt_ref: str, parties: tuple[str, str]) -> bool: ...

class RelationsStore(Protocol):
    def create(self, session: Session) -> tuple[str, Optional[Session]]: ...
    def read(self, session_id: str) -> Optional[Session]: ...
    def update(self, previous_revision: int, changed: Session) -> bool: ...

class PortUnavailable(RuntimeError):
    """Absent or failing protected dependency: never interpreted as permission."""
