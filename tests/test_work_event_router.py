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
             "branch": "develop", "baseline_sha": BASE, "state": "READY"}
        ]}

    def event(self, labels=("studio:work:alpha",), action="synchronize", sha=HEAD):
        return {"event_name": "pull_request", "action": action,
                "pull_request": {
                    "number": 12, "draft": False,
                    "body": "Studio-Job: FEATURE-100-CODE-01\n",
                    "labels": [{"name": x} for x in labels],
                    "base": {"repo": {"full_name": SOURCE_REPO}},
                    "head": {"sha": sha}
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
                               {"auto_ai_enabled": True, "source_writes_enabled": True})
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

    def test_debug_requires_verified_failure(self):
        e = self.event(labels=("studio:work:debug",))
        self.assertEqual(router.route_event(e, self.policy, self.job())["reason"],
                         "VERIFIED_FAILURE_EVIDENCE_REQUIRED")
        e["pull_request"]["labels"].append({"name": "studio:evidence:ci-failed"})
        self.assertEqual(router.route_event(e, self.policy, self.job())["reason"],
                         "AUTO_AI_DISABLED")

    def test_integration_requires_verified_dependencies(self):
        e = self.event(labels=("studio:work:integrate",))
        self.assertEqual(router.route_event(e, self.policy, self.job())["reason"],
                         "INDEPENDENT_UPSTREAM_VERIFICATION_REQUIRED")

    def test_not_authorized_to_dispatch_while_draft(self):
        for role in self.policy["roles"]:
            self.assertNotEqual(role["implement_model"], "GPT-6 Astra")
        self.assertFalse(self.policy["controls"]["auto_spend_enabled"])
        self.assertFalse(self.policy["controls"]["work_event_triggers_installed"])


if __name__ == "__main__":
    unittest.main()
