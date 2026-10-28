#!/usr/bin/env python3
"""Run legacy Studio regression and all C8 real-file SQLite acceptance suites.

No live GitHub/Drive or production database access. Tests create temporary 0700
SQLite databases and leave production authority in SHADOW. Independent QA and
external-provider/host restore gates are deliberately not inferred from this run.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
C8_PATTERN = 'test_sqlite*_c8.py'
# Minimum discovery counts established by the verified 2026-10-28 PR #32 runner.
# Reducing these intentionally requires a reviewed update, not silent test loss.
MIN_LEGACY_TESTS = 172
MIN_C8_TESTS = 95


def test_ids(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from test_ids(test)
        else:
            yield test.id()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', type=Path, help='Optional local test receipt output (not a Studio ledger write)')
    parser.add_argument('--c8-only', action='store_true', help='Run only new SQLite C8 suites')
    args = parser.parse_args(argv)
    if sqlite3.sqlite_version_info < (3, 45, 0):
        print(f'SQLITE_C8_BLOCKED: requires SQLite >= 3.45.0; found {sqlite3.sqlite_version}', file=sys.stderr)
        return 2
    loader = unittest.TestLoader()
    suites = []
    if not args.c8_only:
        suites.append(loader.discover(str(ROOT / 'tests'), pattern='test_*.py'))
    suites.append(loader.discover(str(ROOT / 'tests/v16/db'), pattern=C8_PATTERN))
    observed = [list(test_ids(suite)) for suite in suites]
    legacy_count = 0 if args.c8_only else len(observed[0])
    c8_count = len(observed[-1])
    all_ids = [item for group in observed for item in group]
    duplicate_ids = sorted({tid for tid in all_ids if all_ids.count(tid) > 1})
    discovery_failures = []
    if not args.c8_only and legacy_count < MIN_LEGACY_TESTS:
        discovery_failures.append(f'legacy discovery fell below {MIN_LEGACY_TESTS}: {legacy_count}')
    if c8_count < MIN_C8_TESTS:
        discovery_failures.append(f'C8 discovery fell below {MIN_C8_TESTS}: {c8_count}')
    if duplicate_ids:
        discovery_failures.append('duplicate discovered unittest IDs: ' + ', '.join(duplicate_ids[:20]))
    if loader.errors:
        discovery_failures.extend(loader.errors)
    if discovery_failures:
        for failure in discovery_failures:
            print('SQLITE_C8_DISCOVERY_FAILURE: ' + failure, file=sys.stderr)
        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(json.dumps({'schema': 'SQLITE-C8-LOCAL-TEST/1',
                'successful': False, 'discovery_failures': discovery_failures,
                'legacy_discovered': legacy_count, 'c8_discovered': c8_count}, indent=2) + '\n')
        return 1
    start = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(suites))
    receipt = dict(schema='SQLITE-C8-LOCAL-TEST/1', sqlite_version=sqlite3.sqlite_version,
                   legacy_discovered=legacy_count, c8_discovered=c8_count,
                   discovered_total=len(all_ids), discovered_unique=len(set(all_ids)),
                   tests_run=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                   skipped=len(result.skipped), successful=result.wasSuccessful(),
                   elapsed_seconds=round(time.monotonic()-start, 4),
                   external_provider_qa='NOT_RUN', off_host_restore='NOT_RUN',
                   owner_cutover='NOT_AUTHORIZED', independent_qa='NOT_RUN')
    print('C8_TEST_SUMMARY ' + json.dumps(receipt, sort_keys=True))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n')
    return 0 if result.wasSuccessful() and result.testsRun == len(all_ids) else 1


if __name__ == '__main__':
    raise SystemExit(main())
