"""CAB-I R9 core. Injectable capabilities, no global SQL connection or provider effects.

P2 must inject a CAB-I table-restricted serial transaction capability. All provider
calls occur outside writer transactions. A test fixture may implement this protocol
using SQLite; it is never production authority by itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any, Mapping, Sequence


from nomad_studio.contracts.runtime_ports_v1 import Result, Decision


@dataclass(frozen=True, slots=True)
class SourceFence:
    project_id: str
    repo: str
    branch: str
    expected_parent_commit: str
    resource_scope_digest: str
    feature_id: str
    bible_pair_digest: str


class Invalid(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def json_bytes(obj: object) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def digest(obj: object) -> str:
    return sha256(json_bytes(obj)).hexdigest()


def bytes_digest(data: bytes) -> str:
    return sha256(data).hexdigest()


_SHA = re.compile(r'^[0-9a-f]{64}$')
_COMMIT = re.compile(r'^[0-9a-f]{40}(?:[0-9a-f]{24})?$')
_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$')


def check_sha(value: Any, *, commit: bool = False) -> str:
    if not isinstance(value, str) or not (_COMMIT if commit else _SHA).fullmatch(value):
        raise Invalid('INVALID_INPUT', 'invalid immutable digest')
    return value


def check_id(value: Any) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise Invalid('INVALID_INPUT', 'invalid bounded identifier')
    return value


def validate(record: Mapping[str, Any], required: Sequence[str], optional: Sequence[str] = ()) -> dict[str, Any]:
    if not isinstance(record, Mapping) or not set(required) <= record.keys() or not record.keys() <= set(required) | set(optional):
        raise Invalid('INVALID_INPUT', 'record has missing or unknown keys')
    return dict(record)


def normalize_path(path: str, *, casefold: bool = True) -> tuple[str, str]:
    """Reject aliases before normalizing so one file cannot acquire many lease keys."""
    if not isinstance(path, str) or not path or len(path) > 16384 or '\x00' in path:
        raise Invalid('INVALID_INPUT', 'invalid resource path')
    if path.startswith(('/', '\\')) or re.match(r'^[A-Za-z]:', path):
        raise Invalid('SCOPE_EXCEEDED', 'absolute or drive-qualified path')
    if not path.isascii() or path.casefold().startswith('.git/') or path.casefold() == '.git':
        raise Invalid('SCOPE_EXCEEDED', 'ambiguous or protected path')
    if '\\' in path or '//' in path or any(s in ('', '.', '..') for s in path.split('/')):
        raise Invalid('SCOPE_EXCEEDED', 'traversal or noncanonical path')
    reserved = {'con', 'prn', 'aux', 'nul'} | {f'com{i}' for i in range(1, 10)} | {f'lpt{i}' for i in range(1, 10)}
    if any(s.endswith((' ', '.')) or s.startswith(' ') or ':' in s or s.split('.')[0].casefold() in reserved for s in path.split('/')):
        raise Invalid('SCOPE_EXCEEDED', 'platform alias path')
    return path, path.casefold() if casefold else path


def overlapping(a: str, b: str) -> bool:
    return a == b or a.startswith(b + '/') or b.startswith(a + '/')


def row_one(db: Any, sql: str, params: tuple = ()) -> Mapping[str, Any] | None:
    rows = db.read(sql, params)
    return dict(rows[0]) if rows else None


def run_record(db: Any, feature_id: str, project_id: str | None = None) -> dict[str, Any]:
    row = row_one(db, 'SELECT * FROM v1_feature_runs WHERE feature_id=?', (check_id(feature_id),))
    if row is None:
        raise Invalid('NOT_FOUND', 'feature does not exist')
    if project_id is not None and row['project_id'] != project_id:
        raise Invalid('WRONG_PROJECT', 'feature belongs to another project')
    return dict(row)


def check_lineage(run: Mapping[str, Any], record: Mapping[str, Any]) -> None:
    if record.get('project_id') != run['project_id']:
        raise Invalid('WRONG_PROJECT', 'cross-project record')
    if record.get('feature_id') != run['feature_id']:
        raise Invalid('WRONG_FEATURE', 'cross-feature record')
    if record.get('bible_pair_digest') != run['bible_pair_digest']:
        raise Invalid('STALE_BIBLE', 'stale bible pair')
    if record.get('feature_contract_digest') != run['active_contract_digest']:
        raise Invalid('REVISION_CONFLICT', 'stale feature contract')


def failure(exc: Exception) -> Result:
    if not isinstance(exc, Invalid):
        raise exc
    status = ('NOT_FOUND' if exc.code == 'NOT_FOUND' else
              'CONFLICT' if exc.code in ('IDEMPOTENCY_CONFLICT', 'LEASE_CONFLICT', 'LEASE_FENCED',
                                         'SOURCE_CONFLICT', 'REVISION_CONFLICT', 'STALE_BIBLE') else 'REJECTED')
    return Result(status, exc.code)


def store_bytes(cas: Any, data: bytes) -> tuple[str, str]:
    """CAS returns durable provider ref; independently reread before trusting content."""
    if not isinstance(data, bytes):
        raise Invalid('INVALID_INPUT', 'CAS only accepts byte strings')
    sha = bytes_digest(data)
    try:
        ref = cas.put(data, sha)
        if not isinstance(ref, str) or not ref or cas.get(ref) != data:
            raise Invalid('EVIDENCE_INVALID', 'CAS readback mismatched content')
    except (OSError, KeyError, LookupError) as exc:
        raise Invalid('EVIDENCE_INVALID', 'CAS unavailable during readback') from exc
    return sha, ref


def verified_bytes(cas: Any, ref: str, expected: str) -> bytes:
    check_sha(expected)
    try:
        data = cas.get(ref)
    except (OSError, KeyError, LookupError) as exc:
        raise Invalid('EVIDENCE_INVALID', 'CAS evidence missing') from exc
    if not isinstance(data, bytes) or bytes_digest(data) != expected:
        raise Invalid('EVIDENCE_INVALID', 'CAS bytes mismatch')
    return data
