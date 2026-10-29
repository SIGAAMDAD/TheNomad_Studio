"""R9 project registry. Sole writer of v1_projects/v1_project_constitutions.

`state` is a P2-supplied CAB-B-only transaction capability, not a SQLite
connection or a general cross-domain state store. There is no hierarchy.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from nomad_studio.production.intake._support import (
    Result, Reject, canonical, check_digest, check_id, owner_principal,
    reject, result, read_one, write, read, validate_record,
)


class ProjectRegistry:
    def __init__(self, state: Any):
        self.state = state

    def register_project(self, project_spec: Mapping[str, Any], authenticated_owner: Result) -> Result:
        try:
            spec = validate_record('ProjectSpec', project_spec)
            owner_principal(authenticated_owner, spec['owner_principal_id'])
            check_digest(spec['constitution_digest'])
            def transaction(tx):
                row = read_one(tx, 'SELECT * FROM v1_projects WHERE project_id=?', (spec['project_id'],))
                if row:
                    constitution = read_one(tx, 'SELECT payload_json FROM v1_project_constitutions WHERE project_id=? AND epoch=1', (spec['project_id'],))
                    if constitution and json.loads(constitution['payload_json']) == spec:
                        return result('OK', spec)
                    reject('REVISION_CONFLICT', 'CONFLICT')
                if not authenticated_owner.receipt_ref:
                    reject('EVIDENCE_INVALID')
                tx.execute('INSERT INTO v1_projects(project_id,owner_id,constitution_digest,state,authority_epoch) VALUES (?,?,?,?,?)',
                           (spec['project_id'], spec['owner_principal_id'], spec['constitution_digest'], spec['initial_state'], 1))
                tx.execute('INSERT INTO v1_project_constitutions(project_id,epoch,constitution_digest,owner_activation_ref,payload_json) VALUES (?,?,?,?,?)',
                           (spec['project_id'], 1, spec['constitution_digest'], authenticated_owner.receipt_ref, canonical(spec)))
                return result('OK', spec, receipt_ref=authenticated_owner.receipt_ref)
            return write(self.state, transaction)
        except Reject as e:
            return e.as_result()

    def set_project_state(self, project_id: str, expected_epoch: int, new_state: str,
                          authenticated_owner: Result) -> Result:
        try:
            check_id(project_id)
            if type(expected_epoch) is not int or expected_epoch < 1 or new_state not in {'INCUBATING', 'ACTIVE', 'PAUSED'}:
                reject('INVALID_INPUT')
            def transaction(tx):
                current = read_one(tx, 'SELECT * FROM v1_projects WHERE project_id=?', (project_id,))
                if not current:
                    reject('NOT_FOUND', 'NOT_FOUND')
                owner_principal(authenticated_owner, current['owner_id'])
                if current['authority_epoch'] != expected_epoch:
                    reject('REVISION_CONFLICT', 'CONFLICT')
                updated = tx.execute('UPDATE v1_projects SET state=?, authority_epoch=authority_epoch+1 WHERE project_id=? AND authority_epoch=?',
                                     (new_state, project_id, expected_epoch))
                if getattr(updated, 'rowcount', updated if isinstance(updated, int) else None) != 1:
                    reject('REVISION_CONFLICT', 'CONFLICT')
                return result('OK', {'project_id': project_id, 'state': new_state, 'authority_epoch': expected_epoch + 1})
            return write(self.state, transaction)
        except Reject as e:
            return e.as_result()

    def get_project(self, project_id: str) -> Result:
        try:
            check_id(project_id)
            def query(tx):
                project = read_one(tx, 'SELECT * FROM v1_projects WHERE project_id=?', (project_id,))
                if not project:
                    reject('NOT_FOUND', 'NOT_FOUND')
                constitution = read_one(tx, 'SELECT payload_json FROM v1_project_constitutions WHERE project_id=? AND epoch=1', (project_id,))
                if not constitution:
                    reject('EVIDENCE_INVALID', 'BLOCKED_LOCAL')
                spec = json.loads(constitution['payload_json'])
                return result('OK', {**spec, 'state': project['state'], 'authority_epoch': project['authority_epoch']})
            return read(self.state, query)
        except Reject as e:
            return e.as_result()

    def list_owner_projects(self, owner_principal_id: str) -> list[dict[str, Any]]:
        check_id(owner_principal_id)
        def query(tx):
            projects = tx.fetch_all('SELECT * FROM v1_projects WHERE owner_id=? ORDER BY project_id', (owner_principal_id,))
            return [dict(p) for p in projects]
        return read(self.state, query)


def build_project_port(*, state: Any) -> ProjectRegistry:
    return ProjectRegistry(state)
