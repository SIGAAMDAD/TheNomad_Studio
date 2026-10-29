"""Frozen publication payload validation and provider-neutral observations."""
from __future__ import annotations
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

HEX = re.compile(r'^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$')
SHA256 = re.compile(r'^[0-9a-f]{64}$')
KEY = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$')
IDENT = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,255}$')
REPO = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')
REF = re.compile(r'^refs/heads/[A-Za-z0-9_./-]+$')

class ValidationError(ValueError): pass
class ProviderConflict(Exception): pass
class ProviderUnknown(Exception): pass
class ProviderForbidden(Exception): pass
class ProviderRateLimited(Exception):
    def __init__(self, retry_after_us: int = 0): self.retry_after_us = retry_after_us

@dataclass(frozen=True)
class Observation:
    kind: str  # APPLIED, ABSENT_SAFE, ABSENT_UNSAFE, CONFLICT, UNKNOWN
    revision: str | None = None
    detail: str = ''


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fields(value: object, required: set[str], optional: set[str] = frozenset()) -> dict:
    if not isinstance(value, Mapping) or set(value) - (required | optional) or required - set(value):
        raise ValidationError('closed payload field set violated')
    if not all(isinstance(k, str) for k in value): raise ValidationError('non-string field')
    return dict(value)


def ident(v: Any, pattern=IDENT) -> str:
    if not isinstance(v, str) or not pattern.fullmatch(v): raise ValidationError('invalid identifier')
    return v


def positive(v: Any, *, zero: bool = False) -> int:
    if type(v) is not int or v < (0 if zero else 1): raise ValidationError('invalid nonnegative integer')
    return v


def path(v: Any) -> str:
    if not isinstance(v, str) or not v or len(v) > 512 or '\\' in v or '\x00' in v:
        raise ValidationError('unsafe path')
    parts = v.split('/')
    if v.startswith('/') or any(x in ('', '.', '..') for x in parts): raise ValidationError('unsafe path')
    return v


def revision(v: Any) -> str:
    if not isinstance(v, str) or not v or len(v) > 256 or any(ord(c) < 33 or ord(c) > 126 for c in v):
        raise ValidationError('invalid revision')
    return v


def validate_prepare(value: object) -> dict:
    p = fields(value, {'provider','action','effect_key','artifact_ref','artifact_sha256',
                       'artifact_size','expected_revision','target','bindings'})
    provider, action = p['provider'], p['action']
    if (provider, action) not in {('github','update_ref'), ('github','create_pr'),('drive','replace_file')}:
        raise ValidationError('unsupported provider action')
    p['effect_key'] = ident(p['effect_key'], KEY)
    p['artifact_ref'] = ident(p['artifact_ref'])
    if not isinstance(p['artifact_sha256'], str) or not SHA256.fullmatch(p['artifact_sha256']):
        raise ValidationError('invalid byte digest')
    positive(p['artifact_size'])
    p['expected_revision'] = revision(p['expected_revision'])
    b = fields(p['bindings'], {'job_id','assignment_epoch','source_sha','source_path',
                              'lease_id','lease_epoch','qa_receipt_id','qa_subject_sha','approval_ref'})
    for k in ('job_id','lease_id','qa_receipt_id','approval_ref'): ident(b[k])
    for k in ('assignment_epoch','lease_epoch'): positive(b[k], zero=True)
    for k in ('source_sha','qa_subject_sha'):
        if not isinstance(b[k], str) or not HEX.fullmatch(b[k]): raise ValidationError('invalid source or QA SHA')
    path(b['source_path'])
    if b['qa_subject_sha'].lower() != b['source_sha'].lower():
        raise ValidationError('review subject differs from source')
    t = p['target']
    if action == 'update_ref':
        t = fields(t, {'repository','ref','proposed_revision','manifest_path'})
        ident(t['repository'], REPO); ident(t['ref'], REF); path(t['manifest_path'])
        if not HEX.fullmatch(str(t['proposed_revision'])): raise ValidationError('invalid proposed git SHA')
        if not HEX.fullmatch(p['expected_revision']): raise ValidationError('invalid expected git SHA')
        if t['proposed_revision'] == p['expected_revision']: raise ValidationError('no-op ref proposal')
    elif action == 'create_pr':
        t = fields(t, {'repository','head_ref','base_ref','head_revision','manifest_path','title','body'})
        ident(t['repository'], REPO)
        ident(t['head_ref'], REF); ident(t['base_ref'], REF)
        if t['head_ref'] == t['base_ref']: raise ValidationError('self PR')
        if not HEX.fullmatch(str(t['head_revision'])) or t['head_revision'] != p['expected_revision']:
            raise ValidationError('head SHA mismatch')
        path(t['manifest_path'])
        if not isinstance(t['title'], str) or not 1 <= len(t['title']) <= 250:
            raise ValidationError('invalid PR title')
        if not isinstance(t['body'], str) or len(t['body']) > 16000: raise ValidationError('invalid PR body')
    else:
        t = fields(t, {'file_id','parent_id','mime_type'})
        for k in ('file_id','parent_id'): ident(t[k])
        if not isinstance(t['mime_type'], str) or not 1 <= len(t['mime_type']) <= 128:
            raise ValidationError('invalid MIME')
    p['target'] = t
    p['bindings'] = b
    if len(canonical(p)) > 32768: raise ValidationError('payload too large')
    return p


def fingerprint(p: Mapping[str, Any]) -> str:
    return digest(canonical(p).encode('utf-8'))
