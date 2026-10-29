"""CAB-B-local R9 port results, strict record boundaries and scoped state adapter.

P2 owns the canonical ports and supplies a table-limited state capability for
v1_projects, v1_project_constitutions, v1_briefs, v1_feature_contracts.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable

ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$')
SHA = re.compile(r'^[a-f0-9]{64}$')
KINDS = {'NARRATIVE', 'QUEST_GRAPH', 'DIALOGUE', 'SCENE', 'CODE', 'ASSET', 'AUDIO', 'ANIMATION', 'TEST', 'OTHER'}
STATES = {'INCUBATING', 'ACTIVE', 'PAUSED'}


from nomad_studio.contracts.runtime_ports_v1 import Result


@dataclass(frozen=True, slots=True)
class Reject(Exception):
    code: str
    status: str = 'REJECTED'
    record: Mapping[str, Any] | None = None
    def as_result(self) -> Result:
        return Result(self.status, self.code, self.record)


def reject(code: str, status: str = 'REJECTED', record: Mapping[str, Any] | None = None) -> None:
    raise Reject(code, status, record)


def result(status: str, record: Mapping[str, Any], *, receipt_ref: str | None = None, evidence_refs: tuple[str, ...] = ()) -> Result:
    return Result(status, 'OK', record, receipt_ref, evidence_refs)


def canonical(obj: Any) -> str:
    try:
        return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        reject('INVALID_INPUT')
    raise AssertionError()


def sha(obj: Any) -> str:
    return hashlib.sha256(canonical(obj).encode('utf-8')).hexdigest()


def text_digest(value: str) -> str:
    # Digest the unchanged owner-submitted UTF-8 text, not canonicalized JSON.
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def check_id(value: Any) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        reject('INVALID_INPUT')
    return value


def check_digest(value: Any) -> str:
    if not isinstance(value, str) or not SHA.fullmatch(value):
        reject('INVALID_INPUT')
    return value


def check_text(value: Any, limit: int = 16384) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or '\x00' in value:
        reject('INVALID_INPUT')
    return value


def fields(value: Any, keys: set[str], required: set[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not required <= set(value) or not set(value) <= keys:
        reject('INVALID_INPUT')
    return dict(value)


def validate_record(kind: str, value: Any) -> dict[str, Any]:
    if kind == 'ProjectSpec':
        obj = fields(value, {'project_id', 'owner_principal_id', 'constitution_digest', 'initial_state', 'repository_ref'},
                     {'project_id', 'owner_principal_id', 'constitution_digest', 'initial_state'})
        check_id(obj['project_id']); check_id(obj['owner_principal_id']); check_digest(obj['constitution_digest'])
        if obj['initial_state'] not in STATES: reject('INVALID_INPUT')
        if 'repository_ref' in obj: check_text(obj['repository_ref'])
    elif kind == 'ProductionBrief':
        obj = fields(value, {'brief_id', 'project_id', 'owner_principal_id', 'original_text', 'original_digest',
                            'owner_command_ref', 'feature_id', 'submitted_at_us'},
                     {'brief_id', 'project_id', 'owner_principal_id', 'original_text', 'original_digest',
                      'owner_command_ref', 'feature_id', 'submitted_at_us'})
        for field in ('brief_id', 'project_id', 'owner_principal_id', 'owner_command_ref', 'feature_id'): check_id(obj[field])
        check_text(obj['original_text']); check_digest(obj['original_digest'])
        if type(obj['submitted_at_us']) is not int or obj['submitted_at_us'] < 1: reject('INVALID_INPUT')
    elif kind == 'DeliverableRequirement':
        obj = fields(value, {'deliverable_id', 'kind', 'description', 'requirement_refs', 'acceptance_ids', 'required'},
                     {'deliverable_id', 'kind', 'description', 'requirement_refs', 'acceptance_ids', 'required'})
        check_id(obj['deliverable_id']); check_text(obj['description'])
        if obj['kind'] not in KINDS or type(obj['required']) is not bool: reject('INVALID_INPUT')
        for array_name in ('requirement_refs', 'acceptance_ids'):
            arr = obj[array_name]
            if not isinstance(arr, list) or len(arr) > 4096 or len(set(map(str, arr))) != len(arr): reject('INVALID_INPUT')
            for item in arr: check_id(item)
    elif kind == 'FeatureContract':
        obj = fields(value, {'project_id', 'feature_id', 'brief_id', 'original_brief_digest', 'bible_pair_digest',
                            'revision', 'contract_digest', 'scope_digest', 'deliverables', 'acceptance_ids',
                            'budget_limit_units', 'required_capabilities', 'unresolved_boundary_refs',
                            'repository_ref', 'feature_branch'},
                     {'project_id', 'feature_id', 'brief_id', 'original_brief_digest', 'bible_pair_digest',
                      'revision', 'contract_digest', 'scope_digest', 'deliverables', 'acceptance_ids',
                      'budget_limit_units', 'required_capabilities', 'unresolved_boundary_refs'})
        for field in ('project_id', 'feature_id', 'brief_id'): check_id(obj[field])
        for field in ('original_brief_digest', 'bible_pair_digest', 'contract_digest', 'scope_digest'): check_digest(obj[field])
        if type(obj['revision']) is not int or obj['revision'] < 1 or type(obj['budget_limit_units']) is not int or obj['budget_limit_units'] < 0: reject('INVALID_INPUT')
        if not isinstance(obj['deliverables'], list) or not obj['deliverables']: reject('INVALID_INPUT')
        for item in obj['deliverables']: validate_record('DeliverableRequirement', item)
        for field in ('acceptance_ids', 'required_capabilities', 'unresolved_boundary_refs'):
            arr = obj[field]
            if not isinstance(arr, list) or len(arr) > 4096 or len(set(map(str, arr))) != len(arr): reject('INVALID_INPUT')
            for item in arr: check_id(item)
        for field in ('repository_ref', 'feature_branch'):
            if field in obj: check_text(obj[field])
    else:
        reject('INVALID_INPUT')
    return obj


def owner_principal(identity: Result, expected: str) -> str:
    # Verified identity is passed only from the root's trusted identity port.
    # Never use owner_id/role from the submitted project or brief as a proof.
    if (getattr(identity, 'status', None) not in {'OK', 'ACCEPTED'} or getattr(identity, 'code', None) != 'OK'
            or not isinstance(getattr(identity, 'record', None), Mapping)):
        reject('UNAUTHENTICATED')
    principal = identity.record.get('principal_id')
    if not isinstance(principal, str): reject('UNAUTHENTICATED')
    if principal != expected: reject('PERMISSION_DENIED')
    if not identity.receipt_ref: reject('EVIDENCE_INVALID')
    check_id(identity.receipt_ref)
    return principal


def read_one(tx: Any, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
    row = tx.fetch_one(sql, params)
    return dict(row) if row is not None else None


def read(store: Any, fn: Callable[[Any], Any]) -> Any:
    if not callable(getattr(store, 'read', None)):
        reject('LOCAL_RECOVERY_REQUIRED', 'BLOCKED_LOCAL')
    return store.read(fn)


def write(store: Any, fn: Callable[[Any], Any]) -> Any:
    # The injected write is a CAB-B-only transaction capability supplied by P2.
    if not callable(getattr(store, 'write', None)):
        reject('LOCAL_RECOVERY_REQUIRED', 'BLOCKED_LOCAL')
    return store.write(fn)
