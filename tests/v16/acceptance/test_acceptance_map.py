"""Acceptance-map validation must never promote unproven cases."""
import copy
import unittest
import subprocess
import tempfile
from pathlib import Path
from scripts.sqlite_acceptance import check_map, execute_case, run_tests, clean_head


class AcceptanceMapTests(unittest.TestCase):
    def setUp(self):
        self.sha = 'a' * 40
        self.expected = {'cases': {'TX-01': 'NOT_RUN', 'PUB-01': 'NOT_RUN'}}
        self.inventory = {'subject_sha': self.sha, 'cases': [
            {'case_id': case, 'subject_sha': self.sha, 'status': 'NOT_RUN',
             'required_evidence': 'named assertion', 'gap': 'unverified', 'existing_assertions': []}
            for case in self.expected['cases']]}

    def test_unverified_requirements_remain_gaps(self):
        errors, gaps = check_map(self.inventory, self.expected)
        self.assertEqual(errors, [])
        self.assertEqual({x['case_id'] for x in gaps}, set(self.expected['cases']))

    def test_family_filter_and_unknown_family(self):
        errors, gaps = check_map(self.inventory, self.expected, ['TX'])
        self.assertEqual(errors, [])
        self.assertEqual([x['case_id'] for x in gaps], ['TX-01'])
        self.assertTrue(check_map(self.inventory, self.expected, ['NOPE'])[0])

    def test_duplicate_and_missing_cases_fail(self):
        self.inventory['cases'][1] = copy.deepcopy(self.inventory['cases'][0])
        errors, _ = check_map(self.inventory, self.expected)
        self.assertTrue(any('duplicate' in e for e in errors))
        self.assertTrue(any('differ' in e for e in errors))

    def test_stale_sha_and_false_pass_fail(self):
        self.inventory['cases'][0]['subject_sha'] = 'b' * 40
        self.inventory['cases'][0]['status'] = 'EXECUTED_PASS'
        errors, _ = check_map(self.inventory, self.expected)
        self.assertTrue(any('stale' in e for e in errors))
        self.assertTrue(any('unexplained' in e for e in errors))

    def test_nonstring_case_id_fails_cleanly(self):
        self.inventory['cases'][0]['case_id'] = ['TX-01']
        errors, _ = check_map(self.inventory, self.expected)
        self.assertTrue(any('string case_id' in e for e in errors))

    def test_mapped_case_requires_complete_execution_spec(self):
        self.inventory['cases'][0]['status'] = 'MAPPED'
        errors, _ = check_map(self.inventory, self.expected)
        self.assertTrue(any('complete executable mapping missing' in e for e in errors))

    def test_partial_without_assertion_fails(self):
        self.inventory['cases'][0]['status'] = 'PARTIAL'
        self.assertTrue(any('requires assertions' in e for e in check_map(self.inventory, self.expected)[0]))


class CheckoutTests(unittest.TestCase):
    def test_dirty_source_is_rejected_but_bytecode_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def git(*args):
                return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True, text=True)
            git('init', '-q')
            git('config', 'user.name', 'test')
            git('config', 'user.email', 'test@example.invalid')
            (root / 'source.py').write_text('x = 1\n')
            git('add', 'source.py')
            git('commit', '-qm', 'fixture')
            expected = git('rev-parse', 'HEAD').stdout.strip()
            cache = root / 'tests' / '__pycache__'
            cache.mkdir(parents=True)
            (cache / 'source.cpython-312.pyc').write_bytes(b'cache')
            self.assertEqual(clean_head(root), expected)
            (root / 'other.py').write_text('x = 2\n')
            with self.assertRaisesRegex(ValueError, 'clean source SHA'):
                clean_head(root)
            (root / 'other.py').unlink()
            (root / 'source.py').write_text('x = 3\n')
            with self.assertRaisesRegex(ValueError, 'clean source SHA'):
                clean_head(root)


class CaseExecutionTests(unittest.TestCase):
    def setUp(self):
        self.sha = 'a' * 40
        self.case = {'case_id': 'TX-01', 'status': 'MAPPED', 'execution': {'coverage': 'COMPLETE',
                     'tests': ['tests.v16.acceptance.test_acceptance_map.CaseExecutionTests.test_example'],
                     'assertions': ['specific expected behavior'], 'kind': 'REAL_SQLITE_FIXTURE'}}
        self.inventory = {'cases': [self.case]}

    def test_example(self):
        self.assertTrue(True)

    def test_missing_mapping_never_runs_or_passes(self):
        del self.case['execution']
        receipt = execute_case(self.inventory, 'TX-01', self.sha, lambda _: self.fail('unexpected run'))
        self.assertEqual(receipt['status'], 'NOT_RUN')

    def test_actual_named_test_execution(self):
        receipt = execute_case(self.inventory, 'TX-01', self.sha, run_tests)
        self.assertEqual(receipt['status'], 'EXECUTED_PASS')
        self.assertEqual(receipt['execution']['tests_run'], 1)
        self.assertEqual(receipt['independent_qa'], 'NOT_RUN')
        self.assertTrue(receipt['evidence_uri'].startswith('sha256:'))

    def test_evidence_digest_binds_source_and_case(self):
        observed = {'tests_run': 1, 'failures': 0, 'errors': 0, 'skipped': 0, 'exit_code': 0}
        one = execute_case(self.inventory, 'TX-01', self.sha, lambda _: observed)
        two = execute_case(self.inventory, 'TX-01', 'b' * 40, lambda _: observed)
        self.assertNotEqual(one['evidence_uri'], two['evidence_uri'])
        self.assertEqual(one['commands'][0][-1], self.case['execution']['tests'][0])

    def test_skip_or_failure_cannot_pass(self):
        for observed in ({'tests_run': 1, 'failures': 0, 'errors': 0, 'skipped': 1, 'exit_code': 0},
                         {'tests_run': 0, 'failures': 0, 'errors': 1, 'skipped': 0, 'exit_code': 1},
                         {'tests_run': 1, 'failures': 1, 'errors': 0, 'skipped': 0, 'exit_code': 1}):
            with self.subTest(observed=observed):
                self.assertEqual(execute_case(self.inventory, 'TX-01', self.sha, lambda _: observed)['status'],
                                 'EXECUTED_FAIL')

    def test_invalid_and_duplicate_test_names_rejected(self):
        for names in (['bad-module'], ['tests.a', 'tests.a'], [[]]):
            self.case['execution']['tests'] = names
            with self.subTest(names=names), self.assertRaises(ValueError):
                execute_case(self.inventory, 'TX-01', self.sha, lambda _: {})

    def test_unknown_or_duplicate_case_rejected(self):
        with self.assertRaises(ValueError):
            execute_case(self.inventory, 'TX-99', self.sha, lambda _: {})
        self.inventory['cases'].append(self.case)
        with self.assertRaises(ValueError):
            execute_case(self.inventory, 'TX-01', self.sha, lambda _: {})


if __name__ == '__main__':
    unittest.main()
