#!/usr/bin/env python3
"""Fail-closed requirement mapping checks for SQLite C8 acceptance.

The inventory records partial evidence; a successful fixture suite does not by
itself satisfy an acceptance case. This checker emits gaps rather than promoting
partial coverage. It does not run live provider or owner acceptance checks.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import subprocess
import hashlib
import platform
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / 'docs/production/c8/acceptance_inventory.json'
EXPECTED = ROOT / 'docs/sqlite/ACCEPTANCE_STATUS.json'


def valid_execution(spec):
    if not isinstance(spec, dict) or spec.get('coverage') != 'COMPLETE':
        return False
    tests, assertions = spec.get('tests'), spec.get('assertions')
    return (isinstance(tests, list) and bool(tests)
            and all(isinstance(t, str) and re.fullmatch(r'tests\.[A-Za-z0-9_.]+', t) for t in tests)
            and len(tests) == len(set(tests))
            and isinstance(assertions, list) and bool(assertions)
            and all(isinstance(a, str) and bool(a.strip()) for a in assertions)
            and spec.get('kind') in {'STATIC', 'FAKE_PROVIDER', 'REAL_SQLITE_FIXTURE'})


def check_map(inventory, expected, families=()):
    """Validate a case catalogue and expose every unresolved requirement."""
    errors = []
    cases = inventory.get('cases')
    if not isinstance(cases, list):
        return ['inventory cases must be an array'], []
    expected_ids = set(expected.get('cases', {}))
    raw_ids = [row.get('case_id') for row in cases if isinstance(row, dict)]
    ids = [x for x in raw_ids if isinstance(x, str)]
    if len(ids) != len(cases):
        errors.append('every row must have a string case_id')
    if len(ids) != len(set(ids)):
        errors.append('duplicate case ID')
    if set(ids) != expected_ids:
        errors.append('case IDs differ from authoritative acceptance status')
    source_sha = inventory.get('subject_sha', '')
    if not isinstance(source_sha, str) or not re.fullmatch(r'[0-9a-f]{40}', source_sha):
        errors.append('invalid inventory subject SHA')
    requested = set(families)
    known_families = {x.split('-')[0] for x in expected_ids}
    if requested - known_families:
        errors.append('unknown family: ' + ', '.join(sorted(requested - known_families)))
    gaps = []
    for row in cases:
        if not isinstance(row, dict):
            continue
        case = row.get('case_id')
        if not isinstance(case, str):
            continue
        if row.get('subject_sha') != source_sha:
            errors.append(f'{case}: stale or missing subject SHA')
        if row.get('status') not in ('NOT_RUN', 'PARTIAL', 'MAPPED'):
            errors.append(f'{case}: unsupported or unexplained status')
        if not row.get('required_evidence') or not row.get('gap'):
            errors.append(f'{case}: missing requirement or evidence gap')
        links = row.get('existing_assertions')
        if not isinstance(links, list):
            errors.append(f'{case}: invalid assertion list')
        elif row.get('status') == 'PARTIAL' and not links:
            errors.append(f'{case}: partial status requires assertions')
        mapped = row.get('status') == 'MAPPED'
        if mapped:
            execution = row.get('execution')
            if not valid_execution(execution):
                errors.append(f'{case}: complete executable mapping missing')
        if (not requested or case.split('-')[0] in requested) and not mapped:
            gaps.append({'case_id': case, 'status': row.get('status'), 'reason': row.get('gap')})
    return errors, gaps


def execute_case(inventory, case_id, source_sha, runner):
    """Run only a complete, explicitly mapped case; partial links are not proof.

    runner accepts a list of exact unittest identifiers and returns a structured
    execution result; the SHA is verified by the caller against a clean checkout.
    """
    matches = [row for row in inventory.get('cases', []) if row.get('case_id') == case_id]
    if len(matches) != 1:
        raise ValueError('case ID missing or duplicated')
    if not re.fullmatch(r'[0-9a-f]{40}', source_sha):
        raise ValueError('invalid tested source SHA')
    case = matches[0]
    spec = case.get('execution')
    result = {'schema': 'NOMAD-ACCEPTANCE-RESULT/1', 'case_id': case_id,
              'subject_repo': 'SIGAAMDAD/TheNomad_Studio', 'subject_sha': source_sha,
              'status': 'NOT_RUN', 'independent_qa': 'NOT_RUN', 'external_gate': 'NOT_RUN',
              'environment': {'python': platform.python_version(), 'sqlite': sqlite3.sqlite_version,
                              'os': platform.platform(), 'host_kind': 'local'}}
    if case.get('status') != 'MAPPED' or not isinstance(spec, dict) or spec.get('coverage') != 'COMPLETE':
        result['blocked_reason'] = 'complete assertion mapping is missing'
        return result
    if not valid_execution(spec):
        raise ValueError('invalid complete execution mapping')
    tests = spec['tests']
    assertions = spec['assertions']
    observed = runner(tests)
    if not isinstance(observed, dict):
        raise ValueError('malformed test runner result')
    result.update({'tests': tests, 'kind': spec['kind'], 'observed_assertions': assertions,
                   'commands': [[sys.executable, '-m', 'unittest', name] for name in tests],
                   'execution': observed})
    # Every named method must actually execute exactly once; skips and collection
    # errors are never treated as passes, including when unittest exits zero.
    passed = (observed.get('tests_run') == len(tests) and observed.get('failures') == 0
              and observed.get('errors') == 0 and observed.get('skipped') == 0
              and observed.get('exit_code') == 0)
    result['status'] = 'EXECUTED_PASS' if passed else 'EXECUTED_FAIL'
    result['evidence_uri'] = 'sha256:' + hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return result


def clean_head(root=ROOT):
    """Resolve the actual checkout SHA and reject tracked working-tree changes."""
    try:
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True,
                                       stderr=subprocess.DEVNULL).strip()
        dirty = subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=all'],
                                        cwd=root, text=True, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError('cannot verify source checkout') from exc
    # Python bytecode generated by running the suite is not source input.
    dirty_lines = [line for line in dirty.splitlines()
                   if not (line.startswith('?? ') and '/__pycache__/' in line and line.endswith('.pyc'))]
    if not re.fullmatch(r'[0-9a-f]{40}', head) or dirty_lines:
        raise ValueError('checkout must have an exact clean source SHA')
    return head


def run_tests(tests):
    harness = r"""import json, sys, unittest
suite = unittest.TestLoader().loadTestsFromNames(sys.argv[1:])
result = unittest.TextTestRunner(stream=open('/dev/null','w'), verbosity=0).run(suite)
print(json.dumps({'tests_run': result.testsRun, 'failures': len(result.failures),
                  'errors': len(result.errors), 'skipped': len(result.skipped)}))
sys.exit(0 if result.wasSuccessful() else 1)
"""
    try:
        proc = subprocess.run([sys.executable, '-c', harness, *tests], cwd=ROOT,
                              capture_output=True, text=True, timeout=120)
        data = json.loads(proc.stdout)
        data['exit_code'] = proc.returncode
        return data
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {'tests_run': 0, 'failures': 0, 'errors': 1, 'skipped': 0, 'exit_code': 1}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-map', action='store_true')
    parser.add_argument('--run-case', help='execute a completely mapped case on a clean checkout')
    parser.add_argument('--run-mapped', action='store_true', help='execute all mapped cases and fail on remaining gaps')
    parser.add_argument('--family', action='append', default=[])
    parser.add_argument('--json', type=Path, help='write local report to this path')
    args = parser.parse_args(argv)
    if args.family and not args.check_map and not args.run_case:
        args.run_mapped = True
    if not args.check_map and not args.family and not args.run_case and not args.run_mapped:
        parser.error('select --check-map or at least one --family')
    try:
        inventory = json.loads(INVENTORY.read_text())
        if args.run_case:
            subject_sha = clean_head()
            receipt = execute_case(inventory, args.run_case, subject_sha, run_tests)
            if clean_head() != subject_sha:
                raise ValueError('checkout changed while tests ran')
            output = json.dumps(receipt, indent=2, sort_keys=True) + '\n'
            if args.json:
                args.json.write_text(output)
            else:
                print(output, end='')
            return 0 if receipt['status'] == 'EXECUTED_PASS' else 1
        expected = json.loads(EXPECTED.read_text())
        errors, gaps = check_map(inventory, expected, args.family)
        if args.run_mapped:
            if errors:
                raise ValueError('; '.join(errors))
            subject_sha = clean_head()
            selected = [c for c in inventory['cases'] if c.get('status') == 'MAPPED'
                        and (not args.family or c['case_id'].split('-')[0] in args.family)]
            results = [execute_case(inventory, c['case_id'], subject_sha, run_tests) for c in selected]
            if clean_head() != subject_sha:
                raise ValueError('checkout changed while tests ran')
            report = {'schema': 'NOMAD-ACCEPTANCE-RUN/1', 'subject_sha': subject_sha,
                      'results': results, 'unverified_cases': gaps}
            output = json.dumps(report, indent=2, sort_keys=True) + '\n'
            if args.json:
                args.json.write_text(output)
            else:
                print(output, end='')
            return 1 if gaps or any(r['status'] != 'EXECUTED_PASS' for r in results) else 0
    except (OSError, ValueError, TypeError) as exc:
        errors, gaps = [str(exc)], []
        inventory = {}
    report = {'schema': 'NOMAD-ACCEPTANCE-MAP-CHECK/1', 'subject_sha': inventory.get('subject_sha'),
              'errors': errors, 'unverified_cases': gaps, 'independent_qa': 'NOT_RUN',
              'external_provider_qa': 'NOT_RUN'}
    output = json.dumps(report, indent=2, sort_keys=True) + '\n'
    if args.json:
        args.json.write_text(output)
    else:
        print(output, end='')
    return 1 if errors or gaps else 0


if __name__ == '__main__':
    sys.exit(main())
