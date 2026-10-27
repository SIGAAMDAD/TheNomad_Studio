"""Policy and scheduling regression tests; no external services or API charges."""
import copy
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import studio

ROOT = pathlib.Path(__file__).resolve().parents[1]


def fixture():
    jobs = {"schema": 1, "jobs": []}
    slots = studio.read(ROOT, "slots.json")
    config = studio.read(ROOT, "config.json")
    config["auto_dispatch_enabled"] = True
    for w in slots["slots"]:
        # The checked-out tree may be the live studio/runtime branch.
        # Unit tests must never inherit an actual worker claim or epoch.
        w["job"] = None
        w["epoch"] = 0
        w["enabled"] = w["id"] != "S00"
    return jobs, slots, config


def job(jid, kind="design", paths=None, deps=None, state="READY", priority=10):
    return {"id": jid, "kind": kind, "state": state, "approved": True,
            "role": "rpg-test-role", "acceptance": ["observable result"],
            "dependencies": deps or [], "priority": priority,
            "repository": "docs", "owned_paths": paths or [f"docs/{jid}.md"]}


class StudioTests(unittest.TestCase):
    def test_fixture_discards_real_runtime_assignment(self):
        # Reproduce the S01 -> OPS-BOOT-001 reservation from the failed
        # runtime-branch Actions job without modifying the actual queue.
        recorded_slots = studio.read(ROOT, "slots.json")
        recorded_slots["slots"][1]["job"] = "OPS-BOOT-001"
        recorded_slots["slots"][1]["epoch"] = 7
        original_read = studio.read

        def simulated_runtime_read(root, filename):
            if filename == "slots.json":
                return copy.deepcopy(recorded_slots)
            return original_read(root, filename)

        with mock.patch.object(studio, "read", side_effect=simulated_runtime_read):
            jobs, slots, config = fixture()
        self.assertIsNone(slots["slots"][1]["job"])
        self.assertEqual(slots["slots"][1]["epoch"], 0)
        self.assertEqual(studio.validate(jobs, slots, config), [])
        jobs["jobs"].append(job("DES-101"))
        self.assertEqual(studio.dispatch(jobs, slots, config)[0]["job"], "DES-101")

    def test_clean_bootstrap_validates(self):
        jobs, slots, config = fixture()
        self.assertEqual(studio.validate(jobs, slots, config), [])

    def test_safe_dispatch_respects_capability_and_wip(self):
        jobs, slots, config = fixture()
        jobs["jobs"] = [job("DES-101"), job("DES-102"), job("DES-103")]
        assignments = studio.dispatch(jobs, slots, config, now="2026-10-26T00:00:00Z")
        self.assertEqual(len(assignments), 2)
        self.assertEqual([x["slot"] for x in assignments], ["S01", "S02"])
        self.assertEqual(studio.validate(jobs, slots, config), [])
        self.assertEqual(studio.dispatch(jobs, slots, config), [])

    def test_disabled_flag_is_read_only(self):
        jobs, slots, config = fixture()
        jobs["jobs"].append(job("DES-101"))
        config["auto_dispatch_enabled"] = False
        before = copy.deepcopy((jobs, slots))
        self.assertEqual(studio.dispatch(jobs, slots, config), [])
        self.assertEqual(before, (jobs, slots))

    def test_dependency_gate(self):
        jobs, slots, config = fixture()
        jobs["jobs"] = [job("DES-101", state="QUEUED"), job("DES-102", deps=["DES-101"])]
        self.assertEqual(studio.dispatch(jobs, slots, config), [])
        jobs["jobs"][0]["state"] = "VERIFIED"
        jobs["jobs"][0]["qa_evidence"] = {"verdict": "PASS", "subject_ref": "doc:rev1", "reviewer_slot": "S08"}
        self.assertEqual(len(studio.dispatch(jobs, slots, config)), 1)

    def test_cycle_and_missing_reference_rejected(self):
        jobs, slots, config = fixture()
        jobs["jobs"] = [job("DES-101", deps=["DES-102"]), job("DES-102", deps=["DES-101"])]
        self.assertIn("dependency cycle", " ".join(studio.validate(jobs, slots, config)))
        jobs["jobs"][1]["dependencies"] = ["DES-999"]
        self.assertIn("missing", " ".join(studio.validate(jobs, slots, config)))

    def test_ownership_conflict_blocks_overlapping_path(self):
        jobs, slots, config = fixture()
        jobs["jobs"] = [job("DES-101", paths=["source/ai"]),
                        job("DES-102", paths=["source/ai/planner.cpp"])]
        result = studio.dispatch(jobs, slots, config)
        self.assertEqual(len(result), 1)
        self.assertEqual(studio.validate(jobs, slots, config), [])

    def test_no_conflict_for_unrelated_repositories(self):
        first = job("DES-101", paths=["src"])
        second = job("DES-102", paths=["src"])
        second["repository"] = "different"
        self.assertFalse(studio.conflicting(first, second))

    def test_code_blocked_without_source_permission(self):
        jobs, slots, config = fixture()
        candidate = job("ENG-101", kind="code", paths=["src/ai"])
        candidate.update({"repository": "SIGAAMDAD/NomadFramework", "branch": "develop", "workstream": "framework_engine", "language": "C++17", "work_branch": "studio/ENG-101", "baseline_sha": "1" * 40,
                          "architecture_impact": "none",
                          "architecture_review": {"reviewer_role": "architecture_enforcer",
                                                  "reviewed_baseline_sha": "1"*40,
                                                  "verdict": "NO_CHANGE"}})
        jobs["jobs"].append(candidate)
        self.assertEqual(studio.validate(jobs, slots, config), [])
        self.assertEqual(studio.dispatch(jobs, slots, config), [])
        config["source_writes_enabled"] = True
        self.assertEqual(len(studio.dispatch(jobs, slots, config)), 1)

    def test_stale_receipt_rejected(self):
        jobs, slots, config = fixture()
        jobs["jobs"].append(job("DES-101"))
        studio.dispatch(jobs, slots, config)
        receipt = {"job_id": "DES-101", "slot_id": "S01", "assignment_epoch": 7,
                   "attempt_id": "RUN-001", "outcome": "CHECKPOINTED", "checkpoint_ref": "doc:rev2"}
        with self.assertRaisesRegex(ValueError, "epoch mismatch"):
            studio.record(jobs, slots, receipt)

    def test_checkpoint_keeps_ownership(self):
        jobs, slots, config = fixture()
        jobs["jobs"].append(job("DES-101"))
        studio.dispatch(jobs, slots, config)
        receipt = {"job_id": "DES-101", "slot_id": "S01", "assignment_epoch": 1,
                   "attempt_id": "RUN-001", "outcome": "CHECKPOINTED", "checkpoint_ref": "doc:rev2"}
        self.assertEqual(studio.record(jobs, slots, receipt)["outcome"], "CHECKPOINTED")
        self.assertEqual(slots["slots"][1]["job"], "DES-101")
        self.assertEqual(studio.validate(jobs, slots, config), [])

    def test_submission_releases_slot_for_creative_review(self):
        jobs, slots, config = fixture()
        jobs["jobs"].append(job("DES-101"))
        studio.dispatch(jobs, slots, config)
        receipt = {"job_id": "DES-101", "slot_id": "S01", "assignment_epoch": 1,
                   "attempt_id": "RUN-001", "outcome": "SUBMITTED", "artifact_ref": "doc:rev3"}
        studio.record(jobs, slots, receipt)
        self.assertIsNone(slots["slots"][1]["job"])
        self.assertEqual(jobs["jobs"][0]["state"], "SUBMITTED")
        self.assertEqual(studio.validate(jobs, slots, config), [])

    def test_code_submission_requires_external_readback(self):
        jobs, slots, config = fixture()
        candidate = job("ENG-101", kind="code")
        candidate.update({"repository": "SIGAAMDAD/NomadFramework", "branch": "develop", "workstream": "framework_engine", "language": "C++17", "work_branch": "studio/ENG-101", "baseline_sha": "1" * 40,
                          "architecture_impact": "none",
                          "architecture_review": {"reviewer_role": "architecture_enforcer",
                                                  "reviewed_baseline_sha": "1"*40,
                                                  "verdict": "NO_CHANGE"}})
        jobs["jobs"].append(candidate)
        config["source_writes_enabled"] = True
        studio.dispatch(jobs, slots, config)
        receipt = {"job_id": "ENG-101", "slot_id": "S06", "assignment_epoch": 1,
                   "attempt_id": "RUN-001", "outcome": "SUBMITTED", "artifact_ref": "some patch"}
        with self.assertRaisesRegex(ValueError, "GitHub and Drive readback"):
            studio.record(jobs, slots, receipt)

    def test_verified_requires_exact_qa_evidence(self):
        jobs, slots, config = fixture()
        candidate = job("DES-101", state="VERIFIED")
        jobs["jobs"].append(candidate)
        self.assertIn("QA PASS", " ".join(studio.validate(jobs, slots, config)))
        candidate["qa_evidence"] = {"verdict": "PASS", "subject_ref": "doc:rev2", "reviewer_slot": "S08"}
        self.assertEqual(studio.validate(jobs, slots, config), [])

    def test_process_pending_worker_receipt_is_idempotent(self):
        jobs, slots, config = fixture()
        jobs["jobs"].append(job("DES-101"))
        studio.dispatch(jobs, slots, config)
        with tempfile.TemporaryDirectory() as temp:
            folder = pathlib.Path(temp) / "studio" / "receipts" / "DES-101"
            folder.mkdir(parents=True)
            receipt = {"job_id": "DES-101", "slot_id": "S01", "assignment_epoch": 1,
                       "attempt_id": "RUN-001", "outcome": "CHECKPOINTED", "checkpoint_ref": "doc:revision2"}
            (folder / "RUN-001.json").write_text(json.dumps(receipt))
            self.assertEqual(len(studio.process_pending(pathlib.Path(temp), jobs, slots, config)), 1)
            self.assertEqual(studio.process_pending(pathlib.Path(temp), jobs, slots, config), [])
            self.assertEqual(jobs["jobs"][0]["applied_attempts"], ["RUN-001"])

    def test_process_rejects_wrong_receipt_file_path(self):
        jobs, slots, config = fixture()
        jobs["jobs"].append(job("DES-101"))
        studio.dispatch(jobs, slots, config)
        with tempfile.TemporaryDirectory() as temp:
            folder = pathlib.Path(temp) / "studio" / "receipts" / "DES-101"
            folder.mkdir(parents=True)
            receipt = {"job_id": "DES-101", "slot_id": "S01", "assignment_epoch": 1,
                       "attempt_id": "RUN-001", "outcome": "CHECKPOINTED", "checkpoint_ref": "doc:revision2"}
            (folder / "wrong.json").write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, "Receipt path"):
                studio.process_pending(pathlib.Path(temp), jobs, slots, config)

    def test_context_contains_only_relevant_job(self):
        jobs, slots, config = fixture()
        jobs["jobs"].append(job("DES-101"))
        studio.dispatch(jobs, slots, config)
        result = studio.context(jobs, slots, "S01")
        self.assertEqual(result["status"], "ASSIGNED")
        self.assertEqual(result["job"]["id"], "DES-101")
        self.assertNotIn("qa_evidence", result["job"])
        self.assertEqual(studio.context(jobs, slots, "S09")["status"], "NO_WORK")


    def test_review_dispatches_after_submission_not_verification(self):
        jobs, slots, config = fixture()
        jobs["jobs"] = [job("DES-201", state="SUBMITTED"),
                         job("QA-201", kind="review", deps=["DES-201"])]
        jobs["jobs"][0]["artifact_ref"] = "docs:immutable-revision"
        jobs["jobs"][1]["subject_job"] = "DES-201"
        self.assertEqual(studio.validate(jobs, slots, config), [])
        reservations = studio.dispatch(jobs, slots, config)
        self.assertEqual(len(reservations), 1)
        self.assertEqual(reservations[0]["slot"], "S08")
        self.assertEqual(reservations[0]["job"], "QA-201")

    def test_review_does_not_run_before_submission(self):
        jobs, slots, config = fixture()
        jobs["jobs"] = [job("DES-201"), job("QA-201", kind="review", deps=["DES-201"])]
        jobs["jobs"][1]["subject_job"] = "DES-201"
        reservations = studio.dispatch(jobs, slots, config)
        self.assertEqual([x["job"] for x in reservations], ["DES-201"])

    def test_review_requires_exact_parent(self):
        jobs, slots, config = fixture()
        jobs["jobs"] = [job("DES-201", state="SUBMITTED"), job("QA-201", kind="review", deps=["DES-201"])]
        self.assertIn("subject_job", " ".join(studio.validate(jobs, slots, config)))

    def test_verified_code_requires_matching_ci(self):
        jobs, slots, config = fixture()
        code = job("ENG-201", kind="code", state="VERIFIED")
        code.update({"repository": "SIGAAMDAD/NomadFramework",
                      "branch": "develop", "workstream": "framework_engine", "language": "C++17", "work_branch": "studio/ENG-201",
                      "baseline_sha": "f" * 40,
                      "architecture_impact": "none",
                      "architecture_review": {"reviewer_role": "architecture_enforcer",
                                              "reviewed_baseline_sha": "f"*40,
                                              "verdict": "NO_CHANGE"},
                      "final_commit_sha": "a" * 40, "drive_report_url": "https://docs.google.com/document/d/real/edit",
                     "qa_evidence": {"verdict": "PASS", "subject_ref": "a" * 40, "reviewer_slot": "S09"}})
        jobs["jobs"] = [code]
        self.assertIn("passing CI", " ".join(studio.validate(jobs, slots, config)))
        code["ci_evidence"] = {"verdict": "PASS", "subject_sha": "b" * 40}
        self.assertIn("passing CI", " ".join(studio.validate(jobs, slots, config)))
        code["ci_evidence"]["subject_sha"] = "a" * 40
        self.assertEqual(studio.validate(jobs, slots, config), [])

    def test_duplicate_scheduled_task_binding_rejected(self):
        jobs, slots, config = fixture()
        slots["slots"][1]["scheduled_task_id"] = "task-1"
        slots["slots"][2]["scheduled_task_id"] = "task-1"
        self.assertIn("same scheduled", " ".join(studio.validate(jobs, slots, config)))

    def test_disabled_slot_cannot_hold_active_writer(self):
        jobs, slots, config = fixture()
        jobs["jobs"].append(job("DES-201"))
        studio.dispatch(jobs, slots, config)
        slots["slots"][1]["enabled"] = False
        self.assertIn("disabled slot", " ".join(studio.validate(jobs, slots, config)))


    def test_end_to_end_creative_submission_unlocks_independent_qa(self):
        jobs, slots, config = fixture()
        jobs["jobs"] = [job("DES-301"), job("QA-301", kind="review", deps=["DES-301"])]
        jobs["jobs"][1]["subject_job"] = "DES-301"
        first = studio.dispatch(jobs, slots, config)
        self.assertEqual([x["job"] for x in first], ["DES-301"])
        studio.record(jobs, slots, {"job_id": "DES-301", "slot_id": "S01",
                   "assignment_epoch": 1, "attempt_id": "RUN-301", "outcome": "SUBMITTED",
                   "artifact_ref": "doc:revision301"})
        self.assertEqual(jobs["jobs"][0]["submitted_by_slot"], "S01")
        next_jobs = studio.dispatch(jobs, slots, config)
        self.assertEqual(next_jobs, [{"slot": "S08", "job": "QA-301", "epoch": 1}])
        studio.record(jobs, slots, {"job_id": "QA-301", "slot_id": "S08",
                   "assignment_epoch": 1, "attempt_id": "RUN-302", "outcome": "SUBMITTED",
                   "artifact_ref": "doc:qa-report302",
                   "review_result": {"verdict": "PASS", "subject_ref": "doc:revision301", "findings": []}})
        self.assertEqual(studio.validate(jobs, slots, config), [])
        self.assertEqual([j["state"] for j in jobs["jobs"]], ["SUBMITTED", "SUBMITTED"])

    def test_verified_creative_rejects_self_review_and_wrong_revision(self):
        jobs, slots, config = fixture()
        candidate = job("DES-301", state="VERIFIED")
        candidate.update({"artifact_ref": "doc:revisionA", "submitted_by_slot": "S01",
                          "qa_evidence": {"verdict": "PASS", "subject_ref": "doc:revisionB",
                                          "reviewer_slot": "S08"}})
        jobs["jobs"] = [candidate]
        self.assertIn("submitted artifact", " ".join(studio.validate(jobs, slots, config)))
        candidate["qa_evidence"]["subject_ref"] = "doc:revisionA"
        candidate["qa_evidence"]["reviewer_slot"] = "S01"
        self.assertIn("self-review", " ".join(studio.validate(jobs, slots, config)))
        candidate["qa_evidence"]["reviewer_slot"] = "S08"
        self.assertEqual(studio.validate(jobs, slots, config), [])


if __name__ == "__main__":
    unittest.main()
