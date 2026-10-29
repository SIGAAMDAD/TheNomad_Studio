"""Classify observable conduct for routing; no mind-reading or sanctions."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .models import EvidencePort, EvidenceRef, Verification


class Behavior(str, Enum):
    EVIDENCED_SKEPTICISM = "EVIDENCED_SKEPTICISM"
    PROFESSIONAL_EXPLORATION = "PROFESSIONAL_EXPLORATION"
    TECHNICAL_DISAGREEMENT = "TECHNICAL_DISAGREEMENT"
    UNPROFESSIONAL_COMMUNICATION = "UNPROFESSIONAL_COMMUNICATION"
    FORGED_AUTHORITY = "FORGED_AUTHORITY"
    UNAUTHORIZED_CHANGE = "UNAUTHORIZED_CHANGE"
    UNSUPPORTED_ALLEGATION = "UNSUPPORTED_ALLEGATION"


@dataclass(frozen=True)
class ConductObservation:
    observation_id: str
    worker_id: str
    behavior: Behavior
    evidence: EvidenceRef
    context_ref: str

    def __post_init__(self) -> None:
        if not self.observation_id or not self.worker_id or not self.context_ref:
            raise ValueError("observation, worker and context references are required")


@dataclass(frozen=True)
class ConductAssessment:
    route: str
    verified_observation_ids: tuple[str, ...]
    unverified_count: int
    reasons: tuple[str, ...]
    authorization_effect: str = "NONE"


def assess_conduct(
    observations: Iterable[ConductObservation], *, verifier: EvidencePort | None,
) -> ConductAssessment:
    ids: set[str] = set()
    verified: list[ConductObservation] = []
    unverified = 0
    provider_failed = False
    for item in observations:
        if item.observation_id in ids:
            raise ValueError("replayed conduct observation")
        ids.add(item.observation_id)
        if verifier is None:
            provider_failed = True
            unverified += 1
            continue
        try:
            receipt = verifier.verify(item.evidence)
        except Exception:
            provider_failed = True
            unverified += 1
            continue
        if receipt.status is Verification.VERIFIED and receipt.observed_sha256 == item.evidence.claimed_sha256:
            verified.append(item)
        else:
            provider_failed |= receipt.status is Verification.UNSUPPORTED
            unverified += 1
    kinds = {x.behavior for x in verified}
    if kinds & {Behavior.FORGED_AUTHORITY, Behavior.UNAUTHORIZED_CHANGE}:
        route, reasons = "SECURITY_REVIEW_RECOMMENDED", ("verified protected-conduct observation",)
    elif Behavior.UNPROFESSIONAL_COMMUNICATION in kinds:
        route, reasons = "CONDUCT_REVIEW_RECOMMENDED", ("observed communication subject to human/context review",)
    elif kinds & {Behavior.EVIDENCED_SKEPTICISM, Behavior.PROFESSIONAL_EXPLORATION,
                   Behavior.TECHNICAL_DISAGREEMENT}:
        route, reasons = "TECHNICAL_REVIEW", ("professional disagreement is not itself a conduct sanction",)
    else:
        route, reasons = "INSUFFICIENT_EVIDENCE", ("no verified actionable observation",)
    if provider_failed:
        reasons += ("evidence verification unavailable for some observations",)
    return ConductAssessment(route, tuple(sorted(x.observation_id for x in verified)),
                             unverified, reasons)
