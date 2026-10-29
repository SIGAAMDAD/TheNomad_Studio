"""R9 CAB-E byte-backed local tests and adversarial gates.

The fixture runs actual Python subprocesses and installs the exact R8/R9 new-only
DDL. Its proof providers are controlled local unit-test adapters, not production
runner/provider acceptance receipts. Never use this suite as live CI/host proof.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from nomad_studio.production.quality.api import QualityService
from nomad_studio.review.independence import reviewer_principal
from nomad_studio.verification.validation import canonical, digest, test_evidence, bible_receipt
test_evidence.__test__ = False

ROOT = Path(__file__).resolve().parent
D = 'd' * 64
E = 'e' * 64
B = 'b' * 64
C = 'c' * 64
COMMIT = 'a' * 40
DIFFERENT = 'b' * 40


def digest_bytes(b: bytes) -> str: return hashlib.sha256(b).hexdigest()


@dataclass(frozen=True)
class Result:
    status: str
    code: str
    record: dict | None = None
    receipt_ref: str | None = None
    evidence_refs: tuple[str, ...] = ()


class Store:
    def __init__(self):
        self.db = sqlite3.connect(':memory:', check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript((ROOT / 'frozen_schema_v1.sql').read_text())
        self.db.execute('PRAGMA foreign_keys=ON')
        self.lock = threading.RLock()

    def read(self, callback):
        with self.lock: return callback(self)

    def transact(self, callback):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                value = callback(self)
                self.db.execute('COMMIT')
                return value
            except BaseException:
                self.db.execute('ROLLBACK')
                raise

    def fetch_one(self, sql, args=()):
        row = self.db.execute(sql, args).fetchone()
        return dict(row) if row is not None else None

    def fetch_all(self, sql, args=()):
        return [dict(x) for x in self.db.execute(sql, args)]

    def execute(self, sql, args=()): return self.db.execute(sql, args)

    def seed(self, contract):
        self.execute('INSERT INTO v1_projects VALUES(?,?,?,?,?)', ('proj', 'owner',D,'ACTIVE',1))
        self.execute('INSERT INTO v1_briefs VALUES(?,?,?,?,?,?,?,?)', ('proj','brief','feature','owner','text',D,'owner-proof',1))
        self.execute('INSERT INTO v1_feature_contracts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                     ('proj','feature',1,'brief',D,B,C,D,D,D,20,json.dumps(contract)))
        self.execute('INSERT INTO v1_feature_runs VALUES(?,?,?,?,?,?,?,?,?,?)',
                     ('proj','feature','brief',C,B,'VERIFYING',1,1,'feature',1))
        graph = {'id':'graph','feature_id':'feature','revision':1}
        self.execute('INSERT INTO v1_graph_revisions VALUES(?,?,?,?,?,?)',('feature',1,D,C,B,json.dumps(graph)))
        self.execute('INSERT INTO v1_tasks VALUES(?,?,?,?,?,?,?,?,?)',
                     ('feature','task',1,'cabal','tasksha','COMPLETED','manifest',0,'{}'))


class Proofs:
    def __init__(self):
        self.blobs = {}
        self.logs = {}
        self.test_proofs = {}
        self.fake = False
        self.tamper = False
        self.toolchain_change = False
        self.adapter_change = False
        self.call_count = 0
        self.lineage = {'principals':['author'],'roots':['author-root']}

    def save(self, key, kind, value):
        data = canonical(value)
        self.blobs[(key,kind)] = data
        return data

    def read_record(self, key, kind): return self.blobs[(key,kind)]

    def persist_receipt(self, key, kind, data):
        if (key,kind) in self.blobs and self.blobs[key,kind] != data:
            raise ValueError('immutable receipt collision')
        self.blobs[key,kind] = data

    def read_artifact(self, key): return self.blobs[(key,'bytes')]

    def persist_test_evidence(self, feature, evidence):
        key=(feature,evidence['commit'],evidence['test_id'])
        data=canonical(evidence)
        if key in self.test_proofs and self.test_proofs[key] != data:
            raise ValueError('immutable test evidence collision')
        self.test_proofs[key]=data

    def load_test_evidence(self, feature, commit, test_id):
        return self.test_proofs[(feature,commit,test_id)]

    def adapter_binding(self, project, feature, commit, adapter):
        return {'technical_edition_id':'technical-v1','bible_pair_digest':B,
                'source_commit':commit,'adapter_ref':adapter,
                'adapter_digest':('f'*64 if self.adapter_change else D),'simulated':self.fake}

    def trusted_runner(self, project, feature, commit, adapter):
        return {'trusted':not self.fake,'simulated':self.fake,'adapter_digest':D,
                'quality_signer':'quality-signer'}

    def author_lineage(self, key, subject):
        return {**self.lineage,'commit':COMMIT,'simulated':self.fake}

    def execute(self, test_id: str, commit: str = COMMIT):
        command = ['cmd','/c','echo',test_id] if sys.platform == 'win32' else ['/bin/echo',test_id]
        cp = subprocess.run(command, capture_output=True, check=False)
        data = cp.stdout + cp.stderr
        item = {'test_id':test_id,'commit':commit,'exit_code':cp.returncode,
                'log_digest':digest_bytes(data),'trusted_runner_ref':'local-runner',
                'observed_at_us':1234,'adapter_digest':D,'toolchain_digest':E,'input_digest':D}
        self.logs[test_id] = (item,data)
        return item

    def verify_test(self, item):
        self.call_count += 1
        stored, data = self.logs[item['test_id']]
        checked = dict(stored)
        checked.update({'log_bytes':data + (b'tampered' if self.tamper else b''),
                        'trusted_runner':not self.fake,'simulated':self.fake,
                        'executed':True,'command':['/bin/echo',item['test_id']],
                        'runner_principal_id':'local-runner-principal'})
        if self.toolchain_change: checked['toolchain_digest'] = 'f' * 64
        return checked


class Repo:
    def __init__(self): self.commit = COMMIT; self.simulated = False
    def inspect_branch(self, project, repo, branch):
        return Result('OK','OK',{'repo':repo,'branch':branch,'project_id':project,
                                'feature_id':'feature','commit':self.commit,
                                'verified':True,'simulated':self.simulated},'repo-proof')


class Artifacts:
    def __init__(self, proofs): self.proofs=proofs; self.manifest_sha = None
    def get_feature_manifest(self, feature, digest_value):
        return Result('OK','OK',{'manifest_ref':'manifest','manifest_digest':self.manifest_sha,
                                 'simulated':False},'artifact-proof')
    def verify_artifact_bytes(self, manifest_ref, expected_digest):
        data=self.proofs.read_artifact(manifest_ref)
        return Result('OK','OK',{'content_sha256':digest_bytes(data),'simulated':False},'byte-proof')


class Adapter:
    def __init__(self, proofs): self.proofs=proofs; self.missing=None; self.fail=False
    def resolve_adapter(self, project, edition, commit):
        return Result('OK','OK',{'adapter_ref':'adapter','adapter_digest':D,
                      'source_commit':commit,'simulated':False},'resolved-adapter')
    def _result(self, ids):
        ids = [x for x in ids if x!=self.missing]
        evidence=[self.proofs.execute(x) for x in ids]
        if self.fail and evidence: evidence[0]['exit_code']=1
        return Result('OK','OK',{'test_evidence':evidence},'executed-local')
    def run_import(self, *args):return self._result(['adapter.import'])
    def run_build(self, *args):return self._result(['adapter.build'])
    def run_feature_smoke(self, *args):return self._result(['adapter.smoke','ACCEPT1'])
    def verify_assets(self, *args):return self._result(['adapter.assets'])


class WorkGraph:
    def __init__(self):self.calls=[];self.fail=False
    def create_rework(self, feature, failure, rev):
        self.calls.append((feature,failure,rev))
        return Result('BLOCKED_LOCAL' if self.fail else 'ACCEPTED','ACCEPTED_PENDING')


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.store=Store();self.proofs=Proofs();self.repo=Repo();self.artifacts=Artifacts(self.proofs)
        self.adapter=Adapter(self.proofs);self.graph=WorkGraph()
        self.contract={'project_id':'proj','feature_id':'feature','brief_id':'brief',
                       'bible_pair_digest':B,'contract_digest':C,'repository_ref':'repo','feature_branch':'feature',
                       'acceptance_ids':['ACCEPT1'],'deliverables':[{'deliverable_id':'CODE1',
                       'kind':'CODE','required':True,'requirement_refs':['CLAUSE1'],'acceptance_ids':['ACCEPT1'],'description':'code'}]}
        self.store.seed(self.contract)
        self.content=b'actual artifact bytes'
        self.proofs.blobs[('artifact','bytes')]=self.content
        self.manifest={'project_id':'proj','feature_id':'feature','contract_digest':C,'bible_pair_digest':B,
                       'artifacts':[{'artifact_id':'artifact','project_id':'proj','feature_id':'feature',
                         'task_id':'task','kind':'SOURCE','source_parent_commit':DIFFERENT,'resource_path':'game/file.bin',
                         'content_sha256':digest_bytes(self.content),'producer_principal':'author',
                         'bible_pair_digest':B,'feature_contract_digest':C,'byte_proof_ref':'artifact'}],
                       'deliverable_bindings':{'CODE1':['artifact']}}
        self.manifest_bytes=self.proofs.save('manifest','FeatureArtifactManifest',self.manifest)
        self.proofs.blobs[('manifest','bytes')]=self.manifest_bytes
        self.artifacts.manifest_sha=digest_bytes(self.manifest_bytes)
        self.quality=QualityService(store=self.store, artifacts=self.artifacts, repository=self.repo,
                                     project_adapter=self.adapter,workgraph=self.graph,
                                     proofs=self.proofs,result_type=Result,clock_us=lambda:5678)
        self.author={'principals':['author'],'roots':['author-root']}
        self.reviewer=self.identity('reviewer','independent-root')
        self.bible_ref='bible-proof'

    def tearDown(self):self.store.db.close()

    @staticmethod
    def identity(principal, root):
        return Result('OK','OK',{'principal_id':principal,'issuer_id':'trusted-identity-provider',
                       'lineage_principals':[principal],'lineage_roots':[root],
                       'authenticated':True,'simulated':False},'identity-proof')

    def review_pass(self):
        assigned=self.quality.review_submission('task',self.artifacts.manifest_sha,self.author,self.reviewer)
        self.assertEqual('ACCEPTED',assigned.status,assigned)
        review_id=assigned.record['review_id']
        record={'review_id':review_id,'subject_digest':self.artifacts.manifest_sha,
                'verdict':'ACCEPT','reviewer_principal_id':'reviewer',
                'checked_commit':COMMIT,'checked_clause_refs':['CLAUSE1'],'findings':[]}
        self.proofs.save('review-proof','IndependentReview',record)
        verdict=self.quality.record_review_verdict(review_id,'review-proof',self.reviewer)
        self.assertEqual('OK',verdict.status,verdict)
        return review_id

    def gates(self):
        return self.quality.run_production_gates('feature',COMMIT,'manifest','adapter')

    def ready(self,review_id):
        bible={'project_id':'proj','feature_id':'feature','bible_pair_digest':B,
               'subject_digest':self.artifacts.manifest_sha,'clause_refs_checked':['CLAUSE1'],
               'violations':[],'semantic_review_ref':review_id,'mechanical_check_refs':['mechanical-1'],
               'result':'PASS'}
        self.proofs.save('mechanical-1','BibleTraceEvidence',{'result':'PASS','subject':self.artifacts.manifest_sha})
        self.proofs.save(self.bible_ref,'BibleComplianceReceipt',bible)
        return self.quality.issue_qa_ready('feature',COMMIT,C,{
            'manifest_ref':'manifest','project_adapter_ref':'adapter',
            'bible_compliance_ref':self.bible_ref,'independent_review_ref':review_id})

    def full_ready(self):
        rid=self.review_pass()
        self.assertEqual('OK',self.gates().status)
        ready=self.ready(rid)
        self.assertEqual('OK',ready.status,ready)
        return ready

    def test_full_receipt_has_exact_sha_and_bible_and_runner(self):
        ready=self.full_ready()
        r=ready.record
        self.assertEqual('QA_READY',r['status'])
        self.assertEqual(COMMIT,r['tested_commit'])
        self.assertEqual(5,len(r['test_evidence']))
        self.assertEqual('quality-signer',r['trusted_quality_signer'])
        self.assertEqual(1,self.store.fetch_one('SELECT COUNT(*) AS n FROM v1_qa_ready_receipts')['n'])

    def test_idempotent_qa_ready_same_bytes(self):
        ready=self.full_ready()
        again=self.ready(ready.record['independent_review_ref'])
        self.assertEqual('OK',again.status,again)
        self.assertEqual(ready.receipt_ref,again.receipt_ref)

    def test_independent_qa_pass_is_separate_from_qa_ready(self):
        ready=self.full_ready()
        self.assertIsNone(self.store.fetch_one('SELECT * FROM v1_independent_qa'))
        data={'feature_id':'feature','commit':COMMIT,'reviewer':'qa-reviewer',
              'verdict':'PASS','qa_ready_receipt_id':ready.receipt_ref,
              'failed_task_ids':[],'finding_refs':[]}
        self.proofs.save('qa-verdict','IndependentQA',data)
        verdict=self.quality.record_independent_qa('feature',COMMIT,
                 {'qa_ready_receipt_id':ready.receipt_ref,'evidence_ref':'qa-verdict','verdict':'PASS'},
                 self.identity('qa-reviewer','qa-independent-root'))
        self.assertEqual('OK',verdict.status,verdict)
        self.assertEqual(0,len(self.graph.calls))
        self.assertEqual('VERIFYING',self.store.fetch_one('SELECT phase FROM v1_feature_runs')['phase'])

    def test_independent_qa_failure_issues_scoped_workgraph_rework(self):
        ready=self.full_ready()
        data={'feature_id':'feature','commit':COMMIT,'reviewer':'qa-reviewer',
              'verdict':'FAIL','qa_ready_receipt_id':ready.receipt_ref,
              'failed_task_ids':['task'],'finding_refs':['finding-1']}
        self.proofs.save('finding-1','QAFinding',{'task_id':'task','finding':'verified-failure'})
        self.proofs.save('qa-verdict','IndependentQA',data)
        verdict=self.quality.record_independent_qa('feature',COMMIT,
                 {'qa_ready_receipt_id':ready.receipt_ref,'evidence_ref':'qa-verdict','verdict':'FAIL'},
                 self.identity('qa-reviewer','qa-independent-root'))
        self.assertEqual('OK',verdict.status,verdict)
        self.assertEqual(1,len(self.graph.calls))
        self.assertEqual('feature',self.graph.calls[0][0])
        self.assertEqual(COMMIT,self.graph.calls[0][1]['source_commit'])
        self.assertEqual(1,self.graph.calls[0][2])

    def test_review_rejects_author(self):
        r=self.quality.review_submission('task',self.artifacts.manifest_sha,self.author,
                                         self.identity('author','author-root'))
        self.assertEqual('REJECTED',r.status)

    def test_review_rejects_same_root_different_principal(self):
        r=self.quality.review_submission('task',self.artifacts.manifest_sha,self.author,
                                         self.identity('other','author-root'))
        self.assertEqual('REJECTED',r.status)

    def test_review_rejects_unverified_identity(self):
        r=self.quality.review_submission('task',self.artifacts.manifest_sha,self.author,
                                         Result('OK','OK',self.reviewer.record))
        self.assertEqual('REJECTED',r.status)

    def test_review_rejects_fabricated_subject_digest(self):
        r=self.quality.review_submission('task',D,self.author,self.reviewer)
        self.assertEqual('CONFLICT',r.status)

    def test_review_rejects_fabricated_author_lineage(self):
        r=self.quality.review_submission('task',self.artifacts.manifest_sha,
                         {'principals':['someone-else'],'roots':['different']},self.reviewer)
        self.assertEqual('REJECTED',r.status)

    def test_review_rejects_stale_graph(self):
        self.store.execute('UPDATE v1_feature_runs SET graph_revision=0')
        r=self.quality.review_submission('task',self.artifacts.manifest_sha,self.author,self.reviewer)
        self.assertEqual('CONFLICT',r.status)

    def test_gate_rejects_missing_required_test(self):
        self.adapter.missing='ACCEPT1'
        r=self.gates()
        self.assertEqual('NOT_VERIFIED',r.status)
        self.assertEqual('TEST_NOT_RUN',r.code)

    def test_gate_rejects_nonzero_exit(self):
        self.adapter.fail=True
        r=self.gates()
        self.assertEqual('REJECTED',r.status)
        self.assertEqual('TEST_FAILED',r.code)

    def test_gate_rejects_missing_required_artifact(self):
        self.manifest['deliverable_bindings']['CODE1']=[]
        self._update_manifest()
        r=self.gates()
        self.assertEqual('BLOCKED_LOCAL',r.status)

    def _update_manifest(self):
        self.manifest_bytes=self.proofs.save('manifest','FeatureArtifactManifest',self.manifest)
        self.proofs.blobs[('manifest','bytes')]=self.manifest_bytes
        self.artifacts.manifest_sha=digest_bytes(self.manifest_bytes)

    def test_gate_rejects_tampered_artifact(self):
        self.proofs.blobs[('artifact','bytes')]=b'modified'
        r=self.gates()
        self.assertEqual('REJECTED',r.status)

    def test_gate_rejects_tampered_log_bytes(self):
        self.proofs.tamper=True
        r=self.gates()
        self.assertEqual('REJECTED',r.status)

    def test_gate_rejects_changed_toolchain(self):
        self.proofs.toolchain_change=True
        r=self.gates()
        self.assertEqual('REJECTED',r.status)

    def test_gate_rejects_simulated_runner(self):
        self.proofs.fake=True
        r=self.gates()
        self.assertEqual('NOT_VERIFIED',r.status)

    def test_gate_rejects_head_drift(self):
        self.repo.commit=DIFFERENT
        r=self.gates()
        self.assertEqual('CONFLICT',r.status)

    def test_gate_rejects_simulated_source_readback(self):
        self.repo.simulated=True
        r=self.gates()
        self.assertEqual('NOT_VERIFIED',r.status)

    def test_qa_ready_rejects_missing_review(self):
        self.assertEqual('OK',self.gates().status)
        r=self.ready('unknown-review')
        self.assertEqual('BLOCKED_LOCAL',r.status)

    def test_qa_ready_rejects_missing_build(self):
        rid=self.review_pass()
        r=self.ready(rid)
        self.assertEqual('NOT_VERIFIED',r.status)

    def test_qa_ready_rejects_bible_failure(self):
        rid=self.review_pass();self.assertEqual('OK',self.gates().status)
        r=self.ready(rid)
        bible=json.loads(self.proofs.read_record(self.bible_ref,'BibleComplianceReceipt'))
        bible['result']='FAIL'
        self.proofs.save(self.bible_ref,'BibleComplianceReceipt',bible)
        denied=self.quality.issue_qa_ready('feature',COMMIT,C,{
            'manifest_ref':'manifest','project_adapter_ref':'adapter',
            'bible_compliance_ref':self.bible_ref,'independent_review_ref':rid})
        self.assertEqual('REJECTED',denied.status)

    def test_qa_ready_rejects_new_source_after_gates(self):
        rid=self.review_pass();self.assertEqual('OK',self.gates().status)
        self.repo.commit=DIFFERENT
        r=self.ready(rid)
        self.assertEqual('CONFLICT',r.status)

    def test_qa_ready_rejects_stale_runner(self):
        rid=self.review_pass();self.assertEqual('OK',self.gates().status)
        self.proofs.toolchain_change=True
        r=self.ready(rid)
        self.assertEqual('REJECTED',r.status)

    def test_qa_ready_rejects_bible_semantic_review_not_linked(self):
        rid=self.review_pass();self.assertEqual('OK',self.gates().status)
        self.ready(rid)
        b=json.loads(self.proofs.read_record(self.bible_ref,'BibleComplianceReceipt'))
        b['semantic_review_ref']='different-review'
        self.proofs.save(self.bible_ref,'BibleComplianceReceipt',b)
        r=self.quality.issue_qa_ready('feature',COMMIT,C,{
            'manifest_ref':'manifest','project_adapter_ref':'adapter',
            'bible_compliance_ref':self.bible_ref,'independent_review_ref':rid})
        self.assertEqual('REJECTED',r.status)

    def test_qa_independence_rejects_same_author_root(self):
        ready=self.full_ready()
        data={'feature_id':'feature','commit':COMMIT,'reviewer':'new-person',
              'verdict':'PASS','qa_ready_receipt_id':ready.receipt_ref,
              'failed_task_ids':[],'finding_refs':[]}
        self.proofs.save('qa-verdict','IndependentQA',data)
        r=self.quality.record_independent_qa('feature',COMMIT,
               {'qa_ready_receipt_id':ready.receipt_ref,'evidence_ref':'qa-verdict','verdict':'PASS'},
               self.identity('new-person','author-root'))
        self.assertEqual('REJECTED',r.status)

    def test_qa_independence_rejects_quality_signer(self):
        ready=self.full_ready()
        data={'feature_id':'feature','commit':COMMIT,'reviewer':'quality-signer',
              'verdict':'PASS','qa_ready_receipt_id':ready.receipt_ref,
              'failed_task_ids':[],'finding_refs':[]}
        self.proofs.save('qa-verdict','IndependentQA',data)
        r=self.quality.record_independent_qa('feature',COMMIT,
               {'qa_ready_receipt_id':ready.receipt_ref,'evidence_ref':'qa-verdict','verdict':'PASS'},
               self.identity('quality-signer','quality-root'))
        self.assertEqual('REJECTED',r.status)

    def test_qa_independence_rejects_changed_qa_source_sha(self):
        ready=self.full_ready();self.repo.commit=DIFFERENT
        r=self.quality.record_independent_qa('feature',COMMIT,
                {'qa_ready_receipt_id':ready.receipt_ref,'evidence_ref':'qa-verdict','verdict':'PASS'},
                self.identity('qa-reviewer','qa-independent-root'))
        self.assertEqual('CONFLICT',r.status)

    def test_independent_qa_rejects_fabricated_verdict(self):
        ready=self.full_ready()
        self.proofs.save('qa-verdict','IndependentQA',{'feature_id':'feature','commit':COMMIT,
                        'reviewer':'imposter','verdict':'PASS','qa_ready_receipt_id':ready.receipt_ref,
              'failed_task_ids':[],'finding_refs':[]})
        r=self.quality.record_independent_qa('feature',COMMIT,
               {'qa_ready_receipt_id':ready.receipt_ref,'evidence_ref':'qa-verdict','verdict':'PASS'},
               self.identity('qa-reviewer','qa-independent-root'))
        self.assertEqual('REJECTED',r.status)

    def test_gate_records_required_clauses_and_runner_principal(self):
        self.assertEqual('OK',self.gates().status)
        mapping=self.store.fetch_one('SELECT requirement_id,deliverable_id,acceptance_check_id FROM v1_quality_requirements')
        self.assertEqual({'requirement_id':'CLAUSE1','deliverable_id':'CODE1',
                          'acceptance_check_id':'ACCEPT1'},mapping)
        t=self.store.fetch_one('SELECT runner_principal_id FROM v1_test_executions LIMIT 1')
        self.assertEqual('local-runner-principal',t['runner_principal_id'])

    def test_review_assignment_idempotent_same_subject(self):
        first=self.quality.review_submission('task',self.artifacts.manifest_sha,self.author,self.reviewer)
        second=self.quality.review_submission('task',self.artifacts.manifest_sha,self.author,self.reviewer)
        self.assertEqual('ACCEPTED',first.status)
        self.assertEqual(first.receipt_ref,second.receipt_ref)
        self.assertEqual(1,self.store.fetch_one('SELECT COUNT(*) AS n FROM v1_review_assignments')['n'])

    def test_gate_rejects_artifact_wrong_type_for_code(self):
        self.manifest['artifacts'][0]['kind']='OTHER'
        del self.manifest['artifacts'][0]['source_parent_commit']
        self._update_manifest()
        self.assertEqual('REJECTED',self.gates().status)

    def test_gate_rejects_cross_feature_artifact(self):
        self.manifest['artifacts'][0]['feature_id']='another-feature'
        self._update_manifest()
        self.assertEqual('REJECTED',self.gates().status)

    def test_gate_rejects_wrong_technical_adapter_binding(self):
        self.proofs.adapter_change=True
        r=self.gates()
        self.assertEqual('REJECTED',r.status)
        self.assertEqual('EVIDENCE_INVALID',r.code)

    def test_review_rejects_unsigned_clauseless_semantic_verdict(self):
        assigned=self.quality.review_submission('task',self.artifacts.manifest_sha,self.author,self.reviewer)
        self.assertEqual('ACCEPTED',assigned.status)
        rid=assigned.record['review_id']
        self.proofs.save('incomplete-review','IndependentReview',{
            'review_id':rid,'subject_digest':self.artifacts.manifest_sha,'verdict':'ACCEPT',
            'reviewer_principal_id':'reviewer','checked_commit':COMMIT,
            'checked_clause_refs':[],'findings':[]})
        result=self.quality.record_review_verdict(rid,'incomplete-review',self.reviewer)
        self.assertEqual('REJECTED',result.status)
        self.assertEqual('REVIEW_CONFLICT',result.code)

    def test_qa_ready_rejects_tampered_immutable_test_proof(self):
        rid=self.review_pass()
        self.assertEqual('OK',self.gates().status)
        key=('feature',COMMIT,'adapter.build')
        original=json.loads(self.proofs.test_proofs[key])
        original['trusted_runner_ref']='forged-runner'
        self.proofs.test_proofs[key]=canonical(original)
        r=self.ready(rid)
        self.assertEqual('REJECTED',r.status)

    def test_independent_qa_rejects_tampered_ready_receipt_bytes(self):
        ready=self.full_ready()
        self.proofs.blobs[(ready.receipt_ref,'QAReadyReceipt')]=b'{}'
        self.proofs.save('qa-verdict','IndependentQA',{'feature_id':'feature','commit':COMMIT,
            'reviewer':'qa-reviewer','verdict':'PASS','qa_ready_receipt_id':ready.receipt_ref,
            'failed_task_ids':[],'finding_refs':[]})
        r=self.quality.record_independent_qa('feature',COMMIT,{
             'qa_ready_receipt_id':ready.receipt_ref,'evidence_ref':'qa-verdict','verdict':'PASS'},
             self.identity('qa-reviewer','qa-independent-root'))
        self.assertEqual('REJECTED',r.status)
        self.assertEqual('EVIDENCE_INVALID',r.code)

    def test_independent_qa_failure_without_scoped_tasks_is_denied(self):
        ready=self.full_ready()
        self.proofs.save('qa-verdict','IndependentQA',{'feature_id':'feature','commit':COMMIT,
            'reviewer':'qa-reviewer','verdict':'FAIL','qa_ready_receipt_id':ready.receipt_ref,
            'failed_task_ids':[],'finding_refs':['finding-1']})
        r=self.quality.record_independent_qa('feature',COMMIT,{
             'qa_ready_receipt_id':ready.receipt_ref,'evidence_ref':'qa-verdict','verdict':'FAIL'},
             self.identity('qa-reviewer','qa-independent-root'))
        self.assertEqual('REJECTED',r.status)
        self.assertEqual(0,len(self.graph.calls))

    def test_sql_foreign_key_denies_unknown_feature(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.execute('INSERT INTO v1_quality_requirements VALUES(?,?,?,?,?)',
                               ('foreign-feature','CL','D','CHECK',D))

    def test_contracts_are_strict(self):
        t=self.proofs.execute('test1')
        with self.assertRaises(ValueError):test_evidence({**t,'injected':'rogue'})
        with self.assertRaises(ValueError):test_evidence({**t,'exit_code':True})
        with self.assertRaises(ValueError):bible_receipt({'result':'PASS'})

    def test_schema_new_only_has_expected_quality_tables_and_writers(self):
        owners=json.loads((ROOT/'frozen_table_owners.json').read_text())['owner_per_table']
        required={'v1_independent_qa','v1_qa_ready_receipts','v1_quality_requirements',
                  'v1_review_assignments','v1_review_verdicts','v1_test_executions'}
        self.assertEqual({k for k,v in owners.items() if v=='CAB-E'},required)
        for name in required:
            self.assertTrue(self.store.fetch_one("SELECT name FROM sqlite_master WHERE name=?",(name,)))
        fk=self.store.fetch_all('PRAGMA foreign_key_check')
        self.assertEqual([],fk)


if __name__=='__main__': unittest.main()
