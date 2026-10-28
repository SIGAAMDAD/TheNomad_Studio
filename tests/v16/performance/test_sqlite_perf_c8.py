"""PERF-01: source-executable SQLite latency and query plan evidence."""
import sqlite3
import unittest

from scripts.sqlite_perf_bench import run_benchmark


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3,45), 'SQLite >=3.45 required')
class SQLitePerformanceC8(unittest.TestCase):
    def test_performance_receipt_contains_real_plan_samples_and_fixed_input(self):
        report = run_benchmark(rows=1500, iterations=60)
        self.assertEqual(report['kind'], 'LOCAL_REPRODUCIBLE_OBSERVATION')
        self.assertEqual(report['rows'], 1500)
        self.assertEqual(report['iterations'], 60)
        self.assertEqual(report['independent_qa'], 'NOT_ATTESTED')
        self.assertEqual(report['external_provider'], 'NOT_TESTED')
        for label, index in (('department_inbox_indexed','department_recipient_sequence_idx'),
                             ('due_outbox','due_outbox_idx')):
            with self.subTest(label=label):
                item=report['queries'][label]
                self.assertEqual(item['rows_returned'], 50)
                self.assertTrue(any(index in p for p in item['query_plan']))
                self.assertIn('SELECT', item['sql'])
                self.assertLessEqual(item['min_us'], item['median_us'])
                self.assertLessEqual(item['median_us'], item['p95_us'])
                self.assertLessEqual(item['p95_us'], item['p99_us'])
                self.assertLessEqual(item['p99_us'], item['max_us'])

    def test_full_history_baseline_is_real_scan_and_matches_indexed_result(self):
        report = run_benchmark(rows=1500, iterations=30)
        baseline = report['queries']['department_inbox_baseline_scan']
        indexed = report['queries']['department_inbox_indexed']
        self.assertTrue(any('SCAN department_events' in x for x in baseline['query_plan']))
        self.assertEqual(baseline['params'], indexed['params'])
        self.assertEqual(baseline['rows_returned'], indexed['rows_returned'])
        self.assertGreater(report['inbox_median_speedup_observed'], 0)

    def test_benchmark_rejects_unbounded_inputs(self):
        for rows, iterations in ((0,100),(100,1),(1000000,100),(100,999999),
                                 (True,100),(100,True)):
            with self.subTest(rows=rows, iterations=iterations):
                with self.assertRaises(ValueError):
                    run_benchmark(rows=rows, iterations=iterations)


if __name__=='__main__':
    unittest.main()
