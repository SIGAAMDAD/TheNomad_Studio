#!/usr/bin/env python3
"""Operator-triggered production proof gate, separate from source staging merge."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from scripts.sqlite_attestations import evidence_digest,load_external_trust,verify_set
from scripts.sqlite_c8_merge_gate import evaluate,ROOT

PRODUCTION_KINDS=('INDEPENDENT_QA','OWNER_STAGING_MERGE','EXTERNAL_PROVIDER',
                  'OFFHOST_RESTORE','HR_FAULT_MATRIX','PERFORMANCE',
                  'OWNER_AUTHORITY_CUTOVER','OWNER_PRODUCTION_RELEASE')

def evaluate_production(coverage,inventory,*,checkout_sha,pr_head_sha,attestations=None,trust=None):
    issues=evaluate(coverage,inventory,checkout_sha=checkout_sha,pr_head_sha=pr_head_sha,
                    attestations=attestations,trust=trust)
    try:
        verify_set(attestations,PRODUCTION_KINDS,trust=trust,head_sha=pr_head_sha,
                   checkout_sha=checkout_sha,inventory_sha=inventory.get('subject_sha'),
                   receipt_digest=evidence_digest(coverage))
    except (TypeError,KeyError,ValueError):
        issues.append('INDEPENDENT_SIGNED_PRODUCTION_RELEASE_ATTESTATIONS_MISSING_OR_INVALID')
    return sorted(set(issues))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--coverage',type=Path,required=True)
    p.add_argument('--inventory',type=Path,required=True)
    p.add_argument('--attestations',type=Path,required=True)
    p.add_argument('--checkout-sha',required=True)
    p.add_argument('--pr-head-sha',required=True)
    args=p.parse_args()
    if ROOT==args.attestations.resolve() or ROOT in args.attestations.resolve().parents:
        raise ValueError('trusted evidence must be outside source checkout')
    errors=evaluate_production(json.loads(args.coverage.read_text()),json.loads(args.inventory.read_text()),
                               checkout_sha=args.checkout_sha,pr_head_sha=args.pr_head_sha,
                               attestations=json.loads(args.attestations.read_text()),trust=load_external_trust())
    print(json.dumps({'schema':'NOMAD-C8-PRODUCTION-GATE/1',
                      'verdict':'BLOCKED' if errors else 'PASS','blockers':errors},sort_keys=True))
    return int(bool(errors))

if __name__=='__main__':
    raise SystemExit(main())
