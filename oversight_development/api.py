"""P1 clean-room equivalent of build_component(deps).

Only hr.review_worker and hr.coaching_plan are public integration candidates.
The additional typed pure methods are optional internal helpers. No gateway,
filesystem, case store, registry, credential, or transport writes occur here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping

from .coaching import CoachingAction, CoachingPlan, make_coaching_plan, requalify
from .competency import review_worker
from .conduct import ConductAssessment, ConductObservation, assess_conduct
from .models import (CompetencyPolicy, CompetencyReview, CompetencySample,
                     DevelopmentResult, Dimension, EvidencePort)
from .persona import PersonaRegistryPort, propose_persona_revision


@dataclass(frozen=True)
class ReviewWorkerRequest:
    worker_id: str
    assignment_epoch: int
    policy: CompetencyPolicy
    samples: tuple[CompetencySample, ...]
    as_of: datetime


@dataclass(frozen=True)
class CoachingPlanRequest:
    review: CompetencyReview
    fixtures: Mapping[Dimension, str]
    due_checkpoint: str
    created_at: datetime
    actions: tuple[CoachingAction, ...] = (
        CoachingAction.IMPROVE_INSTRUCTIONS,
        CoachingAction.UPDATE_SKILL_REFERENCE,
        CoachingAction.SCHEDULE_REQUALIFICATION,
    )


class DevelopmentComponent:
    """Effect-free local facade. Read-only adapter methods are injected, not owned."""

    def __init__(self, evidence: EvidencePort | None = None,
                 persona: PersonaRegistryPort | None = None) -> None:
        self._evidence = evidence
        self._persona = persona

    def review_worker(self, request: ReviewWorkerRequest) -> CompetencyReview:
        if not isinstance(request, ReviewWorkerRequest):
            raise TypeError("ReviewWorkerRequest required")
        return review_worker(worker_id=request.worker_id,
                             assignment_epoch=request.assignment_epoch,
                             policy=request.policy, samples=request.samples,
                             as_of=request.as_of, verifier=self._evidence)

    def coaching_plan(self, request: CoachingPlanRequest) -> CoachingPlan:
        if not isinstance(request, CoachingPlanRequest):
            raise TypeError("CoachingPlanRequest required")
        return make_coaching_plan(request.review, fixtures=request.fixtures,
                                  due_checkpoint=request.due_checkpoint,
                                  created_at=request.created_at,
                                  actions=request.actions)

    def propose_persona(self, **kwargs: object) -> DevelopmentResult:
        return propose_persona_revision(registry=self._persona, **kwargs)

    def assess_conduct(self, observations: Iterable[ConductObservation]) -> ConductAssessment:
        return assess_conduct(observations, verifier=self._evidence)

    def requalify(self, plan: CoachingPlan, latest: CompetencyReview):
        return requalify(plan, latest)

    def execute(self, operation: str, request: object) -> DevelopmentResult:
        if operation == "hr.review_worker":
            if not isinstance(request, ReviewWorkerRequest):
                return DevelopmentResult("DENIED", "invalid typed review request")
            result = self.review_worker(request)
            return DevelopmentResult(result.status.value, "nonprivileged local review only", result)
        if operation == "hr.coaching_plan":
            if not isinstance(request, CoachingPlanRequest):
                return DevelopmentResult("DENIED", "invalid typed coaching request")
            try:
                plan = self.coaching_plan(request)
            except ValueError as exc:
                return DevelopmentResult("DENIED", str(exc))
            return DevelopmentResult("PROPOSED", "plan has no personnel or registry effect", plan)
        return DevelopmentResult("UNSUPPORTED", "operation not admitted by provisional facade")


def build_component(deps: Mapping[str, object]) -> DevelopmentComponent:
    """Only inject explicitly supplied read-only ports; absence fails closed."""
    evidence = deps.get("evidence_verifier")
    persona = deps.get("persona_registry_reader")
    if evidence is not None and not all(callable(getattr(evidence, name, None))
                                        for name in ("verify", "verify_sample")):
        raise TypeError("evidence_verifier must implement verify and verify_sample")
    if persona is not None and not callable(getattr(persona, "read_current", None)):
        raise TypeError("persona_registry_reader must implement read_current")
    return DevelopmentComponent(evidence=evidence, persona=persona)
