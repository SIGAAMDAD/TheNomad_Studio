"""Project-neutral, pure contract types for provisional Oversight development.

A hash embedded in a sample is a *claim*, not proof: EvidencePort must observe
and verify it independently. None of these types authorize a personnel action.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import re
from typing import Mapping, Protocol

DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")


def digest_ok(value: str) -> bool:
    return isinstance(value, str) and bool(DIGEST_RE.fullmatch(value))


class Dimension(str, Enum):
    VALIDATED_TOOL_OPERATION = "validated_tool_operation"
    SOURCE_CORRECTNESS = "source_correctness"
    REPEATABILITY = "repeatability"
    REWORK_ATTRIBUTION = "rework_attribution"
    CHECKPOINT_INTEGRITY = "checkpoint_integrity"
    PEER_HANDOFF_CLARITY = "peer_handoff_clarity"


class Origin(str, Enum):
    WORKER = "WORKER"
    ENVIRONMENT = "ENVIRONMENT"
    SOURCE_CONFLICT = "SOURCE_CONFLICT"
    ROLE_MISMATCH = "ROLE_MISMATCH"
    UNKNOWN = "UNKNOWN"


class Verification(str, Enum):
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    UNSUPPORTED = "UNSUPPORTED"


class ReviewStatus(str, Enum):
    REVIEWABLE = "REVIEWABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class EvidenceRef:
    reference_id: str
    claimed_sha256: str
    source_sha256: str | None = None

    def __post_init__(self) -> None:
        if not self.reference_id or not digest_ok(self.claimed_sha256):
            raise ValueError("nonempty reference and SHA-256 evidence digest required")
        if self.source_sha256 is not None and not digest_ok(self.source_sha256):
            raise ValueError("source SHA-256 must be a digest")


@dataclass(frozen=True)
class VerificationReceipt:
    status: Verification
    observed_sha256: str | None = None
    observation_ref: str | None = None
    observed_source_sha256: str | None = None
    observed_measurements: Mapping[Dimension, bool | None] | None = None

    def __post_init__(self) -> None:
        if self.status is Verification.VERIFIED:
            if not digest_ok(self.observed_sha256) or not self.observation_ref:
                raise ValueError("verified evidence requires an independently observed digest/ref")
        if self.observed_source_sha256 is not None and not digest_ok(self.observed_source_sha256):
            raise ValueError("invalid independently observed source digest")
        if self.observed_measurements is not None and any(
            not isinstance(k, Dimension) or (v is not None and type(v) is not bool)
            for k, v in self.observed_measurements.items()
        ):
            raise ValueError("invalid independently observed rubric measurements")


class EvidencePort(Protocol):
    """Injected independent read-only artifact/source verifier.

    Callers must not transform untrusted model text into VERIFIED. P2 must bind a
    real authenticated verifier or keep outcomes UNSUPPORTED.
    """
    def verify(self, evidence: EvidenceRef) -> VerificationReceipt: ...

    def verify_sample(self, sample: CompetencySample) -> VerificationReceipt: ...


@dataclass(frozen=True)
class CompetencySample:
    sample_id: str
    worker_id: str
    role: str
    specialty: str
    cohort: str
    observed_at: datetime
    evidence: EvidenceRef
    measurements: Mapping[Dimension, bool | None]
    failure_origin: Origin = Origin.WORKER
    attribution_ref: str | None = None

    def __post_init__(self) -> None:
        if not all((self.sample_id, self.worker_id, self.role, self.specialty, self.cohort)):
            raise ValueError("sample identity and comparable cohort required")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("sample observation time must be timezone-aware")
        if not self.measurements or any(
            not isinstance(k, Dimension) or (v is not None and type(v) is not bool)
            for k, v in self.measurements.items()
        ):
            raise ValueError("measurements must be typed per-dimension observations")
        if self.failure_origin is not Origin.WORKER and not self.attribution_ref:
            raise ValueError("non-worker exclusion requires attributable evidence reference")


@dataclass(frozen=True)
class CompetencyPolicy:
    role: str
    specialty: str
    cohort: str
    min_samples: int
    min_distinct_artifacts: int
    min_distinct_days: int
    max_age_days: int
    min_observations_per_dimension: int
    passing_fraction: float
    required_dimensions: tuple[Dimension, ...] = tuple(Dimension)
    policy_revision: int = 1

    def __post_init__(self) -> None:
        if not all((self.role, self.specialty, self.cohort)):
            raise ValueError("role, specialty and cohort are required")
        if not (1 <= self.min_samples <= 100000 and
                1 <= self.min_distinct_artifacts <= self.min_samples and
                1 <= self.min_distinct_days <= self.min_samples and
                1 <= self.max_age_days <= 3650 and
                1 <= self.min_observations_per_dimension <= self.min_samples and
                0.0 < self.passing_fraction <= 1.0 and self.policy_revision >= 1):
            raise ValueError("invalid bounded review sample policy")
        if not self.required_dimensions or len(set(self.required_dimensions)) != len(self.required_dimensions):
            raise ValueError("required dimensions must be distinct and nonempty")
        if set(self.required_dimensions) != set(Dimension):
            raise ValueError("all six competency dimensions are mandatory")


@dataclass(frozen=True)
class DimensionResult:
    observed: int
    passed: int
    pass_fraction: float
    meets_rubric: bool


@dataclass(frozen=True)
class CompetencyReview:
    worker_id: str
    assignment_epoch: int
    policy: CompetencyPolicy
    status: ReviewStatus
    as_of: datetime
    accepted_samples: int
    distinct_artifacts: int
    distinct_days: int
    rejected_or_unverified: int
    noncomparable: int
    environment_excluded: int
    missing_dimensions: tuple[Dimension, ...]
    dimensions: Mapping[Dimension, DimensionResult]
    evidence_digest: str | None
    sample_ids: tuple[str, ...]
    notes: tuple[str, ...]

    @property
    def deficits(self) -> tuple[Dimension, ...]:
        if self.status is not ReviewStatus.REVIEWABLE:
            return ()
        return tuple(k for k in self.policy.required_dimensions if not self.dimensions[k].meets_rubric)


@dataclass(frozen=True)
class DevelopmentResult:
    status: str
    reason: str
    value: object | None = None


def utc_now_guard(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("expected timezone-aware date")
