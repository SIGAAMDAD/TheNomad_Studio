"""Unit tests of independent QA *mechanisms*, not Studio Tier B acceptance."""
import tempfile
import unittest
from pathlib import Path
from qa.core import Result, source_files, tree_digest, safe_relative
from qa.contract import assert_owned, path_owned, ContractError
from qa.scanning import scan

PLAN = {'workgroups': [{'id':f'WG-{c}', 'owned_paths': [f'src/{c}/**'] if c!='L' else ['qa/**', 'tests/acceptance/**', 'tests/unit/l/**']} for c in 'ABCDEFGHIJKLM']}

class ContractTests(unittest.TestCase):
    def test_l_ownership(self):
        assert_owned('qa/runner.py', 'WG-L', PLAN)
        assert_owned('tests/acceptance/test_offline.py', 'WG-L', PLAN)
        for bad in ('CONTRACTS/openapi.json', 'plan.json', 'src/studio/app/server.py', '../qa/runner.py', '/tmp/foo'):
            with self.subTest(path=bad), self.assertRaises((ValueError, ContractError)):
                assert_owned(bad, 'WG-L', PLAN)

    def test_tree_digest_immutable_to_timestamp(self):
        with tempfile.TemporaryDirectory() as d:
            r = Path(d)
            (r/'x.py').write_text('hello')
            first = tree_digest(source_files(r))
            (r/'x.py').touch()
            self.assertEqual(first, tree_digest(source_files(r)))
            (r/'x.py').write_text('HELLO')
            self.assertNotEqual(first, tree_digest(source_files(r)))

    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root/'source').write_text('bytes')
            (root/'link').symlink_to(root/'source')
            with self.assertRaises(ValueError):
                source_files(root)

    def test_safe_relative(self):
        for value in ('../a', 'a/../b', '.hidden/./z', 'a\\b', '/x', '', 'a//b', 'C:/root'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                safe_relative(value)

    def test_missing_evidence_cannot_pass(self):
        with self.assertRaises(ValueError):
            Result('X', 'B', 'PASS', 'claimed', {})

class StaticSourceTests(unittest.TestCase):
    def test_detects_prohibited_api_and_legacy(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root/'src').mkdir()
            (root/'src/worker.py').write_text('from openai import OpenAI\nclient = OpenAI()\nlegacy_schema = True\n')
            hits=scan(root)
            self.assertTrue(any(h['requirement']=='NEG-AI-API' for h in hits))
            self.assertTrue(any(h['requirement']=='NEG-LEGACY' for h in hits))
            (root/'src/worker.py').write_text('def manual_launch_prompt():\n    return "copy to browser"\n')
            self.assertEqual([], scan(root))

if __name__ == '__main__':
    unittest.main()
