"""Drive IN_PRODUCTION -> QA_READY using only frozen I/E adapter and QA ports.

P2 supplies authoritative read-only context from C/H/D/B and CAB-I source
fences. This controller does not assert jobs complete, invent a commit or
replace CAB-E's independent quality decision. All provider calls occur outside
CAB-J transactions; uncertain provider effects require I readback.
"""
from __future__ import annotations
from collections.abc import Mapping
from typing import Any
from .wire import Result, fail, valid_digest, valid_sha, valid_id


class PostProductionPipeline:
    def __init__(self, *, director: Any, artifacts: Any, repository: Any,
                 adapter: Any, quality: Any, trusted_context: Any):
        self.director = director
        self.artifacts = artifacts
        self.repo = repository
        self.adapter = adapter
        self.quality = quality
        self.context = trusted_context  # P2 reads authoritative state, not agent prose

    @staticmethod
    def _require(response: Any, feature: str, project: str, contract: str, bible: str) -> bool:
        return (isinstance(response, Result) and response.status == 'OK' and response.code == 'OK'
                and valid_id(response.receipt_ref)
                and isinstance(response.record, Mapping)
                and response.record.get('project_id') == project
                and response.record.get('feature_id') == feature
                and response.record.get('feature_contract_digest') == contract
                and response.record.get('bible_pair_digest') == bible
                and response.record.get('simulated') is not True)

    def _context(self, feature: str, stage: str, status: Mapping[str, Any]) -> Mapping[str, Any] | None:
        # The trusted context reader is a P2 composition adapter, not a new
        # public CAB API. It verifies C/E/I persisted records at this stage.
        result = self.context.observe(feature, stage, status)
        if not self._require(result, feature, status['project_id'],
                             status['feature_contract_digest'], status['bible_pair_digest']):
            return None
        return result.record

    def tick(self, owner_principal_id: str, feature_id: str) -> Result:
        status = self.director.get_feature_status(feature_id, owner_principal_id)
        if status.status != 'OK':
            return status
        state = status.record
        phase = state['status']
        if phase not in ('IN_PRODUCTION','INTEGRATING','VERIFYING'):
            return status
        project, contract, bible = state['project_id'], state['feature_contract_digest'], state['bible_pair_digest']
        trusted = self._context(feature_id, phase, state)
        if trusted is None:
            return fail('BLOCKED_LOCAL','EVIDENCE_INVALID')
        if phase == 'IN_PRODUCTION':
            if trusted.get('all_tasks_verified') is not True or trusted.get('independent_reviews_complete') is not True:
                return fail('BLOCKED_LOCAL','EVIDENCE_INVALID')
            fence = trusted.get('source_fence')
            if not fence or any(getattr(fence, k, None) != v for k,v in (('project_id',project),('feature_id',feature_id),('bible_pair_digest',bible))):
                return fail('BLOCKED_LOCAL','SOURCE_CONFLICT')
            manifests = self.artifacts.get_feature_manifest(feature_id,contract)
            if not self._require(manifests,feature_id,project,contract,bible):
                return fail('BLOCKED_LOCAL','EVIDENCE_INVALID')
            integrated = self.repo.integrate_feature(feature_id, state['graph_revision'], manifests.record, fence)
            if integrated.status == 'UNKNOWN_EXTERNAL':
                return integrated
            if not self._require(integrated,feature_id,project,contract,bible):
                return fail('BLOCKED_LOCAL','PROVIDER_NOT_VERIFIED')
            # CAB-I readback before accepting published branch bytes.
            data = integrated.record
            if not valid_sha(data.get('source_commit')) or not valid_id(data.get('effect_ref')) or not data.get('repo') or not data.get('branch'):
                return fail('BLOCKED_LOCAL','EVIDENCE_INVALID')
            readback = self.repo.readback_commit(data['effect_ref'],data['repo'],data['branch'])
            if not self._require(readback,feature_id,project,contract,bible) or readback.record.get('source_commit') != data['source_commit']:
                return fail('BLOCKED_LOCAL','PROVIDER_NOT_VERIFIED')
            transition = self.director.advance_run(feature_id,state['revision'],'INTEGRATING',{'receipt_ref':readback.receipt_ref,'source_commit':data['source_commit']})
            return self.director.get_feature_status(feature_id,owner_principal_id) if transition.status=='OK' else transition
        if phase == 'INTEGRATING':
            sha = trusted.get('source_commit')
            technical_edition = trusted.get('technical_edition_id')
            if not valid_sha(sha) or not valid_id(technical_edition) or not isinstance(trusted.get('trusted_runner'),Mapping):
                return fail('BLOCKED_LOCAL','EVIDENCE_INVALID')
            adapter = self.adapter.resolve_adapter(project, technical_edition, sha)
            if not self._require(adapter,feature_id,project,contract,bible):
                return fail('BLOCKED_LOCAL','PROVIDER_NOT_VERIFIED')
            adapter_ref = adapter.record.get('adapter_ref')
            if not valid_id(adapter_ref):
                return fail('BLOCKED_LOCAL','EVIDENCE_INVALID')
            # Execute sequentially: build and smoke must never start after a
            # failed import. Every observation is checked before the next effect.
            checks = (
                lambda: self.adapter.run_import(project,sha,adapter_ref,trusted['trusted_runner']),
                lambda: self.adapter.run_build(project,sha,adapter_ref,trusted['trusted_runner']),
                lambda: self.adapter.run_feature_smoke(feature_id,sha,trusted.get('acceptance_ids',()),adapter_ref,trusted['trusted_runner']),
            )
            for check in checks:
                test = check()
                if not self._require(test,feature_id,project,contract,bible) or test.record.get('exit_code') != 0 or test.record.get('source_commit') != sha:
                    return fail('BLOCKED_LOCAL','TEST_FAILED')
            # CAB-E independently assesses the entire set; CAB-J cannot waive it.
            manifest_ref = trusted.get('manifest_ref')
            if not valid_id(manifest_ref):
                return fail('BLOCKED_LOCAL','EVIDENCE_INVALID')
            quality = self.quality.run_production_gates(feature_id,sha,manifest_ref,adapter_ref)
            if not self._require(quality,feature_id,project,contract,bible) or quality.record.get('source_commit') != sha:
                return fail('BLOCKED_LOCAL','TEST_FAILED')
            transitioned = self.director.advance_run(feature_id,state['revision'],'VERIFYING',{'receipt_ref':quality.receipt_ref,'source_commit':sha})
            return self.director.get_feature_status(feature_id,owner_principal_id) if transitioned.status=='OK' else transitioned
        sha = trusted.get('source_commit')
        evidence = trusted.get('test_and_bible_evidence')
        if not valid_sha(sha) or not isinstance(evidence,Mapping) or not valid_id(trusted.get('quality_evidence_ref')):
            return fail('BLOCKED_LOCAL','EVIDENCE_INVALID')
        receipt = self.quality.issue_qa_ready(feature_id,sha,contract,evidence)
        if not self._require(receipt,feature_id,project,contract,bible):
            return fail('BLOCKED_LOCAL','EVIDENCE_INVALID')
        if receipt.record.get('source_commit') != sha or receipt.record.get('tested_commit') != sha:
            return fail('BLOCKED_LOCAL','SOURCE_CONFLICT')
        advanced = self.director.advance_run(feature_id,state['revision'],'QA_READY',{'receipt_ref':receipt.receipt_ref,'source_commit':sha})
        return self.director.get_feature_status(feature_id,owner_principal_id) if advanced.status=='OK' else advanced
