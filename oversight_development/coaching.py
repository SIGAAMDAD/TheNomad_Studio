"""Bounded coaching and requalification; recommendation-only pure transformations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json
from typing import Mapping

from .models import CompetencyReview, Dimension, ReviewStatus, utc_now_guard


class CoachingAction(str, Enum):
    IMPROVE_INSTRUCTIONS = "IMPROVE_INSTRUCTIONS"
    UPDATE_SKILL_REFERENCE = "UPDATE_SKILL_REFERENCE"
    SCHEDULE_REQUALIFICATION = "SCHEDULE_REQUALIFICATION"
    REQUEST_PERSONA_STYLE_REVIEW = "REQUEST_PERSONA_STYLE_REVIEW"


@dataclass(frozen=True)
class RetestCriterion:
    dimension: Dimension
    fixture_id: str
    required_pass_fraction: float
    minimum_observations: int


@dataclass(frozen=True)
class CoachingPlan:
    plan_id: str
    worker_id: str
    assignment_epoch: int
    policy_revision: int
    baseline_evidence_digest: str
    baseline_deficits: tuple[Dimension, ...]
    actions: tuple[CoachingAction, ...]
    criteria: tuple[RetestCriterion, ...]
    due_checkpoint: str
    created_at: datetime
    disposition: str = "OPEN"


@dataclass(frozen=True)
class RequalificationResult:
    plan_id: str
    status: str
    recommendation: str
    latest_evidence_digest: str | None
    # Intentionally no assignment mutation, promotion, or signed notice.


def make_coaching_plan(
    review: CompetencyReview, *, fixtures: Mapping[Dimension, str],
    due_checkpoint: str, created_at: datetime,
    actions: tuple[CoachingAction, ...] = (
        CoachingAction.IMPROVE_INSTRUCTIONS,
        CoachingAction.UPDATE_SKILL_REFERENCE,
        CoachingAction.SCHEDULE_REQUALIFICATION,
    ),
) -> CoachingPlan:
    utc_now_guard(created_at)
    if review.status is not ReviewStatus.REVIEWABLE or not review.evidence_digest:
        raise ValueError("verified review required before coaching")
    if not review.deficits:
        raise ValueError("no evidenced competency deficit to coach")
    if not due_checkpoint.strip() or not actions or len(set(actions)) != len(actions):
        raise ValueError("checkpoint and unique bounded coaching actions required")
    if any(not isinstance(x, CoachingAction) for x in actions):
        raise ValueError("unapproved coaching action")
    if created_at < review.as_of:
        raise ValueError("plan cannot predate reviewed evidence")
    criteria = []
    for dimension in review.deficits:
        fixture_id = fixtures.get(dimension)
        if not isinstance(fixture_id, str) or not fixture_id.strip():
            raise ValueError("each deficit requires an actual retest fixture reference")
        criteria.append(RetestCriterion(
            dimension, fixture_id, review.policy.passing_fraction,
            review.policy.min_observations_per_dimension,
        ))
    canonical = {
        "worker_id": review.worker_id, "epoch": review.assignment_epoch,
        "policy_revision": review.policy.policy_revision,
        "baseline": review.evidence_digest,
        "criteria": [(x.dimension.value, x.fixture_id, x.required_pass_fraction,
                      x.minimum_observations) for x in criteria],
        "actions": [x.value for x in actions], "checkpoint": due_checkpoint,
        "created": created_at.isoformat(),
    }
    plan_id = "coaching-" + hashlib.sha256(json.dumps(canonical, sort_keys=True,
                    separators=(",", ":")).encode()).hexdigest()
    return CoachingPlan(
        plan_id, review.worker_id, review.assignment_epoch, review.policy.policy_revision,
        review.evidence_digest, review.deficits, actions, tuple(criteria),
        due_checkpoint, created_at,
    )


def requalify(plan: CoachingPlan, latest: CompetencyReview) -> RequalificationResult:
    if plan.worker_id != latest.worker_id or plan.assignment_epoch != latest.assignment_epoch:
        raise ValueError("worker or assignment epoch changed; stale coaching plan")
    if plan.policy_revision != latest.policy.policy_revision:
        raise ValueError("policy revision changed; a fresh plan is required")
    if latest.status is not ReviewStatus.REVIEWABLE or not latest.evidence_digest:
        return RequalificationResult(plan.plan_id, "EVIDENCE_INSUFFICIENT", "RETEST", None)
    if plan.baseline_evidence_digest == latest.evidence_digest:
        raise ValueError("replayed baseline as requalification result")
    if any(not latest.dimensions[criterion.dimension].meets_rubric or
           latest.dimensions[criterion.dimension].observed < criterion.minimum_observations
           for criterion in plan.criteria):
        return RequalificationResult(plan.plan_id, "RETEST_REQUIRED", "COACH", latest.evidence_digest)
    return RequalificationResult(plan.plan_id, "REQUALIFIED_ON_EVIDENCE",
                                 "OWNER_REVIEW_ONLY", latest.evidence_digest)
