#!/usr/bin/env python3
"""Source identity and branch safety checks for studio engineering jobs."""
import copy
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import source_targets
import studio
import production


class SourceTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.routes = source_targets.read_routes()

    def source_job(self, repo="SIGAAMDAD/TheNomad_GOAP", target="main"):
        return {"id": "FEATURE-6-G00", "kind": "code", "role": "rpg-ai-programmer",
                "repository": repo, "branch": target,
                "work_branch": "studio/FEATURE-6-G00", "baseline_sha": "a"*40}

    def pr(self, job):
        repo = job["repository"]
        return {"base": {"ref": job["branch"], "repo": {"full_name": repo}},
                "head": {"ref": job["work_branch"], "sha": "b"*40,
                         "repo": {"full_name": repo}}}

    def test_four_real_source_repositories_and_exact_branches(self):
        got = source_targets.route_table(self.routes)
        self.assertEqual(got, {
            "SIGAAMDAD/TheNomad_GOAP": "main",
            "SIGAAMDAD/NomadFramework": "develop",
            "SIGAAMDAD/The_Nomad": "Nomad3D",
            "SIGAAMDAD/Terrain3D": "Terrain3D_NomadRenderer"
        })
        self.assertNotIn("SIGAAMDAD/TheNomad_Studio", got)

    def test_goap_job_is_source_repo_not_studio_repo(self):
        j = self.source_job()
        self.assertIsNone(source_targets.validate_source_job(j, self.routes))
        self.assertIsNone(source_targets.validate_source_pr(self.pr(j), j, self.routes))
        self.assertEqual(source_targets.expected_work_branch(j["id"]), j["work_branch"])

    def test_control_repo_cannot_own_real_gameplay_source(self):
        job = self.source_job(repo="SIGAAMDAD/TheNomad_Studio")
        self.assertEqual(source_targets.validate_source_job(job, self.routes),
                         "CONTROL_REPOSITORY_FORBIDDEN_FOR_SOURCE")

    def test_wrong_target_branch_or_work_branch_rejected(self):
        job = self.source_job(repo="SIGAAMDAD/The_Nomad", target="main")
        self.assertEqual(source_targets.validate_source_job(job, self.routes),
                         "SOURCE_TARGET_BRANCH_MISMATCH")
        job = self.source_job()
        job["work_branch"] = "main"
        self.assertEqual(source_targets.validate_source_job(job, self.routes),
                         "SOURCE_WORK_BRANCH_MISMATCH")

    def test_fork_and_branch_mismatch_rejected(self):
        job = self.source_job()
        pr = self.pr(job)
        pr["head"]["repo"]["full_name"] = "stranger/fork"
        self.assertEqual(source_targets.validate_source_pr(pr, job, self.routes),
                         "PR_HEAD_REPOSITORY_MISMATCH")
        pr = self.pr(job)
        pr["base"]["ref"] = "develop"
        self.assertEqual(source_targets.validate_source_pr(pr, job, self.routes),
                         "PR_BASE_BRANCH_MISMATCH")
        pr = self.pr(job)
        pr["head"]["ref"] = "studio/ANOTHER-JOB"
        self.assertEqual(source_targets.validate_source_pr(pr, job, self.routes),
                         "PR_HEAD_WORK_BRANCH_MISMATCH")

    def test_unlisted_live_repo_never_receives_automatic_source_dispatch(self):
        job = self.source_job()
        config = {"repos": [{"repo": "SIGAAMDAD/NomadFramework", "branch": "develop"}]}
        self.assertFalse(source_targets.allowed_in_runtime(job, config))
        config["repos"].append({"repo": job["repository"], "branch": job["branch"]})
        self.assertTrue(source_targets.allowed_in_runtime(job, config))

    def test_production_spec_rejects_control_repo_and_wrong_branch(self):
        base = {"schema": 1, "feature_id": "FEATURE-6",
                "source_issue": "https://github.com/SIGAAMDAD/TheNomad_Studio/issues/6",
                "jobs": [self.source_job() | {
                    "brief": "Create verified GOAP project bootstrap",
                    "acceptance": ["Build with C++17 and test compiler"],
                    "owned_paths": ["src/goap"],
                    "qa_role": "rpg-technical-qa-analyst",
                    "role_manual_uri": "skills://rpg-ai-programmer",
                }]}
        feature = {"id": "FEATURE-6", "issue_url": base["source_issue"]}
        existing = {"schema": 1, "jobs": []}
        config = {"schema": 1, "source_writes_enabled": False}
        produced, candidate = production.build_jobs(feature, base, config, existing)
        self.assertEqual(produced[0]["state"], "BLOCKED")
        self.assertEqual(produced[0]["repository"], "SIGAAMDAD/TheNomad_GOAP")
        self.assertEqual(produced[0]["work_branch"], "studio/FEATURE-6-G00")
        invalid = copy.deepcopy(base)
        invalid["jobs"][0]["repository"] = "SIGAAMDAD/TheNomad_Studio"
        with self.assertRaisesRegex(ValueError, "CONTROL_REPOSITORY_FORBIDDEN"):
            production.build_jobs(feature, invalid, config, existing)

    def test_source_gate_validates_real_work_branch(self):
        config = {"schema": 1, "director_slot": "S00", "max_concurrent_writers": 2,
                  "max_reviewers": 1, "source_writes_enabled": False,
                  "auto_ai_enabled": False, "auto_dispatch_enabled": False,
                  "repos": [{"repo": "SIGAAMDAD/TheNomad_GOAP", "branch": "main"}]}
        slots = {"schema": 1, "slots": [
            {"id": f"S{i:02}", "enabled": (i == 0), "epoch": 0, "job": None}
            for i in range(10)
        ]}
        job = self.source_job()
        job.update({"state": "READY", "approved": True,
                    "acceptance": ["compile"], "owned_paths": ["src/goap"]})
        jobs = {"schema": 1, "jobs": [job]}
        self.assertEqual(studio.validate(jobs, slots, config), [])
        job["repository"] = "SIGAAMDAD/TheNomad_Studio"
        self.assertIn("CONTROL_REPOSITORY_FORBIDDEN_FOR_SOURCE",
                      " ".join(studio.validate(jobs, slots, config)))


if __name__ == "__main__":
    unittest.main()
