"""R06 candidate registry reproducibility and removal of runnable V1 prompts."""
import json
from pathlib import Path
import unittest
from scripts.build_current_registry import create

ROOT=Path(__file__).resolve().parents[3]

class CandidateRegistryTests(unittest.TestCase):
    def test_census_and_no_legacy_launchers(self):
        source=json.loads((ROOT/'studio/v16/worker/ROLE_REGISTRY.v1.json').read_text())
        departments=json.loads((ROOT/'studio/workforce/v4/departments.v4.json').read_text())
        output=create(source,departments)
        registry=json.loads(output)
        self.assertEqual(registry['role_count'],174)
        self.assertEqual(len(registry['roles']),174)
        self.assertEqual(registry['department_count'],12)
        self.assertEqual(len(registry['departments']),12)
        self.assertNotIn('studio/v1/prompts/',output.decode())
        self.assertEqual(registry['execution_authority'],'NONE')
        self.assertEqual(output,(ROOT/'studio/v16/worker/ROLE_REGISTRY_CURRENT_CANDIDATE.json').read_bytes())
        for role in registry['roles']:
            self.assertTrue(role['skill_uri'].startswith('skills://rpg-'))
