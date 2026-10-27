"""Source-grounded checks for five reusable native Work task prompts."""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "studio" / "workforce" / "WORKER_PROMPTS_V2.md"
STAGES = {
    "W01": ("alpha", "studio:work:alpha", "GPT-6 Luna"),
    "W02": ("implement", "studio:work:implement", "GPT-6.1 Terra"),
    "W03": ("debug", "studio:work:debug", "GPT-6 Sol"),
    "W04": ("integrate", "studio:work:integrate", "GPT-6 Sol"),
    "W05": ("qa", "studio:work:qa", "GPT-6 Sol"),
}
ROUTES = (
    "SIGAAMDAD/Terrain3D:Terrain3D_NomadRenderer",
    "SIGAAMDAD/NomadFramework:develop",
    "SIGAAMDAD/The_Nomad:Nomad3D",
    "SIGAAMDAD/NomadMain:main",
    "SIGAAMDAD/TheNomad_GOAP:main",
)


class WorkerPromptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = PROMPTS.read_text(encoding="utf-8")
        cls.blocks = {
            key: body for key, body in re.findall(
                r"(?ms)^## (W0[1-5]) [^\n]*\n.*?\x60\x60\x60text\n(.*?)\n\x60\x60\x60", cls.content
            )
        }

    def test_exactly_five_unique_canonical_prompts(self):
        self.assertEqual(set(self.blocks), set(STAGES))

    def test_each_worker_verifies_event_repo_baseline_and_gates(self):
        for wid, (stage, label, model) in STAGES.items():
            with self.subTest(worker=wid):
                p = self.blocks[wid]
                self.assertIn(f"studio/workorders/<JOB_ID>/{stage}", p)
                self.assertIn("action=ready_for_review", p)
                self.assertIn(label, p)
                self.assertIn("studio/runtime/studio/jobs.json", p)
                self.assertIn("studio/runtime/studio/config.json", p)
                self.assertIn("source_writes_enabled=true", p)
                self.assertIn("auto_ai_enabled=true", p)
                self.assertIn("source_targets.v1.json", p)
                self.assertIn("architecture_policy.v1.json", p)
                self.assertIn("lease", p)
                self.assertIn("BLOCKED", p)
                for route in ROUTES:
                    self.assertIn(route, p)
                self.assertIn(model, self.content)

    def test_new_branch_and_continuation_rules(self):
        self.assertIn("MAY create its missing studio/<JOB_ID>", self.blocks["W01"])
        self.assertIn("approved baseline SHA", self.blocks["W01"])
        self.assertIn("force-push", self.blocks["W01"])
        for worker in ("W01", "W02", "W03", "W04"):
            self.assertIn("source", self.blocks[worker].lower())
            self.assertIn("commit", self.blocks[worker].lower())
        self.assertIn("No source writes", self.blocks["W05"])

    def test_qa_independence_and_subject_sha(self):
        qa = self.blocks["W05"]
        self.assertIn("Studio-Subject-SHA", qa)
        self.assertIn("final_commit_sha", qa)
        self.assertIn("PASS or REWORK", qa)
        self.assertIn("NOT_RUN", qa)
        self.assertIn("Do not commit or push implementation code", qa)

    def test_controller_and_source_not_conflated(self):
        for p in self.blocks.values():
            self.assertIn("TheNomad_Studio", p)
            self.assertIn("METADATA-ONLY", p)
            self.assertIn("SOURCE", p)
        self.assertIn("prompts do NOT select execution model", self.content)


if __name__ == "__main__":
    unittest.main()
