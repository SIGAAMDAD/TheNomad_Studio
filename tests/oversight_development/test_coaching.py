import unittest
from dataclasses import replace
from datetime import timedelta

from oversight_development.api import CoachingPlanRequest, build_component
from oversight_development.coaching import CoachingAction, make_coaching_plan, requalify
from oversight_development.models import Dimension, ReviewStatus
from test_competency import NOW, run_review, samples


class CoachingTests(unittest.TestCase):
    def setUp(self):
        self.dim = Dimension.SOURCE_CORRECTNESS
        from test_competency import policy, FakeVerifier
        from oversight_development.competency import review_worker
        data = list(samples())
        for i in (0, 1):
            data[i] = replace(data[i], measurements={**data[i].measurements, self.dim: False})
        self.review = review_worker(worker_id="worker-7", assignment_epoch=4,
                                    policy=policy(), samples=data, as_of=NOW,
                                    verifier=FakeVerifier())
        self.assertEqual(self.review.deficits, (self.dim,))

    def plan(self):
        return make_coaching_plan(self.review, fixtures={self.dim: "fixture-source-v3"},
                                  due_checkpoint="checkpoint-Q4", created_at=NOW)

    def test_plan_has_bound_retest_and_no_direct_effect(self):
        plan = self.plan()
        self.assertEqual(plan.disposition, "OPEN")
        self.assertEqual(len(plan.criteria), 1)
        self.assertEqual(plan.criteria[0].dimension, self.dim)
        self.assertEqual(plan.criteria[0].fixture_id, "fixture-source-v3")
        self.assertFalse(hasattr(plan, "grant_role"))
        self.assertIn(CoachingAction.SCHEDULE_REQUALIFICATION, plan.actions)

    def test_deterministic_plan_id(self):
        self.assertEqual(self.plan().plan_id, self.plan().plan_id)

    def test_insufficient_review_cannot_generate_coaching(self):
        with self.assertRaisesRegex(ValueError, "verified review"):
            make_coaching_plan(replace(self.review, status=ReviewStatus.INSUFFICIENT_EVIDENCE),
                               fixtures={self.dim: "f"}, due_checkpoint="Q4", created_at=NOW)

    def test_missing_retest_fixture_denied(self):
        with self.assertRaisesRegex(ValueError, "fixture"):
            make_coaching_plan(self.review, fixtures={}, due_checkpoint="Q4", created_at=NOW)

    def test_unbounded_coaching_action_denied(self):
        with self.assertRaises(ValueError):
            make_coaching_plan(self.review, fixtures={self.dim: "f"},
                               due_checkpoint="Q4", created_at=NOW,
                               actions=("PROMOTE",))

    def test_requalification_requires_new_evidence(self):
        with self.assertRaisesRegex(ValueError, "replayed"):
            requalify(self.plan(), self.review)

    def test_requalification_recommendation_no_promotion(self):
        from test_competency import policy, FakeVerifier
        from oversight_development.competency import review_worker
        new_data = tuple(replace(s, sample_id="retest-" + s.sample_id,
                                 evidence=replace(s.evidence, reference_id="retest-" + s.evidence.reference_id,
                                                  claimed_sha256=f"{i+10:064x}"))
                         for i, s in enumerate(samples()))
        latest = review_worker(worker_id="worker-7", assignment_epoch=4,
                               policy=policy(), samples=new_data, as_of=NOW,
                               verifier=FakeVerifier())
        outcome = requalify(self.plan(), latest)
        self.assertEqual(outcome.status, "REQUALIFIED_ON_EVIDENCE")
        self.assertEqual(outcome.recommendation, "OWNER_REVIEW_ONLY")
        self.assertFalse(hasattr(outcome, "new_role"))

    def test_changed_epoch_denied(self):
        with self.assertRaisesRegex(ValueError, "epoch"):
            requalify(self.plan(), replace(self.review, assignment_epoch=5))

    def test_coaching_api_does_not_enact(self):
        component = build_component({})
        result = component.execute("hr.coaching_plan", CoachingPlanRequest(
            self.review, {self.dim: "fixture-source-v3"}, "Q4", NOW))
        self.assertEqual(result.status, "PROPOSED")
        self.assertEqual(result.value.disposition, "OPEN")
        self.assertEqual(component.execute("hr.coaching_plan", object()).status, "DENIED")


if __name__ == "__main__":
    unittest.main()
