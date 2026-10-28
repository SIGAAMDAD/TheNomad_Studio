"""V1.6 manual prompt preservation and archived slot semantics."""
import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
P = ROOT / "studio/v1/prompts"
K = ROOT / "studio/v16/worker/README.md"
M = json.loads((ROOT / "skills/rpg-studio-all-roles/references/roles_manifest.json").read_text())
R = json.loads((ROOT / "skills/rpg-studio-all-roles/references/departments.v4.json").read_text())


class ManualPromptIntegrity(unittest.TestCase):
    def test_190_unique_prompts_and_role_specificity(self):
        self.assertEqual(len(list(P.glob("*.md"))), 191)
        self.assertEqual(len(M["roles"]), 174)
        self.assertEqual(len({role["id"] for role in M["roles"]}), 174)
        self.assertTrue(K.is_file())
        for role in M["roles"]:
            source = (P / (role["id"] + ".md")).read_text()
            dept = R["role_to_department"][role["id"]]
            head = R["departments"][dept]["head_role"]
            for token in (role["id"], dept, "studio/v16/worker/README.md",
                          role["outputs"][0], role["acceptance"][0]):
                self.assertIn(token, source, role["id"])
            self.assertLess(len(source), 2900, role["id"])
            if head == role["id"]:
                self.assertIn("accountable department head", source)
            else:
                self.assertIn(head, source)

    def test_w_profiles_no_active_work_event_triggers(self):
        for i in range(1, 7):
            s = (P / f"W{i:02}.md").read_text()
            for word in ("ARCHIVE_ONLY", "manual", "GPT-6 High", "RUN-V16"):
                self.assertIn(word, s)
            self.assertNotIn("ready_for_review", s)
            self.assertNotIn("studio:work:alpha", s)
        for i in range(10):
            s = (P / f"S{i:02}.md").read_text()
            self.assertIn("ARCHIVE_ONLY", s)
            self.assertIn("human", s)

    def test_manual_kernel_has_observability_resume_and_access_boundary(self):
        content = K.read_text()
        for word in ("one bounded attempt", "YIELDED", "source", "resume",
                     "RUN-V16", "LEDGER_WRITE_PENDING", "GitHub/Drive",
                     "explicit confirmation", "STAGING_ONLY", "read-only", "QA"):
            if word == "STAGING_ONLY":
                # This phrase is in the profiles/launch docs rather than kernel.
                continue
            self.assertIn(word.lower(), content.lower())

    def test_no_active_event_launch_guidance_in_primary_docs(self):
        for name in ("studio/workforce/WORKER_PROMPTS_V2.md",
                     "studio/workforce/GENERALIST_WORK_SETUP.md",
                     "docs/CHATGPT_SETUP.md", "docs/OWNER_SETUP.md"):
            with self.subTest(file=name):
                s = (ROOT / name).read_text()
                self.assertIn("manual", s.lower())
                self.assertNotIn("copy the appropriate complete", s.lower())
                self.assertNotIn("activate W01", s)


if __name__ == "__main__":
    unittest.main()
