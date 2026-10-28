"""The report gate rejects fake claims and wrong source revisions."""
import unittest
from adapter_gate import MATRIX, evaluate_receipt, run_adapter
import json


class AdapterGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.case = json.loads(MATRIX.read_text())["cases"][0]
        cls.sha = "a"*40
        cls.good = {"source_sha": cls.sha, "case_id": cls.case["id"],
                    "adapter_owner": cls.case["owner"], "synthetic": False,
                    "observed_effects": 0, "observed": cls.case["expected"],
                    "command": "python integration_tests.py", "evidence_sha256": "1"*64,
                    "isolated_test_resource": "scratch-db-1", "tested_by": "qa-independent"}

    def test_default_is_explicitly_not_run(self):
        result = run_adapter(None, self.sha, "qa-independent", "author-worker")
        self.assertEqual(result["gate"], "BLOCKED")
        self.assertTrue(all(r["status"] == "NOT_RUN" for r in result["records"]))
        self.assertEqual(len(result["records"]), 18)

    def test_admission_requires_evidence_revision_and_distinct_reviewer(self):
        status, reason = evaluate_receipt(self.case, self.good, self.sha, "qa-independent", "author-worker")
        self.assertEqual(status, "PASS_EVIDENCE_PRESENT")
        self.assertEqual(reason, "AWAIT_INDEPENDENT_AUTHENTICATION")
        for delta in ({"source_sha": "b"*40}, {"synthetic": True},
                      {"observed_effects": 1}, {"observed": "ALLOW"},
                      {"tested_by": "author-worker"}, {"evidence_sha256": "unknown"},
                      {"isolated_test_resource": ""}):
            with self.subTest(delta=delta):
                bad = dict(self.good, **delta)
                self.assertEqual(evaluate_receipt(self.case, bad, self.sha, "qa-independent", "author-worker")[0], "REWORK")


if __name__ == "__main__":
    unittest.main()
