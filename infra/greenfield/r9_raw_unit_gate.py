#!/usr/bin/env python3
"""Evidence-producing R9 raw-unit verification, not a live-host certification.

The complete-suite mode retains its failures: obsolete P2 composition or external
provider dependencies cannot be converted into green status by mock receipts.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import py_compile
import re
import sqlite3
import subprocess
import sys
import time

EXCLUDED = {'__pycache__', '.pytest_cache', '.git', 'build', 'dist', '.venv', 'venv'}


def check_python_syntax(root: Path) -> dict:
    checked = 0
    for directory in ('src', 'tests', 'infra'):
        for path in sorted((root / directory).rglob('*.py')):
            if set(path.relative_to(root).parts) & EXCLUDED:
                continue
            try:
                ast.parse(path.read_bytes(), filename=str(path))
            except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
                raise RuntimeError(f'Python syntax failed: {path.relative_to(root)}: {exc}') from exc
            checked += 1
    if checked < 80:
        raise RuntimeError('source inventory unexpectedly small; check source extraction')
    return {'python_files': checked}


def check_schema(root: Path) -> dict:
    contracts = root / 'CONTRACTS'
    sql = (contracts / 'initial_schema_v1.sql').read_bytes()
    owners = json.loads((contracts / 'schema_table_owners.json').read_text())['owner_per_table']
    schema = json.loads((contracts / 'records.schema.json').read_text())
    if not schema.get('$defs'):
        raise RuntimeError('record schemas missing')
    with sqlite3.connect(':memory:') as db:
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript(sql.decode())
        actual = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'v1_%'")}
        if len(actual) != 56 or actual != set(owners):
            raise RuntimeError('physical table/owner registry mismatch')
        if db.execute('PRAGMA foreign_keys').fetchone()[0] != 1 or list(db.execute('PRAGMA foreign_key_check')):
            raise RuntimeError('SQLite FK enforcement not active')
    return {'tables': 56, 'schema_sha256': hashlib.sha256(sql).hexdigest(), 'record_types': len(schema['$defs'])}


def run_tests(root: Path, mode: str, log: Path) -> dict:
    env = os.environ.copy()
    env['NOMAD_R9_CONTRACTS_DIR'] = str(root / 'CONTRACTS')
    env['PYTHONPATH'] = os.pathsep.join([str(root / 'src'), str(root / 'tests'), str(root),
                   str(root / 'tests' / 'trust'), str(root / 'tests' / 'creative_authority' / 'impact'),
                   str(root / 'tests' / 'oversight_development'), env.get('PYTHONPATH', '')])
    args = [sys.executable, '-m', 'pytest', '-q', str(root / 'tests')]
    if mode == 'raw-unit':
        args.extend(['--ignore=' + str(root / 'tests' / 'integration')])
    result = subprocess.run(args, cwd=root, env=env, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=600)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(result.stdout)
    match = re.search(r'(\d+) passed', result.stdout)
    passed = int(match.group(1)) if match else 0
    return {'exit_code': result.returncode, 'passed': passed,
            'log_sha256': hashlib.sha256(result.stdout.encode()).hexdigest(),
            'command': args, 'mode': mode,
            'log_path': str(log),
            'completed': result.returncode == 0 and passed >= 1000}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('--mode', choices=['raw-unit', 'full'], default='raw-unit')
    parser.add_argument('--out', type=Path, default=Path('r9-unit-receipt.json'))
    parser.add_argument('--log', type=Path, default=Path('r9-unit-tests.log'))
    args = parser.parse_args()
    root = args.root.resolve()
    receipt = {'mode': args.mode, 'source_root': str(root), 'schema': None, 'syntax': None,
               'tests': None, 'result': 'FAIL', 'simulated_only': True,
               'live_provider_proof': False, 'production_ready': False}
    try:
        receipt['syntax'] = check_python_syntax(root)
        receipt['schema'] = check_schema(root)
        receipt['tests'] = run_tests(root, args.mode, args.log.resolve())
        if not receipt['tests']['completed']:
            raise RuntimeError('tests failed or campaign was incomplete')
        receipt['result'] = 'PASS_RAW_UNIT_ONLY' if args.mode == 'raw-unit' else 'PASS_TEST_SUITE_ONLY'
    except Exception as exc:
        receipt['error'] = str(exc)
    finally:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
        print(json.dumps({k:v for k,v in receipt.items() if k not in ('source_root',)}, indent=2))
    return 0 if receipt['result'].startswith('PASS_') else 1


if __name__ == '__main__':
    raise SystemExit(main())
