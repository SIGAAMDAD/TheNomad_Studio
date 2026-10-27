"""Policy and scheduling regression tests; no external services or API charges."""
import copy
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import studio

ROOT = pathlib.Path(__file__).resolve().parents[1]


def fixture():
    jobs = {"schema": 1, "jobs": []}
    slots = studio.read(ROOT, "slots.json")
    config = studio.read(ROOT, "config.json")
    config["auto_dispatch_enabled"] = True
    for w in slots["slots"]:
        # Runtime assignments are not test fixtures: retain only slot capabilities.
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
        candidate.update({"repository": "SIGAAMDAD/NomadFramework", "branch": "develop", "baseline_sha": "1" * 40})
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
        candidate.update({"repository": "SIGAAMDAD/NomadFramework", "branch": "develop", "baseline_sha": "1" * 40})
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


if __name__ == "__main__":
    unittest.main()
