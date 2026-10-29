"""Closed Council record contracts; no R7/R8 runtime alias or hidden extensions."""
from __future__ import annotations
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
SHA = re.compile(r"^[0-9a-f]{64}$")
CASE_KINDS = frozenset({'DESIGN', 'API', 'ASSET', 'IMPLEMENTATION'})
PROTECTED = frozenset({'BIBLE_ACTIVATE', 'ACTIVATE_BIBLE', 'MINT_OWNER_APPROVAL',
                        'FORGE_QA', 'PROTECTED_MAINLINE_MERGE', 'RELEASE_PUBLISH',
                        'CONSTITUTION_AMEND', 'BUDGET_EXPAND', 'PROJECT_SCOPE_EXPAND',
                        'EXTERNAL_UNSCOPED_WRITE', 'RECOVERY_OVERRIDE'})

class InvalidRecord(ValueError):
    pass


def object_record(raw: object, required: set[str], optional: set[str] = frozenset()) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != set(required) | (set(raw) & optional):
        raise InvalidRecord('unexpected or missing record fields')
    if any(type(k) is not str for k in raw):
        raise InvalidRecord('invalid field key')
    return dict(raw)


def ident(value: object) -> str:
    if not isinstance(value, str) or not IDENT.fullmatch(value):
        raise InvalidRecord('invalid identifier')
    return value


def sha(value: object) -> str:
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise InvalidRecord('invalid digest')
    return value


def text(value: object) -> str:
    if type(value) is not str or not value.strip() or '\x00' in value or len(value) > 16384:
        raise InvalidRecord('invalid text')
    return value


def refs(value: object, *, max_items: int = 256, required: bool = False) -> list[str]:
    if type(value) is not list or len(value) > max_items or (required and not value):
        raise InvalidRecord('invalid references')
    result = [ident(x) for x in value]
    if len(result) != len(set(result)):
        raise InvalidRecord('duplicate references')
    return result


def digest(value: object) -> str:
    try:
        data = json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                          separators=(',', ':')).encode('utf-8')
    except (TypeError, ValueError, RecursionError, OverflowError) as e:
        raise InvalidRecord('invalid JSON record') from e
    if len(data) > 524288:
        raise InvalidRecord('record size exceeded')
    return hashlib.sha256(data).hexdigest()


def proposal(raw: object) -> dict[str, Any]:
    p = object_record(raw, {'proposal_id', 'author_cabal_id', 'summary', 'clause_refs',
                            'affected_resources', 'dependency_refs', 'provenance_refs', 'action'})
    ident(p['proposal_id']); ident(p['author_cabal_id']); text(p['summary'])
    for k in ('clause_refs', 'affected_resources', 'dependency_refs', 'provenance_refs'):
        refs(p[k], required=k in ('clause_refs', 'provenance_refs'))
    action = ident(p['action'])
    if action in PROTECTED or action != 'RESOLVE_IN_BIBLE':
        raise InvalidRecord('council has no protected mutation authority')
    return p


def conflict(raw: object) -> dict[str, Any]:
    c = object_record(raw, {'case_id', 'project_id', 'feature_contract_digest',
          'graph_revision', 'kind', 'summary', 'clause_refs', 'affected_resources',
          'dependency_refs', 'provenance_refs', 'proposals'})
    ident(c['case_id']); ident(c['project_id']); sha(c['feature_contract_digest'])
    if type(c['graph_revision']) is not int or c['graph_revision'] < 0:
        raise InvalidRecord('invalid graph revision')
    if c['kind'] not in CASE_KINDS: raise InvalidRecord('unsupported collision kind')
    text(c['summary'])
    for k in ('clause_refs', 'affected_resources', 'dependency_refs', 'provenance_refs'):
        refs(c[k], required=k in ('clause_refs', 'provenance_refs'))
    if type(c['proposals']) is not list or not 2 <= len(c['proposals']) <= 32:
        raise InvalidRecord('council must retain alternative proposals')
    c['proposals'] = [proposal(x) for x in c['proposals']]
    ids = [p['proposal_id'] for p in c['proposals']]
    if len(ids) != len(set(ids)):
        raise InvalidRecord('duplicate proposals')
    for p in c['proposals']:
        for key in ('clause_refs', 'affected_resources', 'dependency_refs'):
            if not set(p[key]) <= set(c[key]):
                raise InvalidRecord('proposal exceeds conflict scope')
    return c


def dissent(raw: object) -> dict[str, Any]:
    d = object_record(raw, {'dissent_id', 'author_id', 'reason', 'proposal_id',
                            'clause_refs', 'evidence_refs'})
    ident(d['dissent_id']); ident(d['author_id']); ident(d['proposal_id'])
    text(d['reason']); refs(d['clause_refs'], required=True); refs(d['evidence_refs'], required=True)
    return d


def selected(raw: object) -> str:
    v = object_record(raw, {'proposal_id'})
    return ident(v['proposal_id'])
