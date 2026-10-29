"""R9 QualityPort. No provider authority or database writer is created here.

P2 injects a CAB-E-table-limited transactional store, frozen Artifact/Repository/
ProjectAdapter/WorkGraph ports and independently trusted byte/runner readback.
All provider operations occur *outside* writer transactions; source is rechecked
immediately before the writer CAS. Proofs are not taken from worker prose.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from nomad_studio.review.independence import reviewer_principal
from nomad_studio.verification.validation import (
    artifact_manifest, bible_receipt, canonical, digest, ref, sha, test_evidence, unique_refs,
)

# This file deliberately imports the frozen R8/R9 dataclass shape only by name.
# P2 MUST bind nomad_studio.contracts.runtime_ports_v1.Result to this service;
# that contract is installed by P2, never recreated or aliased in CAB-E.
try:
    from nomad_studio.contracts.runtime_ports_v1 import Result
except ImportError:
    # P1-only source-kit lacks the target contract under src; P2 installs it.
    # Tests inject the frozen contract via dependency, below. No legacy wire shim.
    Result = None


class QualityError(Exception):
    def __init__(self, status: str, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


def _deny(code: str = 'EVIDENCE_INVALID', status: str = 'REJECTED') -> None:
    raise QualityError(status, code)


def _sha_bytes(data: bytes) -> str:
    if not isinstance(data, bytes):
        _deny()
    return hashlib.sha256(data).hexdigest()


def _unpack_json(data: bytes) -> Mapping[str, Any]:
    def distinct(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    try:
        record = json.loads(data.decode('utf-8'), object_pairs_hook=distinct,
                            parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
        if not isinstance(record, dict): _deny()
        return record
    except (UnicodeError, ValueError, TypeError):
        _deny()


def _record(result: Any, *, receipt: bool = True) -> Mapping[str, Any]:
    if getattr(result, 'status', None) != 'OK':
        status = getattr(result, 'status', 'NOT_VERIFIED')
        if status in ('CONFLICT', 'NOT_FOUND', 'RETRYABLE', 'UNKNOWN_EXTERNAL', 'BLOCKED_LOCAL'):
            _deny(getattr(result, 'code', 'PROVIDER_NOT_VERIFIED'), status)
        _deny('PROVIDER_NOT_VERIFIED', 'NOT_VERIFIED')
    value = getattr(result, 'record', None)
    if not isinstance(value, Mapping) or (receipt and not getattr(result, 'receipt_ref', None)):
        _deny()
    if value.get('simulated') is True:
        _deny('PROVIDER_NOT_VERIFIED', 'NOT_VERIFIED')
    return value


class QualityService:
    """Frozen QualityPort methods plus CAB-E-internal independent review verdict.

    `store` exposes read(fn) and transact(fn), where transact's only permitted
    writes are the six CAB-E quality tables from schema_table_owners.json.
    Reader/Tx expose fetch_one, fetch_all and (Tx only) execute. The P2 writer
    supplies serializability, journal/idempotency handling and scoped authority.

    `proofs` is a *trusted local service injected by P2*, not caller-supplied
    Record. It implements read_record(ref, kind)->bytes, read_artifact(ref)->bytes,
    attestation with actual log_bytes and runner_principal_id,
    persist_test_evidence(feature,TestEvidence) and load_test_evidence(feature,commit,test_id)
    for immutable receipt bytes, trusted_runner(project,feature,commit,adapter)->Record,
    and author_lineage(task_or_feature, subject_digest)->Record. Test doubles cannot
    be registered as production evidence providers.
    """

    def __init__(self, *, store: Any, artifacts: Any, repository: Any, project_adapter: Any,
                 workgraph: Any, proofs: Any, result_type: Any = None, clock_us: Any = None):
        self.store, self.artifacts, self.repository = store, artifacts, repository
        self.project_adapter, self.workgraph, self.proofs = project_adapter, workgraph, proofs
        self.Result = result_type or Result
        if self.Result is None:
            raise RuntimeError('P2 must inject the frozen runtime Result type')
        self.clock_us = clock_us or (lambda: time.time_ns() // 1000)

    def _result(self, status: str, code: str, record: Mapping | None = None,
                receipt_ref: str | None = None, evidence_refs: Sequence[str] = ()) -> Any:
        return self.Result(status, code, record, receipt_ref, tuple(evidence_refs))

    def _guard(self, fn):
        try:
            return fn()
        except QualityError as exc:
            return self._result(exc.status, exc.code)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            return self._result('REJECTED', 'INVALID_INPUT')
        except sqlite3.IntegrityError:
            return self._result('CONFLICT', 'REVISION_CONFLICT')
        except sqlite3.OperationalError as exc:
            if 'locked' in str(exc).lower() or 'busy' in str(exc).lower():
                return self._result('RETRYABLE', 'BACKPRESSURE')
            raise

    def _context(self, feature_id: str) -> tuple[dict, dict]:
        ref(feature_id)
        def read(tx):
            run = tx.fetch_one('SELECT project_id,feature_id,bible_pair_digest,active_contract_digest,graph_revision,qa_branch_ref,phase FROM v1_feature_runs WHERE feature_id=?', (feature_id,))
            if run is None:
                return None
            contract = tx.fetch_one('SELECT project_id,feature_id,bible_pair_digest,contract_digest,payload_json FROM v1_feature_contracts WHERE feature_id=? AND contract_digest=?', (feature_id, run['active_contract_digest']))
            return (dict(run), dict(contract) if contract is not None else None)
        rows = self.store.read(read)
        if rows is None:
            _deny('NOT_FOUND', 'NOT_FOUND')
        run, contract_row = rows
        if not contract_row or run['project_id'] != contract_row['project_id'] or run['bible_pair_digest'] != contract_row['bible_pair_digest']:
            _deny('STALE_BIBLE', 'CONFLICT')
        contract = json.loads(contract_row['payload_json'])
        if (not isinstance(contract, dict) or contract.get('project_id') != run['project_id'] or
            contract.get('feature_id') != feature_id or contract.get('bible_pair_digest') != run['bible_pair_digest'] or
            contract.get('contract_digest') != run['active_contract_digest']):
            _deny('REVISION_CONFLICT', 'CONFLICT')
        for key in ('project_id', 'feature_id'):
            ref(run[key])
        for key in ('bible_pair_digest', 'active_contract_digest'):
            sha(run[key])
        return run, contract

    def _source(self, run: Mapping, contract: Mapping, commit: str) -> None:
        sha(commit, commit=True)
        repo = contract.get('repository_ref')
        branch = contract.get('feature_branch') or run['qa_branch_ref']
        if not isinstance(repo, str) or not repo or not isinstance(branch, str) or not branch:
            _deny('EVIDENCE_INVALID', 'BLOCKED_LOCAL')
        report = _record(self.repository.inspect_branch(run['project_id'], repo, branch))
        expected = {'repo': repo, 'branch': branch, 'project_id': run['project_id'],
                    'feature_id': run['feature_id'], 'commit': commit, 'verified': True, 'simulated': False}
        if any(report.get(k) != v for k, v in expected.items()):
            _deny('SOURCE_CONFLICT', 'CONFLICT')

    def _manifest(self, run: Mapping, contract: Mapping, expected_ref: str) -> tuple[dict, dict, str]:
        ref(expected_ref)
        result = _record(self.artifacts.get_feature_manifest(run['feature_id'], contract['contract_digest']))
        if result.get('manifest_ref') != expected_ref or result.get('simulated') is not False:
            _deny()
        data = self.proofs.read_record(expected_ref, 'FeatureArtifactManifest')
        manifest = _unpack_json(data)
        if not isinstance(manifest.get('artifacts'), list) or not isinstance(manifest.get('deliverable_bindings'), dict):
            _deny()
        actual_sha = _sha_bytes(data)
        if result.get('manifest_digest') != actual_sha:
            _deny()
        if manifest.get('feature_id') != run['feature_id'] or manifest.get('project_id') != run['project_id'] or manifest.get('contract_digest') != contract['contract_digest'] or manifest.get('bible_pair_digest') != run['bible_pair_digest']:
            _deny('WRONG_FEATURE')
        artifacts: dict[str, dict] = {}
        for raw in manifest['artifacts']:
            item = artifact_manifest(raw)
            if (item['feature_id'] != run['feature_id'] or item['project_id'] != run['project_id'] or
                item['bible_pair_digest'] != run['bible_pair_digest'] or
                item['feature_contract_digest'] != contract['contract_digest']):
                _deny('WRONG_FEATURE')
            if item['artifact_id'] in artifacts:
                _deny()
            task = self.store.read(lambda tx: tx.fetch_one('SELECT feature_id,graph_revision FROM v1_tasks WHERE task_id=?', (item['task_id'],)))
            if not task or task['feature_id'] != run['feature_id'] or task['graph_revision'] != run['graph_revision']:
                _deny('WRONG_FEATURE', 'CONFLICT')
            raw_content = self.proofs.read_artifact(item['byte_proof_ref'])
            if _sha_bytes(raw_content) != item['content_sha256']:
                _deny()
            # CAB-I owns the artifact ledger and authoritative byte readback.
            proof = _record(self.artifacts.verify_artifact_bytes(item['byte_proof_ref'], item['content_sha256']))
            if proof.get('content_sha256') != item['content_sha256'] or proof.get('simulated') is not False:
                _deny()
            artifacts[item['artifact_id']] = item
        for deliverable in contract.get('deliverables', []):
            if not isinstance(deliverable, dict):
                _deny('INVALID_INPUT')
            identifier = ref(deliverable.get('deliverable_id'))
            binds = manifest['deliverable_bindings'].get(identifier, [])
            if not isinstance(binds, list) or len(binds) != len(set(binds)) or any(x not in artifacts for x in binds):
                _deny()
            if deliverable.get('required') is True and not binds:
                _deny('EVIDENCE_INVALID', 'BLOCKED_LOCAL')
            allowed_kinds = {'CODE': {'SOURCE'}, 'SCENE': {'SCENE'}, 'ASSET': {'ASSET'},
                             'AUDIO': {'AUDIO'}, 'ANIMATION': {'ASSET'}, 'TEST': {'TEST_LOG'},
                             'DIALOGUE': {'DIALOGUE'}, 'QUEST_GRAPH': {'QUEST_GRAPH'},
                             'NARRATIVE': {'OTHER'}, 'OTHER': {'OTHER'}}
            if deliverable.get('kind') not in allowed_kinds or any(artifacts[x]['kind'] not in allowed_kinds[deliverable['kind']] for x in binds):
                _deny('EVIDENCE_INVALID')
            if not set(deliverable.get('acceptance_ids', [])) <= set(contract.get('acceptance_ids', [])):
                _deny('INVALID_INPUT')
        if not artifacts:
            _deny('EVIDENCE_INVALID', 'BLOCKED_LOCAL')
        return dict(manifest), artifacts, actual_sha

    def _adapter_digest(self, run: Mapping, commit: str, adapter_ref: str) -> str:
        binding = self.proofs.adapter_binding(run['project_id'], run['feature_id'], commit, adapter_ref)
        if not isinstance(binding, Mapping) or binding.get('simulated') is not False:
            _deny('PROVIDER_NOT_VERIFIED','NOT_VERIFIED')
        if (binding.get('bible_pair_digest') != run['bible_pair_digest'] or
            binding.get('source_commit') != commit or binding.get('adapter_ref') != adapter_ref):
            _deny('EVIDENCE_INVALID')
        edition = ref(binding.get('technical_edition_id'))
        expected_digest = sha(binding.get('adapter_digest'))
        resolved = _record(self.project_adapter.resolve_adapter(run['project_id'], edition, commit))
        if (resolved.get('adapter_ref') != adapter_ref or resolved.get('adapter_digest') != expected_digest or
            resolved.get('source_commit') != commit or resolved.get('simulated') is not False):
            _deny('EVIDENCE_INVALID')
        return expected_digest

    def _test(self, raw: Mapping, commit: str, adapter_digest: str) -> tuple[dict, str]:
        item = test_evidence(raw)
        if item['commit'] != commit or item['adapter_digest'] != adapter_digest or item['exit_code'] != 0:
            _deny('TEST_FAILED')
        # The trusted P2 reader MUST read executed native runner data and
        # original stdout/stderr log bytes, not a model-uploaded assertion.
        checked = self.proofs.verify_test(item)
        if not isinstance(checked, Mapping) or checked.get('trusted_runner') is not True or checked.get('simulated') is not False or checked.get('executed') is not True:
            _deny('TEST_NOT_RUN', 'NOT_VERIFIED')
        if not isinstance(checked.get('command'), (list, tuple)) or not checked['command']:
            _deny('TEST_NOT_RUN', 'NOT_VERIFIED')
        for key in ('test_id', 'commit', 'exit_code', 'trusted_runner_ref', 'observed_at_us',
                    'adapter_digest', 'toolchain_digest', 'input_digest'):
            if checked.get(key) != item[key]:
                _deny()
        if _sha_bytes(checked.get('log_bytes')) != item['log_digest']:
            _deny()
        principal = ref(checked.get('runner_principal_id'))
        return item, principal

    def _required_test_ids(self, contract: Mapping) -> set[str]:
        checks = unique_refs(contract.get('acceptance_ids'))
        return {'adapter.import', 'adapter.build', 'adapter.smoke', 'adapter.assets', *checks}

    def _test_rows(self, feature_id: str, commit: str) -> dict[str, dict]:
        rows = self.store.read(lambda tx: tx.fetch_all(
            'SELECT * FROM v1_test_executions WHERE feature_id=? AND commit_sha=?', (feature_id, commit)))
        indexed = {}
        for row in rows:
            item = dict(row)
            if item['test_id'] in indexed:
                _deny('EVIDENCE_INVALID', 'CONFLICT')
            indexed[item['test_id']] = item
        return indexed

    def review_submission(self, task_id: str, subject_digest: str, author_lineage: Mapping,
                          reviewer_identity: Any) -> Any:
        return self._guard(lambda: self._review_submission(task_id, subject_digest, author_lineage, reviewer_identity))

    def _review_submission(self, task_id, subject_digest, author_lineage, reviewer_identity):
        ref(task_id); sha(subject_digest)
        row = self.store.read(lambda tx: tx.fetch_one('SELECT task_id,feature_id,output_manifest_ref,graph_revision FROM v1_tasks WHERE task_id=?', (task_id,)))
        if row is None: _deny('NOT_FOUND', 'NOT_FOUND')
        run, contract = self._context(row['feature_id'])
        if row['graph_revision'] != run['graph_revision'] or not row['output_manifest_ref']:
            _deny('REVISION_CONFLICT', 'CONFLICT')
        if _sha_bytes(self.proofs.read_artifact(row['output_manifest_ref'])) != subject_digest:
            _deny('SOURCE_CONFLICT', 'CONFLICT')
        if not isinstance(author_lineage, Mapping) or set(author_lineage) != {'principals','roots'}:
            _deny('INVALID_INPUT')
        unique_refs(author_lineage['principals']); unique_refs(author_lineage['roots'])
        trusted = self.proofs.author_lineage(task_id, subject_digest)
        if (not isinstance(trusted, Mapping) or trusted.get('simulated') is not False or
            trusted.get('principals') != author_lineage.get('principals') or
            trusted.get('roots') != author_lineage.get('roots')):
            _deny()
        try: principal = reviewer_principal(reviewer_identity, author_lineage)
        except ValueError: _deny('REVIEW_CONFLICT')
        if trusted.get('commit') is None:
            _deny()
        self._source(run,contract,trusted['commit'])
        assignment = 'review-' + digest({'task_id':task_id, 'subject_digest':subject_digest,
                                         'reviewer':principal, 'author_lineage':author_lineage,
                                         'source_commit':trusted['commit']})
        def change(tx):
            current = tx.fetch_one('SELECT graph_revision FROM v1_feature_runs WHERE feature_id=?', (run['feature_id'],))
            if not current or current['graph_revision'] != run['graph_revision']:
                _deny('REVISION_CONFLICT', 'CONFLICT')
            existing = tx.fetch_one('SELECT task_id,subject_digest,reviewer_principal_id,author_lineage_digest FROM v1_review_assignments WHERE review_id=?', (assignment,))
            if existing:
                if (existing['task_id'], existing['subject_digest'], existing['reviewer_principal_id'], existing['author_lineage_digest']) != (task_id, subject_digest, principal, digest(author_lineage)):
                    _deny('REVIEW_CONFLICT', 'CONFLICT')
                return
            tx.execute('INSERT INTO v1_review_assignments(feature_id,review_id,task_id,subject_digest,reviewer_principal_id,author_lineage_digest,state) VALUES(?,?,?,?,?,?,?)',
                       (run['feature_id'], assignment, task_id, subject_digest, principal, digest(author_lineage), 'ASSIGNED'))
        self.store.transact(change)
        return self._result('ACCEPTED', 'ACCEPTED_PENDING', {'review_id': assignment, 'task_id': task_id,
                           'subject_digest': subject_digest}, assignment)

    def record_review_verdict(self, review_id: str, verdict_ref: str, reviewer_identity: Any) -> Any:
        """CAB-E internal intake for independently authored semantic code/bible review."""
        return self._guard(lambda: self._record_review_verdict(review_id, verdict_ref, reviewer_identity))

    def _record_review_verdict(self, review_id, verdict_ref, reviewer_identity):
        ref(review_id); ref(verdict_ref)
        row = self.store.read(lambda tx: tx.fetch_one('SELECT * FROM v1_review_assignments WHERE review_id=?', (review_id,)))
        if row is None: _deny('NOT_FOUND', 'NOT_FOUND')
        raw = _unpack_json(self.proofs.read_record(verdict_ref, 'IndependentReview'))
        required_review_fields = {'review_id','subject_digest','verdict','reviewer_principal_id',
                                  'checked_commit','checked_clause_refs','findings'}
        if (set(raw) != required_review_fields or raw.get('review_id') != review_id or
            raw.get('subject_digest') != row['subject_digest'] or
            raw.get('verdict') not in ('ACCEPT', 'REJECT', 'INCONCLUSIVE') or
            raw.get('reviewer_principal_id') != row['reviewer_principal_id']):
            _deny()
        checked_clauses = set(unique_refs(raw['checked_clause_refs']))
        findings = unique_refs(raw['findings'])
        if raw['verdict'] == 'ACCEPT' and findings: _deny('REVIEW_CONFLICT')
        author_lineage = self.proofs.author_lineage(row['task_id'], row['subject_digest'])
        if author_lineage.get('simulated') is not False or digest({k: author_lineage[k] for k in ('principals','roots')}) != row['author_lineage_digest']:
            _deny()
        run, contract = self._context(row['feature_id'])
        self._source(run,contract,author_lineage['commit'])
        if raw['checked_commit'] != author_lineage['commit'] or not {
                clause for entry in contract.get('deliverables',[]) for clause in entry.get('requirement_refs',[])
                } <= checked_clauses:
            _deny('REVIEW_CONFLICT')
        try: principal = reviewer_principal(reviewer_identity, {k:author_lineage[k] for k in ('principals','roots')})
        except ValueError: _deny('REVIEW_CONFLICT')
        if principal != row['reviewer_principal_id']:
            _deny('REVIEW_CONFLICT')
        evidence_digest = _sha_bytes(self.proofs.read_record(verdict_ref, 'IndependentReview'))
        verdict_id = 'verdict-' + digest({'review_id':review_id,'subject_digest':row['subject_digest'],
                                          'verdict':raw['verdict'],'evidence_digest':evidence_digest})
        # Store provider-readback reviewer bytes under a deterministic local receipt.
        # The SQL verdict stores only the hash and cannot prove review commit.
        review_bytes = self.proofs.read_record(verdict_ref, 'IndependentReview')
        self.proofs.persist_receipt(verdict_id, 'IndependentReviewVerdict', review_bytes)
        if _sha_bytes(self.proofs.read_record(verdict_id, 'IndependentReviewVerdict')) != evidence_digest:
            _deny()
        def change(tx):
            current = tx.fetch_one('SELECT state FROM v1_review_assignments WHERE review_id=?', (review_id,))
            if current and current['state'] == raw['verdict']:
                previous = tx.fetch_one('SELECT evidence_digest,verdict FROM v1_review_verdicts WHERE review_id=?', (review_id,))
                if previous and previous['evidence_digest'] == evidence_digest and previous['verdict'] == raw['verdict']:
                    return
            if not current or current['state'] != 'ASSIGNED': _deny('REVISION_CONFLICT', 'CONFLICT')
            tx.execute('INSERT INTO v1_review_verdicts VALUES(?,?,?,?,?,?)',
                       (verdict_id, review_id, row['subject_digest'], evidence_digest, raw['verdict'], principal))
            tx.execute('UPDATE v1_review_assignments SET state=? WHERE review_id=?', (raw['verdict'], review_id))
        self.store.transact(change)
        return self._result('OK', 'OK', {'verdict_id': verdict_id, 'verdict': raw['verdict']}, verdict_id, (verdict_ref,))

    def run_production_gates(self, feature_id: str, tested_commit: str, manifest_ref: str,
                             project_adapter_ref: str) -> Any:
        return self._guard(lambda: self._run_production_gates(feature_id, tested_commit, manifest_ref, project_adapter_ref))

    def _run_production_gates(self, feature_id, commit, manifest_ref, adapter_ref):
        ref(adapter_ref); sha(commit, commit=True)
        run, contract = self._context(feature_id)
        if run['graph_revision'] < 1: _deny('REVISION_CONFLICT', 'CONFLICT')
        self._source(run, contract, commit)
        manifest, artifacts, manifest_sha = self._manifest(run, contract, manifest_ref)
        bound_digest = self._adapter_digest(run, commit, adapter_ref)
        runner = self.proofs.trusted_runner(run['project_id'], feature_id, commit, adapter_ref)
        if not isinstance(runner, Mapping) or runner.get('simulated') is not False or runner.get('trusted') is not True:
            _deny('TEST_NOT_RUN', 'NOT_VERIFIED')
        adapter_digest = sha(runner.get('adapter_digest'))
        if adapter_digest != bound_digest: _deny('EVIDENCE_INVALID')
        # Exact frozen adapter methods; real provider calls must happen outside the writer transaction.
        calls = (
            self.project_adapter.run_import(run['project_id'], commit, adapter_ref, runner),
            self.project_adapter.run_build(run['project_id'], commit, adapter_ref, runner),
            self.project_adapter.run_feature_smoke(feature_id, commit, contract.get('acceptance_ids', []), adapter_ref, runner),
            self.project_adapter.verify_assets(feature_id, commit, manifest_ref, adapter_ref, runner),
        )
        tests: dict[str, dict] = {}
        for result in calls:
            record = _record(result)
            evidence = record.get('test_evidence')
            if not isinstance(evidence, list) or not evidence:
                _deny('TEST_NOT_RUN', 'NOT_VERIFIED')
            for raw in evidence:
                item, runner_principal = self._test(raw, commit, adapter_digest)
                if item['test_id'] in tests: _deny('EVIDENCE_INVALID', 'CONFLICT')
                tests[item['test_id']] = (item, runner_principal)
        required = self._required_test_ids(contract)
        if not required <= tests.keys():
            _deny('TEST_NOT_RUN', 'NOT_VERIFIED')
        # Preserve the full frozen TestEvidence record, including trusted_runner_ref,
        # in immutable byte storage: SQL has runner_principal_id, not runner_ref.
        for item, _ in tests.values():
            self.proofs.persist_test_evidence(feature_id, item)
            if _unpack_json(self.proofs.load_test_evidence(feature_id, commit, item['test_id'])) != item:
                _deny('EVIDENCE_INVALID')
        self._source(run, contract, commit)  # HEAD drift after external checks invalidates the run.
        requirements = {}
        for deliverable in contract.get('deliverables', []):
            for clause in deliverable.get('requirement_refs', []):
                for acceptance in deliverable.get('acceptance_ids', []):
                    key = (ref(clause), ref(acceptance))
                    if key in requirements and requirements[key] != deliverable['deliverable_id']:
                        _deny('BIBLE_CONFLICT', 'CONFLICT')
                    requirements[key] = deliverable['deliverable_id']
        def change(tx):
            latest = tx.fetch_one('SELECT active_contract_digest,bible_pair_digest,graph_revision FROM v1_feature_runs WHERE feature_id=?', (feature_id,))
            if not latest or latest['active_contract_digest'] != run['active_contract_digest'] or latest['bible_pair_digest'] != run['bible_pair_digest'] or latest['graph_revision'] != run['graph_revision']:
                _deny('REVISION_CONFLICT', 'CONFLICT')
            for (clause,check), deliverable_id in requirements.items():
                requirement_digest = digest({'contract_digest':contract['contract_digest'],
                                             'requirement_id':clause,'acceptance_check_id':check,
                                             'deliverable_id':deliverable_id})
                old = tx.fetch_one('SELECT deliverable_id,requirement_digest FROM v1_quality_requirements WHERE feature_id=? AND requirement_id=? AND acceptance_check_id=?', (feature_id,clause,check))
                if old:
                    if old['deliverable_id'] != deliverable_id or old['requirement_digest'] != requirement_digest:
                        _deny('REVISION_CONFLICT','CONFLICT')
                else:
                    tx.execute('INSERT INTO v1_quality_requirements VALUES(?,?,?,?,?)',
                               (feature_id,clause,deliverable_id,check,requirement_digest))
            for item, runner_principal in tests.values():
                existing = tx.fetch_one('SELECT * FROM v1_test_executions WHERE feature_id=? AND commit_sha=? AND test_id=?', (feature_id, commit, item['test_id']))
                candidate = (commit, item['test_id'], item['exit_code'], item['log_digest'], item['adapter_digest'], item['toolchain_digest'], item['input_digest'], runner_principal, item['observed_at_us'])
                if existing:
                    previous = tuple(existing[key] for key in ('commit_sha','test_id','exit_code','log_digest','adapter_digest','toolchain_digest','input_digest','runner_principal_id','observed_at_us'))
                    if previous != candidate: _deny('EVIDENCE_INVALID', 'CONFLICT')
                    continue
                tx.execute('INSERT INTO v1_test_executions(feature_id,execution_id,commit_sha,test_id,exit_code,log_digest,adapter_digest,toolchain_digest,input_digest,runner_principal_id,observed_at_us) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                           (feature_id, 'test-' + uuid.uuid4().hex, *candidate))
        self.store.transact(change)
        return self._result('OK', 'OK', {'feature_id': feature_id, 'tested_commit': commit,
                            'manifest_digest': manifest_sha, 'test_ids': sorted(tests), 'gate': 'PRODUCTION_TESTED'},
                            'gate-' + digest({'feature_id':feature_id,'commit':commit,'manifest':manifest_sha,'tests':sorted(tests)}),
                            (manifest_ref,))

    def issue_qa_ready(self, feature_id: str, source_commit: str, feature_contract_digest: str,
                       test_and_bible_evidence: Mapping) -> Any:
        return self._guard(lambda: self._issue_qa_ready(feature_id, source_commit, feature_contract_digest, test_and_bible_evidence))

    def _issue_qa_ready(self, feature_id, commit, contract_digest, details):
        sha(commit, commit=True); sha(contract_digest)
        required_keys = {'manifest_ref', 'project_adapter_ref', 'bible_compliance_ref', 'independent_review_ref'}
        if not isinstance(details, Mapping) or set(details) != required_keys:
            _deny('INVALID_INPUT')
        for key in details: ref(details[key])
        run, contract = self._context(feature_id)
        if contract_digest != run['active_contract_digest']:
            _deny('REVISION_CONFLICT', 'CONFLICT')
        self._source(run, contract, commit)
        manifest, artifacts, manifest_sha = self._manifest(run, contract, details['manifest_ref'])
        bible = bible_receipt(_unpack_json(self.proofs.read_record(details['bible_compliance_ref'], 'BibleComplianceReceipt')))
        if (bible['project_id'] != run['project_id'] or bible['feature_id'] != feature_id or
            bible['bible_pair_digest'] != run['bible_pair_digest'] or bible['subject_digest'] != manifest_sha or
            bible['result'] != 'PASS' or bible['violations']):
            _deny('BIBLE_CONFLICT')
        required_clauses = {c for d in contract['deliverables'] for c in d.get('requirement_refs', [])}
        if not required_clauses <= set(bible['clause_refs_checked']):
            _deny('BIBLE_CONFLICT')
        if bible['semantic_review_ref'] != details['independent_review_ref']:
            _deny('REVIEW_CONFLICT')
        for evidence_ref in bible['mechanical_check_refs']:
            blob = self.proofs.read_record(evidence_ref, 'BibleTraceEvidence')
            if not blob: _deny()
        review_id = details['independent_review_ref']
        def review_read(tx):
            return tx.fetch_one('SELECT a.*,v.verdict_id,v.verdict,v.evidence_digest,v.reviewer_principal_id AS verdict_reviewer FROM v1_review_assignments a JOIN v1_review_verdicts v ON v.review_id=a.review_id WHERE a.review_id=? AND a.feature_id=?', (review_id,feature_id))
        review = self.store.read(review_read)
        if not review or review['state'] != 'ACCEPT' or review['verdict'] != 'ACCEPT' or review['subject_digest'] != manifest_sha or review['verdict_reviewer'] != review['reviewer_principal_id']:
            _deny('REVIEW_CONFLICT', 'BLOCKED_LOCAL')
        checked_review_bytes = self.proofs.read_record(review['verdict_id'], 'IndependentReviewVerdict')
        if _sha_bytes(checked_review_bytes) != review['evidence_digest']:
            _deny()
        checked_review = _unpack_json(checked_review_bytes)
        if (checked_review.get('checked_commit') != commit or
            checked_review.get('review_id') != review_id or
            checked_review.get('subject_digest') != manifest_sha or
            checked_review.get('reviewer_principal_id') != review['reviewer_principal_id'] or
            checked_review.get('verdict') != 'ACCEPT' or checked_review.get('findings') != []):
            _deny('REVIEW_CONFLICT')
        required_clauses = {c for d in contract['deliverables'] for c in d.get('requirement_refs', [])}
        if not required_clauses <= set(unique_refs(checked_review.get('checked_clause_refs'))):
            _deny('REVIEW_CONFLICT')
        lineage = self.proofs.author_lineage(review['task_id'], review['subject_digest'])
        if lineage.get('simulated') is not False or digest({k:lineage[k] for k in ('principals','roots')}) != review['author_lineage_digest'] or review['reviewer_principal_id'] in lineage['principals']:
            _deny('REVIEW_CONFLICT')
        bound_digest = self._adapter_digest(run, commit, details['project_adapter_ref'])
        runner = self.proofs.trusted_runner(run['project_id'], feature_id, commit, details['project_adapter_ref'])
        if not isinstance(runner, Mapping) or runner.get('trusted') is not True or runner.get('simulated') is not False:
            _deny('TEST_NOT_RUN', 'NOT_VERIFIED')
        adapter_digest = sha(runner.get('adapter_digest'))
        if adapter_digest != bound_digest: _deny('EVIDENCE_INVALID')
        tests = self._test_rows(feature_id, commit)
        if not self._required_test_ids(contract) <= tests.keys():
            _deny('TEST_NOT_RUN', 'NOT_VERIFIED')
        checked_tests = []
        for test_id in sorted(tests):
            row = tests[test_id]
            test = _unpack_json(self.proofs.load_test_evidence(feature_id, commit, test_id))
            verified_test, verified_principal = self._test(test,commit,adapter_digest)
            for sql_key, test_key in (('test_id','test_id'),('commit_sha','commit'),
                                      ('exit_code','exit_code'),('log_digest','log_digest'),
                                      ('adapter_digest','adapter_digest'),('toolchain_digest','toolchain_digest'),
                                      ('input_digest','input_digest'),('observed_at_us','observed_at_us')):
                if row[sql_key] != verified_test[test_key]: _deny('EVIDENCE_INVALID')
            if row['runner_principal_id'] != verified_principal: _deny('EVIDENCE_INVALID')
            checked_tests.append(verified_test)
        self._source(run, contract, commit)
        receipt = {'receipt_id':'qa-ready-pending','project_id':run['project_id'],'feature_id':feature_id,
                   'bible_pair_digest':run['bible_pair_digest'],'feature_contract_digest':contract_digest,
                   'source_commit':commit,'tested_commit':commit,'artifact_manifest_digest':manifest_sha,
                   'bible_compliance_receipt_ref':details['bible_compliance_ref'],'test_evidence':checked_tests,
                   'blocking_issues':0,'independent_review_ref':review_id,
                   'trusted_quality_signer':ref(runner['quality_signer']), 'status':'QA_READY'}
        receipt['receipt_id'] = 'qa-ready-' + digest({k:v for k,v in receipt.items() if k != 'receipt_id'})
        receipt_id = receipt['receipt_id']
        quality_digest = digest(receipt)
        # Write the complete immutable receipt bytes and independently read them back.
        # CAB-E's SQL table stores only its checksum, not the original evidence.
        self.proofs.persist_receipt(receipt_id, 'QAReadyReceipt', canonical(receipt))
        if _sha_bytes(self.proofs.read_record(receipt_id, 'QAReadyReceipt')) != hashlib.sha256(canonical(receipt)).hexdigest():
            _deny('EVIDENCE_INVALID')
        def change(tx):
            current = tx.fetch_one('SELECT active_contract_digest,bible_pair_digest,graph_revision FROM v1_feature_runs WHERE feature_id=?', (feature_id,))
            if not current or current['active_contract_digest'] != contract_digest or current['bible_pair_digest'] != run['bible_pair_digest'] or current['graph_revision'] != run['graph_revision']:
                _deny('REVISION_CONFLICT', 'CONFLICT')
            prev = tx.fetch_one('SELECT * FROM v1_qa_ready_receipts WHERE feature_id=? AND commit_sha=?', (feature_id, commit))
            if prev:
                # Old QA_READY is never silently reused after new bible/manifest/test evidence.
                if (prev['contract_digest'] != contract_digest or prev['manifest_digest'] != manifest_sha or
                    prev['quality_digest'] != quality_digest):
                    _deny('EVIDENCE_INVALID', 'CONFLICT')
                return prev['receipt_id']
            tx.execute('INSERT INTO v1_qa_ready_receipts(feature_id,receipt_id,commit_sha,contract_digest,manifest_digest,quality_digest,quality_signer,created_at_us) VALUES(?,?,?,?,?,?,?,?)',
                       (feature_id,receipt_id,commit,contract_digest,manifest_sha,quality_digest,receipt['trusted_quality_signer'],self.clock_us()))
            return receipt_id
        persisted = self.store.transact(change)
        receipt['receipt_id'] = persisted
        return self._result('OK','OK',receipt,persisted,(details['manifest_ref'],details['bible_compliance_ref'],review_id))

    def record_independent_qa(self, feature_id: str, source_commit: str, qa_verdict: Mapping,
                              reviewer_identity: Any) -> Any:
        return self._guard(lambda: self._record_independent_qa(feature_id, source_commit, qa_verdict, reviewer_identity))

    def _record_independent_qa(self, feature_id, commit, qa_verdict, identity):
        sha(commit, commit=True)
        if not isinstance(qa_verdict, Mapping) or set(qa_verdict) != {'qa_ready_receipt_id','evidence_ref','verdict'}:
            _deny('INVALID_INPUT')
        ref(qa_verdict['qa_ready_receipt_id']); ref(qa_verdict['evidence_ref'])
        verdict = qa_verdict['verdict']
        if verdict not in ('PASS','FAIL','INCONCLUSIVE'): _deny('INVALID_INPUT')
        run, contract = self._context(feature_id)
        self._source(run,contract,commit)
        ready = self.store.read(lambda tx: tx.fetch_one('SELECT * FROM v1_qa_ready_receipts WHERE feature_id=? AND commit_sha=? AND receipt_id=?', (feature_id,commit,qa_verdict['qa_ready_receipt_id'])))
        if not ready or ready['contract_digest'] != run['active_contract_digest']:
            _deny('RELEASE_BLOCKED', 'BLOCKED_LOCAL')
        # This persisted row is only an index. The full immutable QA_READY
        # record must still exist and match the original persisted digest.
        ready_bytes = self.proofs.read_record(ready['receipt_id'], 'QAReadyReceipt')
        if _sha_bytes(ready_bytes) != ready['quality_digest']:
            _deny()
        ready_body = _unpack_json(ready_bytes)
        if (ready_body.get('status') != 'QA_READY' or ready_body.get('project_id') != run['project_id'] or
            ready_body.get('feature_id') != feature_id or ready_body.get('source_commit') != commit or
            ready_body.get('tested_commit') != commit or ready_body.get('feature_contract_digest') != run['active_contract_digest'] or
            ready_body.get('artifact_manifest_digest') != ready['manifest_digest'] or
            ready_body.get('trusted_quality_signer') != ready['quality_signer']):
            _deny()
        lineage = self.proofs.author_lineage(feature_id, commit)
        if not isinstance(lineage, Mapping) or lineage.get('simulated') is not False:
            _deny()
        try: principal = reviewer_principal(identity, {k:lineage[k] for k in ('principals','roots')})
        except ValueError: _deny('REVIEW_CONFLICT')
        if principal == ready['quality_signer']:
            _deny('REVIEW_CONFLICT')
        checked = _unpack_json(self.proofs.read_record(qa_verdict['evidence_ref'],'IndependentQA'))
        fixed = {'feature_id':feature_id,'commit':commit,'reviewer':principal,
                 'verdict':verdict,'qa_ready_receipt_id':qa_verdict['qa_ready_receipt_id']}
        if (set(checked) != {*fixed,'failed_task_ids','finding_refs'} or
            any(checked.get(k) != v for k,v in fixed.items())):
            _deny()
        tasks = unique_refs(checked['failed_task_ids'])
        findings = unique_refs(checked['finding_refs'])
        if verdict == 'PASS':
            if tasks or findings: _deny('EVIDENCE_INVALID')
        else:
            if not tasks or not findings: _deny('EVIDENCE_INVALID')
            for task in tasks:
                task_row = self.store.read(lambda tx: tx.fetch_one('SELECT feature_id,graph_revision FROM v1_tasks WHERE task_id=?', (task,)))
                if not task_row or task_row['feature_id'] != feature_id or task_row['graph_revision'] != run['graph_revision']:
                    _deny('WRONG_FEATURE')
            for finding in findings:
                if not self.proofs.read_record(finding,'QAFinding'):
                    _deny('EVIDENCE_INVALID')
        evidence_sha = _sha_bytes(self.proofs.read_record(qa_verdict['evidence_ref'],'IndependentQA'))
        qa_id = 'qa-' + uuid.uuid4().hex
        def change(tx):
            existing = tx.fetch_one('SELECT qa_id,verdict,evidence_digest,reviewer_principal_id FROM v1_independent_qa WHERE feature_id=? AND commit_sha=?', (feature_id,commit))
            if existing:
                if existing['verdict'] != verdict or existing['evidence_digest'] != evidence_sha or existing['reviewer_principal_id'] != principal:
                    _deny('REVIEW_CONFLICT','CONFLICT')
                return existing['qa_id']
            current = tx.fetch_one('SELECT active_contract_digest,graph_revision FROM v1_feature_runs WHERE feature_id=?', (feature_id,))
            if not current or current['active_contract_digest'] != run['active_contract_digest'] or current['graph_revision'] != run['graph_revision']:
                _deny('REVISION_CONFLICT','CONFLICT')
            tx.execute('INSERT INTO v1_independent_qa(feature_id,qa_id,commit_sha,reviewer_principal_id,verdict,evidence_digest,recorded_at_us) VALUES(?,?,?,?,?,?,?)',
                       (feature_id,qa_id,commit,principal,verdict,evidence_sha,self.clock_us()))
            return qa_id
        persisted = self.store.transact(change)
        if verdict != 'PASS':
            # WorkGraphPort is the *only* graph writer. Send a verified,
            # feature/commit-scoped failure; never mutate CAB-C tables here.
            failed = {'qa_id':persisted,'feature_id':feature_id,'source_commit':commit,
                      'qa_ready_receipt_id':qa_verdict['qa_ready_receipt_id'],
                      'verdict':verdict,'evidence_ref':qa_verdict['evidence_ref'],
                      'failed_task_ids':list(tasks),'finding_refs':list(findings),
                      'evidence_digest':evidence_sha}
            rework = self.workgraph.create_rework(feature_id,failed,run['graph_revision'])
            if getattr(rework,'status',None) not in ('OK','ACCEPTED'):
                return self._result('BLOCKED_LOCAL','LOCAL_RECOVERY_REQUIRED',{'qa_id':persisted,'verdict':verdict},persisted,(qa_verdict['evidence_ref'],))
        return self._result('OK','OK',{'qa_id':persisted,'verdict':verdict,'source_commit':commit},persisted,(qa_verdict['evidence_ref'],))


def build_quality_port(deps: Mapping[str, Any]) -> QualityService:
    """P2 composition seam; owns no connection, schema install, or identity issuing."""
    return QualityService(**deps)
