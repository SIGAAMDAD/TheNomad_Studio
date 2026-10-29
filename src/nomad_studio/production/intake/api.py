"""R9 immutable owner brief intake: v1_briefs is the sole owned write table."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ._support import (Result, Reject, check_id, check_text, fields, owner_principal,
                       read, read_one, reject, result, text_digest, validate_record, write)


class BriefService:
    def __init__(self, state: Any, trusted_now_us: Any, contract_port: Any = None):
        self.state = state
        self.trusted_now_us = trusted_now_us
        self.contract_port = contract_port

    def admit_brief(self, owner_command: Mapping[str, Any], owner_identity: Result) -> Result:
        try:
            command = fields(owner_command,
                             {'brief_id', 'project_id', 'feature_id', 'original_text', 'owner_command_ref'},
                             {'brief_id', 'project_id', 'feature_id', 'original_text', 'owner_command_ref'})
            for key in ('brief_id', 'project_id', 'feature_id', 'owner_command_ref'):
                check_id(command[key])
            check_text(command['original_text'])
            if not owner_identity.receipt_ref or not isinstance(owner_identity.record, Mapping):
                reject('EVIDENCE_INVALID')
            # In production the root records the command ID separately from the
            # native credential receipt; they are deliberately different names.
            if owner_identity.record.get('principal_kind') == 'OWNER':
                authenticated_command = self.state.read(lambda tx: read_one(
                    tx, 'SELECT owner_principal_id,project_id,request_digest FROM v1_owner_commands WHERE command_id=?',
                    (command['owner_command_ref'],)))
                if (not authenticated_command or authenticated_command['project_id'] != command['project_id']
                    or authenticated_command['owner_principal_id'] != owner_identity.record.get('principal_id')
                    or authenticated_command['request_digest'] != owner_identity.record.get('action_digest')):
                    reject('EVIDENCE_INVALID')
            elif owner_identity.receipt_ref != command['owner_command_ref']:
                reject('EVIDENCE_INVALID')
            if not callable(self.trusted_now_us): reject('LOCAL_RECOVERY_REQUIRED', 'BLOCKED_LOCAL')
            submitted_at = self.trusted_now_us()
            if type(submitted_at) is not int or submitted_at <= 0: reject('EVIDENCE_INVALID')
            def transaction(tx):
                project = read_one(tx, 'SELECT * FROM v1_projects WHERE project_id=?', (command['project_id'],))
                if not project: reject('NOT_FOUND', 'NOT_FOUND')
                owner_principal(owner_identity, project['owner_id'])
                if project['state'] != 'ACTIVE': reject('SCOPE_EXCEEDED')
                brief = {'brief_id': command['brief_id'], 'project_id': command['project_id'],
                         'feature_id': command['feature_id'], 'owner_principal_id': project['owner_id'],
                         'original_text': command['original_text'], 'original_digest': text_digest(command['original_text']),
                         'owner_command_ref': command['owner_command_ref'], 'submitted_at_us': submitted_at}
                existing = read_one(tx, 'SELECT * FROM v1_briefs WHERE brief_id=? OR feature_id=? OR owner_command_ref=?',
                                    (brief['brief_id'], brief['feature_id'], brief['owner_command_ref']))
                if existing:
                    if all(existing[k] == brief[k] for k in brief if k != 'submitted_at_us'):
                        return result('OK', validate_record('ProductionBrief', dict(existing)), receipt_ref=brief['owner_command_ref'])
                    reject('IDEMPOTENCY_CONFLICT', 'CONFLICT')
                tx.execute('INSERT INTO v1_briefs(project_id,brief_id,feature_id,owner_principal_id,original_text,original_digest,owner_command_ref,submitted_at_us) VALUES (?,?,?,?,?,?,?,?)',
                           tuple(brief[k] for k in ('project_id', 'brief_id', 'feature_id', 'owner_principal_id',
                                                    'original_text', 'original_digest', 'owner_command_ref', 'submitted_at_us')))
                return result('OK', validate_record('ProductionBrief', brief), receipt_ref=brief['owner_command_ref'])
            return write(self.state, transaction)
        except Reject as e:
            return e.as_result()

    def get_original_brief(self, brief_id: str, project_id: str) -> Result:
        try:
            check_id(brief_id); check_id(project_id)
            def query(tx):
                record = read_one(tx, 'SELECT * FROM v1_briefs WHERE brief_id=?', (brief_id,))
                if not record: reject('NOT_FOUND', 'NOT_FOUND')
                if record['project_id'] != project_id: reject('WRONG_PROJECT')
                if record['original_digest'] != text_digest(record['original_text']): reject('EVIDENCE_INVALID', 'BLOCKED_LOCAL')
                return result('OK', validate_record('ProductionBrief', record), receipt_ref=record['owner_command_ref'])
            return read(self.state, query)
        except Reject as e:
            return e.as_result()

    def amend_brief(self, feature_id: str, amendment: Mapping[str, Any], owner_identity: Result) -> Result:
        """An amendment never mutates the original brief; it revises the pinned contract."""
        try:
            check_id(feature_id)
            change = fields(amendment,
                            {'project_id', 'expected_revision', 'deliverable_id', 'description', 'owner_proof_ref'},
                            {'project_id', 'expected_revision', 'deliverable_id', 'description', 'owner_proof_ref'})
            for field in ('project_id', 'deliverable_id', 'owner_proof_ref'): check_id(change[field])
            check_text(change['description'])
            if type(change['expected_revision']) is not int or change['expected_revision'] < 1: reject('INVALID_INPUT')
            def query(tx):
                brief = read_one(tx, 'SELECT * FROM v1_briefs WHERE feature_id=?', (feature_id,))
                if not brief: reject('NOT_FOUND', 'NOT_FOUND')
                if brief['project_id'] != change['project_id']: reject('WRONG_PROJECT')
                project = read_one(tx, 'SELECT * FROM v1_projects WHERE project_id=?', (brief['project_id'],))
                if not project: reject('EVIDENCE_INVALID', 'BLOCKED_LOCAL')
                owner_principal(owner_identity, project['owner_id'])
                if project['state'] != 'ACTIVE': reject('SCOPE_EXCEEDED')
                return validate_record('ProductionBrief', brief)
            original = read(self.state, query)
            if self.contract_port is None: reject('LOCAL_RECOVERY_REQUIRED', 'BLOCKED_LOCAL')
            # Explicit owner decision verified in the compiler, separately from session identity.
            change = dict(change)
            change['feature_id'] = feature_id
            return self.contract_port.revise_contract(feature_id, change, original, self.contract_port.current_bible_pair_digest(feature_id))
        except Reject as e:
            return e.as_result()
