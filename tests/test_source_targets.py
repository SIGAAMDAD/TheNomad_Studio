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
        workstreams = {
            "SIGAAMDAD/TheNomad_GOAP": "goap_core",
            "SIGAAMDAD/NomadFramework": "framework_engine",
            "SIGAAMDAD/The_Nomad": "core_gameplay",
            "SIGAAMDAD/Terrain3D": "rendering_engine",
            "SIGAAMDAD/NomadMain": "mod_gameplay",
        }
        return {"id": "FEATURE-6-G00", "kind": "code", "role": "rpg-ai-programmer",
                "repository": repo, "branch": target,
                "workstream": workstreams.get(repo), "language": "C#" if repo.endswith("/NomadMain") else "C++17",
                "work_branch": "studio/FEATURE-6-G00", "baseline_sha": "a"*40}

    def pr(self, job):
        repo = job["repository"]
        return {"base": {"ref": job["branch"], "repo": {"full_name": repo}},
                "head": {"ref": job["work_branch"], "sha": "b"*40,
                         "repo": {"full_name": repo}}}

    def test_five_real_source_repositories_and_exact_branches(self):
        got = source_targets.route_table(self.routes)
        self.assertEqual(got, {
            "SIGAAMDAD/TheNomad_GOAP": "main",
            "SIGAAMDAD/NomadFramework": "develop",
            "SIGAAMDAD/The_Nomad": "Nomad3D",
            "SIGAAMDAD/Terrain3D": "Terrain3D_NomadRenderer",
            "SIGAAMDAD/NomadMain": "main"
        })
        self.assertNotIn("SIGAAMDAD/TheNomad_Studio", got)

    def test_goap_job_is_source_repo_not_studio_repo(self):
        j = self.source_job()
        self.assertIsNone(source_targets.validate_source_job(j, self.routes))
        self.assertIsNone(source_targets.validate_source_pr(self.pr(j), j, self.routes))
        self.assertEqual(source_targets.expected_work_branch(j["id"]), j["work_branch"])

    def test_owner_workstreams_route_to_distinct_source_repositories(self):
        cases = [
            ("rendering_engine", "SIGAAMDAD/Terrain3D", "Terrain3D_NomadRenderer", "C++17"),
            ("framework_engine", "SIGAAMDAD/NomadFramework", "develop", "C++17"),
            ("core_gameplay", "SIGAAMDAD/The_Nomad", "Nomad3D", "C#"),
            ("mod_gameplay", "SIGAAMDAD/NomadMain", "main", "C#"),
            ("goap_core", "SIGAAMDAD/TheNomad_GOAP", "main", "C++17"),
        ]
        for workstream, repo, branch, language in cases:
            with self.subTest(workstream=workstream):
                job = self.source_job(repo=repo, target=branch)
                job.update({"workstream": workstream, "language": language})
                self.assertIsNone(source_targets.validate_source_job(job, self.routes))
                self.assertIsNone(source_targets.validate_source_pr(self.pr(job), job, self.routes))

    def test_mod_specific_code_must_be_in_nomadmain_csharp(self):
        mod = self.source_job(repo="SIGAAMDAD/NomadMain", target="main")
        self.assertIsNone(source_targets.validate_source_job(mod, self.routes))
        wrong_lang = dict(mod, language="C++17")
        self.assertEqual(source_targets.validate_source_job(wrong_lang, self.routes),
                         "MOD_GAMEPLAY_REQUIRES_CSHARP")
        wrong_repo = dict(mod, repository="SIGAAMDAD/The_Nomad", branch="Nomad3D")
        self.assertEqual(source_targets.validate_source_job(wrong_repo, self.routes),
                         "SOURCE_WORKSTREAM_REPOSITORY_MISMATCH")
        wrong_stream = dict(mod, workstream="core_gameplay")
        self.assertEqual(source_targets.validate_source_job(wrong_stream, self.routes),
                         "SOURCE_WORKSTREAM_REPOSITORY_MISMATCH")
        missing_stream = dict(mod)
        missing_stream.pop("workstream")
        self.assertEqual(source_targets.validate_source_job(missing_stream, self.routes),
                         "SOURCE_WORKSTREAM_REQUIRED")

    def test_core_gameplay_cannot_be_routed_into_nomadmain(self):
        core = self.source_job(repo="SIGAAMDAD/The_Nomad", target="Nomad3D")
        self.assertIsNone(source_targets.validate_source_job(core, self.routes))
        bad = dict(core, repository="SIGAAMDAD/NomadMain", branch="main", language="C#")
        self.assertEqual(source_targets.validate_source_job(bad, self.routes),
                         "SOURCE_WORKSTREAM_REPOSITORY_MISMATCH")

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

    def test_production_admission_preserves_mod_language_and_owner_repo(self):
        feature = {
            "id": "FEATURE-6",
            "issue_url": "https://github.com/SIGAAMDAD/TheNomad_Studio/issues/6"
        }
        mod = self.source_job(repo="SIGAAMDAD/NomadMain", target="main")
        mod.update({
            "id": "FEATURE-6-MOD-01", "work_branch": "studio/FEATURE-6-MOD-01",
            "role": "rpg-gameplay-programmer",
            "role_manual_uri": "skills://rpg-gameplay-programmer",
            "brief": "Implement mod-dependent C# behavior",
            "owned_paths": ["Source/Items"],
            "acceptance": ["Build NomadMain.csproj and run behavior tests"],
            "qa_role": "rpg-technical-qa-analyst"
        })
        spec = {"schema": 1, "feature_id": "FEATURE-6",
                "source_issue": feature["issue_url"], "jobs": [mod]}
        cfg = {"schema": 1, "source_writes_enabled": False}
        out, _ = production.build_jobs(feature, spec, cfg, {"schema": 1, "jobs": []})
        self.assertEqual(out[0]["state"], "BLOCKED")
        self.assertEqual(out[0]["repository"], "SIGAAMDAD/NomadMain")
        self.assertEqual(out[0]["workstream"], "mod_gameplay")
        self.assertEqual(out[0]["language"], "C#")
        bad = copy.deepcopy(spec)
        bad["jobs"][0]["language"] = "C++17"
        with self.assertRaisesRegex(ValueError, "MOD_GAMEPLAY_REQUIRES_CSHARP"):
            production.build_jobs(feature, bad, cfg, {"schema": 1, "jobs": []})
        wrong_owner = copy.deepcopy(spec)
        wrong_owner["jobs"][0]["repository"] = "SIGAAMDAD/The_Nomad"
        wrong_owner["jobs"][0]["branch"] = "Nomad3D"
        with self.assertRaisesRegex(ValueError, "SOURCE_WORKSTREAM_REPOSITORY_MISMATCH"):
            production.build_jobs(feature, wrong_owner, cfg, {"schema": 1, "jobs": []})

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
