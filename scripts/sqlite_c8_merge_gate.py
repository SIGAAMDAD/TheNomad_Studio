#!/usr/bin/env python3
"""Fail-closed C8 *merge* gate; independent from green regression/coverage reports.

A local test pass or a mapped assertion is not an independent review or an
external provider/canary authorization. This checks evidence completeness and
recorded status; the corresponding receipts must originate from separately
authorized actors and must be reviewed outside this script.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / 'docs/production/c8/acceptance_inventory.json'
STATUS = ROOT / 'docs/sqlite/ACCEPTANCE_STATUS.json'
REQUIRED_EXTERNAL = ('independent_qa', 'external_provider_tests', 'off_host_restore',
                     'full_hr_fault_matrix', 'performance_thresholds')


def is_sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{40}', value) is not None


def evaluate(coverage, inventory, status, *, checkout_sha, pr_head_sha):
    """Return all blocking reasons; never treat local mapped cases as approval."""
    issues = []
    if not is_sha(checkout_sha) or not is_sha(pr_head_sha):
        issues.append('SUBJECT_SHA_UNVERIFIED')
    if coverage.get('schema') != 'NOMAD-ACCEPTANCE-RUN/1':
        issues.append('COVERAGE_RECEIPT_SCHEMA_INVALID')
    if coverage.get('subject_sha') != checkout_sha:
        issues.append('TESTED_CHECKOUT_SHA_MISMATCH')
    provenance = coverage.get('source_identity') or {}
    if provenance.get('tested_checkout_sha') != checkout_sha or provenance.get('pr_head_sha') != pr_head_sha:
        issues.append('PR_PROVENANCE_MISMATCH')
    if provenance.get('inventory_baseline_sha') != inventory.get('subject_sha'):
        issues.append('INVENTORY_BASELINE_SHA_MISMATCH')
    cases = inventory.get('cases')
    if not isinstance(cases, list) or len(cases) != 76 or len({c.get('case_id') for c in cases}) != 76:
        issues.append('ACCEPTANCE_CASE_CATALOGUE_INVALID')
        cases = []
    if coverage.get('unverified_cases') or len(cases) != 76 or any(c.get('status') != 'MAPPED' for c in cases):
        issues.append('ACCEPTANCE_COVERAGE_INCOMPLETE')
    results = coverage.get('results')
    results = results if isinstance(results, list) else []
    case_ids = {c['case_id'] for c in cases}
    if len(results) != 76 or len({r.get('case_id') for r in results}) != 76 or any(
        r.get('status') != 'EXECUTED_PASS' or r.get('subject_sha') != checkout_sha or
        r.get('case_id') not in case_ids for r in results
    ):
        issues.append('EXACT_SHA_EXECUTABLE_EVIDENCE_MISSING')
    if any(c.get('reviewer_verdict') != 'PASS' for c in cases):
        issues.append('INDEPENDENT_CASE_REVIEWS_MISSING')
    details = status.get('c8_local_evidence') or {}
    if details.get('tested_source_commit') != pr_head_sha:
        issues.append('RELEASE_SOURCE_SHA_NOT_REVIEWED')
    for field in REQUIRED_EXTERNAL:
        if details.get(field) != 'PASS':
            issues.append('EXTERNAL_GATE_' + field.upper() + '_MISSING')
    if status.get('no_production_go') is not False:
        issues.append('OWNER_RELEASE_NOT_AUTHORIZED')
    if status.get('CANARY') != 'PASS':
        issues.append('OWNER_CANARY_NOT_ACCEPTED')
    return sorted(set(issues))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--coverage', type=Path, required=True, help='same-run exact-checkout acceptance receipt')
    parser.add_argument('--inventory', type=Path, default=INVENTORY)
    parser.add_argument('--status', type=Path, default=STATUS)
    parser.add_argument('--json', type=Path)
    args = parser.parse_args(argv)
    errors = []
    try:
        checkout_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        event_path = os.environ.get('GITHUB_EVENT_PATH')
        event = json.loads(Path(event_path).read_text()) if event_path else {}
        pr_head = ((event.get('pull_request') or {}).get('head') or {}).get('sha')
        if os.environ.get('GITHUB_SHA') and os.environ['GITHUB_SHA'] != checkout_sha:
            errors.append('CI_CHECKOUT_SHA_MISMATCH')
        errors.extend(evaluate(json.loads(args.coverage.read_text()),
                               json.loads(args.inventory.read_text()),
                               json.loads(args.status.read_text()),
                               checkout_sha=checkout_sha, pr_head_sha=pr_head))
    except (OSError, ValueError, TypeError, KeyError, subprocess.CalledProcessError) as exc:
        errors.append('EVIDENCE_UNAVAILABLE_OR_INVALID: ' + type(exc).__name__)
    result = {'schema': 'NOMAD-C8-MERGE-GATE/1', 'verdict': 'BLOCKED' if errors else 'PASS',
              'blockers': sorted(set(errors))}
    output = json.dumps(result, indent=2, sort_keys=True) + '\n'
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(output)
    print('C8_MERGE_GATE ' + output)
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
