"""Minimal NOMAD/HR/RECOMMENDATION/1 candidate projection.

Matches the bundled public reference enum/fields but conveys no real owner
approval, private-case access or signing. P2 must confirm actual schema bytes.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from .coaching import CoachingPlan, RequalificationResult
from .models import CompetencyReview, ReviewStatus, digest_ok
from .persona import PersonaRevisionRequest

ALLOWED = frozenset({"COACH", "RETEST", "REVIEW_PERSONA", "NO_ACTION"})


@dataclass(frozen=True)
class HRRecommendation:
    version: str
    recommendation_id: str
    subject_worker_id: str
    observed_evidence_digest: str
    recommendation: str
    reason_class: str
    current_assignment_epoch: int
    policy_revision: int
    requires_owner: bool
    effect_status: str
    case_id: None = None
    owner_decision_ref: None = None

    def __post_init__(self) -> None:
        if (self.version != "NOMAD/HR/RECOMMENDATION/1" or
                self.recommendation not in ALLOWED or
                self.reason_class not in {"CAPABILITY", "QUALIFICATION", "INSUFFICIENT_EVIDENCE"} or
                self.effect_status != "PROPOSED" or
                not self.requires_owner or not digest_ok(self.observed_evidence_digest) or
                self.current_assignment_epoch < 0 or self.policy_revision < 1):
            raise ValueError("invalid provisional HR recommendation fields")

    def to_dict(self) -> dict[str, object]:
        return vars(self).copy()


def from_review(review: CompetencyReview, plan: CoachingPlan | None = None,
                requalification: RequalificationResult | None = None) -> HRRecommendation:
    if review.status is not ReviewStatus.REVIEWABLE or not review.evidence_digest:
        raise ValueError("cannot recommend personnel action on insufficient evidence")
    if plan is not None and (plan.worker_id != review.worker_id or
                             plan.assignment_epoch != review.assignment_epoch or
                             plan.policy_revision != review.policy.policy_revision):
        raise ValueError("inconsistent coaching provenance")
    if requalification is not None and (plan is None or
                                        requalification.plan_id != plan.plan_id):
        raise ValueError("requalification must refer to current coaching plan")
    recommendation = ("RETEST" if requalification is not None and
                      requalification.status != "REQUALIFIED_ON_EVIDENCE" else
                      "NO_ACTION" if not review.deficits else "COACH")
    canonical = [review.worker_id, review.assignment_epoch, review.policy.policy_revision,
                 review.evidence_digest, recommendation, plan.plan_id if plan else None,
                 requalification.status if requalification else None]
    rec_id = "rec-" + hashlib.sha256(json.dumps(canonical,
                 separators=(",", ":")).encode()).hexdigest()
    return HRRecommendation("NOMAD/HR/RECOMMENDATION/1", rec_id, review.worker_id,
                            review.evidence_digest, recommendation, "CAPABILITY",
                            review.assignment_epoch, review.policy.policy_revision,
                            True, "PROPOSED")


def from_persona_request(request: PersonaRevisionRequest,
                         *, assignment_epoch: int, policy_revision: int) -> HRRecommendation:
    if request.status != "OWNER_REVIEW_REQUIRED":
        raise ValueError("only pending persona request can be recommended")
    return HRRecommendation("NOMAD/HR/RECOMMENDATION/1", request.request_id,
                            request.worker_id, request.reason_evidence_digest,
                            "REVIEW_PERSONA", "QUALIFICATION", assignment_epoch,
                            policy_revision, True, "PROPOSED")
