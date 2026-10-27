"""Validate that scheduled runtime reconciliation has all reviewed dependencies."""
import ast
import pathlib
import re
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]
WF=(ROOT/".github/workflows/studio-reconcile.yml").read_text()
FILES={p.stem for p in (ROOT/"scripts").glob("*.py")}

class ReconcileBundleTests(unittest.TestCase):
    def test_all_local_imports_are_preserved(self):
        relevant=["studio","production","github_api","source_targets","architecture_enforcer"]
        for name in relevant:
            self.assertIn("scripts/"+name+".py",WF)
        self.assertIn("source_targets.v1.json",WF)
        self.assertIn("architecture_policy.v1.json",WF)
        staged=set(re.findall(r"scripts/([a-z_]+)\.py",WF))
        self.assertTrue(set(relevant).issubset(staged))
        for name in relevant:
            tree=ast.parse((ROOT/"scripts"/(name+".py")).read_text())
            imports={alias.name.partition(".")[0] for node in ast.walk(tree)
                     if isinstance(node,ast.Import) for alias in node.names}
            imports|={node.module.partition(".")[0] for node in ast.walk(tree)
                      if isinstance(node,ast.ImportFrom) and node.module}
            missing=(imports & FILES)-staged
            self.assertFalse(missing,f"{name} requires missing reviewed scripts {missing}")

    def test_uses_explicit_runtime_root_and_fails_on_invalid_state(self):
        self.assertIn('PYTHONPATH: ${{ runner.temp }}/nomad_scripts',WF)
        self.assertIn('--root "$GITHUB_WORKSPACE" validate',WF)
        self.assertIn("python \"$RUNNER_TEMP/nomad_scripts/studio.py\"",WF)

if __name__=="__main__":unittest.main()
