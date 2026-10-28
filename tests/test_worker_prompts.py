"""V1.6 local-only High worker setup and 174-role registry regression gates."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("manual_v16_kernel", ROOT / "studio/v16/worker/manual_kernel.py")
kernel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kernel)


class ManualWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = kernel.validate_registry()
        cls.role_ids = {role["role_id"] for role in cls.registry["roles"]}
        cls.profiles = kernel.read_json(kernel.PROFILE)["specialties"]
        cls.example = {
            "job_id": "TEST-100", "worker_id": "MANUAL-ENGINEER-1",
            "role_id": "rpg-ai-programmer", "source_repo": "SIGAAMDAD/TheNomad_GOAP",
            "source_branch": "main", "source_base_sha": "a" * 40,
            "source_archive_sha256": "b" * 64,
            "owned_path_prefixes": ["src/planner", "tests/planner"],
            "acceptance_ids": ["AC-1", "AC-2"],
            "scope": "Implement a local bounded GOAP fixture and regression test",
        }

    def test_exact_174_registry_12_heads_hash_stable(self):
        self.assertEqual(len(self.registry["roles"]), 174)
        self.assertEqual(len(self.role_ids), 174)
        self.assertEqual(self.registry["department_count"], 12)
        self.assertEqual(self.registry, kernel.build_registry())
        self.assertEqual(self.registry["profiles_sha256"], kernel.digest(kernel.PROFILE))
        for role in self.registry["roles"]:
            self.assertTrue(role["head_role_id"] in self.role_ids)
            self.assertEqual(len(role["local_launcher_sha256"]), 64)
            self.assertEqual(role["manual_skill_uri"], "skills://" + role["role_id"])

    def test_all_role_prompts_keep_unique_mission_acceptance_and_manual_contract(self):
        for role in self.registry["roles"]:
            p = ROOT / role["local_launcher"]
            with self.subTest(role=role["role_id"]):
                s = p.read_text()
                self.assertIn(role["role_id"], s)
                self.assertIn(role["required_output"], s)
                self.assertIn(role["acceptance"], s)
                self.assertIn("studio/v16/worker/README.md", s)
                self.assertIn("GitHub/Drive", s)
                self.assertNotIn("PROMPT_KERNEL_V15.md", s)
                self.assertEqual(kernel.digest(p), role["local_launcher_sha256"])

    def test_specialties_no_active_model_dispatch(self):
        p = kernel.read_json(kernel.PROFILE)
        self.assertEqual(p["launch_mode"], "USER_OPENED_MANUAL_CHAT")
        self.assertEqual(p["reasoning"], "HIGH")
        self.assertEqual(p["executable_dispatch_routes"], [])
        for spec in ("JUNIOR_IMPLEMENTATION", "MID_LEVEL_ENGINEERING",
                     "SENIOR_DEBUGGING", "SENIOR_INTEGRATION", "INDEPENDENT_QA",
                     "DEPARTMENT_HEAD", "STUDIO_DIRECTOR", "SPECIALIST"):
            self.assertIn(spec, self.profiles)
        self.assertEqual(self.profiles["INDEPENDENT_QA"]["source_access"], "READ_ONLY")
        self.assertEqual(self.profiles["SENIOR_INTEGRATION"]["source_access"], "STAGING_ONLY")

    def test_offline_launcher_is_scoped_and_does_not_claim_verified_remote(self):
        s = kernel.launch_text(self.example, "JUNIOR_IMPLEMENTATION", self.registry)
        for token in ("GPT-6 HIGH", "LOCAL_PATCH_ONLY", "SIGAAMDAD/TheNomad_GOAP",
                      "src/planner", "before", "consent", "High", "attempt ID"):
            self.assertIn(token.lower(), s.lower())
        self.assertIn("independent remote readback NOT established", s)
        self.assertNotIn("source_writes_enabled=true", s)

    def test_reject_invalid_scope_path_and_roles(self):
        samples = [("role_id", "rpg-imaginary"), ("source_repo", "not-repo"),
                   ("source_branch", "wrong-default"), ("source_base_sha", "latest"),
                   ("owned_path_prefixes", ["src/../tests"]),
                   ("owned_path_prefixes", ["src\\secrets"]),
                   ("owned_path_prefixes", ["src/%2e%2e/foo"]),
                   ("owned_path_prefixes", ["src/📁"]),
                   ("owned_path_prefixes", [".github/workflows"]),
                   ("acceptance_ids", []), ("scope", "a")]
        for field, value in samples:
            with self.subTest(field=field, value=value):
                j = dict(self.example, **{field: value})
                with self.assertRaises(kernel.JobError):
                    kernel.validate_job(j, self.registry, "JUNIOR_IMPLEMENTATION")

    def test_cli_dispatch_is_text_only_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "job.json"
            path.write_text(json.dumps(self.example))
            base = [sys.executable, "-m", "studio.v16.worker.manual_kernel", "launch",
                    "--job", str(path), "--role", "rpg-ai-programmer", "--specialty"]
            ok = subprocess.run(base + ["JUNIOR_IMPLEMENTATION"], cwd=ROOT,
                                text=True, capture_output=True)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertIn("DRAFT LAUNCHER", ok.stdout)
            bad = subprocess.run(base + ["AUTOMATIC_PR_TRIGGER"], cwd=ROOT,
                                 text=True, capture_output=True)
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn("BLOCKED_LOCAL_INPUT", bad.stderr)

    def test_registry_detects_prompt_mutation_without_rebuilding(self):
        expected = copy.deepcopy(self.registry)
        expected["roles"][0]["local_launcher_sha256"] = "0" * 64
        self.assertNotEqual(expected, kernel.build_registry())

    def test_v16_contracts_and_retired_labels_static(self):
        retirement = kernel.read_json(ROOT / "studio/v16/contracts/legacy-retirement.v1.json")
        self.assertFalse(retirement["active_ai_chat_dispatch"])
        self.assertEqual(retirement["historical_delivery_authority"], "ARCHIVE_ONLY")
        self.assertEqual(len(retirement["historical_routes"]), 5)
        hierarchy = kernel.read_json(ROOT / "studio/workforce/hierarchy.v2.json")
        self.assertEqual(hierarchy["status"], "ARCHIVE_ONLY_NO_AI_DISPATCH")
        self.assertTrue(all(p["mode"] == "ARCHIVED_LEGACY_STAGE" for p in hierarchy["profiles"].values()))
        self.assertEqual(hierarchy["event_trigger_strategy"]["mode"], "ARCHIVE_ONLY_NO_DISPATCH")


if __name__ == "__main__":
    unittest.main()
