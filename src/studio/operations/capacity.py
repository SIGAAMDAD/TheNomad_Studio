"""Provider-neutral, no-overage capacity admission for optional Student resources.

Source of truth remains the existing Studio scheduler and C's transactional
reservation ledger; this module only validates a verified quota observation.
Unknown or expired optional entitlement never causes paid remote dispatch.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Mapping


class CapacityError(ValueError):
    pass


class Profile(str, Enum):
    FREE_BASELINE = "FREE_BASELINE"
    STUDENT_ENHANCED = "STUDENT_ENHANCED"


class EvidenceState(str, Enum):
    VERIFIED = "VERIFIED"
    UNKNOWN = "UNKNOWN"
    EXPIRED = "EXPIRED"
    DENIED = "DENIED"


@dataclass(frozen=True)
class Entitlement:
    provider: str
    resource: str
    account_scope: str
    state: EvidenceState
    observed_at: datetime
    expires_at: datetime | None
    remaining: int | None
    evidence_ref: str | None

    def __post_init__(self) -> None:
        for name in ("provider", "resource", "account_scope"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise CapacityError(f"{name} required")
        if self.observed_at.tzinfo is None:
            raise CapacityError("observation time must be timezone-aware")
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise CapacityError("expiry time must be timezone-aware")
        if self.remaining is not None and (type(self.remaining) is not int or self.remaining < 0):
            raise CapacityError("remaining must be a nonnegative integer")
        if self.state == EvidenceState.VERIFIED and not self.evidence_ref:
            raise CapacityError("verified entitlement must carry source evidence")


@dataclass(frozen=True)
class Admission:
    accepted: bool
    code: str
    profile: Profile
    provider: str
    resource: str
    units: int
    evidence_ref: str | None


def admit_optional(*, profile: Profile, entitlement: Entitlement | None,
                   provider: str, resource: str, account_scope: str,
                   units: int, now: datetime, maximum_observation_age_seconds: int = 900) -> Admission:
    """Fail closed on optional spend; does not authorize a charge or reserve budget.

    Caller must ALSO obtain an atomic existing F/C reservation. Admission is a
    local precondition only, never a remote effect or a proof of usable credit.
    """
    if not isinstance(profile, Profile):
        raise CapacityError("unknown profile")
    if any(not isinstance(x, str) or not x for x in (provider, resource, account_scope)):
        raise CapacityError("provider/resource/account scope required")
    if type(units) is not int or units <= 0:
        raise CapacityError("positive integer units required")
    if now.tzinfo is None:
        raise CapacityError("now must be timezone-aware")
    if type(maximum_observation_age_seconds) is not int or maximum_observation_age_seconds <= 0:
        raise CapacityError("observation age bound invalid")
    def no(code: str) -> Admission:
        return Admission(False, code, profile, provider, resource, units,
                         entitlement.evidence_ref if entitlement is not None else None)
    if profile == Profile.FREE_BASELINE:
        return no("optional_provider_disabled")
    if entitlement is None:
        return no("entitlement_unknown")
    if (entitlement.provider, entitlement.resource, entitlement.account_scope) != (provider, resource, account_scope):
        return no("scope_mismatch")
    if entitlement.state != EvidenceState.VERIFIED:
        return no("entitlement_" + entitlement.state.value.lower())
    age = (now - entitlement.observed_at).total_seconds()
    if age < 0 or age > maximum_observation_age_seconds:
        return no("observation_stale")
    if entitlement.expires_at is not None and now >= entitlement.expires_at:
        return no("entitlement_expired")
    if entitlement.remaining is None or entitlement.remaining < units:
        return no("insufficient_verified_capacity")
    return Admission(True, "eligible_requires_atomic_reservation", profile,
                     provider, resource, units, entitlement.evidence_ref)


def select_profile(environment: Mapping[str, str]) -> Profile:
    """Missing config defaults to free; misspellings fail instead of spending."""
    raw = environment.get("STUDIO_CAPACITY_PROFILE", Profile.FREE_BASELINE.value)
    try:
        return Profile(raw)
    except ValueError as exc:
        raise CapacityError("unsupported capacity profile") from exc
