import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from oversight_development.api import ReviewWorkerRequest, build_component
from oversight_development.competency import review_worker
from oversight_development.models import (
    CompetencyPolicy, CompetencySample, Dimension, EvidenceRef, Origin,
    ReviewStatus, Verification, VerificationReceipt,
)

NOW = datetime(2026, 10, 29, 0, 0, tzinfo=timezone.utc)


class FakeVerifier:
    """Synthetic fixture verifier, never production observation proof."""
    def __init__(self, state=Verification.VERIFIED):
        self.state = state
        self.calls = 0

    def verify(self, evidence):
        self.calls += 1
        if self.state == "FAULT":
            raise RuntimeError("synthetic verifier outage")
        if self.state is Verification.VERIFIED:
            return VerificationReceipt(Verification.VERIFIED, evidence.claimed_sha256,
                                       "synthetic-receipt:" + evidence.reference_id)
        return VerificationReceipt(self.state)

    def verify_sample(self, sample):
        receipt = self.verify(sample.evidence)
        return replace(receipt, observed_measurements=dict(sample.measurements))


class WrongDigestVerifier(FakeVerifier):
    def verify(self, evidence):
        return VerificationReceipt(Verification.VERIFIED, "f" * 64, "bad-digest-fixture")


def policy():
    return CompetencyPolicy("integration", "source-qa", "cohort-A", 3, 2, 2, 45, 3, 0.75)


def samples(*, fail=None):
    fail = fail or {}
    output = []
    for i in range(3):
        ms = {k: not (k in fail and i in fail[k]) for k in Dimension}
        output.append(CompetencySample(
            "sample-" + str(i), "worker-7", "integration", "source-qa", "cohort-A",
            NOW - timedelta(days=3 - i), EvidenceRef("ref-" + str(i), f"{i+1:064x}"), ms,
        ))
    return tuple(output)


def run_review(data=None, verifier=None, **kwargs):
    return review_worker(worker_id="worker-7", assignment_epoch=4, policy=policy(),
                         samples=samples() if data is None else data, as_of=NOW,
                         verifier=verifier if verifier is not None else FakeVerifier(), **kwargs)


class CompetencyTests(unittest.TestCase):
    def test_six_dimension_review_passes_sufficient_verified_samples(self):
        review = run_review()
        self.assertEqual(review.status, ReviewStatus.REVIEWABLE)
        self.assertEqual(len(review.dimensions), 6)
        self.assertEqual(len(review.evidence_digest), 64)
        self.assertEqual(review.deficits, ())
        self.assertEqual(review.distinct_artifacts, 3)

    def test_review_evidence_digest_order_independent(self):
        self.assertEqual(run_review().evidence_digest,
                         run_review(data=tuple(reversed(samples()))).evidence_digest)

    def test_review_insufficient_for_two_samples(self):
        review = run_review(data=samples()[:2])
        self.assertEqual(review.status, ReviewStatus.INSUFFICIENT_EVIDENCE)
        self.assertEqual(review.dimensions, {})
        self.assertIsNone(review.evidence_digest)

    def test_missing_dimension_denies_review(self):
        altered = tuple(replace(x, measurements={k: v for k, v in x.measurements.items()
                             if k is not Dimension.PEER_HANDOFF_CLARITY}) for x in samples())
        review = run_review(data=altered)
        self.assertEqual(review.status, ReviewStatus.INSUFFICIENT_EVIDENCE)
        self.assertIn(Dimension.PEER_HANDOFF_CLARITY, review.missing_dimensions)

    def test_unavailable_provider_is_unsupported_not_worker_failure(self):
        review = review_worker(worker_id="worker-7", assignment_epoch=4,
                               policy=policy(), samples=samples(), as_of=NOW, verifier=None)
        self.assertEqual(review.status, ReviewStatus.UNSUPPORTED)
        self.assertEqual(review.accepted_samples, 0)
        self.assertFalse(review.deficits)

    def test_provider_fault_is_unsupported_not_worker_failure(self):
        review = run_review(verifier=FakeVerifier("FAULT"))
        self.assertEqual(review.status, ReviewStatus.UNSUPPORTED)
        self.assertIn("evidence_provider_error", review.notes)

    def test_rejected_evidence_does_not_count(self):
        self.assertEqual(run_review(verifier=FakeVerifier(Verification.REJECTED)).status,
                         ReviewStatus.INSUFFICIENT_EVIDENCE)

    def test_wrong_digest_does_not_verify(self):
        self.assertEqual(run_review(verifier=WrongDigestVerifier()).accepted_samples, 0)

    def test_environment_failure_not_attributed_to_worker(self):
        data = list(samples())
        data[0] = replace(data[0], failure_origin=Origin.ENVIRONMENT,
                          attribution_ref="verified-host-outage-ref",
                          measurements={k: False for k in Dimension})
        review = run_review(data=data)
        self.assertEqual(review.accepted_samples, 2)
        self.assertEqual(review.environment_excluded, 1)
        self.assertEqual(review.status, ReviewStatus.INSUFFICIENT_EVIDENCE)
        self.assertEqual(review.dimensions, {})

    def test_source_conflict_not_attributed_to_worker(self):
        data = list(samples())
        data[0] = replace(data[0], failure_origin=Origin.SOURCE_CONFLICT,
                          attribution_ref="source-merge-conflict-ref")
        self.assertIn("excluded_source_conflict", run_review(data=data).notes)

    def test_role_mismatch_not_comparable(self):
        data = list(samples())
        data[0] = replace(data[0], role="different-role")
        review = run_review(data=data)
        self.assertEqual(review.status, ReviewStatus.INSUFFICIENT_EVIDENCE)
        self.assertEqual(review.noncomparable, 1)

    def test_stale_evidence_not_comparable(self):
        data = list(samples())
        data[0] = replace(data[0], observed_at=NOW - timedelta(days=100))
        self.assertEqual(run_review(data=data).noncomparable, 1)

    def test_replayed_sample_id_denied(self):
        with self.assertRaisesRegex(ValueError, "replayed sample"):
            run_review(data=samples() + (samples()[0],))

    def test_duplicate_evidence_reference_denied(self):
        data = list(samples())
        data[2] = replace(data[2], evidence=data[1].evidence)
        with self.assertRaisesRegex(ValueError, "replayed evidence"):
            run_review(data=data)

    def test_attested_rubric_measurements_must_match_claimed(self):
        class TamperedRubric(FakeVerifier):
            def verify_sample(self, sample):
                receipt = super().verify_sample(sample)
                observed = dict(receipt.observed_measurements)
                observed[Dimension.SOURCE_CORRECTNESS] = False
                return replace(receipt, observed_measurements=observed)
        review = run_review(verifier=TamperedRubric())
        self.assertEqual(review.accepted_samples, 0)
        self.assertEqual(review.status, ReviewStatus.INSUFFICIENT_EVIDENCE)

    def test_source_sha_must_be_independently_observed(self):
        data = list(samples())
        data[0] = replace(data[0], evidence=replace(data[0].evidence,
                          source_sha256="a" * 64))
        review = run_review(data=data)
        self.assertEqual(review.accepted_samples, 2)
        self.assertEqual(review.status, ReviewStatus.INSUFFICIENT_EVIDENCE)

    def test_review_worker_api_candidate(self):
        component = build_component({"evidence_verifier": FakeVerifier()})
        result = component.execute("hr.review_worker", ReviewWorkerRequest(
            "worker-7", 4, policy(), samples(), NOW))
        self.assertEqual(result.status, "REVIEWABLE")
        self.assertEqual(component.execute("hr.retire_worker", {}).status, "UNSUPPORTED")

    def test_bad_policy_values_denied(self):
        with self.assertRaises(ValueError):
            CompetencyPolicy("a", "b", "c", 0, 1, 1, 90, 1, 0.7)

    def test_bad_non_worker_attribution_denied(self):
        with self.assertRaises(ValueError):
            replace(samples()[0], failure_origin=Origin.ENVIRONMENT)

    def test_no_scalar_worker_ranking(self):
        self.assertFalse(hasattr(run_review(), "worker_rank"))
        self.assertFalse(hasattr(run_review(), "personality_score"))


if __name__ == "__main__":
    unittest.main()
