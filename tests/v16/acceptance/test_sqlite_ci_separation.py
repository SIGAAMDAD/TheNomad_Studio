"""The regression, coverage and release verdicts have different failure semantics."""
import unittest
from copy import deepcopy
from scripts.sqlite_c8_merge_gate import evaluate


HEAD = 'a' * 40
CHECKOUT = 'b' * 40


def complete_fixture():
    ids = [f'CASE-{i:02d}' for i in range(76)]
    inv = {'subject_sha': 'c' * 40,
           'cases': [{'case_id': k, 'status': 'MAPPED', 'reviewer_verdict': 'PASS'} for k in ids]}
    report = {'schema': 'NOMAD-ACCEPTANCE-RUN/1', 'subject_sha': CHECKOUT,
              'source_identity': {'tested_checkout_sha': CHECKOUT, 'pr_head_sha': HEAD,
                                  'inventory_baseline_sha': inv['subject_sha']},
              'results': [{'case_id': k, 'subject_sha': CHECKOUT, 'status': 'EXECUTED_PASS'} for k in ids],
              'unverified_cases': []}
    status = {'no_production_go': False, 'CANARY': 'PASS',
              'c8_local_evidence': {'tested_source_commit': HEAD,
                  'independent_qa': 'PASS', 'external_provider_tests': 'PASS',
                  'off_host_restore': 'PASS', 'full_hr_fault_matrix': 'PASS',
                  'performance_thresholds': 'PASS'}}
    return report, inv, status


class IndependentMergeGateTests(unittest.TestCase):
    def test_complete_grounded_fixture_has_no_blockers(self):
        report, inv, status = complete_fixture()
        self.assertEqual(evaluate(report, inv, status, checkout_sha=CHECKOUT, pr_head_sha=HEAD), [])

    def test_all_local_tests_pass_but_partial_coverage_remains_blocked(self):
        report, inv, status = complete_fixture()
        inv['cases'][0]['status'] = 'PARTIAL'
        report['results'] = report['results'][1:]
        report['unverified_cases'] = [{'case_id': inv['cases'][0]['case_id'], 'status': 'PARTIAL'}]
        issues = evaluate(report, inv, status, checkout_sha=CHECKOUT, pr_head_sha=HEAD)
        self.assertIn('ACCEPTANCE_COVERAGE_INCOMPLETE', issues)
        self.assertIn('EXACT_SHA_EXECUTABLE_EVIDENCE_MISSING', issues)

    def test_missing_independent_qa_and_owner_release_block_green_ci(self):
        report, inv, status = complete_fixture()
        status['no_production_go'] = True
        status['c8_local_evidence']['independent_qa'] = 'NOT_RUN'
        inv['cases'][1]['reviewer_verdict'] = 'NOT_REVIEWED'
        issues = evaluate(report, inv, status, checkout_sha=CHECKOUT, pr_head_sha=HEAD)
        self.assertIn('INDEPENDENT_CASE_REVIEWS_MISSING', issues)
        self.assertIn('EXTERNAL_GATE_INDEPENDENT_QA_MISSING', issues)
        self.assertIn('OWNER_RELEASE_NOT_AUTHORIZED', issues)

    def test_pr_head_and_checkout_sha_both_must_match(self):
        report, inv, status = complete_fixture()
        report['source_identity']['pr_head_sha'] = 'd' * 40
        report['results'][0]['subject_sha'] = 'f' * 40
        issues = evaluate(report, inv, status, checkout_sha=CHECKOUT, pr_head_sha=HEAD)
        self.assertIn('PR_PROVENANCE_MISMATCH', issues)
        self.assertIn('EXACT_SHA_EXECUTABLE_EVIDENCE_MISSING', issues)

    def test_baseline_sha_is_distinct_but_pinned(self):
        report, inv, status = complete_fixture()
        report['source_identity']['inventory_baseline_sha'] = 'd' * 40
        self.assertIn('INVENTORY_BASELINE_SHA_MISMATCH',
                      evaluate(report, inv, status, checkout_sha=CHECKOUT, pr_head_sha=HEAD))

    def test_missing_external_gates_fail_independently(self):
        report, inv, status = complete_fixture()
        for field in ('external_provider_tests', 'off_host_restore', 'full_hr_fault_matrix',
                      'performance_thresholds'):
            altered = deepcopy(status)
            altered['c8_local_evidence'][field] = 'NOT_RUN'
            with self.subTest(field=field):
                self.assertIn('EXTERNAL_GATE_' + field.upper() + '_MISSING',
                              evaluate(report, inv, altered, checkout_sha=CHECKOUT, pr_head_sha=HEAD))


if __name__ == '__main__':
    unittest.main()

# GitHub Actions must advertise distinct checks. The regression suite cannot
# become red merely because owner/independent acceptance has not happened.
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]


class CIWorkflowSeparationTests(unittest.TestCase):
    def _workflow(self, name):
        return (ROOT / '.github/workflows' / name).read_text()

    def test_three_distinct_checks_with_no_push_trigger_duplication(self):
        files = ('studio-sqlite-c8.yml', 'studio-sqlite-c8-coverage.yml',
                 'studio-sqlite-c8-merge-gate.yml')
        sources = [self._workflow(name) for name in files]
        self.assertTrue(all('  pull_request:' in text for text in sources))
        self.assertTrue(all('\n  push:' not in text for text in sources))
        self.assertEqual(len({text.splitlines()[0] for text in sources}), 3)

    def test_coverage_has_no_release_authority(self):
        regression = self._workflow('studio-sqlite-c8.yml')
        coverage = self._workflow('studio-sqlite-c8-coverage.yml')
        strict = self._workflow('studio-sqlite-c8-merge-gate.yml')
        self.assertNotIn('Require complete acceptance mappings', regression)
        self.assertNotIn('--run-mapped', regression)
        self.assertIn('--run-mapped --report-only', coverage)
        self.assertIn('scripts/sqlite_c8_merge_gate.py', strict)
        self.assertIn('--run-mapped --json', strict)
        self.assertNotIn('--report-only', strict)
