#!/usr/bin/env python3
"""Bounded pre-pilot evidence runner. Never claims browser or provider acceptance.

Runs source/static and local test tiers. Full mode requires the actual repo,
APSW and the production factory. Does not call GitHub or alter branches.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

L1_TEST_FILES = (
    'tests/unit/test_pre_pilot_fault_matrix.py',
    'tests/unit/test_build_recovery_local.py',
    'tests/unit/test_command_delivery_pump.py',
    'tests/unit/test_github_webhook_hints.py',
    'tests/unit/test_rate_governor.py',
    'tests/unit/test_http_rate_governor.py',
    'tests/unit/test_runtime_safe_shutdown.py',
    'tests/unit/test_connected_delivery_builder.py',
)
FULL_REQUIRED = (
    'src/studio/api/http.py',
    'src/studio/app/production_factory.py',
    'schema/initial_schema.sql',
    'tests/integration/test_production_factory_apsw.py',
    'tests/integration/test_command_delivery_apsw.py',
    'tests/integration/test_build_recovery_apsw.py',
)
# Explicit mapping: only these scenario IDs have identified L1 implementations.
# Actual source tests prove one aspect of each scenario, not the entire L2 gate.
LOCAL_CASES = {
    'C10', 'C11', 'C12', 'C14', 'C16', 'C17',
    'D01', 'D03', 'D04', 'D05', 'D06', 'D08', 'D10', 'D11',
    'D12', 'D13', 'D14', 'D15', 'D16',
    'E03', 'E04', 'E05', 'E06', 'E09', 'E10',
    'F01', 'F08', 'F09', 'F12',
}
CASE_ID = re.compile(r'^\|\s*([A-G][0-9]{2})\s*\|', re.M)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_tests(root: Path, paths: list[str], out: Path, timeout: int) -> dict:
    argv = [sys.executable, '-m', 'pytest', '-q', *paths]
    env = dict(os.environ, PYTHONPATH=str(root / 'src'))
    start = time.monotonic()
    try:
        run = subprocess.run(argv, cwd=root, env=env, text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             timeout=timeout, check=False)
        out.write_text(run.stdout)
        return {'status': 'PASS' if run.returncode == 0 else 'FAIL',
                'exit_code': run.returncode,
                'elapsed_seconds': round(time.monotonic() - start, 3),
                'command': argv, 'log': str(out),
                'sha256': digest(out)}
    except subprocess.TimeoutExpired as exc:
        out.write_bytes((exc.stdout or b'') + b'\nTIMEOUT\n')
        return {'status': 'FAIL', 'exit_code': None,
                'timeout_seconds': timeout, 'command': argv,
                'log': str(out), 'sha256': digest(out)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--mode', choices=('local', 'full'), default='local')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=120)
    args = parser.parse_args()
    root = args.root.resolve()
    plan = args.plan.resolve() if args.plan else root/'tests/prepilot/fixtures/R5R_PRE_PILOT_PLAN.md'
    args.out.mkdir(parents=True, exist_ok=True)
    missing_plan = not plan.is_file()
    ids = CASE_ID.findall(plan.read_text()) if not missing_plan else []
    duplicates = sorted({s for s in ids if ids.count(s) != 1})
    missing_suite = sorted(set(L1_TEST_FILES) - {p for p in L1_TEST_FILES if (root/p).is_file()})
    manifest = {'mode': args.mode, 'root': str(root),
                'plan': str(plan), 'plan_sha256': digest(plan) if not missing_plan else None,
                'test_case_count': len(ids), 'test_cases': {},
                'source_sha256': {}, 'tiers': {},
                'missing_source': [], 'validation_defects': []}
    if missing_plan:
        manifest['validation_defects'].append('missing pre-pilot plan')
    if len(ids) != 92 or duplicates or len(set(ids)) != 92:
        manifest['validation_defects'].append(f'case catalog must have 92 unique IDs; observed {len(ids)}; duplicate {duplicates}')
    if missing_suite:
        manifest['validation_defects'].append(f'missing L1 tests: {missing_suite}')
    if args.timeout < 1 or args.timeout > 3600:
        manifest['validation_defects'].append('invalid bounded test timeout')
    for rel in ('src/studio/app/build_recovery.py', 'src/studio/app/command_delivery.py',
                'src/studio/app/github_webhooks.py'):
        path = root/rel
        if path.exists():
            manifest['source_sha256'][rel] = digest(path)
        else:
            manifest['validation_defects'].append('missing local source: ' + rel)
    if not manifest['validation_defects']:
        selected = list(L1_TEST_FILES)
        if args.mode == 'full':
            manifest['missing_source'] = [p for p in FULL_REQUIRED if not (root/p).is_file()]
            try:
                import apsw  # noqa: F401 - production driver availability
            except ImportError:
                manifest['missing_source'].append('Python APSW driver')
            if not manifest['missing_source']:
                selected = ['tests/unit', 'tests/integration']
        if manifest['missing_source']:
            manifest['tiers']['L1_UNIT_REFERENCE'] = {'status':'NOT_RUN', 'reason':'full source/driver missing'}
            manifest['tiers']['L2_INTEGRATED_LOCAL'] = {'status':'NOT_RUN', 'reason':', '.join(manifest['missing_source'])}
        else:
            outcome = run_tests(root, selected, args.out/'tests.log',args.timeout)
            manifest['tiers']['L1_UNIT_REFERENCE'] = outcome
            manifest['tiers']['L2_INTEGRATED_LOCAL'] = ({'status':'NOT_RUN', 'reason':'only unit subset executed'}
                                                       if args.mode == 'local' else outcome)
    for case_id in sorted(set(ids)):
        passed = manifest['tiers'].get('L1_UNIT_REFERENCE', {}).get('status') == 'PASS'
        manifest['test_cases'][case_id] = {
            'L1': 'PASS_REFERENCE_ONLY' if passed and case_id in LOCAL_CASES else 'NOT_RUN',
            'L2': 'NOT_RUN' if args.mode=='local' else ('BLOCKED' if manifest['missing_source'] else 'UNVERIFIED_CASE'),
            'L3': 'NOT_RUN', 'L4': 'NOT_RUN'}
    manifest['tiers']['L3_HOSTED_CI'] = {'status':'NOT_RUN', 'reason':'requires exact remote SHA'}
    manifest['tiers']['L4_LIVE_BROWSER'] = {'status':'EXTERNAL_BLOCKED',
        'reason':'must observe authentic existing consumer chat and two verified receipts'}
    # Even a full suite pass cannot establish every F-01..F-20 and live browser gate.
    manifest['verdict'] = 'NOT_READY_FOR_PILOT'
    report=args.out/'report.json'
    report.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    print(f'{report}: {manifest["verdict"]}; cases={len(ids)}; '
          f'L1={manifest["tiers"].get("L1_UNIT_REFERENCE",{}).get("status","NOT_RUN")}')
    return 0 if not manifest['validation_defects'] and (
        manifest['tiers'].get('L1_UNIT_REFERENCE',{}).get('status') == 'PASS') else 1


if __name__=='__main__':
    raise SystemExit(main())
