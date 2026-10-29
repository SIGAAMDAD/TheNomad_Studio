"""Event-driven CAB-J pipeline: accepts one brief, plans graph and queues work.

B/C/D/G/I own their own records. Production stage advancement is only through
DirectorPort. P2 delivers durable outbox entries, re-authenticates owner commands
and calls this coordinator; no unattended ChatGPT host is presumed available.
"""
from __future__ import annotations
from collections.abc import Mapping
from typing import Any
from .wire import Record, Result, ok, fail, valid_id, valid_digest


class ProductionWorkflow:
    def __init__(self, *, director: Any, brief_port: Any, bible_port: Any,
                 contract_port: Any, context_port: Any, graph_port: Any,
                 capability_port: Any, scheduling_port: Any,
                 payload_reader: Any = None):
        self.director = director
        self.briefs = brief_port
        self.bibles = bible_port
        self.contracts = contract_port
        self.context = context_port
        self.graphs = graph_port
        self.capabilities = capability_port
        self.scheduling = scheduling_port
        self.payload_reader = payload_reader

    @staticmethod
    def _extract(response: Any, *, project: str | None = None, feature: str | None = None) -> Record | None:
        if not isinstance(response, Result) or response.status not in ('OK', 'ACCEPTED') or response.code not in ('OK', 'ACCEPTED_PENDING') or not isinstance(response.record, Mapping):
            return None
        record = response.record
        if project is not None and record.get('project_id') != project:
            return None
        if feature is not None and record.get('feature_id') != feature:
            return None
        return record

    def _step(self, feature: str, owner: str, state: str, receipt: Record) -> Result:
        current = self.director.get_feature_status(feature, owner)
        if current.status != 'OK':
            return current
        return self.director.advance_run(feature, current.record['revision'], state, receipt)

    def submit(self, owner_command: Record, authenticated_owner: Result) -> Result:
        """B owns original request; D/I/B/C/G/C perform one automatic plan.

        Any transient failure stops at its exact stage; P2 durable outbox retries
        after authoritative readback, not from a conversational transcript.
        """
        if self.payload_reader is None:
            return fail('BLOCKED_LOCAL', 'LOCAL_RECOVERY_REQUIRED')
        try:
            payload = self.payload_reader.read_request(owner_command['command_id'], owner_command['request_digest'])
        except (KeyError, LookupError, OSError, ValueError, TypeError):
            return fail('REJECTED', 'EVIDENCE_INVALID')
        if not isinstance(payload, Mapping) or not isinstance(payload.get('original_text'), str):
            return fail('REJECTED', 'EVIDENCE_INVALID')
        # The authenticated digest is over the original immutable request body.
        from .wire import digest
        if digest(payload) != owner_command['request_digest']:
            return fail('REJECTED', 'EVIDENCE_INVALID')
        from hashlib import sha256
        command_id = owner_command['command_id']
        admitted = self.briefs.admit_brief({
            'brief_id': 'brief-' + sha256(command_id.encode('utf-8')).hexdigest()[:32],
            'project_id': owner_command['project_id'],
            'feature_id': owner_command.get('feature_id') or 'feature-' + sha256(command_id.encode('utf-8')).hexdigest()[:32],
            'original_text': payload['original_text'],
            'owner_command_ref': command_id,
        }, authenticated_owner)
        brief = self._extract(admitted)
        if brief is None or brief.get('owner_principal_id') != owner_command.get('owner_principal_id') or brief.get('owner_command_ref') != owner_command.get('command_id'):
            return fail('BLOCKED_LOCAL', 'EVIDENCE_INVALID')
        feature, project, owner = brief['feature_id'], brief['project_id'], brief['owner_principal_id']
        if owner_command.get('project_id') != project or (owner_command.get('feature_id') not in (None, feature)):
            return fail('REJECTED', 'WRONG_FEATURE')
        pair_result = self.bibles.snapshot_active_pair(project, {'project_id': project, 'feature_id': feature})
        pair = self._extract(pair_result, project=project)
        if not pair or not valid_digest(pair.get('pair_digest')):
            return fail('BLOCKED_LOCAL', 'STALE_BIBLE')
        bundle_result = self.context.build_bundle(project, brief['brief_id'], pair['pair_digest'], None)
        bundle = self._extract(bundle_result, project=project)
        if bundle is None:
            return fail('BLOCKED_LOCAL', 'EVIDENCE_INVALID')
        compiled = self.contracts.compile_contract(brief['brief_id'], pair, bundle)
        contract = self._extract(compiled, project=project, feature=feature)
        if contract is None or contract.get('bible_pair_digest') != pair['pair_digest'] or contract.get('brief_id') != brief['brief_id']:
            return fail('BLOCKED_LOCAL', 'EVIDENCE_INVALID')
        opened = self.director.open_run(brief, contract)
        if opened.status != 'OK':
            return opened
        current = self.director.get_feature_status(feature, owner)
        if current.status != 'OK':
            return current
        phase = current.record['status']
        if phase == 'RECEIVED':
            outcome = self._step(feature, owner, 'CONTEXT_PINNED', {'receipt_ref': bundle_result.receipt_ref})
            if outcome.status != 'OK':
                return outcome
            phase = 'CONTEXT_PINNED'
        if phase == 'CONTEXT_PINNED':
            outcome = self._step(feature, owner, 'CONTRACT_READY', {'receipt_ref': compiled.receipt_ref})
            if outcome.status != 'OK':
                return outcome
            phase = 'CONTRACT_READY'
        if phase == 'CONTRACT_READY':
            capabilities = self.capabilities.resolve_capabilities(project, contract.get('required_capabilities', []), pair['pair_digest'])
            snapshot = self._extract(capabilities, project=project)
            if snapshot is None:
                return fail('BLOCKED_LOCAL', 'CAPACITY_LIMIT')
            planned = self.graphs.plan_feature(contract, snapshot)
            graph = self._extract(planned, project=project, feature=feature)
            if graph is None or graph.get('feature_contract_digest') != contract['contract_digest'] or graph.get('bible_pair_digest') != pair['pair_digest']:
                return fail('BLOCKED_LOCAL', 'EVIDENCE_INVALID')
            decision = self.graphs.validate_graph(graph, contract['contract_digest'])
            if getattr(decision, 'status', None) != 'ALLOW' or not getattr(decision, 'allowed', False) or getattr(decision, 'code', None) != 'OK':
                return fail('BLOCKED_LOCAL', 'EVIDENCE_INVALID')
            outcome = self._step(feature, owner, 'PLANNED', {'receipt_ref': planned.receipt_ref})
            if outcome.status != 'OK':
                return outcome
            phase = 'PLANNED'
        if phase == 'PLANNED':
            graph_revision = self.director.get_feature_status(feature, owner).record['graph_revision']
            queued = self.scheduling.enqueue_ready(feature, graph_revision, {'project_id': project, 'bible_pair_digest': pair['pair_digest']})
            if self._extract(queued, project=project, feature=feature) is None:
                return fail('BLOCKED_LOCAL', 'PROVIDER_NOT_VERIFIED')
            outcome = self._step(feature, owner, 'IN_PRODUCTION', {'receipt_ref': queued.receipt_ref})
            if outcome.status != 'OK':
                return outcome
        return self.director.get_feature_status(feature, owner)

    def replan_changes(self, feature_id: str, owner: str, amendment: Record,
                       authenticated_owner: Result) -> Result:
        """Changed dependencies only; the B/C-owned stores decide actual delta."""
        current = self.director.get_feature_status(feature_id, owner)
        if current.status != 'OK':
            return current
        if current.record['status'] in ('CANCELLED', 'MERGED'):
            return fail('CONFLICT', 'REVISION_CONFLICT')
        amended = self.briefs.amend_brief(feature_id, amendment, authenticated_owner)
        change = self._extract(amended, project=current.record['project_id'], feature=feature_id)
        if change is None:
            return fail('BLOCKED_LOCAL', 'EVIDENCE_INVALID')
        # CAB-B must expose the original immutable brief from its own store.
        original = self.briefs.get_original_brief(change['brief_id'], change['project_id'])
        old_brief = self._extract(original, project=change['project_id'], feature=feature_id)
        if old_brief is None:
            return fail('BLOCKED_LOCAL', 'EVIDENCE_INVALID')
        revised = self.contracts.revise_contract(feature_id, change, old_brief, current.record['bible_pair_digest'])
        contract = self._extract(revised, project=current.record['project_id'], feature=feature_id)
        if contract is None:
            return fail('BLOCKED_LOCAL', 'EVIDENCE_INVALID')
        if contract.get('scope_digest') == change.get('old_scope_digest') and not change.get('affected_dependencies'):
            return current
        # Explicit graph rework, not re-plan of the entire project.
        rework = self.graphs.create_rework(feature_id, change, current.record['graph_revision'])
        graph = self._extract(rework, project=current.record['project_id'], feature=feature_id)
        if graph is None or graph.get('revision', 0) <= current.record['graph_revision']:
            return fail('BLOCKED_LOCAL', 'EVIDENCE_INVALID')
        outcome = self._step(feature_id, owner, 'REWORK_REQUIRED', {'reason': 'scoped-owner-change', 'affected_dependencies': change.get('affected_dependencies', []), 'new_contract_digest': contract['contract_digest'], 'new_graph_revision': graph['revision']})
        return self.director.get_feature_status(feature_id, owner) if outcome.status == 'OK' else outcome
