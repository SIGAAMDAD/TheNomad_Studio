import unittest

from oversight_development.recommendations import from_review, from_persona_request
from oversight_development.models import ReviewStatus
from oversight_development.persona import PersonaRevisionRequest
from test_competency import run_review


class RecommendationTests(unittest.TestCase):
    def test_reference_schema_field_set_and_enums(self):
        # Schema is used at authoring time; the source ZIP stands alone without packet.
        rec = from_review(run_review())
        self.assertEqual(set(rec.to_dict()), {
            "version", "recommendation_id", "subject_worker_id", "observed_evidence_digest",
            "recommendation", "reason_class", "current_assignment_epoch",
            "policy_revision", "requires_owner", "effect_status", "case_id",
            "owner_decision_ref",
        })
        self.assertEqual(rec.effect_status, "PROPOSED")
        self.assertEqual(rec.recommendation, "NO_ACTION")
        self.assertTrue(rec.requires_owner)

    def test_insufficient_review_has_no_hr_recommendation(self):
        from dataclasses import replace
        with self.assertRaisesRegex(ValueError, "insufficient evidence"):
            from_review(replace(run_review(), status=ReviewStatus.INSUFFICIENT_EVIDENCE))

    def test_persona_review_recommendation_is_owner_pending_only(self):
        request = PersonaRevisionRequest("request-1", "worker-7", 1, 2,
                      "1" * 64, "2" * 64, {"skepticism": 80}, "3" * 64)
        rec = from_persona_request(request, assignment_epoch=4, policy_revision=2)
        self.assertEqual(rec.recommendation, "REVIEW_PERSONA")
        self.assertEqual(rec.effect_status, "PROPOSED")
        self.assertIsNone(rec.owner_decision_ref)


if __name__ == "__main__":
    unittest.main()
