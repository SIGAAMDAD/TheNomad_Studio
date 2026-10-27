"""Studio V3 integration tests: board events, multi-profession intake, QA and owner gates."""
import copy
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import board_sync
import github_api
import production
import studio

ROOT = pathlib.Path(__file__).resolve().parents[1]


def fixture():
    config = studio.read(ROOT, "config.json")
    config["auto_dispatch_enabled"] = True
    slots = studio.read(ROOT, "slots.json")
    for s in slots["slots"]:
        s["enabled"] = s["id"] in ("S00", "S01", "S02", "S03", "S04", "S05", "S08", "S09")
        s["job"] = None
        s["epoch"] = 0
    jobs = {"schema": 1, "jobs": []}
    features = {"schema": 1, "features": []}
    board = {"schema": 1, "repo": "SIGAAMDAD/TheNomad_Studio",
             "project_url": None, "project_writeback": False,
             "trusted_approvers": ["SIGAAMDAD"]}
    return config, slots, jobs, features, board


def issue(n=3, labels=None):
    return {"number": n, "title": "Playable Sortior encounter", "body": "Preserve canon",
            "html_url": f"https://github.com/SIGAAMDAD/TheNomad_Studio/issues/{n}",
            "repository_url": "https://api.github.com/repos/SIGAAMDAD/TheNomad_Studio",
            "labels": [{"name": x} for x in (labels or [])],
            "updated_at": "2026-10-26T21:00:00Z"}


def event(label="studio:approved", actor="SIGAAMDAD", change="labeled", n=1):
    return {"event": change, "label": {"name": label}, "actor": {"login": actor},
            "created_at": f"2026-10-26T21:00:{n:02}Z", "id": n}


def job(jid="DES-401", state="READY", kind="design", deps=None):
    return {"id": jid, "kind": kind, "state": state, "approved": True,
            "role": "rpg-gameplay-designer", "role_manual_uri": "skills://rpg-gameplay-designer",
            "owned_paths": ["studio/evidence/" + jid + ".md"],
            "acceptance": ["real artifact"], "dependencies": deps or [],
            "repository": "SIGAAMDAD/TheNomad_Studio", "priority": 10}


def review(jid="QA-DES-401", subject="DES-401"):
    r = job(jid, kind="review", deps=[subject])
    r["subject_job"] = subject
    r["role"] = "rpg-qa-lead"
    r["owned_paths"] = ["studio/reviews/" + jid + ".md"]
    return r


def receipt(jid, slot, epoch, attempt, artifact, result=None):
    x = {"job_id": jid, "slot_id": slot, "assignment_epoch": epoch,
         "attempt_id": attempt, "outcome": "SUBMITTED", "artifact_ref": artifact}
    if result is not None:
        x["review_result"] = result
    return x


class StudioV3Tests(unittest.TestCase):
    def test_owner_label_checks_current_label_and_event_actor(self):
        obj = issue(labels=["studio:approved"])
        self.assertFalse(github_api.owner_label(obj, [], "studio:approved", ["SIGAAMDAD"]))
        self.assertFalse(github_api.owner_label(obj, [event(actor="unknown")],
                                                 "studio:approved", ["SIGAAMDAD"]))
        self.assertTrue(github_api.owner_label(obj, [event()], "studio:approved", ["SIGAAMDAD"]))
        self.assertFalse(github_api.owner_label(obj, [event(), event(change="unlabeled", n=2)],
                                                 "studio:approved", ["SIGAAMDAD"]))

    def test_board_sync_idempotent_and_not_an_approval_authority(self):
        _, _, _, features, cfg = fixture()
        source = [issue()]
        board_sync.sync(features, source, {}, cfg)
        self.assertEqual(features["features"][0]["state"], "INBOX")
        board_sync.sync(features, source, {}, cfg)
        self.assertEqual(len(features["features"]), 1)
        source[0]["labels"] = [{"name": "studio:approved"}]
        board_sync.sync(features, source, {"3": [event()]}, cfg)
        self.assertTrue(features["features"][0]["approved"])
        self.assertEqual(features["features"][0]["state"], "SCOPING")
        board_sync.sync(features, source, {"3": [event()]}, cfg)
        self.assertEqual(len(features["features"]), 1)

    def test_project_draft_is_not_executable(self):
        _, _, _, features, cfg = fixture()
        item = {"id": "PVTI_7", "content": {"id": "DI_1", "title": "New quest", "body": "Story"},
                "fieldValueByName": {"name": "Ready"}}
        board_sync.sync(features, [], {}, cfg, [item])
        self.assertEqual(features["features"][0]["state"], "INBOX")
        self.assertFalse(features["features"][0]["approved"])
        self.assertIn("Convert draft", features["features"][0]["blocker"])

    def test_only_own_repository_issue_is_imported_from_board(self):
        _, _, _, features, cfg = fixture()
        other = issue(7)
        other["repository_url"] = "https://api.github.com/repos/OTHER/repo"
        board_sync.sync(features, [other], {}, cfg)
        self.assertEqual(features["features"], [])

    def test_project_url_validation(self):
        self.assertEqual(github_api.parse_project_url("https://github.com/users/SIGAAMDAD/projects/7"),
                         ("SIGAAMDAD", 7, "user"))
        self.assertEqual(github_api.parse_project_url("https://github.com/orgs/nomad/projects/8"),
                         ("nomad", 8, "organization"))
        with self.assertRaises(ValueError):
            github_api.parse_project_url("https://example.org/steal-token")

    def test_malformed_spec_or_unapproved_feature_not_admitted(self):
        config, slots, jobs, features, _ = fixture()
        features["features"] = [{"id": "FEATURE-3", "source": "github_issue", "state": "SCOPING",
                                 "issue_url": issue()["html_url"], "approved": False, "jobs": []}]
        with tempfile.TemporaryDirectory() as t:
            root = pathlib.Path(t)
            (root / "studio" / "specs").mkdir(parents=True)
            (root / "studio" / "specs" / "FEATURE-3.json").write_text("{}")
            result, out = production.admit(root, features, jobs, slots, config)
            self.assertEqual(result["jobs"], [])
            self.assertEqual(out["admitted"], [])
            features["features"][0]["approved"] = True
            result, out = production.admit(root, features, jobs, slots, config)
            self.assertEqual(result["jobs"], [])
            self.assertEqual(features["features"][0]["state"], "BLOCKED")

    def test_multidiscipline_admission_creates_qa_and_verified_dependencies(self):
        config, slots, jobs, features, _ = fixture()
        features["features"] = [{"id": "FEATURE-3", "source": "github_issue",
                                 "issue_url": issue()["html_url"], "approved": True,
                                 "state": "SCOPING", "repo": "SIGAAMDAD/TheNomad_Studio", "jobs": []}]
        spec = {"schema": 1, "feature_id": "FEATURE-3", "source_issue": issue()["html_url"],
                "jobs": [
                    {"id": "FEATURE-3-DES-01", "kind": "design", "role": "rpg-gameplay-designer",
                     "role_manual_uri": "skills://rpg-gameplay-designer", "brief": "Combat matrix",
                     "owned_paths": ["drive:document:design-03"], "acceptance": ["decision table"],
                     "qa_role": "rpg-qa-lead"},
                    {"id": "FEATURE-3-WRT-01", "kind": "writing", "role": "rpg-game-writer",
                     "role_manual_uri": "skills://rpg-game-writer", "brief": "Narrative",
                     "owned_paths": ["drive:document:story-03"], "acceptance": ["keyed story"],
                     "dependencies": ["FEATURE-3-DES-01"], "qa_role": "rpg-qa-lead"}
                ]}
        with tempfile.TemporaryDirectory() as t:
            root = pathlib.Path(t)
            (root / "studio" / "specs").mkdir(parents=True)
            (root / "studio" / "specs" / "FEATURE-3.json").write_text(json.dumps(spec))
            result, out = production.admit(root, features, jobs, slots, config)
        self.assertEqual(len(out["admitted"]), 4)
        self.assertEqual(len(result["jobs"]), 4)
        self.assertEqual(result["jobs"][2]["dependencies"], ["QA-FEATURE-3-DES-01"])
        self.assertEqual(studio.validate(result, slots, config), [])
        again, noop = production.admit(ROOT, features, result, slots, config)
        self.assertEqual(noop["admitted"], [])
        self.assertEqual(len(again["jobs"]), 4)

    def test_safe_code_admission_stays_blocked(self):
        config, slots, jobs, features, _ = fixture()
        feature = {"id": "FEATURE-3", "approved": True, "issue_url": issue()["html_url"],
                   "repo": "SIGAAMDAD/TheNomad_Studio", "jobs": []}
        spec = {"schema": 1, "feature_id": "FEATURE-3", "source_issue": feature["issue_url"],
                "jobs": [{"id": "FEATURE-3-ENG-01", "kind": "code", "role": "rpg-gameplay-programmer",
                          "role_manual_uri": "skills://rpg-gameplay-programmer", "brief": "Implement",
                          "owned_paths": ["src/enemy.cpp"], "acceptance": ["compiler + tests"],
                          "qa_role": "rpg-technical-qa-analyst",
                          "repository": "SIGAAMDAD/NomadFramework", "branch": "develop",
                          "baseline_sha": "a" * 40}]}
        out, _ = production.build_jobs(feature, spec, config, jobs)
        self.assertEqual(out[0]["state"], "BLOCKED")
        self.assertEqual(out[1]["state"], "READY")

    def test_review_receipt_requires_independence_and_exact_revision(self):
        config, slots, jobs, _, _ = fixture()
        jobs["jobs"] = [job(), review()]
        studio.dispatch(jobs, slots, config)
        studio.record(jobs, slots, receipt("DES-401", "S01", 1, "RUN-101", "github:SIGAAMDAD/TheNomad_Studio@" + "a"*40 + ":x.md"))
        studio.dispatch(jobs, slots, config)
        bad = receipt("QA-DES-401", "S08", 1, "RUN-102", "github:SIGAAMDAD/TheNomad_Studio@" + "b"*40 + ":qa.md",
                      {"verdict": "PASS", "subject_ref": "wrong", "findings": []})
        with self.assertRaisesRegex(ValueError, "exact submitted"):
            studio.record(jobs, slots, bad)

    def test_qa_pass_promotes_exact_subject(self):
        config, slots, jobs, features, _ = fixture()
        jobs["jobs"] = [job(), review()]
        feature = {"id": "FEATURE-3", "approved": True, "jobs": ["DES-401", "QA-DES-401"],
                   "state": "PRODUCTION"}
        features["features"].append(feature)
        studio.dispatch(jobs, slots, config)
        subject = "github:SIGAAMDAD/TheNomad_Studio@" + "a"*40 + ":producer.md"
        report = "github:SIGAAMDAD/TheNomad_Studio@" + "b"*40 + ":report.md"
        studio.record(jobs, slots, receipt("DES-401", "S01", 1, "RUN-101", subject))
        self.assertEqual(studio.dispatch(jobs, slots, config)[0]["slot"], "S08")
        studio.record(jobs, slots, receipt("QA-DES-401", "S08", 1, "RUN-102", report,
                       {"verdict": "PASS", "subject_ref": subject, "findings": []}))
        self.assertEqual(production.adjudicate(jobs, external_verifier=lambda _: True)[0]["outcome"],
                         "VERIFIED")
        self.assertEqual(studio.validate(jobs, slots, config), [])
        self.assertEqual(production.feature_status(features, jobs)[0]["to"], "READY_FOR_OWNER")
        feature["owner_accept_requested"] = True
        self.assertEqual(production.feature_status(features, jobs)[0]["to"], "OWNER_ACCEPTED")
        feature["owner_finalize_requested"] = True
        self.assertEqual(production.feature_status(features, jobs), [])
        feature["finalization_evidence"] = {"verified": True, "artifact_ref": "github:verified"}
        self.assertEqual(production.feature_status(features, jobs)[0]["to"], "FINALIZED")

    def test_rework_then_resubmission_same_job(self):
        config, slots, jobs, _, _ = fixture()
        jobs["jobs"] = [job(), review()]
        studio.dispatch(jobs, slots, config)
        studio.record(jobs, slots, receipt("DES-401", "S01", 1, "RUN-101", "doc:rev1"))
        studio.dispatch(jobs, slots, config)
        studio.record(jobs, slots, receipt("QA-DES-401", "S08", 1, "RUN-102", "doc:qa1",
                       {"verdict": "REWORK", "subject_ref": "doc:rev1",
                        "findings": [{"id": "F1", "issue": "wrong ID"}]}))
        self.assertEqual(production.adjudicate(jobs, external_verifier=lambda _: True)[0]["outcome"], "REWORK")
        self.assertEqual(jobs["jobs"][0]["state"], "READY")
        self.assertEqual(jobs["jobs"][1]["state"], "QUEUED")
        self.assertEqual(jobs["jobs"][0]["qa_findings"][0]["id"], "F1")
        self.assertEqual(studio.dispatch(jobs, slots, config)[0]["job"], "DES-401")
        studio.record(jobs, slots, receipt("DES-401", "S01", 2, "RUN-103", "doc:rev2"))
        production.adjudicate(jobs, external_verifier=lambda _: True)
        self.assertEqual(jobs["jobs"][1]["state"], "READY")
        studio.dispatch(jobs, slots, config)
        studio.record(jobs, slots, receipt("QA-DES-401", "S08", 2, "RUN-104", "doc:qa2",
                       {"verdict": "PASS", "subject_ref": "doc:rev2", "findings": []}))
        production.adjudicate(jobs, external_verifier=lambda _: True)
        self.assertEqual(jobs["jobs"][0]["state"], "VERIFIED")
        self.assertEqual(studio.validate(jobs, slots, config), [])

    def test_pending_github_readback_cannot_claim_verified(self):
        config, slots, jobs, _, _ = fixture()
        jobs["jobs"] = [job(), review()]
        studio.dispatch(jobs, slots, config)
        studio.record(jobs, slots, receipt("DES-401", "S01", 1, "RUN-101", "drive:doc:rev1"))
        studio.dispatch(jobs, slots, config)
        studio.record(jobs, slots, receipt("QA-DES-401", "S08", 1, "RUN-102", "drive:qa:rev1",
                       {"verdict": "PASS", "subject_ref": "drive:doc:rev1", "findings": []}))
        self.assertEqual(production.adjudicate(jobs, external_verifier=lambda _: False), [])
        self.assertEqual(jobs["jobs"][0]["state"], "REVIEW")
        reviewjob = jobs["jobs"][1]
        reviewjob["readback_evidence"] = {
            "verified": True, "verified_by": "S00", "subject_ref": "drive:doc:rev1",
            "report_ref": "drive:qa:rev1", "evidence_uri": "https://docs.google.com/document/d/example/edit"
        }
        self.assertEqual(production.adjudicate(jobs, external_verifier=lambda _: False)[0]["outcome"],
                         "VERIFIED")

    def test_no_unattested_code_pass(self):
        config, slots, jobs, _, _ = fixture()
        config["source_writes_enabled"] = True
        coder = job("ENG-401", kind="code")
        coder.update({"repository": "SIGAAMDAD/NomadFramework", "branch": "develop",
                      "baseline_sha": "a"*40, "state": "SUBMITTED", "final_commit_sha": "b"*40,
                      "drive_report_url": "https://docs.google.com/document/d/final/edit",
                      "submitted_by_slot": "S07"})
        tester = review("QA-ENG-401", "ENG-401")
        tester.update({"state": "SUBMITTED", "artifact_ref": "github:SIGAAMDAD/TheNomad_Studio@" + "c"*40 + ":qa.md",
                       "review_result": {"verdict": "PASS", "subject_ref": "b"*40,
                                         "reviewer_slot": "S09", "findings": []}})
        jobs["jobs"] = [coder, tester]
        self.assertEqual(production.adjudicate(jobs, external_verifier=lambda _: True)[0]["outcome"],
                         "QA_PASS_CI_OR_HANDOFF_PENDING")
        self.assertEqual(coder["state"], "REVIEW")
        coder["ci_evidence"] = {"verdict": "PASS", "subject_sha": "b"*40}
        self.assertEqual(production.adjudicate(jobs, external_verifier=lambda _: True)[0]["outcome"],
                         "VERIFIED")
        self.assertEqual(studio.validate(jobs, slots, config), [])


if __name__ == "__main__":
    unittest.main()
