"""Integrity checks for the C8 acceptance inventory, not case certification."""
import ast
import json
from pathlib import Path
from scripts.sqlite_acceptance import valid_execution
import unittest

ROOT = Path(__file__).resolve().parents[3]


class C8InventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inventory = json.loads((ROOT / 'docs/production/c8/acceptance_inventory.json').read_text())
        cls.source = json.loads((ROOT / 'docs/sqlite/ACCEPTANCE_STATUS.json').read_text())

    def test_complete_unique_case_set(self):
        ids = [row['case_id'] for row in self.inventory['cases']]
        self.assertEqual(len(ids), 76)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), set(self.source['cases']))
        self.assertEqual(sum(name.startswith('HR-FAULT-') for name in ids), 26)

    def test_no_unsubstantiated_pass_and_links_exist(self):
        for case in self.inventory['cases']:
            with self.subTest(case=case['case_id']):
                self.assertIn(case['status'], ('PARTIAL', 'NOT_RUN', 'MAPPED'))
                self.assertEqual(case['subject_sha'], self.inventory['subject_sha'])
                self.assertTrue(case['gap'])
                # Reviewer verdict in source inventory is informational, never approval.
                if case['status'] == 'PARTIAL':
                    self.assertTrue(case['existing_assertions'])
                if case['status'] == 'MAPPED':
                    self.assertTrue(valid_execution(case.get('execution')))
                for evidence in case['existing_assertions']:
                    class_name, method = evidence['test'].split('.')
                    source = ROOT / evidence['path']
                    parsed = ast.parse(source.read_text())
                    classes = [n for n in parsed.body if isinstance(n, ast.ClassDef) and n.name == class_name]
                    self.assertEqual(len(classes), 1)
                    self.assertTrue(any(isinstance(n, ast.FunctionDef) and n.name == method for n in classes[0].body))
                    self.assertTrue(evidence['scope_and_gap'])


if __name__ == '__main__':
    unittest.main()
