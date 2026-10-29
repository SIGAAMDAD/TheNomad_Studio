"""R9 sole FeatureRun lifecycle writer, composed via CAB-J-limited transactions.

The injected transaction capability MUST be restricted to CAB-J tables by P2.
Calls to other domain ports are made *outside* the write transaction. The
R9 outbox publisher is injected by P2, whose writer owns v1_outbox; it must
insert atomically using the same transaction and never invoke a provider there.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, Callable, Protocol

from .wire import Result, Record, ok, fail, digest, valid_id, valid_sha, valid_digest

# Paths are deliberately declared, not inferred from the legacy Component API.
# Higher states require independent domain readback and cannot be asserted by the caller.
_TRANSITIONS = {
    'RECEIVED': {'CONTEXT_PINNED', 'BLOCKED_LOCAL', 'PAUSED', 'CANCELLED'},
    'CONTEXT_PINNED': {'CONTRACT_READY', 'BLOCKED_LOCAL', 'PAUSED', 'CANCELLED'},
    'CONTRACT_READY': {'PLANNED', 'REWORK_REQUIRED', 'BLOCKED_LOCAL', 'PAUSED', 'CANCELLED'},
    'PLANNED': {'IN_PRODUCTION', 'REWORK_REQUIRED', 'BLOCKED_LOCAL', 'HOST_UNSUPPORTED', 'PAUSED', 'CANCELLED'},
    'IN_PRODUCTION': {'INTEGRATING', 'REWORK_REQUIRED', 'BLOCKED_LOCAL', 'HOST_UNSUPPORTED', 'EFFECT_UNKNOWN', 'PAUSED', 'CANCELLED'},
    'INTEGRATING': {'VERIFYING', 'REWORK_REQUIRED', 'BLOCKED_LOCAL', 'EFFECT_UNKNOWN', 'PAUSED', 'CANCELLED'},
    'VERIFYING': {'QA_READY', 'REWORK_REQUIRED', 'BLOCKED_LOCAL', 'EFFECT_UNKNOWN', 'PAUSED', 'CANCELLED'},
    'QA_READY': {'QA_IN_PROGRESS', 'REWORK_REQUIRED', 'PAUSED', 'CANCELLED'},
    'QA_IN_PROGRESS': {'QA_PASSED', 'REWORK_REQUIRED', 'BLOCKED_LOCAL', 'PAUSED', 'CANCELLED'},
    'QA_PASSED': {'MERGE_REQUESTED', 'REWORK_REQUIRED', 'PAUSED'},
    'MERGE_REQUESTED': {'MERGED', 'BLOCKED_LOCAL', 'EFFECT_UNKNOWN', 'PAUSED'},
    'REWORK_REQUIRED': {'PLANNED', 'BLOCKED_LOCAL', 'PAUSED', 'CANCELLED'},
    'PAUSED': {'RECEIVED', 'CONTEXT_PINNED', 'CONTRACT_READY', 'PLANNED', 'IN_PRODUCTION', 'INTEGRATING', 'VERIFYING', 'QA_READY', 'QA_IN_PROGRESS', 'QA_PASSED', 'REWORK_REQUIRED', 'BLOCKED_LOCAL', 'CANCELLED'},
    'BLOCKED_LOCAL': {'RECEIVED', 'CONTEXT_PINNED', 'CONTRACT_READY', 'PLANNED', 'IN_PRODUCTION', 'INTEGRATING', 'VERIFYING', 'REWORK_REQUIRED', 'PAUSED', 'CANCELLED'},
    'HOST_UNSUPPORTED': {'PLANNED', 'IN_PRODUCTION', 'PAUSED', 'CANCELLED'},
    'EFFECT_UNKNOWN': {'IN_PRODUCTION', 'INTEGRATING', 'VERIFYING', 'MERGE_REQUESTED', 'PAUSED', 'CANCELLED'},
    'MERGED': set(), 'CANCELLED': set(),
}
# State requires an actual cross-domain observation, not a self-authored note.
_GATES = {
    'CONTEXT_PINNED': 'context', 'CONTRACT_READY': 'contract',
    'PLANNED': 'graph', 'IN_PRODUCTION': 'scheduler',
    'INTEGRATING': 'repository', 'VERIFYING': 'quality',
    'QA_READY': 'quality', 'QA_IN_PROGRESS': 'quality',
    'QA_PASSED': 'quality', 'MERGE_REQUESTED': 'repository', 'MERGED': 'repository',
}


class CABJTransaction(Protocol):
    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> Any: ...


class CABJWriter(Protocol):
    def transaction(self) -> Any: ...  # P2 supplies context manager with CAB-J table fence


class OutboxPublisher(Protocol):
    def append(self, tx: CABJTransaction, message: Record) -> None: ...


class DirectorService:
    """Implements exactly the frozen DirectorPort signatures.

    `observations` is a P2-injected mapping of trusted readback validators.
    Validators MUST verify evidence against their authority stores or provider
    readbacks, and return Result(status='OK', code='OK', receipt_ref=...). For
    QA_READY the additional CAB-E authoritative SQL receipt is mandatory.
    """

    def __init__(self, writer: CABJWriter, *, outbox: OutboxPublisher,
                 observations: Mapping[str, Callable[[str, Record, Record], Result]],
                 clock_us: Callable[[], int], owner_lookup: Callable[[str], str | None] | None = None):
        self.writer = writer
        self.outbox = outbox
        self.observations = dict(observations)
        self.clock_us = clock_us
        self.owner_lookup = owner_lookup

    @staticmethod
    def _read(tx: CABJTransaction, feature_id: str) -> dict[str, Any] | None:
        cursor = tx.execute('SELECT project_id,feature_id,brief_id,active_contract_digest,bible_pair_digest,phase,revision,graph_revision,qa_branch_ref,updated_at_us FROM v1_feature_runs WHERE feature_id=?', (feature_id,))
        row = cursor.fetchone()
        return dict(zip([c[0] for c in cursor.description], row)) if row else None

    @staticmethod
    def _public(row: Record) -> dict[str, Any]:
        # Frozen FeatureRun definition: the SQL physical 'phase' and
        # 'active_contract_digest' columns must not leak as wire aliases.
        return {
            'project_id': row['project_id'], 'feature_id': row['feature_id'],
            'brief_id': row['brief_id'],
            'feature_contract_digest': row['active_contract_digest'],
            'bible_pair_digest': row['bible_pair_digest'],
            'graph_revision': row['graph_revision'], 'revision': row['revision'],
            'status': row['phase'],
        }

    def open_run(self, brief: Record, feature_contract: Record) -> Result:
        if not isinstance(brief, Mapping) or not isinstance(feature_contract, Mapping):
            return fail('REJECTED', 'INVALID_INPUT')
        for key in ('project_id', 'feature_id', 'brief_id'):
            if not valid_id(brief.get(key)) or brief.get(key) != feature_contract.get(key):
                return fail('REJECTED', 'INVALID_INPUT')
        pair, contract = feature_contract.get('bible_pair_digest'), feature_contract.get('contract_digest')
        if not valid_digest(pair) or not valid_digest(contract) or not valid_digest(brief.get('original_digest')) or feature_contract.get('original_brief_digest') != brief['original_digest']:
            return fail('REJECTED', 'INVALID_INPUT')
        try:
            with self.writer.transaction() as tx:
                stored = tx.execute('SELECT original_digest FROM v1_briefs WHERE project_id=? AND brief_id=? AND feature_id=?',
                                    (brief['project_id'], brief['brief_id'], brief['feature_id'])).fetchone()
                frozen = tx.execute('SELECT bible_pair_digest,original_brief_digest FROM v1_feature_contracts WHERE project_id=? AND feature_id=? AND contract_digest=?',
                                    (brief['project_id'], brief['feature_id'], contract)).fetchone()
                if ((tuple(stored) if stored is not None else None) != (brief['original_digest'],)
                    or (tuple(frozen) if frozen is not None else None) != (pair, brief['original_digest'])):
                    return fail('CONFLICT', 'SOURCE_CONFLICT')
                prev = self._read(tx, brief['feature_id'])
                if prev:
                    if prev['project_id'] == brief['project_id'] and prev['active_contract_digest'] == contract and prev['brief_id'] == brief['brief_id'] and prev['bible_pair_digest'] == pair:
                        return ok(self._public(prev))
                    return fail('CONFLICT', 'REVISION_CONFLICT')
                now = self.clock_us()
                if type(now) is not int or now < 1:
                    return fail('REJECTED', 'INVALID_INPUT')
                tx.execute('INSERT INTO v1_feature_runs(project_id,feature_id,brief_id,active_contract_digest,bible_pair_digest,phase,revision,graph_revision,updated_at_us) VALUES (?,?,?,?,?,?,?,?,?)',
                           (brief['project_id'], brief['feature_id'], brief['brief_id'], contract, pair, 'RECEIVED', 1, 0, now))
                self._event(tx, brief['project_id'], brief['feature_id'], 'RECEIVED', 1, 'open_run')
                return ok(self._public(self._read(tx, brief['feature_id'])))
        except Exception as exc:
            if _database_conflict(exc):
                return fail('CONFLICT', 'REVISION_CONFLICT')
            raise

    def _event(self, tx: CABJTransaction, project: str, feature: str, state: str, revision: int, reason: str) -> None:
        self.outbox.append(tx, {
            'project_id': project, 'feature_id': feature, 'domain': 'production.director',
            'event_type': 'FEATURE_RUN_TRANSITION', 'state': state, 'revision': revision,
            'reason': reason, 'idempotency_key': f'director:{feature}:{revision}',
        })

    def advance_run(self, feature_id: str, expected_revision: int, requested_state: str,
                    evidence_receipt: Record) -> Result:
        if not valid_id(feature_id) or type(expected_revision) is not int or expected_revision < 1 or requested_state not in _TRANSITIONS or not isinstance(evidence_receipt, Mapping):
            return fail('REJECTED', 'INVALID_INPUT')
        # Readback is performed outside the write transaction. An intervening
        # revision/contract mutation is fenced by CAS in the subsequent transaction.
        with self.writer.transaction() as tx:
            snapshot = self._read(tx, feature_id)
        if not snapshot:
            return fail('NOT_FOUND', 'NOT_FOUND')
        if snapshot['revision'] != expected_revision:
            return fail('CONFLICT', 'REVISION_CONFLICT')
        old = snapshot['phase']
        if requested_state not in _TRANSITIONS.get(old, set()):
            return fail('REJECTED', 'INVALID_INPUT')
        reason = evidence_receipt.get('reason')
        if requested_state in {'PAUSED', 'CANCELLED', 'BLOCKED_LOCAL', 'HOST_UNSUPPORTED', 'EFFECT_UNKNOWN', 'REWORK_REQUIRED'}:
            if not isinstance(reason, str) or not reason or len(reason) > 512:
                return fail('REJECTED', 'EVIDENCE_INVALID')
        gate = None if old == 'PAUSED' else _GATES.get(requested_state)
        if gate:
            observe = self.observations.get(gate)
            if observe is None:
                return fail('BLOCKED_LOCAL', 'PROVIDER_NOT_VERIFIED')
            try:
                observation = observe(feature_id, snapshot, evidence_receipt)
            except (OSError, TimeoutError):
                return fail('RETRYABLE', 'LOCAL_RECOVERY_REQUIRED')
            if not isinstance(observation, Result):
                return fail('REJECTED', 'EVIDENCE_INVALID')
            if evidence_receipt.get('receipt_ref') != observation.receipt_ref:
                return fail('BLOCKED_LOCAL', 'EVIDENCE_INVALID')
            if observation.status not in {'OK', 'ACCEPTED'} or observation.code != 'OK' or not observation.receipt_ref or not valid_id(observation.receipt_ref):
                return fail('BLOCKED_LOCAL', 'EVIDENCE_INVALID')
            # A real cross-service receipt must be immutable and scoped to the read state.
            if not isinstance(observation.record, Mapping) or observation.record.get('project_id') != snapshot['project_id'] or observation.record.get('feature_id') != feature_id or observation.record.get('bible_pair_digest') != snapshot['bible_pair_digest'] or observation.record.get('feature_contract_digest') != snapshot['active_contract_digest']:
                return fail('BLOCKED_LOCAL', 'EVIDENCE_INVALID')
        else:
            observation = None
        with self.writer.transaction() as tx:
            live = self._read(tx, feature_id)
            if not live:
                return fail('NOT_FOUND', 'NOT_FOUND')
            if live['revision'] != expected_revision or live['phase'] != old or live['active_contract_digest'] != snapshot['active_contract_digest'] or live['bible_pair_digest'] != snapshot['bible_pair_digest']:
                return fail('CONFLICT', 'REVISION_CONFLICT')
            if requested_state == 'INTEGRATING':
                sha = evidence_receipt.get('source_commit')
                if not valid_sha(sha) or observation.record.get('source_commit') != sha:
                    return fail('BLOCKED_LOCAL', 'SOURCE_CONFLICT')
            if requested_state == 'VERIFYING':
                sha = evidence_receipt.get('source_commit')
                if not valid_sha(sha) or sha != live['qa_branch_ref'] or observation.record.get('source_commit') != sha:
                    return fail('BLOCKED_LOCAL', 'SOURCE_CONFLICT')
            if requested_state == 'QA_READY' and old != 'PAUSED':
                if not self._check_qa(tx, live, observation, evidence_receipt):
                    return fail('BLOCKED_LOCAL', 'EVIDENCE_INVALID')
            if requested_state == 'QA_PASSED' and old != 'PAUSED':
                sha = live['qa_branch_ref']
                if not valid_sha(sha) or observation.record.get('source_commit') != sha:
                    return fail('BLOCKED_LOCAL', 'EVIDENCE_INVALID')
                if not tx.execute("SELECT 1 FROM v1_independent_qa WHERE feature_id=? AND commit_sha=? AND verdict='PASS'", (feature_id, sha)).fetchone():
                    return fail('BLOCKED_LOCAL', 'EVIDENCE_INVALID')
            if requested_state == 'MERGE_REQUESTED':
                sha = live['qa_branch_ref']
                approval = evidence_receipt.get('owner_decision_ref')
                subject = digest({'project_id':live['project_id'], 'feature_id':feature_id, 'source_commit':sha})
                owner = tx.execute('SELECT owner_id FROM v1_projects WHERE project_id=?', (live['project_id'],)).fetchone()
                if not valid_id(approval) or not owner or not tx.execute(
                    "SELECT 1 FROM v1_owner_decisions WHERE project_id=? AND decision_id=? AND owner_id=? AND action='PROTECTED_MAINLINE_MERGE' AND subject_digest=? AND expires_at_us>=? AND consumed_at_us IS NULL",
                    (live['project_id'], approval, owner[0], subject, self.clock_us())).fetchone():
                    return fail('BLOCKED_LOCAL', 'OWNER_ACTION_REQUIRED')
            if old == 'PAUSED' and requested_state != 'CANCELLED':
                marker = tx.execute('SELECT payload_json FROM v1_dashboard_projections WHERE project_id=? AND feature_id=?', (live['project_id'], feature_id)).fetchone()
                if not marker or json.loads(marker[0]).get('resume_state') != requested_state:
                    return fail('CONFLICT', 'REVISION_CONFLICT')
            graph_revision = live['graph_revision']
            if requested_state == 'PLANNED':
                graph_revision = observation.record.get('revision')
                if type(graph_revision) is not int or graph_revision < 1:
                    return fail('BLOCKED_LOCAL', 'EVIDENCE_INVALID')
                graph = tx.execute('SELECT contract_digest,bible_pair_digest FROM v1_graph_revisions WHERE feature_id=? AND graph_revision=?',
                                   (feature_id, graph_revision)).fetchone()
                if graph != (live['active_contract_digest'], live['bible_pair_digest']):
                    return fail('BLOCKED_LOCAL', 'EVIDENCE_INVALID')
            # B owns the revised contract and C owns the rework graph.
            # These records are read back from frozen writer-owned tables. The
            # run's contract pointer changes atomically with its phase CAS.
            active_contract = live['active_contract_digest']
            if requested_state == 'REWORK_REQUIRED' and evidence_receipt.get('new_contract_digest') is not None:
                requested_contract = evidence_receipt['new_contract_digest']
                requested_graph = evidence_receipt.get('new_graph_revision')
                if not valid_digest(requested_contract) or type(requested_graph) is not int or requested_graph <= live['graph_revision']:
                    return fail('REJECTED', 'INVALID_INPUT')
                authorized_contract = tx.execute('SELECT 1 FROM v1_feature_contracts WHERE project_id=? AND feature_id=? AND contract_digest=? AND bible_pair_digest=?',
                                                  (live['project_id'], feature_id, requested_contract, live['bible_pair_digest'])).fetchone()
                authorized_graph = tx.execute('SELECT 1 FROM v1_graph_revisions WHERE feature_id=? AND graph_revision=? AND contract_digest=? AND bible_pair_digest=?',
                                               (feature_id, requested_graph, requested_contract, live['bible_pair_digest'])).fetchone()
                if not authorized_contract or not authorized_graph or requested_contract == live['active_contract_digest']:
                    return fail('BLOCKED_LOCAL', 'EVIDENCE_INVALID')
                active_contract = requested_contract
            if old == 'REWORK_REQUIRED' and requested_state == 'PLANNED' and graph_revision <= live['graph_revision']:
                return fail('CONFLICT', 'REVISION_CONFLICT')
            branch = live['qa_branch_ref']
            if active_contract != live['active_contract_digest'] or requested_state == 'REWORK_REQUIRED':
                branch = None
            if requested_state == 'INTEGRATING':
                branch = evidence_receipt['source_commit']
            if requested_state == 'QA_READY' and observation is not None:
                branch = observation.record['source_commit']
            updated = tx.execute('UPDATE v1_feature_runs SET phase=?, revision=revision+1, graph_revision=?, active_contract_digest=?, qa_branch_ref=?, updated_at_us=? WHERE feature_id=? AND revision=? AND phase=?',
                                 (requested_state, graph_revision, active_contract, branch, self.clock_us(), feature_id, expected_revision, old))
            if updated.rowcount != 1:
                return fail('CONFLICT', 'REVISION_CONFLICT')
            if requested_state == 'PAUSED':
                projection = {'resume_state':old, 'paused_at_revision':expected_revision+1}
                tx.execute('INSERT INTO v1_dashboard_projections(project_id,feature_id,projection_digest,payload_json,updated_at_us) VALUES(?,?,?,?,?) ON CONFLICT(project_id,feature_id) DO UPDATE SET projection_digest=excluded.projection_digest,payload_json=excluded.payload_json,updated_at_us=excluded.updated_at_us',
                           (live['project_id'], feature_id, digest(projection), json.dumps(projection, sort_keys=True), self.clock_us()))
            self._event(tx, live['project_id'], feature_id, requested_state, expected_revision+1, reason or (observation.receipt_ref if observation else 'transition'))
            if observation:
                ref = observation.receipt_ref
                tx.execute('INSERT INTO v1_operator_receipts(feature_id,receipt_id,phase,subject_digest,evidence_ref,issued_at_us) VALUES (?,?,?,?,?,?)',
                           (feature_id, f'director:{feature_id}:{expected_revision + 1}', requested_state, digest(observation.record), ref, self.clock_us()))
            return ok(self._public(self._read(tx, feature_id)))

    @staticmethod
    def _check_qa(tx: CABJTransaction, live: Record, observation: Result, receipt: Record) -> bool:
        data = observation.record
        commit = data.get('source_commit')
        if not valid_sha(commit) or data.get('tested_commit') != commit or receipt.get('source_commit') != commit or live['qa_branch_ref'] != commit:
            return False
        ref = observation.receipt_ref
        stored = tx.execute('SELECT feature_id,commit_sha,contract_digest,manifest_digest,quality_digest,quality_signer FROM v1_qa_ready_receipts WHERE receipt_id=? AND feature_id=?',
                            (ref, live['feature_id'])).fetchone()
        if not stored or stored[0:3] != (live['feature_id'], commit, live['active_contract_digest']):
            return False
        if not valid_digest(stored[3]) or not valid_digest(stored[4]) or not valid_id(stored[5]):
            return False
        if data.get('artifact_manifest_digest') != stored[3] or data.get('trusted_quality_signer') != stored[5]:
            return False
        tests = tx.execute('SELECT COUNT(*),COALESCE(SUM(CASE WHEN exit_code<>0 THEN 1 ELSE 0 END),0) FROM v1_test_executions WHERE feature_id=? AND commit_sha=?',
                           (live['feature_id'], commit)).fetchone()
        if tests[0] < 1 or tests[1] != 0:
            return False
        return True

    def resume_target(self, feature_id: str) -> str | None:
        if not valid_id(feature_id):
            return None
        with self.writer.transaction() as tx:
            live = self._read(tx, feature_id)
            if not live or live['phase'] != 'PAUSED':
                return None
            row = tx.execute('SELECT payload_json FROM v1_dashboard_projections WHERE project_id=? AND feature_id=?',
                             (live['project_id'], feature_id)).fetchone()
            if row is None:
                return None
            data = json.loads(row[0])
            if data.get('paused_at_revision') != live['revision']:
                return None
            state = data.get('resume_state')
            return state if state in _TRANSITIONS['PAUSED'] and state != 'CANCELLED' else None

    def reconcile_run(self, feature_id: str, observed_state: Record) -> Result:
        if not valid_id(feature_id) or not isinstance(observed_state, Mapping):
            return fail('REJECTED', 'INVALID_INPUT')
        with self.writer.transaction() as tx:
            live = self._read(tx, feature_id)
        if live is None:
            return fail('NOT_FOUND', 'NOT_FOUND')
        # On restart, only replay the command whose observed revision still matches;
        # never infer completion from an external provider failure or chat text.
        if observed_state.get('expected_revision') != live['revision'] or not isinstance(observed_state.get('state'), str):
            return fail('CONFLICT', 'REVISION_CONFLICT')
        state = observed_state['state']
        if state == live['phase']:
            return ok(self._public(live))
        return self.advance_run(feature_id, live['revision'], state, observed_state.get('evidence', {}))

    def get_feature_status(self, feature_id: str, owner_principal_id: str) -> Result:
        if not valid_id(feature_id) or not valid_id(owner_principal_id):
            return fail('REJECTED', 'INVALID_INPUT')
        with self.writer.transaction() as tx:
            live = self._read(tx, feature_id)
            if live is None:
                return fail('NOT_FOUND', 'NOT_FOUND')
            # Query the authoritative project, never accept an owner string from a record.
            project = tx.execute('SELECT owner_id FROM v1_projects WHERE project_id=?', (live['project_id'],)).fetchone()
            if project is None or project[0] != owner_principal_id:
                return fail('REJECTED', 'PERMISSION_DENIED')
            return ok(self._public(live))


def _database_conflict(exc: Exception) -> bool:
    import sqlite3
    return isinstance(exc, sqlite3.IntegrityError)
