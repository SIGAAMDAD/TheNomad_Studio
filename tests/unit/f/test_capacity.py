from datetime import datetime, timedelta, timezone
import pytest
from studio.operations.capacity import (Admission, CapacityError, Entitlement,
                                         EvidenceState, Profile, admit_optional, select_profile)

NOW = datetime(2026, 10, 30, tzinfo=timezone.utc)


def entitlement(**kwargs):
    vals = dict(provider="github", resource="actions", account_scope="user:owner",
                state=EvidenceState.VERIFIED, observed_at=NOW - timedelta(minutes=2),
                expires_at=NOW + timedelta(days=1), remaining=200, evidence_ref="receipt:verified")
    vals.update(kwargs)
    return Entitlement(**vals)


def check(**kwargs):
    vals = dict(profile=Profile.STUDENT_ENHANCED, entitlement=entitlement(), provider="github",
                resource="actions", account_scope="user:owner", units=3, now=NOW)
    vals.update(kwargs)
    return admit_optional(**vals)


def test_free_profile_never_uses_student_credit():
    assert not check(profile=Profile.FREE_BASELINE).accepted
    assert select_profile({}) == Profile.FREE_BASELINE


def test_verified_fresh_credit_is_only_precondition():
    answer = check()
    assert answer.accepted and answer.code == "eligible_requires_atomic_reservation"


@pytest.mark.parametrize("variant,expected", [
    ({"entitlement": None}, "entitlement_unknown"),
    ({"entitlement": entitlement(state=EvidenceState.UNKNOWN)}, "entitlement_unknown"),
    ({"entitlement": entitlement(state=EvidenceState.DENIED)}, "entitlement_denied"),
    ({"entitlement": entitlement(state=EvidenceState.EXPIRED)}, "entitlement_expired"),
    ({"entitlement": entitlement(observed_at=NOW - timedelta(hours=1))}, "observation_stale"),
    ({"entitlement": entitlement(expires_at=NOW)}, "entitlement_expired"),
    ({"entitlement": entitlement(remaining=2)}, "insufficient_verified_capacity"),
    ({"account_scope": "org:someone-else"}, "scope_mismatch"),
])
def test_unsafe_optional_spending_is_rejected(variant, expected):
    outcome = check(**variant)
    assert not outcome.accepted and outcome.code == expected


def test_invalid_profile_rejected():
    with pytest.raises(CapacityError):
        select_profile({"STUDIO_CAPACITY_PROFILE": "STUDENT_WITH_PAID_OVERAGE"})


def test_broken_verified_evidence_rejected():
    with pytest.raises(CapacityError):
        entitlement(evidence_ref=None)
