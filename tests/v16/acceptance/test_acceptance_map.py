"""Acceptance-map validation must never promote unproven cases."""
import copy
import unittest
from scripts.sqlite_acceptance import check_map


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

    def test_partial_without_assertion_fails(self):
        self.inventory['cases'][0]['status'] = 'PARTIAL'
        self.assertTrue(any('requires assertions' in e for e in check_map(self.inventory, self.expected)[0]))


if __name__ == '__main__':
    unittest.main()
