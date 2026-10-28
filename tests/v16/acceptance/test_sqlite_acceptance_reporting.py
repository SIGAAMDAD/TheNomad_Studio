"""Regression failures, incomplete coverage, and external gates stay separate."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import sqlite_acceptance as acceptance

SHA = 'a' * 40
BASE = 'b' * 40


class AcceptanceReportingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.inventory_path = self.dir / 'inventory.json'
        self.expected_path = self.dir / 'expected.json'
        self.report_path = self.dir / 'receipt.json'
        self.event_path = self.dir / 'event.json'
        self.event_path.write_text(json.dumps({'pull_request': {
            'head': {'sha': 'c' * 40}, 'base': {'sha': 'd' * 40}}}))
        self.inventory_path.write_text(json.dumps({'subject_sha': BASE, 'cases': [
            {'case_id': 'TX-01', 'subject_sha': BASE, 'status': 'MAPPED',
             'required_evidence': 'claim test', 'gap': 'independent QA pending',
             'existing_assertions': [], 'execution': {'coverage': 'COMPLETE',
               'kind': 'REAL_SQLITE_FIXTURE', 'tests': ['tests.demo.Sample.test_ok'],
               'assertions': ['one committed claim']}},
            {'case_id': 'TX-02', 'subject_sha': BASE, 'status': 'NOT_RUN',
             'required_evidence': 'ordered events', 'gap': 'not tested',
             'existing_assertions': []}]}))
        self.expected_path.write_text(json.dumps({'cases': {'TX-01': 'NOT_RUN', 'TX-02': 'NOT_RUN'}}))
        self.contexts = [patch.object(acceptance, 'INVENTORY', self.inventory_path),
                         patch.object(acceptance, 'EXPECTED', self.expected_path),
                         patch.object(acceptance, 'clean_head', return_value=SHA),
                         patch.object(acceptance, 'run_tests', return_value={
                             'tests_run': 1, 'failures': 0, 'errors': 0,
                             'skipped': 0, 'exit_code': 0}),
                         patch.dict(os.environ, {'GITHUB_SHA': SHA,
                             'GITHUB_EVENT_PATH': str(self.event_path)}, clear=True)]
        for context in self.contexts:
            context.__enter__()
        self.addCleanup(lambda: [c.__exit__(None, None, None) for c in reversed(self.contexts)])

    def test_coverage_green_but_gap_and_provenance_remain_visible(self):
        self.assertEqual(acceptance.main(['--run-mapped', '--report-only',
                                          '--json', str(self.report_path)]), 0)
        result = json.loads(self.report_path.read_text())
        self.assertEqual(result['case_counts'], {'MAPPED': 1, 'PARTIAL': 0, 'NOT_RUN': 1})
        self.assertEqual(result['source_identity']['tested_checkout_sha'], SHA)
        self.assertEqual(result['source_identity']['inventory_baseline_sha'], BASE)
        self.assertEqual(result['source_identity']['pr_head_sha'], 'c' * 40)
        self.assertEqual(result['results'][0]['status'], 'EXECUTED_PASS')
        self.assertEqual(result['unverified_cases'][0]['case_id'], 'TX-02')
        self.assertFalse(result['release_authorized'])

    def test_strict_runner_remains_red_on_unverified_coverage(self):
        self.assertEqual(acceptance.main(['--run-mapped', '--json', str(self.report_path)]), 1)

    def test_report_only_still_fails_if_any_mapped_test_fails(self):
        with patch.object(acceptance, 'run_tests', return_value={
            'tests_run': 1, 'failures': 1, 'errors': 0, 'skipped': 0,
            'exit_code': 1, 'diagnostics': 'AssertionError: expected one writer'}):
            self.assertEqual(acceptance.main(['--run-mapped', '--report-only',
                                              '--json', str(self.report_path)]), 1)
        result = json.loads(self.report_path.read_text())
        self.assertEqual(result['results'][0]['status'], 'EXECUTED_FAIL')
        self.assertIn('AssertionError', result['results'][0]['execution']['diagnostics'])

    def test_changed_ci_sha_refused(self):
        with patch.dict(os.environ, {'GITHUB_SHA': 'f' * 40,
                                     'GITHUB_EVENT_PATH': str(self.event_path)}, clear=True):
            self.assertEqual(acceptance.main(['--run-mapped', '--report-only',
                                              '--json', str(self.report_path)]), 1)
        self.assertIn('GITHUB_SHA differs', self.report_path.read_text())


if __name__ == '__main__':
    unittest.main()
