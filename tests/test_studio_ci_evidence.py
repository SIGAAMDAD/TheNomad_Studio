import copy
import json
import tempfile
import unittest
from scripts import studio_ci_evidence as ci


SHA = "a" * 40
REPO = "SIGAAMDAD/TheNomad_GOAP"
RUN = 3007


def event():
    return {
        "action": "studio-source-ci-v1",
        "repository": {"full_name": "SIGAAMDAD/TheNomad_Studio"},
        "client_payload": {"schema_version": 1, "job_id": "FEATURE-6-G00-01",
                           "source_repo": REPO, "source_sha": SHA,
                           "workflow_run_id": RUN}}


def state():
    return ({"jobs": [{"id": "FEATURE-6-G00-01", "approved": True,
                       "kind": "code", "state": "SUBMITTED",
                       "repository": REPO, "work_branch": "studio/FEATURE-6-G00-01",
                       "final_commit_sha": SHA}]},
            {"repos": [{"repo": REPO, "branch": "main"}],
             "source_writes_enabled": False})


def api_payload():
    return {
        ci.source_api(REPO, RUN): {
            "id": RUN, "head_sha": SHA,
            "head_branch": "studio/FEATURE-6-G00-01",
            "repository": {"full_name": REPO},
            "status": "completed", "conclusion": "success",
            "event": "pull_request", "run_attempt": 1,
            "updated_at": "2026-10-27T19:00:00Z",
        },
        ci.source_api(REPO, RUN, "/jobs?per_page=100"): {
            "total_count": 2, "jobs": [
                {"id": 11, "name": "Linux GCC", "conclusion": "success"},
                {"id": 12, "name": "Windows MSVC", "conclusion": "success"},
            ]},
        ci.source_api(REPO, RUN, "/artifacts?per_page=100"): {
            "total_count": 1, "artifacts": [
                {"id": 100, "name": "goap-toolchain", "size_in_bytes": 4096,
                 "expired": False, "digest": "sha256:deadbeef"}]},
    }


class SourceCIEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.candidate = ci.parse_candidate(event())
        self.jobs, self.config = state()
        self.results = api_payload()

    def check(self):
        return ci.source_run_evidence(
            self.candidate, self.jobs, self.config, lambda url: self.results[url])

    def test_verified_exact_sha_and_no_automatic_qa(self):
        receipt = self.check()
        self.assertTrue(receipt["source_run_metadata_verified"])
        self.assertTrue(receipt["source_job_final_sha_match"])
        self.assertFalse(receipt["source_writes_enabled_at_observation"])
        self.assertEqual(receipt["run_conclusion"], "success")
        self.assertEqual(receipt["independent_qa_verdict"], "NOT_RUN")
        self.assertFalse(receipt["runtime_promotion_authorized"])
        self.assertFalse(receipt["artifacts_content_verified"])
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(ci.commit_record(tmp, receipt)[0])
            self.assertFalse(ci.commit_record(tmp, receipt)[0])

    def test_failed_ci_remains_failed_and_archiveable(self):
        self.results[ci.source_api(REPO, RUN)]["conclusion"] = "failure"
        self.assertEqual(self.check()["run_conclusion"], "failure")

    def test_source_sha_mismatch_blocks(self):
        self.results[ci.source_api(REPO, RUN)]["head_sha"] = "b" * 40
        with self.assertRaisesRegex(ci.EvidenceError, "CI_SHA_BRANCH_OR_REPO_MISMATCH"):
            self.check()

    def test_wrong_branch_blocks(self):
        self.results[ci.source_api(REPO, RUN)]["head_branch"] = "main"
        with self.assertRaisesRegex(ci.EvidenceError, "CI_SHA_BRANCH_OR_REPO_MISMATCH"):
            self.check()

    def test_nonapproved_job(self):
        self.jobs["jobs"][0]["approved"] = False
        with self.assertRaisesRegex(ci.EvidenceError, "SOURCE_JOB_NOT_APPROVED"):
            self.check()

    def test_unknown_repository(self):
        self.candidate["source_repo"] = "SIGAAMDAD/Fake"
        with self.assertRaisesRegex(ci.EvidenceError, "SOURCE_NOT_REGISTERED"):
            self.check()

    def test_unfinished_workflow(self):
        self.results[ci.source_api(REPO, RUN)]["status"] = "in_progress"
        with self.assertRaisesRegex(ci.EvidenceError, "CI_WORKFLOW_NOT_FINAL"):
            self.check()

    def test_no_silent_truncated_ci_jobs(self):
        self.results[ci.source_api(REPO, RUN, "/jobs?per_page=100")]["total_count"] = 3
        with self.assertRaisesRegex(ci.EvidenceError, "INCOMPLETE_CI_JOBS"):
            self.check()

    def test_no_silent_truncated_artifacts(self):
        self.results[ci.source_api(REPO, RUN, "/artifacts?per_page=100")]["total_count"] = 101
        with self.assertRaisesRegex(ci.EvidenceError, "INCOMPLETE_ARTIFACT_INDEX"):
            self.check()

    def test_forged_dispatch_fields(self):
        incoming = event()
        incoming["client_payload"]["ci_status"] = "PASS"
        with self.assertRaisesRegex(ci.EvidenceError, "INVALID_CI_CANDIDATE_FIELDS"):
            ci.parse_candidate(incoming)

    def test_missing_source_credential_denied(self):
        with self.assertRaisesRegex(ci.EvidenceError, "SOURCE_AUDIT_CREDENTIAL_MISSING"):
            ci.github_get("", ci.source_api(REPO, RUN))

    def test_duplicate_dispatch_json_field_denied(self):
        with self.assertRaisesRegex(ci.EvidenceError, "DUPLICATE_FIELD"):
            ci.strict_pairs([("job_id", "A"), ("job_id", "B")])


if __name__ == "__main__":
    unittest.main()
