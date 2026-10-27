"""Architectural-change PR enforcement: deterministic, no network or source writes."""
import copy
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import architecture_enforcer as enforcer
import production
import studio
import work_event_router


class ArchitectureEnforcerTests(unittest.TestCase):
    def setUp(self):
        self.job = {
            "id": "FEATURE-6-G00", "kind": "code", "role": "rpg-ai-programmer",
            "repository": "SIGAAMDAD/TheNomad_GOAP", "workstream": "goap_core",
            "language": "C++17", "branch": "main", "work_branch": "studio/FEATURE-6-G00",
            "baseline_sha": "a"*40, "state": "BLOCKED", "approved": True,
            "architecture_impact": "change",
            "architecture_review": {"reviewer_role": "architecture_enforcer",
                                    "reviewed_baseline_sha": "a"*40,
                                    "verdict": "PROPOSAL_REQUIRED"},
        }
        self.proposal = {
            "pr_url": "https://github.com/SIGAAMDAD/TheNomad_GOAP/pull/12",
            "repository": "SIGAAMDAD/TheNomad_GOAP", "base_branch": "main",
            "head_branch": "studio/arch/FEATURE-6-G00", "pr_number": 12,
            "head_sha": "b"*40, "author": "worker-bot",
        }

    def acceptance(self):
        return {
            "status": "ACCEPTED",
            "proposal_pr_url": self.proposal["pr_url"],
            "reviewed_head_sha": "b"*40,
            "accepted_source_baseline_sha": "a"*40,
            "approved_by": "SIGAAMDAD",
            "verification_source": "authenticated_github_pr_review_and_merge_readback",
            "merged_commit_sha": "c"*40
        }

    def pr(self):
        return {
            "number": 12, "merged": True, "merged_at": "2026-10-27T01:01:00Z",
            "merged_by": {"login": "SIGAAMDAD"},
            "user": {"login": "worker-bot"},
            "head": {"ref": self.proposal["head_branch"], "sha": "b"*40,
                     "repo": {"full_name": self.job["repository"]}},
            "base": {"ref": "main", "repo": {"full_name": self.job["repository"]}}
        }

    def reviews(self):
        return [{"state": "APPROVED", "commit_id": "b"*40,
                 "user": {"login": "SIGAAMDAD"}}]

    def test_change_requires_proposal_and_acceptance(self):
        self.assertEqual(enforcer.gate(self.job), "ARCHITECTURE_PROPOSAL_PR_REQUIRED")
        self.job["architecture_proposal"] = self.proposal
        self.assertEqual(enforcer.gate(self.job), "ARCHITECTURE_OWNER_ACCEPTANCE_REQUIRED")
        self.job["architecture_acceptance"] = self.acceptance()
        self.assertIsNone(enforcer.gate(self.job))

    def test_no_change_still_requires_enforcer_clearance(self):
        job = copy.deepcopy(self.job)
        job["architecture_impact"] = "none"
        self.assertEqual(enforcer.gate(job), "ARCHITECTURE_ENFORCER_CLEARANCE_REQUIRED")
        job["architecture_review"]["verdict"] = "NO_CHANGE"
        self.assertIsNone(enforcer.gate(job))
        job["architecture_review"]["reviewed_baseline_sha"] = "d"*40
        self.assertEqual(enforcer.gate(job), "ARCHITECTURE_REVIEW_BASELINE_STALE")
        del job["architecture_impact"]
        self.assertEqual(enforcer.gate(job), "ARCHITECTURE_IMPACT_UNCLASSIFIED")

    def test_pr_must_be_in_owning_source_repo(self):
        p = dict(self.proposal, pr_url="https://github.com/SIGAAMDAD/TheNomad_Studio/pull/12")
        self.assertEqual(enforcer.proposal_contract(self.job, p),
                         "ARCHITECTURE_PROPOSAL_MUST_BE_SOURCE_PR")
        p = dict(self.proposal, head_branch="studio/FEATURE-6-G00")
        self.assertEqual(enforcer.proposal_contract(self.job, p),
                         "ARCHITECTURE_PROPOSAL_BRANCH_MISMATCH")
        p = dict(self.proposal, base_branch="develop")
        self.assertEqual(enforcer.proposal_contract(self.job, p),
                         "ARCHITECTURE_PROPOSAL_TARGET_MISMATCH")

    def test_real_github_pr_owner_approval_and_merge_required(self):
        self.assertIsNone(enforcer.verify_owner_readback(self.job, self.proposal,
                                                         self.pr(), self.reviews()))
        unmerged = self.pr()
        unmerged["merged"] = False
        self.assertEqual(enforcer.verify_owner_readback(self.job, self.proposal,
                         unmerged, self.reviews()), "ARCHITECTURE_OWNER_MERGE_REQUIRED")
        untrusted = self.pr()
        untrusted["merged_by"]["login"] = "other"
        self.assertEqual(enforcer.verify_owner_readback(self.job, self.proposal,
                         untrusted, self.reviews()), "ARCHITECTURE_OWNER_MERGE_REQUIRED")
        wrong_sha = self.reviews()
        wrong_sha[0]["commit_id"] = "1"*40
        self.assertEqual(enforcer.verify_owner_readback(self.job, self.proposal,
                         self.pr(), wrong_sha), "ARCHITECTURE_EXACT_SHA_OWNER_REVIEW_REQUIRED")

    def test_no_worker_can_approve_own_architecture_proposal(self):
        p = self.pr()
        p["user"]["login"] = "SIGAAMDAD"
        self.assertEqual(enforcer.verify_owner_readback(self.job, self.proposal,
                         p, self.reviews()), "ARCHITECTURE_EXACT_SHA_OWNER_REVIEW_REQUIRED")

    def test_approval_certificate_is_revision_bound(self):
        self.job["architecture_proposal"] = self.proposal
        self.job["architecture_acceptance"] = self.acceptance()
        self.job["architecture_acceptance"]["reviewed_head_sha"] = "e"*40
        self.assertEqual(enforcer.gate(self.job), "ARCHITECTURE_ACCEPTANCE_READBACK_MISMATCH")
        self.job["architecture_acceptance"] = self.acceptance()
        self.job["baseline_sha"] = "d"*40
        self.job["architecture_review"]["reviewed_baseline_sha"] = "d"*40
        self.assertEqual(enforcer.gate(self.job), "ARCHITECTURE_ACCEPTANCE_READBACK_MISMATCH")

    def test_routing_blocks_change_before_source_writes_gate(self):
        job = copy.deepcopy(self.job)
        job["id"] = "FEATURE-100-CODE-01"
        job["work_branch"] = "studio/FEATURE-100-CODE-01"
        job["state"] = "READY"
        policies = work_event_router.read_json(ROOT / "studio/workforce/hierarchy.v2.json")
        event = {"event_name": "pull_request", "action": "opened",
                 "pull_request": {
                     "number": 13, "draft": False,
                     "body": "Studio-Job: FEATURE-100-CODE-01\n",
                     "labels": [{"name": "studio:work:alpha"}],
                     "base": {"ref": "main", "repo": {"full_name": job["repository"]}},
                     "head": {"ref": job["work_branch"], "sha": "c"*40,
                              "repo": {"full_name": job["repository"]}}
                 }}
        result = work_event_router.route_event(event, policies, {"jobs":[job]}, {})
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "ARCHITECTURE_PROPOSAL_PR_REQUIRED")

    def test_ready_job_cannot_dispatch_without_review(self):
        from test_studio import fixture
        jobs, slots, config = fixture()
        j = {
            "id": "FEATURE-6-G00", "kind": "code", "role": "rpg-ai-programmer",
            "state": "READY", "approved": True,
            "repository": self.job["repository"], "workstream": "goap_core",
            "language": "C++17", "branch": "main",
            "work_branch": "studio/FEATURE-6-G00", "baseline_sha": "a"*40,
            "owned_paths": ["src/goap"], "acceptance": ["compile"],
            "architecture_impact": "change",
            "architecture_review": self.job["architecture_review"]
        }
        jobs["jobs"] = [j]
        config["source_writes_enabled"] = True
        config["repos"].append({"repo": "SIGAAMDAD/TheNomad_GOAP", "branch": "main"})
        self.assertIn("ARCHITECTURE_PROPOSAL_PR_REQUIRED",
                      " ".join(studio.validate(jobs, slots, config)))
        self.assertEqual(studio.dispatch(jobs, slots, config), [])

    def test_admission_requires_explicit_architectural_impact(self):
        feature = {"id":"FEATURE-6",
                   "issue_url":"https://github.com/SIGAAMDAD/TheNomad_Studio/issues/6"}
        entry = {
            "id": "FEATURE-6-G00", "kind": "code",
            "role": "rpg-ai-programmer",
            "role_manual_uri": "skills://rpg-ai-programmer",
            "brief": "Propose a new GOAP predicate contract",
            "owned_paths": ["include/goap"], "acceptance": ["Build fixture"],
            "qa_role": "rpg-technical-qa-analyst",
            "repository": self.job["repository"], "workstream": "goap_core",
            "language": "C++17", "branch": "main",
            "work_branch": self.job["work_branch"], "baseline_sha": "a"*40
        }
        spec = {"schema":1, "feature_id":"FEATURE-6",
                "source_issue":feature["issue_url"], "jobs":[entry]}
        cfg = {"schema": 1, "source_writes_enabled": False}
        existing = {"schema": 1, "jobs": []}
        with self.assertRaisesRegex(ValueError, "architecture_impact must explicitly"):
            production.build_jobs(feature, spec, cfg, existing)
        entry["architecture_impact"] = "change"
        produced, _ = production.build_jobs(feature, spec, cfg, existing)
        self.assertEqual(produced[0]["state"], "BLOCKED")
        self.assertEqual(produced[0]["blocker"], "ARCHITECTURE_PROPOSAL_REQUIRED")


if __name__ == "__main__":
    unittest.main()
