#!/usr/bin/env python3
"""Staging source merge gate; production/canary is verified in a different gate."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
if __package__:
    from scripts.sqlite_attestations import evidence_digest, load_external_trust, verify_set
else:
    from sqlite_attestations import evidence_digest, load_external_trust, verify_set

ROOT=Path(__file__).resolve().parents[1]
INVENTORY=ROOT/'docs/production/c8/acceptance_inventory.json'
STATUS=ROOT/'docs/sqlite/ACCEPTANCE_STATUS.json'

def is_sha(value):
    return isinstance(value,str) and re.fullmatch('[0-9a-f]{40}',value) is not None

def evaluate(coverage,inventory,status=None,*,checkout_sha,pr_head_sha,attestations=None,trust=None):
    issues=[]
    if not is_sha(checkout_sha) or not is_sha(pr_head_sha):
        issues.append('SUBJECT_SHA_UNVERIFIED')
    if coverage.get('schema')!='NOMAD-ACCEPTANCE-RUN/1':
        issues.append('COVERAGE_RECEIPT_SCHEMA_INVALID')
    if coverage.get('subject_sha')!=checkout_sha:
        issues.append('TESTED_CHECKOUT_SHA_MISMATCH')
    provenance=coverage.get('source_identity') or {}
    if provenance.get('tested_checkout_sha')!=checkout_sha or provenance.get('pr_head_sha')!=pr_head_sha:
        issues.append('PR_PROVENANCE_MISMATCH')
    if provenance.get('inventory_baseline_sha')!=inventory.get('subject_sha'):
        issues.append('INVENTORY_BASELINE_SHA_MISMATCH')
    cases=inventory.get('cases')
    if not isinstance(cases,list) or len(cases)!=76 or len({x.get('case_id') for x in cases})!=76:
        issues.append('ACCEPTANCE_CASE_CATALOGUE_INVALID')
        cases=[]
    if coverage.get('unverified_cases') or len(cases)!=76 or any(x.get('status')!='MAPPED' for x in cases):
        issues.append('ACCEPTANCE_COVERAGE_INCOMPLETE')
    results=coverage.get('results')
    results=results if isinstance(results,list) else []
    ids={x['case_id'] for x in cases}
    if len(results)!=76 or len({x.get('case_id') for x in results})!=76 or any(
            x.get('status')!='EXECUTED_PASS' or x.get('subject_sha')!=checkout_sha or
            x.get('case_id') not in ids for x in results):
        issues.append('EXACT_SHA_EXECUTABLE_EVIDENCE_MISSING')
    try:
        verify_set(attestations,('INDEPENDENT_QA','OWNER_STAGING_MERGE'),trust=trust,
                   head_sha=pr_head_sha,checkout_sha=checkout_sha,
                   inventory_sha=inventory.get('subject_sha'),receipt_digest=evidence_digest(coverage))
    except (TypeError,KeyError,ValueError):
        issues.append('INDEPENDENT_SIGNED_STAGING_ATTESTATIONS_MISSING_OR_INVALID')
    return sorted(set(issues))

def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--coverage',type=Path,required=True)
    ap.add_argument('--inventory',type=Path,default=INVENTORY)
    ap.add_argument('--status',type=Path,default=STATUS,help='informational only; not authorization')
    ap.add_argument('--json',type=Path)
    ap.add_argument('--subject-sha')
    ap.add_argument('--attestations',type=Path)
    args=ap.parse_args(argv)
    errors=[]
    try:
        checkout_sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
        event=json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text()) if os.environ.get('GITHUB_EVENT_PATH') else {}
        pr_head=((event.get('pull_request') or {}).get('head') or {}).get('sha') or args.subject_sha
        if args.subject_sha and args.subject_sha!=pr_head:
            errors.append('EXPLICIT_SUBJECT_SHA_MISMATCH')
        if os.environ.get('GITHUB_EVENT_NAME')!='workflow_dispatch' and os.environ.get('GITHUB_SHA') and os.environ['GITHUB_SHA']!=checkout_sha:
            errors.append('CI_CHECKOUT_SHA_MISMATCH')
        if args.attestations and os.environ.get('GITHUB_ACTIONS')=='true':
            errors.append('CLI_ATTESTATIONS_NOT_TRUSTED_IN_CI')
        protected=os.environ.get('NOMAD_STAGING_ATTESTATIONS_PATH')
        path=Path(protected) if protected else args.attestations
        if path is None or ROOT==path.resolve() or ROOT in path.resolve().parents:
            raise ValueError('off-checkout evidence required')
        coverage=json.loads(args.coverage.read_text())
        inventory=json.loads(args.inventory.read_text())
        attestations=json.loads(path.read_text())
        errors.extend(evaluate(coverage,inventory,checkout_sha=checkout_sha,pr_head_sha=pr_head,
                               attestations=attestations,trust=load_external_trust()))
    except (OSError,ValueError,TypeError,KeyError,subprocess.CalledProcessError) as ex:
        errors.append('EVIDENCE_UNAVAILABLE_OR_INVALID: '+type(ex).__name__)
    result={'schema':'NOMAD-C8-STAGING-MERGE-GATE/2',
            'verdict':'BLOCKED' if errors else 'PASS','blockers':sorted(set(errors))}
    output=json.dumps(result,indent=2,sort_keys=True)+'\n'
    if args.json:
        args.json.parent.mkdir(parents=True,exist_ok=True)
        args.json.write_text(output)
    print('C8_MERGE_GATE '+output)
    return int(bool(errors))

if __name__=='__main__':
    sys.exit(main())
