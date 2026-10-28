"""A08 signed independent review, source-bound receipt and distinct release states."""
import base64
from datetime import datetime,timedelta,timezone
import unittest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from scripts.sqlite_attestations import canonical,evidence_digest,verify_set
from scripts.sqlite_c8_merge_gate import evaluate
from scripts.sqlite_c8_production_gate import evaluate_production,PRODUCTION_KINDS

HEAD='a'*40
CHECKOUT='b'*40
BASELINE='c'*40

def complete_fixture():
    ids=[f'CASE-{i:02d}' for i in range(76)]
    inv={'subject_sha':BASELINE,'cases':[{'case_id':x,'status':'MAPPED','reviewer_verdict':'NOT_REVIEWED'} for x in ids]}
    report={'schema':'NOMAD-ACCEPTANCE-RUN/1','subject_sha':CHECKOUT,
            'source_identity':{'tested_checkout_sha':CHECKOUT,'pr_head_sha':HEAD,'inventory_baseline_sha':BASELINE},
            'results':[{'case_id':x,'subject_sha':CHECKOUT,'status':'EXECUTED_PASS'} for x in ids],
            'unverified_cases':[]}
    status={'CANARY':'PASS','no_production_go':False,'c8_local_evidence':{'independent_qa':'PASS'}}
    return report,inv,status

def approved(report,kinds=('INDEPENDENT_QA','OWNER_STAGING_MERGE')):
    entries={}
    trust={'principals':{},'revoked':set(),'implementer':'worker-implementation'}
    for index,kind in enumerate(kinds):
        signer=Ed25519PrivateKey.generate()
        issuer='independent-issuer-'+str(index)
        trust['principals'][kind]=(issuer,signer.public_key())
        now=datetime.now(timezone.utc)
        value={'schema':'NOMAD-SIGNED-ATTESTATION/1','kind':kind,'issuer_id':issuer,
               'subject_sha':HEAD,'checkout_sha':CHECKOUT,'inventory_sha':BASELINE,
               'evidence_sha256':evidence_digest(report),'implementer_id':trust['implementer'],
               'policy_version':'POLICY-2','nonce':'approval-nonce-'+str(index),
               'issued_at':(now-timedelta(minutes=1)).isoformat().replace('+00:00','Z'),
               'expires_at':(now+timedelta(hours=1)).isoformat().replace('+00:00','Z')}
        entries[kind]={'payload':value,'signature':base64.b64encode(signer.sign(canonical(value))).decode()}
    return {'schema':'NOMAD-ATTESTATION-BUNDLE/1','attestations':entries},trust

class IndependentMergeGateTests(unittest.TestCase):
    def test_complete_signed_fixture_has_no_staging_blockers(self):
        report,inv,status=complete_fixture()
        proof,trust=approved(report)
        self.assertEqual(evaluate(report,inv,status,checkout_sha=CHECKOUT,pr_head_sha=HEAD,attestations=proof,trust=trust),[])

    def test_all_local_tests_pass_but_partial_coverage_remains_blocked(self):
        report,inv,status=complete_fixture()
        proof,trust=approved(report)
        inv['cases'][0]['status']='PARTIAL'
        report['results']=report['results'][1:]
        report['unverified_cases']=[{'case_id':'CASE-00'}]
        issues=evaluate(report,inv,status,checkout_sha=CHECKOUT,pr_head_sha=HEAD,attestations=proof,trust=trust)
        self.assertIn('ACCEPTANCE_COVERAGE_INCOMPLETE',issues)
        self.assertIn('EXACT_SHA_EXECUTABLE_EVIDENCE_MISSING',issues)

    def test_editable_pass_and_owner_release_cannot_influence_staging(self):
        report,inv,status=complete_fixture()
        inv['cases'][0]['reviewer_verdict']='PASS'
        self.assertIn('INDEPENDENT_SIGNED_STAGING_ATTESTATIONS_MISSING_OR_INVALID',
                      evaluate(report,inv,status,checkout_sha=CHECKOUT,pr_head_sha=HEAD))
        proof,trust=approved(report)
        status['no_production_go']=True
        status['CANARY']='NOT_RUN'
        self.assertEqual(evaluate(report,inv,status,checkout_sha=CHECKOUT,pr_head_sha=HEAD,attestations=proof,trust=trust),[])
        self.assertIn('INDEPENDENT_SIGNED_PRODUCTION_RELEASE_ATTESTATIONS_MISSING_OR_INVALID',
                      evaluate_production(report,inv,checkout_sha=CHECKOUT,pr_head_sha=HEAD,attestations=proof,trust=trust))

    def test_stale_checkout_and_source_baseline_blocked(self):
        report,inv,status=complete_fixture()
        report['source_identity']['pr_head_sha']='d'*40
        report['source_identity']['inventory_baseline_sha']='e'*40
        self.assertIn('PR_PROVENANCE_MISMATCH',evaluate(report,inv,status,checkout_sha=CHECKOUT,pr_head_sha=HEAD))
        self.assertIn('INVENTORY_BASELINE_SHA_MISMATCH',evaluate(report,inv,status,checkout_sha=CHECKOUT,pr_head_sha=HEAD))

    def test_revoked_self_review_tamper_and_replay_rejected(self):
        report,inv,status=complete_fixture()
        proof,trust=approved(report)
        trust['revoked'].add('approval-nonce-0')
        self.assertIn('INDEPENDENT_SIGNED_STAGING_ATTESTATIONS_MISSING_OR_INVALID',
                      evaluate(report,inv,status,checkout_sha=CHECKOUT,pr_head_sha=HEAD,attestations=proof,trust=trust))
        trust['revoked'].clear()
        self.assertRaises(ValueError,verify_set,proof,('INDEPENDENT_QA','OWNER_STAGING_MERGE'),
                          trust=trust,head_sha='d'*40,checkout_sha=CHECKOUT,
                          inventory_sha=BASELINE,receipt_digest=evidence_digest(report))
        trust['implementer']='independent-issuer-0'
        self.assertIn('INDEPENDENT_SIGNED_STAGING_ATTESTATIONS_MISSING_OR_INVALID',
                      evaluate(report,inv,status,checkout_sha=CHECKOUT,pr_head_sha=HEAD,attestations=proof,trust=trust))
        trust['implementer']='worker-implementation'
        report['results'][0]['tamper']=True
        self.assertIn('INDEPENDENT_SIGNED_STAGING_ATTESTATIONS_MISSING_OR_INVALID',
                      evaluate(report,inv,status,checkout_sha=CHECKOUT,pr_head_sha=HEAD,attestations=proof,trust=trust))

    def test_full_signed_production_fixture(self):
        report,inv,_=complete_fixture()
        proof,trust=approved(report,PRODUCTION_KINDS)
        self.assertEqual(evaluate_production(report,inv,checkout_sha=CHECKOUT,pr_head_sha=HEAD,attestations=proof,trust=trust),[])

from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]

class CIWorkflowSeparationTests(unittest.TestCase):
    def _workflow(self,name):
        return (ROOT/'.github/workflows'/name).read_text()
    def test_three_distinct_checks_with_no_push_trigger_duplication(self):
        files=('studio-sqlite-c8.yml','studio-sqlite-c8-coverage.yml','studio-sqlite-c8-merge-gate.yml')
        sources=[self._workflow(x) for x in files]
        self.assertTrue(all('  pull_request:' in x for x in sources))
        self.assertTrue(all('\n  push:' not in x for x in sources))
        self.assertEqual(len({x.splitlines()[0] for x in sources}),3)
    def test_coverage_has_no_release_authority(self):
        regression=self._workflow('studio-sqlite-c8.yml')
        coverage=self._workflow('studio-sqlite-c8-coverage.yml')
        strict=self._workflow('studio-sqlite-c8-merge-gate.yml')
        self.assertNotIn('Require complete acceptance mappings',regression)
        self.assertNotIn('--run-mapped',regression)
        self.assertIn('--run-mapped --report-only',coverage)
        self.assertIn('scripts/sqlite_c8_merge_gate.py',strict)
        self.assertIn('--run-mapped --json',strict)
        self.assertNotIn('--report-only',strict)
