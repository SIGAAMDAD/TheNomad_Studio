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
    start = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(suites))
    receipt = dict(schema='SQLITE-C8-LOCAL-TEST/1', sqlite_version=sqlite3.sqlite_version,
                   tests_run=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                   skipped=len(result.skipped), successful=result.wasSuccessful(),
                   elapsed_seconds=round(time.monotonic()-start, 4),
                   external_provider_qa='NOT_RUN', off_host_restore='NOT_RUN',
                   owner_cutover='NOT_AUTHORIZED', independent_qa='NOT_RUN')
    print('C8_TEST_SUMMARY ' + json.dumps(receipt, sort_keys=True))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n')
    return 0 if result.wasSuccessful() and result.testsRun >= (37 if args.c8_only else 200) else 1


if __name__ == '__main__':
    raise SystemExit(main())
