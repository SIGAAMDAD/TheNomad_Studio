"""Determinism and safety properties of test-only load harness."""
import json
import unittest
from workload import STEPS, run_profile, run_profiles


class SyntheticLoadTests(unittest.TestCase):
    def test_seed_determinism_and_progression(self):
        left = run_profile(25, seed=42)
        right = run_profile(25, seed=42)
        self.assertEqual(left["workload_sha256"], right["workload_sha256"])
        self.assertEqual(left["fault_distribution"], right["fault_distribution"])
        self.assertEqual(left["invariants"], right["invariants"])
        self.assertEqual(left["counters"], right["counters"])
        self.assertNotEqual(left["workload_sha256"], run_profile(25, seed=43)["workload_sha256"])

    def test_required_actor_profiles_and_abort_conditions(self):
        for n in STEPS:
            with self.subTest(actors=n):
                report = run_profile(n, seed=1729)
                self.assertFalse(report["abort"], report)
                self.assertEqual(report["actors"], n)
                self.assertEqual(report["external_provider_calls"], 0)
                self.assertEqual(report["real_chat_sessions"], 0)
                self.assertEqual(sum(report["fault_distribution"].values()), n)
                self.assertEqual(report["counters"]["claims"], n*3)
                self.assertEqual(report["counters"]["claim_collisions"], n)
                self.assertLessEqual(report["counters"]["provider_effects"], n)
                self.assertEqual(report["counters"]["outbox_delivered"], n)

    def test_no_fault_campaign_verifies_all_actors(self):
        report = run_profile(100, seed=11, faults=False)
        self.assertFalse(report["abort"], report)
        self.assertEqual(report["invariants"]["verified"], 100)
        self.assertEqual(report["counters"]["provider_effects"], 100)

    def test_report_maintains_unmeasured_external_gates(self):
        report = run_profiles(seed=1729)
        self.assertEqual(report["contract_approval_status"], "CANDIDATE_PENDING_OWNER_ARCHITECT_QA")
        self.assertEqual([r["actors"] for r in report["results"]], list(STEPS))
        self.assertTrue(all("actual PostgreSQL ACID/PITR or migrations" in r["not_measured"] for r in report["results"]))


if __name__ == "__main__":
    unittest.main()
