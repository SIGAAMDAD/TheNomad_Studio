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

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / 'docs/production/c8/acceptance_inventory.json'
EXPECTED = ROOT / 'docs/sqlite/ACCEPTANCE_STATUS.json'


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
        if row.get('status') not in ('NOT_RUN', 'PARTIAL'):
            errors.append(f'{case}: unsupported or unexplained status')
        if not row.get('required_evidence') or not row.get('gap'):
            errors.append(f'{case}: missing requirement or evidence gap')
        links = row.get('existing_assertions')
        if not isinstance(links, list):
            errors.append(f'{case}: invalid assertion list')
        elif row.get('status') == 'PARTIAL' and not links:
            errors.append(f'{case}: partial status requires assertions')
        if not requested or case.split('-')[0] in requested:
            gaps.append({'case_id': case, 'status': row.get('status'), 'reason': row.get('gap')})
    return errors, gaps


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-map', action='store_true')
    parser.add_argument('--family', action='append', default=[])
    parser.add_argument('--json', type=Path, help='write local report to this path')
    args = parser.parse_args(argv)
    if not args.check_map and not args.family:
        parser.error('select --check-map or at least one --family')
    try:
        inventory = json.loads(INVENTORY.read_text())
        expected = json.loads(EXPECTED.read_text())
        errors, gaps = check_map(inventory, expected, args.family)
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
