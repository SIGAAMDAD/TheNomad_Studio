"""Evidence-gated competency rubric; no scalar worker rank or HR sanction."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Iterable

from .models import (
    CompetencyPolicy, CompetencyReview, CompetencySample, DimensionResult,
    EvidencePort, ReviewStatus, Verification, utc_now_guard,
)


def review_worker(
    *, worker_id: str, assignment_epoch: int, policy: CompetencyPolicy,
    samples: Iterable[CompetencySample], as_of: datetime,
    verifier: EvidencePort | None,
) -> CompetencyReview:
    utc_now_guard(as_of)
    if not worker_id or assignment_epoch < 0:
        raise ValueError("worker id and nonnegative assignment epoch required")
    accepted = []
    rejected = 0
    noncomparable = 0
    environmental = 0
    unsupported = False
    ids: set[str] = set()
    observations: set[tuple[str, str]] = set()
    verification_refs: dict[str, str] = {}
    notes: set[str] = set()
    for sample in samples:
        if sample.sample_id in ids:
            raise ValueError("replayed sample id")
        ids.add(sample.sample_id)
        if sample.worker_id != worker_id or (sample.role, sample.specialty, sample.cohort) != (
            policy.role, policy.specialty, policy.cohort
        ):
            noncomparable += 1
            continue
        age = as_of - sample.observed_at
        if age < timedelta(0) or age > timedelta(days=policy.max_age_days):
            noncomparable += 1
            continue
        if verifier is None:
            unsupported = True
            continue
        try:
            receipt = verifier.verify_sample(sample)
        except Exception:
            # Provider failure is not worker fault and never promotes unverified data.
            unsupported = True
            notes.add("evidence_provider_error")
            continue
        if receipt.status is Verification.UNSUPPORTED:
            unsupported = True
            continue
        if (receipt.status is not Verification.VERIFIED or
                receipt.observed_measurements is None or
                dict(receipt.observed_measurements) != dict(sample.measurements) or
                receipt.observed_sha256 != sample.evidence.claimed_sha256 or
                (sample.evidence.source_sha256 is not None and
                 receipt.observed_source_sha256 != sample.evidence.source_sha256)):
            rejected += 1
            continue
        # Distinct observations may share an artifact if they independently
        # measure different operations, but duplicate proof refs are replay.
        observed_key = (sample.evidence.reference_id, sample.evidence.claimed_sha256)
        if observed_key in observations:
            raise ValueError("replayed evidence reference")
        observations.add(observed_key)
        verification_refs[sample.sample_id] = receipt.observation_ref
        if sample.failure_origin.value != "WORKER":
            environmental += 1
            notes.add("excluded_" + sample.failure_origin.value.lower())
            continue
        accepted.append(sample)
    accepted.sort(key=lambda item: item.sample_id)
    artifacts = len({item.evidence.claimed_sha256 for item in accepted})
    days = len({item.observed_at.astimezone(timezone.utc).date() for item in accepted})
    counts = {
        key: [sum(item.measurements.get(key) is not None for item in accepted),
              sum(item.measurements.get(key) is True for item in accepted)]
        for key in policy.required_dimensions
    }
    missing = tuple(k for k, (observed, _) in counts.items()
                    if observed < policy.min_observations_per_dimension)
    enough = (len(accepted) >= policy.min_samples and
              artifacts >= policy.min_distinct_artifacts and
              days >= policy.min_distinct_days and not missing)
    status = (ReviewStatus.REVIEWABLE if enough else
              ReviewStatus.UNSUPPORTED if unsupported else ReviewStatus.INSUFFICIENT_EVIDENCE)
    dimensions = {}
    evidence_digest = None
    if enough:
        dimensions = {
            key: DimensionResult(
                observed=values[0], passed=values[1],
                pass_fraction=values[1] / values[0],
                meets_rubric=values[1] / values[0] >= policy.passing_fraction,
            ) for key, values in counts.items()
        }
        # Include policy identity and sample proof; stable digest despite input order.
        canonical = {
            "worker_id": worker_id, "assignment_epoch": assignment_epoch,
            "policy_revision": policy.policy_revision,
            "policy_cohort": [policy.role, policy.specialty, policy.cohort],
            "thresholds": [policy.min_samples, policy.min_distinct_artifacts,
                           policy.min_distinct_days, policy.max_age_days,
                           policy.min_observations_per_dimension, policy.passing_fraction],
            "samples": [
                [s.sample_id, s.evidence.reference_id, s.evidence.claimed_sha256,
                 s.evidence.source_sha256, verification_refs[s.sample_id],
                 s.observed_at.isoformat(), sorted((k.value, v) for k, v in s.measurements.items())]
                for s in accepted
            ],
        }
        evidence_digest = hashlib.sha256(json.dumps(canonical, sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()
    if unsupported:
        notes.add("verification_unavailable_or_incomplete")
    if not enough:
        notes.add("insufficient_comparable_verified_evidence")
    return CompetencyReview(
        worker_id=worker_id, assignment_epoch=assignment_epoch, policy=policy,
        status=status, as_of=as_of, accepted_samples=len(accepted),
        distinct_artifacts=artifacts, distinct_days=days,
        rejected_or_unverified=rejected, noncomparable=noncomparable,
        environment_excluded=environmental, missing_dimensions=missing,
        dimensions=dimensions, evidence_digest=evidence_digest,
        sample_ids=tuple(s.sample_id for s in accepted), notes=tuple(sorted(notes)),
    )
