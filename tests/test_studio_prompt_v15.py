"""Enforce V1.5 prompt routing, mission specificity, authority and compactness."""
import json
import pathlib
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]
P=ROOT/"studio/v1/prompts"
K=ROOT/"studio/v1/PROMPT_KERNEL_V15.md"
M=json.loads((ROOT/"skills/rpg-studio-all-roles/references/roles_manifest.json").read_text())
R=json.loads((ROOT/"skills/rpg-studio-all-roles/references/departments.v4.json").read_text())

class PromptIntegrity(unittest.TestCase):
    def test_190_unique_prompts_and_role_specificity(self):
        self.assertEqual(len(list(P.glob("*.md"))),191)
        self.assertEqual(len(M["roles"]),174)
        self.assertEqual(len({role["id"] for role in M["roles"]}),174)
        self.assertTrue(K.is_file())
        for role in M["roles"]:
            source=(P/(role["id"]+".md")).read_text()
            dept=R["role_to_department"][role["id"]]
            head=R["departments"][dept]["head_role"]
            for token in (role["id"],dept,"PROMPT_KERNEL_V15.md",
                          role["outputs"][0],role["acceptance"][0]):
                self.assertIn(token,source,role["id"])
            self.assertLess(len(source),2600,role["id"])
            if head==role["id"]:
                self.assertIn("accountable department head",source)
            else:
                self.assertIn(head,source)
    def test_worker_triggers(self):
        for label in ("alpha","implement","debug","integrate","qa"):
            self.assertTrue(any("studio:work:"+label in
                (P/f"W{i:02}.md").read_text() for i in range(1,6)))
        self.assertIn("ready_for_review",(P/"W01.md").read_text())
        self.assertIn("MANUAL_ONLY",(P/"W06.md").read_text())
        self.assertIn("23:00",(P/"S00.md").read_text())
    def test_kernel_has_controls(self):
        content=K.read_text()
        for word in ("ledger","YIELDED","STARTED","merge","resume_required=true",
                     "elapsed_seconds","assigned","Work/API"):
            self.assertIn(word,content)

if __name__=="__main__": unittest.main()
