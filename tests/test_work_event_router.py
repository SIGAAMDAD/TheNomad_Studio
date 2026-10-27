#!/usr/bin/env python3
"""Offline unit tests for reviewed, non-spending event worker routing."""
import copy
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import work_event_router as router

ALPHA_ROLE = "rpg-gameplay-data-editor-programmer"
SOURCE_REPO = "SIGAAMDAD/NomadFramework"
HEAD = "a" * 40
BASE = "b" * 40


class WorkerRouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = router.read_json(ROOT / "studio/workforce/hierarchy.v2.json")

    def job(self, role=ALPHA_ROLE):
        return {"schema": 1, "jobs": [
            {"id": "FEATURE-100-CODE-01", "approved": True,
             "role": role, "kind": "code", "repository": SOURCE_REPO,
             "workstream": "framework_engine", "language": "C++17",
             "branch": "develop", "work_branch": "studio/FEATURE-100-CODE-01",
             "baseline_sha": BASE, "state": "READY"}
        ]}

    def event(self, labels=("studio:work:alpha",), action="synchronize", sha=HEAD):
        return {"event_name": "pull_request", "action": action,
                "pull_request": {
                    "number": 12, "draft": False,
                    "body": "Studio-Job: FEATURE-100-CODE-01\n",
                    "labels": [{"name": x} for x in labels],
                    "base": {"ref": "develop", "repo": {"full_name": SOURCE_REPO}},
                    "head": {"sha": sha, "ref": "studio/FEATURE-100-CODE-01",
                             "repo": {"full_name": SOURCE_REPO}}
                }}

    def test_full_roster_and_reporting_graph(self):
        self.assertEqual([], router.validate_hierarchy(self.policy))
        self.assertEqual(174, len(self.policy["roles"]))
        self.assertEqual(50, sum(r["department"] == "Engineering" for r in self.policy["roles"]))

    def test_cycles_are_not_allowed(self):
        p = copy.deepcopy(self.policy)
        p["roles"][0]["reports_to"] = p["roles"][0]["id"]
        self.assertTrue(any("cycle" in e for e in router.validate_hierarchy(p)))

    def test_no_default_astra_or_extra_task(self):
        p = copy.deepcopy(self.policy)
        p["profiles"]["W01"]["model"] = "GPT-6 Astra"
        self.assertTrue(any("Astra" in e for e in router.validate_hierarchy(p)))
        p = copy.deepcopy(self.policy)
        p["capacity"]["work_slots_proposed_active"].append("W05")
        self.assertTrue(any("active task count" in e for e in router.validate_hierarchy(p)))

    def test_safe_alpha_is_blocked_by_disabled_autonomy(self):
        x = router.route_event(self.event(), self.policy, self.job(),
                               {"auto_ai_enabled": False, "source_writes_enabled": False})
        self.assertEqual(x["status"], "BLOCKED")
        self.assertEqual(x["reason"], "AUTO_AI_DISABLED")
        self.assertFalse(x["model_invoked"])
        self.assertFalse(x["credits_spent"])

    def test_event_cannot_auto_spend_if_other_gates_are_true(self):
        x = router.route_event(self.event(), self.policy, self.job(),
                               {"auto_ai_enabled": True, "source_writes_enabled": True,
                                "repos": [{"repo": SOURCE_REPO, "branch": "develop"}]})
        self.assertEqual(x["reason"], "NO_SPEND_AUTHORIZATION")

    def test_high_risk_alpha_must_be_only_boilerplate(self):
        j = self.job("rpg-memory-systems-programmer")
        self.assertEqual(router.route_event(self.event(), self.policy, j)["reason"],
                         "HIGH_RISK_ALPHA_REQUIRES_BOILERPLATE_SCOPE")
        self.assertEqual(router.route_event(
            self.event(["studio:work:alpha", "studio:scope:boilerplate"]),
            self.policy, j, {})["reason"], "AUTO_AI_DISABLED")

    def test_missing_or_ambiguous_pr_stage_does_not_route(self):
        e = self.event(labels=())
        self.assertEqual(router.route_event(e, self.policy)["reason"],
                         "EXACTLY_ONE_WORK_STAGE_REQUIRED")
        e = self.event(labels=("studio:work:alpha", "studio:work:debug"))
        self.assertEqual(router.route_event(e, self.policy)["reason"],
                         "EXACTLY_ONE_WORK_STAGE_REQUIRED")

    def test_unverified_pr_sha_does_not_route(self):
        self.assertEqual(router.route_event(
            self.event(sha="untrusted"), self.policy)["reason"],
            "UNVERIFIED_SOURCE_REF")

    def test_missing_approved_runtime_job_blocks(self):
        self.assertEqual(router.route_event(
            self.event(), self.policy)["reason"], "NO_AUTHORITATIVE_RUNTIME_JOB")
        j = self.job()
        j["jobs"][0]["approved"] = False
        self.assertEqual(router.route_event(
            self.event(), self.policy, j)["reason"], "JOB_NOT_APPROVED_OR_MISSING")

    def test_terra_is_distinct_midlevel_engineer(self):
        profiles = self.policy["profiles"]
        self.assertEqual(profiles["W01"]["model"], "GPT-6 Luna")
        self.assertEqual(profiles["W02"]["model"], "GPT-6.1 Terra")
        self.assertEqual(profiles["W03"]["model"], "GPT-6 Sol")
        self.assertEqual(profiles["W04"]["model"], "GPT-6 Sol")
        self.assertEqual(profiles["W05"]["model"], "GPT-6 Sol")
        self.assertEqual(profiles["W06"]["model"], "GPT-6.1 Sol")
        self.assertEqual(len(self.policy["capacity"]["work_slots_proposed_active"]), 5)
        self.assertEqual(len(self.policy["capacity"]["scheduled_slots_proposed_active"]), 5)

    def test_terra_can_implement_regular_code_but_not_critical_hot_paths(self):
        e = self.event(labels=("studio:work:implement",))
        x = router.route_event(e, self.policy, self.job())
        self.assertEqual(x["profile"], "W02")
        self.assertEqual(x["model"], "GPT-6.1 Terra")
        self.assertEqual(x["reason"], "AUTO_AI_DISABLED")
        critical = self.job("rpg-memory-systems-programmer")
        x = router.route_event(e, self.policy, critical)
        self.assertEqual(x["reason"], "HIGH_RISK_IMPLEMENTATION_REQUIRES_SENIOR")
        e = self.event(labels=("studio:work:implement", "studio:scope:boilerplate"))
        x = router.route_event(e, self.policy, critical)
        self.assertEqual(x["reason"], "AUTO_AI_DISABLED")

    def test_debug_requires_verified_failure(self):
        e = self.event(labels=("studio:work:debug",))
        self.assertEqual(router.route_event(e, self.policy, self.job())["reason"],
                         "VERIFIED_FAILURE_EVIDENCE_REQUIRED")
        e["pull_request"]["labels"].append({"name": "studio:evidence:ci-failed"})
        self.assertEqual(router.route_event(e, self.policy, self.job())["reason"],
                         "AUTO_AI_DISABLED")

    def test_integration_is_separate_from_debug_and_alpha(self):
        e = self.event(labels=("studio:work:integrate",))
        self.assertEqual(router.route_event(e, self.policy, self.job())["reason"],
                         "INTEGRATION_REQUIRES_OWN_JOB")
        j = self.job()
        j["jobs"][0]["kind"] = "integration"
        self.assertEqual(router.route_event(e, self.policy, j)["reason"],
                         "INDEPENDENT_UPSTREAM_VERIFICATION_REQUIRED")

    def test_independent_qa_uses_exact_source_subject(self):
        e = self.event(labels=("studio:work:qa",))
        e["pull_request"]["body"] = "Studio-Job: QA-FEATURE-100-CODE-01\n"
        j = self.job()
        j["jobs"][0].update({"state": "SUBMITTED", "final_commit_sha": HEAD,
                              "submitted_by_slot": "S07"})
        j["jobs"].append({"id": "QA-FEATURE-100-CODE-01", "kind": "review",
                          "role": "rpg-technical-qa-analyst", "approved": True,
                          "repository": "SIGAAMDAD/TheNomad_Studio",
                          "slot": "S09", "subject_job": "FEATURE-100-CODE-01"})
        self.assertEqual(router.route_event(e, self.policy, j)["reason"],
                         "AUTO_AI_DISABLED")
        j["jobs"][0]["final_commit_sha"] = BASE
        self.assertEqual(router.route_event(e, self.policy, j)["reason"],
                         "EXACT_SUBJECT_SHA_NOT_READY_FOR_REVIEW")

    def test_goap_repository_is_the_real_source_target(self):
        j = self.job("rpg-gameplay-data-editor-programmer")
        source = j["jobs"][0]
        source.update({"repository": "SIGAAMDAD/TheNomad_GOAP",
                       "workstream": "goap_core", "branch": "main",
                       "work_branch": "studio/FEATURE-100-CODE-01"})
        e = self.event()
        e["pull_request"]["base"]["repo"]["full_name"] = source["repository"]
        e["pull_request"]["base"]["ref"] = "main"
        e["pull_request"]["head"]["repo"]["full_name"] = source["repository"]
        x = router.route_event(e, self.policy, j)
        self.assertEqual(x["reason"], "AUTO_AI_DISABLED")
        self.assertEqual(x["source_repository"], "SIGAAMDAD/TheNomad_GOAP")
        self.assertEqual(x["target_branch"], "main")

    def test_mod_csharp_event_routes_to_nomadmain_not_legacy_game(self):
        job = self.job("rpg-gameplay-programmer")
        source = job["jobs"][0]
        source.update({"repository": "SIGAAMDAD/NomadMain",
                       "workstream": "mod_gameplay", "language": "C#",
                       "branch": "main"})
        e = self.event()
        e["pull_request"]["base"]["repo"]["full_name"] = "SIGAAMDAD/NomadMain"
        e["pull_request"]["base"]["ref"] = "main"
        e["pull_request"]["head"]["repo"]["full_name"] = "SIGAAMDAD/NomadMain"
        x = router.route_event(e, self.policy, job)
        self.assertEqual(x["reason"], "AUTO_AI_DISABLED")
        self.assertEqual(x["source_repository"], "SIGAAMDAD/NomadMain")
        source["language"] = "GDScript"
        self.assertEqual(router.route_event(e, self.policy, job)["reason"],
                         "MOD_GAMEPLAY_REQUIRES_CSHARP")

    def test_control_repo_cannot_be_implementation_repository(self):
        j = self.job()
        j["jobs"][0]["repository"] = "SIGAAMDAD/TheNomad_Studio"
        e = self.event()
        e["pull_request"]["base"]["repo"]["full_name"] = "SIGAAMDAD/TheNomad_Studio"
        e["pull_request"]["head"]["repo"]["full_name"] = "SIGAAMDAD/TheNomad_Studio"
        self.assertEqual(router.route_event(e, self.policy, j)["reason"],
                         "CONTROL_REPOSITORY_FORBIDDEN_FOR_SOURCE")

    def test_wrong_base_and_work_branches_are_rejected(self):
        e = self.event()
        e["pull_request"]["base"]["ref"] = "main"
        self.assertEqual(router.route_event(e, self.policy, self.job())["reason"],
                         "PR_BASE_BRANCH_MISMATCH")
        e = self.event()
        e["pull_request"]["head"]["ref"] = "develop"
        self.assertEqual(router.route_event(e, self.policy, self.job())["reason"],
                         "PR_HEAD_WORK_BRANCH_MISMATCH")

    def test_wrong_source_repository_or_fork_is_rejected(self):
        e = self.event()
        e["pull_request"]["head"]["repo"]["full_name"] = "someoneelse/fork"
        self.assertEqual(router.route_event(e, self.policy, self.job())["reason"],
                         "PR_HEAD_REPOSITORY_MISMATCH")
        e = self.event()
        e["pull_request"]["base"]["repo"]["full_name"] = "SIGAAMDAD/TheNomad_Studio"
        self.assertEqual(router.route_event(e, self.policy, self.job())["reason"],
                         "PR_BASE_REPOSITORY_MISMATCH")

    def test_never_substitute_default_branch_for_game(self):
        j = self.job()
        j["jobs"][0]["repository"] = "SIGAAMDAD/The_Nomad"
        j["jobs"][0]["branch"] = "main"
        e = self.event()
        e["pull_request"]["base"]["repo"]["full_name"] = "SIGAAMDAD/The_Nomad"
        e["pull_request"]["base"]["ref"] = "main"
        e["pull_request"]["head"]["repo"]["full_name"] = "SIGAAMDAD/The_Nomad"
        self.assertEqual(router.route_event(e, self.policy, j)["reason"],
                         "SOURCE_TARGET_BRANCH_MISMATCH")

    def test_missing_work_branch_is_not_routable(self):
        j = self.job()
        del j["jobs"][0]["work_branch"]
        self.assertEqual(router.route_event(self.event(), self.policy, j)["reason"],
                         "SOURCE_WORK_BRANCH_MISMATCH")

    def test_not_authorized_to_dispatch_while_draft(self):
        for role in self.policy["roles"]:
            self.assertNotEqual(role["implement_model"], "GPT-6 Astra")
        self.assertFalse(self.policy["controls"]["auto_spend_enabled"])
        self.assertFalse(self.policy["controls"]["work_event_triggers_installed"])


if __name__ == "__main__":
    unittest.main()
