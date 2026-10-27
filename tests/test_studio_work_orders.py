#!/usr/bin/env python3
"""Unit tests for reusable, hub-triggered engineering Work slots."""
import copy
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import work_event_router as routing

HUB = "SIGAAMDAD/TheNomad_Studio"
SOURCE = "SIGAAMDAD/NomadMain"
BASE = "a"*40
HEAD = "b"*40

class WorkOrderRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = routing.read_json(ROOT / "studio/workforce/hierarchy.v2.json")

    def job(self, workstream="mod_gameplay", repo=SOURCE, branch="main", lang="C#"):
        return {
            "id": "FEATURE-99-CODE-01", "kind": "code", "role": "rpg-gameplay-programmer",
            "approved": True, "state": "READY", "repository": repo,
            "branch": branch, "work_branch": "studio/FEATURE-99-CODE-01",
            "workstream": workstream, "language": lang, "baseline_sha": BASE,
            "architecture_impact": "none",
            "architecture_review": {"reviewer_role": "architecture_enforcer",
                                    "reviewed_baseline_sha": BASE, "verdict": "NO_CHANGE"}
        }

    def event(self, job, stage="alpha", labels=None, job_id=None):
        job_id = job_id or job["id"]
        labels = labels if labels is not None else [f"studio:work:{stage}"]
        return {
            "event_name": "pull_request", "action": "opened", "number": 8,
            "pull_request": {
                "number": 8, "draft": False,
                "user": {"login": "SIGAAMDAD"},
                "title": f"[Studio Work Order] {job_id} ({stage})",
                "body": "\n".join([
                    f"Studio-Job: {job_id}",
                    f"Studio-Stage: {stage}",
                    f"Studio-Source: {job['repository']}",
                    f"Studio-Target: {job['branch']}",
                    f"Studio-Work-Branch: {job['work_branch']}",
                    f"Studio-Source-Baseline: {job['baseline_sha']}",
                ]),
                "labels": [{"name": x} for x in labels],
                "base": {"ref": "main", "repo": {"full_name": HUB}},
                "head": {"ref": f"studio/workorders/{job_id}/{stage}",
                         "sha": HEAD, "repo": {"full_name": HUB}}
            }
        }

    def dispatch(self, job, stage="alpha", event=None, config=None):
        return routing.route_event(
            event or self.event(job, stage), self.policy,
            {"jobs": [job]}, config or {"auto_ai_enabled": False})

    def test_hub_event_can_route_all_five_source_workstreams(self):
        cases = (
            ("rendering_engine", "SIGAAMDAD/Terrain3D", "Terrain3D_NomadRenderer", "C++17"),
            ("framework_engine", "SIGAAMDAD/NomadFramework", "develop", "C++17"),
            ("core_gameplay", "SIGAAMDAD/The_Nomad", "Nomad3D", "C++17"),
            ("mod_gameplay", "SIGAAMDAD/NomadMain", "main", "C#"),
            ("goap_core", "SIGAAMDAD/TheNomad_GOAP", "main", "C++17"),
        )
        for stream, repo, branch, lang in cases:
            with self.subTest(stream=stream):
                job = self.job(workstream=stream, repo=repo, branch=branch, lang=lang)
                event = self.event(job, "alpha")
                x = self.dispatch(job, event=event)
                self.assertEqual(x["reason"], "AUTO_AI_DISABLED")
                self.assertEqual(x["profile"], "W01")
                self.assertEqual(x["source_repository"], repo)
                self.assertEqual(x["target_branch"], branch)
                self.assertEqual(x["source_work_branch"], job["work_branch"])
                self.assertEqual(x["pinned_source_sha"], BASE)
                self.assertNotEqual(x["source_repository"], HUB)
                self.assertFalse(x["model_invoked"])

    def test_terra_switches_from_csharp_to_renderer_without_new_task(self):
        job = self.job()
        e = self.event(job, "implement")
        x = self.dispatch(job, "implement", e)
        self.assertEqual(x["profile"], "W02")
        self.assertEqual(x["source_repository"], "SIGAAMDAD/NomadMain")
        job = self.job(workstream="rendering_engine", repo="SIGAAMDAD/Terrain3D",
                       branch="Terrain3D_NomadRenderer", lang="C++17")
        e = self.event(job, "implement")
        x = self.dispatch(job, "implement", e)
        self.assertEqual(x["profile"], "W02")
        self.assertEqual(x["source_repository"], "SIGAAMDAD/Terrain3D")

    def test_bad_hub_pr_never_reaches_source_worker(self):
        job = self.job()
        e = self.event(job)
        e["pull_request"]["user"]["login"] = "untrusted-contributor"
        self.assertEqual(self.dispatch(job,event=e)["reason"],
                         "WORK_ORDER_MUST_BE_CREATED_BY_OWNER")
        e = self.event(job)
        e["pull_request"]["head"]["repo"]["full_name"] = "fork/studio"
        self.assertEqual(self.dispatch(job,event=e)["reason"],
                         "HUB_WORK_ORDER_PR_REPOSITORY_MISMATCH")
        e = self.event(job)
        e["pull_request"]["base"]["ref"] = "studio/runtime"
        self.assertEqual(self.dispatch(job,event=e)["reason"],
                         "HUB_WORK_ORDER_PR_REPOSITORY_MISMATCH")
        e = self.event(job)
        e["pull_request"]["head"]["ref"] = "studio/FEATURE-99-CODE-01"
        self.assertEqual(self.dispatch(job,event=e)["reason"],
                         "WORK_ORDER_BRANCH_MISMATCH")
        e = self.event(job)
        e["pull_request"]["draft"] = True
        self.assertEqual(self.dispatch(job,event=e)["reason"], "DRAFT_PR")

    def test_authorized_source_binding_rejects_fake_repo_and_head_sha(self):
        job = self.job()
        e = self.event(job)
        e["pull_request"]["body"] = e["pull_request"]["body"].replace(SOURCE, HUB)
        self.assertEqual(self.dispatch(job,event=e)["reason"],
                         "WORK_ORDER_SOURCE_BINDING_MISMATCH")
        e = self.event(job)
        e["pull_request"]["head"]["sha"] = "not-a-sha"
        self.assertEqual(self.dispatch(job,event=e)["reason"],
                         "HUB_WORK_ORDER_HEAD_SHA_MISSING")
        e = self.event(job)
        e["pull_request"]["body"] += "\nStudio-Stage: alpha"
        self.assertEqual(self.dispatch(job,event=e)["reason"],
                         "DUPLICATE_WORK_ORDER_TRAILERS")

    def test_source_job_cannot_be_control_repository(self):
        job = self.job(repo=HUB)
        e = self.event(job)
        self.assertEqual(self.dispatch(job,event=e)["reason"],
                         "CONTROL_REPOSITORY_FORBIDDEN_FOR_SOURCE")

    def test_only_one_stage_allowed(self):
        job = self.job()
        e = self.event(job,labels=["studio:work:alpha","studio:work:implement"])
        self.assertEqual(self.dispatch(job,event=e)["reason"],
                         "EXACT_WORK_ORDER_STAGE_LABEL_REQUIRED")

    def test_stage_sensitive_kind_and_evidence(self):
        job = self.job()
        e = self.event(job,"debug")
        self.assertEqual(self.dispatch(job,stage="debug",event=e)["reason"],
                         "STAGE_EVIDENCE_MISSING")
        e = self.event(job,"debug",labels=["studio:work:debug","studio:evidence:ci-failed"])
        self.assertEqual(self.dispatch(job,stage="debug",event=e)["reason"],
                         "AUTO_AI_DISABLED")
        e = self.event(job,"integrate",labels=["studio:work:integrate","studio:evidence:upstream-verified"])
        self.assertEqual(self.dispatch(job,stage="integrate",event=e)["reason"],
                         "WORK_ORDER_JOB_KIND_MISMATCH")

    def test_qa_requires_separate_review_job_and_exact_source_sha(self):
        job = self.job()
        job.update(state="SUBMITTED",final_commit_sha="e"*40,submitted_by_slot="W02")
        qa = {"id": "QA-FEATURE-99-CODE-01", "kind": "review", "approved": True,
              "state":"READY", "subject_job":job["id"],
              "role":"rpg-technical-qa-analyst"}
        e = self.event(job,"qa",job_id=qa["id"])
        e["pull_request"]["body"] = e["pull_request"]["body"] + "\nStudio-Subject-SHA: " + job["final_commit_sha"]
        x = routing.route_event(e,self.policy,{"jobs":[job,qa]},{"auto_ai_enabled": False})
        self.assertEqual(x["reason"],"AUTO_AI_DISABLED")
        self.assertEqual(x["source_repository"],SOURCE)
        self.assertEqual(x["profile"],"W05")
        e["pull_request"]["body"] = e["pull_request"]["body"].replace(job["final_commit_sha"],"d"*40)
        x = routing.route_event(e,self.policy,{"jobs":[job,qa]},{"auto_ai_enabled": False})
        self.assertEqual(x["reason"],"WORK_ORDER_QA_COMMIT_MISMATCH")

    def test_architecture_impact_blocked_even_for_hub_order(self):
        job = self.job()
        job["architecture_impact"]="change"
        job["architecture_review"]["verdict"]="PROPOSAL_REQUIRED"
        x = self.dispatch(job)
        self.assertEqual(x["reason"],"ARCHITECTURE_PROPOSAL_PR_REQUIRED")

    def test_source_budgets_never_silently_opened(self):
        job = self.job()
        e = self.event(job)
        x = self.dispatch(job,event=e,config={"auto_ai_enabled": True,
              "source_writes_enabled": True,
              "repos": [{"repo":SOURCE,"branch":"main"}]})
        self.assertEqual(x["reason"],"NO_SPEND_AUTHORIZATION")

if __name__=="__main__":
    unittest.main()
